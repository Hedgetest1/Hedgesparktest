"""
llm_pii_guard.py — Runtime PII scanner for outgoing LLM prompts.

The DPIA claims HedgeSpark never sends raw PII to Anthropic / OpenAI.
That claim relied on code review + discipline, with no runtime check.
This module is the runtime enforcement.

Every LLM call site passes the final `user_message` through `assert_clean`
(or `sanitize`) before the HTTP request is fired. If the scanner detects
PII patterns, the call is either:

  * Blocked — `assert_clean` raises `LLMPayloadViolation`. Caller returns
    an empty string (same as a budget-exhausted call).
  * Sanitized — `sanitize` replaces the match with `<redacted:kind>`.

Detection is regex-only (no AST, no tokenization) so the guard stays
well under 1ms per call. False positives are the acceptable failure
mode — blocking a suspect prompt costs an LLM call, leaking PII costs
a regulator action.

Why regex-only vs semantic NLP (Presidio/spaCy NER)
---------------------------------------------------
A 2026-04-23 devil's-advocate audit flagged "could miss natural-
language PII like 'John at Acme ordered yesterday'". The evaluation:

  1. Our LLM flow sends ONLY aggregated metrics (DPIA Art. 28). No
     module concatenates raw customer rows into a prompt. The
     chatbot snapshot passes orders/revenue/products — shop-scope
     structured data, not merchant-narrative text.
  2. Structured-PII patterns (email, phone, card, token, key) ARE
     the exhaustive threat surface for our architecture. Regex hits
     every one.
  3. Semantic NER adds ~200MB model + 8-40 €/mo compute at scale
     for a threat that doesn't exist in our current call shape.

Decision: regex is top-1 for THIS architecture. If a future code
path adds free-form customer text to an LLM prompt (e.g. merchant
pastes support-ticket copy), revisit — but add the NLP layer THEN,
not prematurely. Logged to ledger as [LLM-01] at that trigger.

Patterns
--------
    * Email addresses (RFC 5322-ish)
    * Credit card shapes (Luhn-free 13-19 digits with separators)
    * Phone numbers (E.164 + common national formats)
    * IBAN (2 letters + 13-32 alphanumerics)
    * Shopify access tokens (`shpat_*`, `shpss_*`, `shpca_*`)
    * Bearer / JWT / OAuth secrets
    * Anthropic / OpenAI / Resend API key shapes

Shop domains (`*.myshopify.com`) are NOT treated as PII — they are
tenant identifiers already scoped to the operator, and the autonomous
pipeline legitimately needs them to reason about cross-shop patterns.
Operators who want to redact them too can set
`LLM_PII_GUARD_REDACT_SHOPS=1`.

Public API
----------
    check_for_pii(text) -> list[dict]         # detection only
    sanitize(text) -> tuple[str, list[dict]]  # replace + report
    assert_clean(text, *, context) -> None    # raise on any match
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger("llm_pii_guard")


class LLMPayloadViolation(Exception):
    """Raised when an outgoing LLM prompt contains banned PII patterns."""


# ---------------------------------------------------------------------------
# Regex catalogue
# ---------------------------------------------------------------------------
#
# Ordering matters — more specific patterns (Shopify tokens) before
# generic ones (bearer) so the first-match winner tags the right kind.

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

_SHOPIFY_TOKEN_RE = re.compile(
    r"\bshp(at|ss|ca)_[A-Za-z0-9]{20,64}\b"
)

_ANTHROPIC_KEY_RE = re.compile(
    r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"
)

_OPENAI_KEY_RE = re.compile(
    r"\bsk-[A-Za-z0-9_\-]{20,}\b"
)

_RESEND_KEY_RE = re.compile(
    r"\bre_[A-Za-z0-9_\-]{20,}\b"
)

_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"
)

# IBAN: 2 country letters + up to 32 alphanumerics. We require a minimum
# of 15 chars total to reduce false positives on random uppercase tokens.
_IBAN_RE = re.compile(
    r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b"
)

# Credit card shape — 13-19 digits with optional grouping separators.
# Requires at least 13 digits total, ignores shorter numeric sequences.
_CC_RE = re.compile(
    r"\b(?:\d[ \-]?){12,18}\d\b"
)

# Phone: E.164 (+CC then 7-14 digits) OR national with parens + groups.
_PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s\-]?)?(?:\(\d{2,4}\)[\s\-]?)?\d{3,4}[\s\-]\d{3,4}(?:[\s\-]\d{2,4})?"
)

_BEARER_RE = re.compile(
    r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE
)

_PASSWORD_LIKE_RE = re.compile(
    r"password\s*[:=]\s*['\"]?[^\s'\"]{6,}",
    re.IGNORECASE,
)

_MYSHOPIFY_RE = re.compile(
    r"\b[a-z0-9\-]{1,63}\.myshopify\.com\b",
    re.IGNORECASE,
)


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key",  _ANTHROPIC_KEY_RE),
    ("shopify_token",  _SHOPIFY_TOKEN_RE),
    ("resend_key",     _RESEND_KEY_RE),
    ("openai_key",     _OPENAI_KEY_RE),
    ("jwt",            _JWT_RE),
    ("bearer_token",   _BEARER_RE),
    ("password_like",  _PASSWORD_LIKE_RE),
    ("email",          _EMAIL_RE),
    ("iban",           _IBAN_RE),
    ("credit_card",    _CC_RE),
    # Phone is the noisiest — run last so more specific matches win.
    ("phone",          _PHONE_RE),
]


# ---------------------------------------------------------------------------
# Detection / sanitization
# ---------------------------------------------------------------------------

def _redact_shop_domains_enabled() -> bool:
    return os.getenv("LLM_PII_GUARD_REDACT_SHOPS", "").strip() == "1"


def check_for_pii(text: str | None) -> list[dict[str, Any]]:
    """Return a list of detected PII spans. Empty list means clean."""
    if not text:
        return []
    findings: list[dict[str, Any]] = []
    seen_spans: set[tuple[int, int]] = set()

    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            # Don't double-report overlapping matches — respect
            # ordering (first pattern wins for a given span).
            if any(s <= span[0] < e for s, e in seen_spans):
                continue
            seen_spans.add(span)
            findings.append({
                "kind": kind,
                "span": span,
                "snippet": match.group(0)[:40],
            })

    if _redact_shop_domains_enabled():
        for match in _MYSHOPIFY_RE.finditer(text):
            span = match.span()
            if any(s <= span[0] < e for s, e in seen_spans):
                continue
            seen_spans.add(span)
            findings.append({
                "kind": "shop_domain",
                "span": span,
                "snippet": match.group(0)[:40],
            })

    return findings


def sanitize(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (redacted_text, findings). Preserves text length structure
    for debugging — each match is replaced by `<redacted:kind>`."""
    findings = check_for_pii(text)
    if not findings:
        return text, []
    # Apply replacements right-to-left so earlier span indices stay valid.
    sorted_findings = sorted(findings, key=lambda f: f["span"][0], reverse=True)
    out = text
    for f in sorted_findings:
        start, end = f["span"]
        out = out[:start] + f"<redacted:{f['kind']}>" + out[end:]
    return out, findings


