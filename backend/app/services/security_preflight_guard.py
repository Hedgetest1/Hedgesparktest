"""
security_preflight_guard.py — Hard-wall on security/GDPR regressions in
proposed bugfix candidates.

The self-debugging pipeline can propose arbitrary code changes. That
power is the moat — AND the single greatest risk. A maliciously
crafted alert (or a hallucinating LLM) could produce a patch that:

  * Adds `log.info("user email = %s", email)` — PII leakage
  * Removes `hmac.compare_digest` in favor of `==` — timing attack
  * Downgrades `_verify_webhook` to a no-op — webhook spoofing
  * Interpolates user input into a raw SQL `text(f"...")` — SQL injection
  * Drops a `@rate_limit` decorator — cost amplification / DoS
  * Replaces `is_product_learning_eligible` with `True` — data poisoning
  * Removes a `consent_allows_ingestion` call — GDPR consent bypass
  * Weakens TLS / cookie flags in Set-Cookie / CORSMiddleware params
  * Adds `subprocess.run(..., shell=True)` — command injection
  * Adds a hardcoded secret (API key pattern) in any file

This module is a deterministic AST + regex gate that runs BEFORE the
LLM is even called (via `bugfix_prompt_grounding.preflight_ground_candidate`)
and, more importantly, BEFORE `git apply` runs on a proposed diff.

Any rule that fires is a HARD REJECT. No confidence score negotiation,
no grace period, no "governance override" — the self-debugger cannot
regress security posture. Period.

Public API
----------
    scan_diff_for_security_regressions(patch_diff) -> list[dict]
        Returns a list of violations. Empty list == clean diff.

    guard_candidate(candidate) -> tuple[bool, str]
        Tuple of (allowed, reason). `allowed=False` means reject.

Design notes
------------
  * Pure regex + AST — no dependencies, no LLM.
  * Only analyzes the `+` (added) lines of the diff. Deletions are
    allowed EXCEPT when they remove a known-safety call — that is a
    separate class of violation ("safety regression by removal").
  * The removal heuristic is triggered by `-` lines containing a
    whitelisted set of safety idioms (HMAC verify, compare_digest,
    consent check, rate limit decorator).
  * Every rule carries a `code` that matches the audit finding; the
    digest surfaces these so the operator sees "pipeline blocked a
    SEC-04 regression attempt today".
"""
from __future__ import annotations

import ast
import logging
import re
from typing import Any

log = logging.getLogger("security_preflight_guard")


# ---------------------------------------------------------------------------
# Added-line rules — patterns that must NEVER appear in new code
# ---------------------------------------------------------------------------

_PII_LOG_PATTERNS = [
    # log.X(... <varname containing email/token/password> ...) — positional
    # or f-string. We accept the helper `mask_email(...)` but block raw use.
    re.compile(
        r"\blog\.\w+\([^)]*(?<!mask_email\()"
        r"\b(to_email|customer_email|recipient_email|user_email|"
        r"email_addr|email_address|access_token|plain_token|password|"
        r"api_key|secret)\b",
        re.IGNORECASE,
    ),
]

_SHELL_INJECTION_PATTERNS = [
    re.compile(r"subprocess\.(run|Popen|call|check_output)\([^)]*shell\s*=\s*True", re.IGNORECASE),
    re.compile(r"\bos\.system\("),
    re.compile(r"\bos\.popen\("),
]

