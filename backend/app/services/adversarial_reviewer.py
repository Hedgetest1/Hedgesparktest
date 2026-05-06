"""adversarial_reviewer — Sprint B of CTO-brain pipeline upgrade.

Runs 3 adversarial LLM passes (internal CTO / investor CTO / competitor
CTO) against every TIER_1+ BugFixCandidate that passes reviewer_layer,
to surface concerns a deterministic review cannot catch. Each lens'
finding persists as an `AdversarialReviewFinding` row.

Architecture
------------
Called by bugfix_pipeline AFTER `reviewer_layer.review_entity` returns
"approved" AND candidate.patch_risk_tier >= 1 (TIER_0 skips to save
budget; TIER_0 fixes are low-risk by definition).

For each of the 3 lenses:
  1. Budget gate via `check_budget("adversarial_reviewer")`
  2. PII guard on the prompt (candidate title + summary + patch diff)
  3. Single Haiku call with persona-specific system prompt
  4. Parse structured JSON {severity: 0-10, concern, remediation}
  5. Persist AdversarialReviewFinding row
  6. Record usage via `record_usage(...)`

Returns list[AdversarialReviewFinding] ordered by severity desc.

Safety
------
- Feature-flagged `ADVERSARIAL_REVIEWER_ENABLED` (default off).
- Fail-open: if any lens errors, return findings from the others
  (partial coverage better than blocking apply).
- Truncation rejection: if LLM response is truncated (stop_reason =
  max_tokens), the finding is dropped (same policy as meta_reviewer).
- Ground-truth tokens via usage.input_tokens + usage.output_tokens.

Cost projection (per feedback_external_software_cost_10_100_1k_10k):
  10   merchants: ~€0.04/mo
  100  merchants: ~€0.22/mo
  1k   merchants: ~€1.30/mo
  10k  merchants: ~€13/mo (within €500 monthly ceiling per §8.1)
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx
from sqlalchemy.orm import Session

from app.core.llm_budget import (
    check_budget, is_provider_backed_off, record_429, record_blocked, record_usage,
)
from app.core.llm_pii_guard import assert_clean, LLMPayloadViolation
from app.models.adversarial_review_finding import AdversarialReviewFinding
from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("adversarial_reviewer")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
_TIMEOUT_S = 30.0

# Per-lens system prompt — persona must remain focused to produce
# distinct feedback shapes vs the other two lenses.
_LENS_PROMPTS = {
    "internal": (
        "You are the INTERNAL CTO of HedgeSpark, an AI commerce intelligence "
        "SaaS for Shopify. Review the proposed bug fix from the perspective "
        "of OUR architecture and scale: does this fix cause regressions in "
        "our existing systems? Does it scale to 10k merchants? Does it violate "
        "our zero-dep doctrine or introduce hidden coupling?\n\n"
        "Respond ONLY with a JSON object: "
        '{"severity": <0-10>, "concern": "<one sentence>", '
        '"remediation": "<one actionable sentence>"}. '
        "severity 0-2 = approve, 3-5 = advisory, 6-8 = iterate, 9-10 = escalate. "
        "If no concern, return severity 0 and empty concern/remediation."
    ),
    "investor": (
        "You are an INVESTOR CTO reviewing a proposed code patch. Your "
        "concerns: risk to production, unit economics (cost at scale), "
        "regulatory exposure (GDPR/SOC2), hidden technical debt that will "
        "bite in 6 months. Would you approve this fix for a €10M-ARR SaaS?\n\n"
        "Respond ONLY with a JSON object: "
        '{"severity": <0-10>, "concern": "<one sentence>", '
        '"remediation": "<one actionable sentence>"}. '
        "severity 0-2 = approve, 3-5 = advisory, 6-8 = iterate, 9-10 = escalate. "
        "If no concern, return severity 0 and empty concern/remediation."
    ),
    "competitor": (
        "You are the CEO+CTO of a competing Shopify analytics SaaS "
        "(Triple Whale / Peel / Varos). You are reading HedgeSpark's bug "
        "fix trying to find how it's insufficient. Does it solve the CLASS "
        "of bug or just this instance? What would a 11/10 fix look like "
        "that your company would ship instead?\n\n"
        "Respond ONLY with a JSON object: "
        '{"severity": <0-10>, "concern": "<one sentence>", '
        '"remediation": "<one actionable sentence>"}. '
        "severity 0-2 = approve, 3-5 = advisory, 6-8 = iterate, 9-10 = escalate. "
        "If no concern, return severity 0 and empty concern/remediation."
    ),
}

_LENSES = tuple(_LENS_PROMPTS.keys())


def is_enabled() -> bool:
    """Feature flag — default off (pipeline paused pre-merchant)."""
    return os.getenv("ADVERSARIAL_REVIEWER_ENABLED", "0").lower() in ("1", "true", "yes")


def _build_prompt(candidate: BugFixCandidate) -> str:
    """Compact user prompt — candidate identity + patch summary + diff
    tail. Kept under ~2KB to minimize input tokens."""
    parts = [
        f"Title: {candidate.title}",
    ]
    if candidate.summary:
        parts.append(f"\nSummary:\n{candidate.summary[:400]}")
    if candidate.patch_summary:
        parts.append(f"\nPatch summary:\n{candidate.patch_summary[:400]}")
    if candidate.patch_diff:
        # Cap diff to ~1200 chars to protect prompt budget
        diff = candidate.patch_diff[:1200]
        parts.append(f"\nPatch diff (may be truncated):\n```diff\n{diff}\n```")
    return "\n".join(parts)


def _parse_response(text: str) -> dict | None:
    """Extract the first JSON object from the model's response.
    Returns None on any parse failure."""
    if not text:
        return None
    # Accept either raw JSON or JSON inside a ```json code fence
    match = re.search(r"\{[^{}]*\"severity\"[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _call_lens(lens: str, prompt: str) -> tuple[dict | None, int]:
    """Single Haiku call for one lens. Returns (parsed_finding, tokens_used).
    Returns (None, 0) on any failure — budget, PII, network, parse."""
    # Per-call budget gate
    allowed, reason = check_budget("adversarial_reviewer")
    if not allowed:
        record_blocked("adversarial_reviewer", reason)
        log.info("adversarial_reviewer[%s]: blocked by budget: %s", lens, reason)
        return (None, 0)

    # PII guard on the user prompt
    try:
        assert_clean(prompt, context=f"adversarial_reviewer.{lens}")
    except LLMPayloadViolation as exc:
        log.warning("adversarial_reviewer[%s]: pii_guard blocked: %s", lens, exc)
        return (None, 0)
    except Exception as exc:
        log.debug("adversarial_reviewer[%s]: pii_guard non-fatal: %s", lens, exc)

    if is_provider_backed_off("anthropic"):
        record_blocked("adversarial_reviewer", "anthropic_429_backoff")
        return (None, 0)

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.info("adversarial_reviewer[%s]: no ANTHROPIC_API_KEY", lens)
        return (None, 0)

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": HAIKU_MODEL,
                "max_tokens": _MAX_TOKENS,
                "temperature": 0.1,
                "system": _LENS_PROMPTS[lens],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_TIMEOUT_S,
        )
    except httpx.TimeoutException:
        log.warning("adversarial_reviewer[%s]: timeout after %.1fs", lens, _TIMEOUT_S)
        return (None, 0)
    except Exception as exc:
        log.warning("adversarial_reviewer[%s]: request failed: %s", lens, exc)
        return (None, 0)

    if resp.status_code == 429:
        record_429("anthropic")
        return (None, 0)
    if resp.status_code != 200:
        log.warning("adversarial_reviewer[%s]: HTTP %d", lens, resp.status_code)
        return (None, 0)

    body = resp.json()
    if body.get("stop_reason") == "max_tokens":
        log.warning("adversarial_reviewer[%s]: TRUNCATED — dropped", lens)
        return (None, 0)

    text_out = body.get("content", [{}])[0].get("text", "")
    usage = body.get("usage") or {}
    tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    if tokens == 0:
        tokens = len(text_out) // 4
    record_usage(
        "adversarial_reviewer",
        tokens_used=tokens,
        provider="anthropic",
        model=HAIKU_MODEL,
    )

    parsed = _parse_response(text_out)
    if parsed is None:
        log.info("adversarial_reviewer[%s]: unparseable response", lens)
        return (None, tokens)
    return (parsed, tokens)


def _clamp_severity(raw: object) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, v))


_CRITICAL_SEVERITY_THRESHOLD = 9
_PARTIAL_COVERAGE_THRESHOLD = len(_LENSES) - 1  # < 2 of 3 lenses = partial


def _write_ops_alert(db: Session, severity: str, alert_type: str,
                     source: str, summary: str, detail: dict) -> None:
    """Best-effort ops_alert write — never raises into the caller."""
    try:
        from app.services.alerting import write_alert
        # heal-detection: review event — per-pass log entry
        write_alert(
            db,
            severity=severity,
            source=source,
            alert_type=alert_type,
            summary=summary,
            detail=detail,
        )
    except Exception as exc:
        log.warning("adversarial_reviewer: ops_alert write failed: %s", exc)


def review_with_3_lenses(
    db: Session, candidate: BugFixCandidate,
) -> list[AdversarialReviewFinding]:
    """Run adversarial 3-lens review on a candidate. Returns the
    persisted findings (severity-desc ordered, empty list on disabled/
    error).

    Side effects:
      * Emits `adversarial_critical_finding` ops_alert when any lens
        reports severity >= `_CRITICAL_SEVERITY_THRESHOLD` (escalation
        policy per the memo — human should see these immediately).
      * Emits `adversarial_partial_coverage` ops_alert when fewer than
        `_PARTIAL_COVERAGE_THRESHOLD` lenses produce a finding (e.g.
        budget exhausted mid-review). Closes Gate-2 DA: prevents false
        "passed all 3 lenses" verdict when actually fewer ran.
    """
    if not is_enabled():
        log.debug("adversarial_reviewer: disabled (feature flag off)")
        return []

    if candidate is None or candidate.id is None:
        return []

    prompt = _build_prompt(candidate)
    findings: list[AdversarialReviewFinding] = []

    for lens in _LENSES:
        parsed, tokens = _call_lens(lens, prompt)
        if parsed is None:
            continue
        severity = _clamp_severity(parsed.get("severity"))
        concern = str(parsed.get("concern") or "").strip()[:2000]
        remediation = str(parsed.get("remediation") or "").strip()[:2000]
        finding = AdversarialReviewFinding(
            bugfix_candidate_id=candidate.id,
            lens=lens,
            severity=severity,
            concern=concern or None,
            suggested_remediation=remediation or None,
            llm_provider="anthropic",
            llm_model=HAIKU_MODEL,
            tokens_used=tokens,
        )
        db.add(finding)
        findings.append(finding)

    if findings:
        db.flush()

    findings.sort(key=lambda f: f.severity, reverse=True)

    # DA Gate-2 closure #1: critical finding → ops_alert for human
    critical = [f for f in findings if f.severity >= _CRITICAL_SEVERITY_THRESHOLD]
    for f in critical:
        _write_ops_alert(
            db,
            severity="critical",
            alert_type="adversarial_critical_finding",
            source=f"adversarial_reviewer:{f.lens}",
            summary=(
                f"Adversarial reviewer ({f.lens} lens) flagged bugfix #{candidate.id} "
                f"at severity {f.severity}/10 — requires human review before apply"
            ),
            detail={
                "bugfix_candidate_id": candidate.id,
                "finding_id": f.id,
                "lens": f.lens,
                "severity": f.severity,
                "concern": f.concern,
                "remediation": f.suggested_remediation,
            },
        )

    # DA Gate-2 closure #2: partial coverage → ops_alert so operator
    # doesn't misread "fewer findings" as "all lenses approved". This
    # fires e.g. when budget exhaustion silently skips lens 2+.
    if len(findings) < _PARTIAL_COVERAGE_THRESHOLD:
        skipped_lenses = [L for L in _LENSES if L not in {f.lens for f in findings}]
        _write_ops_alert(
            db,
            severity="warning",
            alert_type="adversarial_partial_coverage",
            source="adversarial_reviewer:partial",
            summary=(
                f"Adversarial review produced only {len(findings)} of "
                f"{len(_LENSES)} lens findings for bugfix #{candidate.id}"
            ),
            detail={
                "bugfix_candidate_id": candidate.id,
                "findings_count": len(findings),
                "expected_lenses": list(_LENSES),
                "missing_lenses": skipped_lenses,
                "reason_hint": (
                    "Likely causes: budget exhaustion mid-review, 429 "
                    "provider backoff, PII guard block, or parse failure. "
                    "Check recent adversarial_reviewer logs."
                ),
            },
        )

    log.info(
        "adversarial_reviewer: candidate=%d findings=%d max_severity=%d critical=%d",
        candidate.id,
        len(findings),
        findings[0].severity if findings else 0,
        len(critical),
    )
    return findings


__all__ = [
    "is_enabled",
    "review_with_3_lenses",
    "HAIKU_MODEL",
    "_LENSES",
]