def assert_clean(text: str | None, *, context: str = "llm_prompt") -> None:
    """Raise `LLMPayloadViolation` if `text` contains any PII pattern.

    The error message lists the offending kinds but NEVER echoes the
    snippets — we don't want to replay the leak into logs. The caller
    is responsible for handling the exception (typically: return empty
    string, bump a counter, surface via digest).
    """
    findings = check_for_pii(text)
    if not findings:
        return
    kinds = sorted({f["kind"] for f in findings})
    _bump_violation_counter()
    raise LLMPayloadViolation(
        f"llm_pii_guard: refusing {context} — detected patterns: {kinds}"
    )


# ---------------------------------------------------------------------------
# Violation counter — surfaced in the compliance synthesizer
# ---------------------------------------------------------------------------

_COUNTER_KEY = "hs:llm_pii_guard:violations"


def _bump_violation_counter() -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_pii_guard.bump")
            return
        from datetime import datetime as _dt, timezone as _tz
        day = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        key = f"{_COUNTER_KEY}:{day}"
        rc.incr(key)
        rc.expire(key, 90 * 24 * 3600)
    except Exception as exc:
        log.warning("llm_pii_guard: violation counter bump failed: %s", exc)


def get_violation_count_7d() -> int:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("llm_pii_guard.read")
            return 0
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        today = _dt.now(_tz.utc)
        total = 0
        for offset in range(7):
            day = (today - _td(days=offset)).strftime("%Y-%m-%d")
            raw = rc.get(f"{_COUNTER_KEY}:{day}")
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                total += int(raw)
            except ValueError:
                pass
        return total
    except Exception as exc:
        log.warning("llm_pii_guard: violation count read failed: %s", exc)
        return 0