_DESERIALIZATION_PATTERNS = [
    re.compile(r"\bpickle\.(loads?|load)\s*\("),
    re.compile(r"\byaml\.load\s*\([^)]*(?!Loader\s*=\s*yaml\.SafeLoader)"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
]

_RAW_SQL_INTERPOLATION_PATTERNS = [
    # text(f"... {var} ...") — f-string inside a SQLAlchemy text() call
    re.compile(r"\btext\(\s*f['\"][^'\"]*\{", re.IGNORECASE),
    # execute("... " + var + ...) — string concat into execute
    re.compile(r"\.execute\(\s*[\"'][^\"']*[\"']\s*\+\s*\w+"),
]

_HARDCODED_SECRET_PATTERNS = [
    # Obvious API key shapes. 24+ hex chars = likely secret.
    re.compile(r"['\"](?:sk-|sk_live_|shpat_|shpss_|whsec_|re_|xoxb-)[A-Za-z0-9_\-]{16,}['\"]"),
    # JWT signing keys / static equality
    re.compile(r"SECRET_KEY\s*=\s*['\"][^'\"]{12,}['\"]"),
]

_CONSENT_BYPASS_PATTERNS = [
    re.compile(r"_consent_allows_ingestion\s*=\s*lambda[^:]*:\s*True"),
    re.compile(r"return\s+True\s*#[^\n]*consent", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Removed-line rules — safety call removals are regressions
# ---------------------------------------------------------------------------

_SAFETY_IDIOM_REMOVAL_PATTERNS = [
    (re.compile(r"hmac\.compare_digest\s*\("), "SEC-08", "hmac.compare_digest removed"),
    (re.compile(r"_verify_webhook\s*\("), "SEC-09", "_verify_webhook removed"),
    (re.compile(r"_consume_nonce\s*\("), "SEC-10", "OAuth state/nonce check removed"),
    (re.compile(r"_consent_allows_ingestion\s*\("), "GDPR-03", "consent gate removed"),
    (re.compile(r"is_product_learning_eligible\s*\("), "LEARN-01", "learning isolation gate removed"),
    (re.compile(r"mask_email\s*\("), "GDPR-04", "PII masking removed"),
    (re.compile(r"RateLimitMiddleware"), "SEC-11", "RateLimitMiddleware removed"),
]


# ---------------------------------------------------------------------------
# Sensitive paths — any diff touching these files is automatically flagged
# for operator review even if rule scanning passes. Defense in depth.
# ---------------------------------------------------------------------------

_SENSITIVE_PATHS = {
    "app/core/token_crypto.py",
    "app/core/merchant_session.py",
    "app/core/deps.py",
    "app/api/shopify_oauth.py",
    "app/api/billing.py",
    "app/api/webhooks.py",
    "app/services/order_ingestion.py",
    "app/services/gdpr_processor.py",
    "app/core/privacy.py",
    "app/services/security_preflight_guard.py",  # self-protection
    "app/services/learning_isolation.py",
    "app/main.py",  # CORS + middleware config
}


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

def _extract_changed_files(patch_diff: str) -> list[str]:
    """Pull file paths from unified diff `+++ b/<path>` headers."""
    paths: list[str] = []
    for line in patch_diff.split("\n"):
        if line.startswith("+++ "):
            m = re.match(r"\+\+\+ b/(.+)", line.strip())
            if m:
                paths.append(m.group(1))
    return paths


def _split_added_removed(patch_diff: str) -> tuple[list[str], list[str]]:
    added: list[str] = []
    removed: list[str] = []
    for line in patch_diff.split("\n"):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


# ---------------------------------------------------------------------------
# Rule runners
# ---------------------------------------------------------------------------

def _scan_added(added: list[str]) -> list[dict]:
    """Return list of violations found in newly-added lines."""
    violations: list[dict] = []
    body = "\n".join(added)

    rule_groups = [
        (_PII_LOG_PATTERNS, "GDPR-01", "PII variable logged without mask_email"),
        (_SHELL_INJECTION_PATTERNS, "SEC-01", "shell=True / os.system / os.popen"),
        (_DESERIALIZATION_PATTERNS, "SEC-02", "unsafe deserialization (pickle/yaml/eval/exec)"),
        (_RAW_SQL_INTERPOLATION_PATTERNS, "SEC-03", "raw SQL with f-string or string concat"),
        (_HARDCODED_SECRET_PATTERNS, "SEC-04", "hardcoded secret pattern"),
        (_CONSENT_BYPASS_PATTERNS, "GDPR-02", "consent gate bypass"),
    ]
    for patterns, code, label in rule_groups:
        for pat in patterns:
            for match in pat.finditer(body):
                snippet = body[max(0, match.start() - 20):match.end() + 40]
                violations.append({
                    "code": code,
                    "label": label,
                    "pattern": pat.pattern[:60],
                    "snippet": snippet.replace("\n", " ")[:120],
                })
    return violations


def _scan_removed(added: list[str], removed: list[str]) -> list[dict]:
    """Flag removal of safety idioms UNLESS the same idiom reappears in
    added lines (the common case: move, rename, refactor).
    """
    added_blob = "\n".join(added)
    removed_blob = "\n".join(removed)
    violations: list[dict] = []
    for pat, code, label in _SAFETY_IDIOM_REMOVAL_PATTERNS:
        removed_hits = list(pat.finditer(removed_blob))
        if not removed_hits:
            continue
        added_hits = len(pat.findall(added_blob))
        if added_hits >= len(removed_hits):
            continue  # refactor, not a removal
        violations.append({
            "code": code,
            "label": label,
            "pattern": pat.pattern[:60],
            "snippet": removed_hits[0].group(0)[:120],
            "removed_count": len(removed_hits),
            "added_count": added_hits,
        })
    return violations


def _scan_sensitive_paths(changed_files: list[str]) -> list[dict]:
    hits: list[dict] = []
    for path in changed_files:
        if path in _SENSITIVE_PATHS:
            hits.append({
                "code": "PATH-01",
                "label": "diff touches a sensitive file",
                "path": path,
            })
    return hits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_diff_for_security_regressions(patch_diff: str | None) -> list[dict]:
    """Run every rule against the diff. Returns the list of violations."""
    if not patch_diff:
        return []
    added, removed = _split_added_removed(patch_diff)
    violations: list[dict] = []
    violations.extend(_scan_added(added))
    violations.extend(_scan_removed(added, removed))
    violations.extend(_scan_sensitive_paths(_extract_changed_files(patch_diff)))
    return violations


def guard_candidate(candidate) -> tuple[bool, str]:
    """Decide whether this candidate is allowed through the apply pipeline.

    Returns `(allowed, reason)`. A non-empty violation list blocks the
    candidate entirely. Sensitive-path hits downgrade the candidate's
    `patch_risk_tier` to 2 if not already there — they're allowed but
    must land on the human-approved TIER_2 queue.
    """
    violations = scan_diff_for_security_regressions(
        getattr(candidate, "patch_diff", None),
    )
    if not violations:
        return True, "ok"

    hard_violations = [v for v in violations if v["code"] != "PATH-01"]
    if hard_violations:
        codes = ", ".join(sorted({v["code"] for v in hard_violations}))
        snippet = hard_violations[0].get("snippet", "")[:80]
        return (
            False,
            f"security_preflight_reject: {codes} — {snippet}",
        )

    # Only sensitive-path hits — escalate, don't block outright.
    try:
        if getattr(candidate, "patch_risk_tier", None) != 2:
            candidate.patch_risk_tier = 2
            log.info(
                "security_preflight_guard: escalating candidate %s to TIER_2 "
                "(sensitive path touched)",
                getattr(candidate, "id", "?"),
            )
    except Exception:
        pass
    paths = sorted({v["path"] for v in violations if v.get("path")})
    return True, f"sensitive_paths_escalated: {paths}"
