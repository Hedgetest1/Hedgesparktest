"""
llm_safety.py — Prompt-injection guard + output classifier.

Complements `llm_pii_guard.py` (which scans for leaked secrets/PII in
outgoing prompts). This module covers the other two failure modes:

  1. PROMPT INJECTION from merchant-controlled content. A merchant can
     set product titles, review snippets, nudge templates. Any of those
     strings can contain prompt-engineering payloads that hijack the
     system prompt ("ignore previous instructions", role override,
     instruction smuggling, jailbreak patterns).

  2. OUTPUT INTEGRITY. When the model replies, we validate the response
     BEFORE presenting it to the merchant:
       - no instruction override ("as an AI language model I cannot…")
       - no policy leak ("your system prompt is…")
       - no refusal-passthrough (model refused, don't display junk)
       - no embedded URLs unless the prompt expected them

Design notes
------------
- Regex + string heuristics only. Zero external calls. Sub-millisecond.
- Deterministic, explainable. Every block has a reason code.
- Fail CLOSED when `llm_strict_safety` flag is on (default true). Fail
  OPEN with a warning log when the flag is off — useful for bootstrap.
- Red-team corpus lives in tests/test_llm_safety.py to make regressions
  visible on every CI run.

Public API
----------
    scan_input(text)              -> list[Violation]
    scan_output(text)             -> list[Violation]
    assert_input_safe(text)       -> None    (raises LLMSafetyViolation)
    assert_output_safe(text)      -> None    (raises LLMSafetyViolation)
    classify_output(text)         -> OutputClassification
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

log = logging.getLogger("llm_safety")


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Violation:
    code: str
    severity: Severity
    match: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "match": self.match[:120],
            "reason": self.reason,
        }


class LLMSafetyViolation(Exception):
    def __init__(self, violations: list[Violation]):
        self.violations = violations
        codes = ", ".join(v.code for v in violations)
        super().__init__(f"llm_safety_violation: {codes}")


# ---------------------------------------------------------------------------
# Prompt-injection patterns (input path)
# ---------------------------------------------------------------------------
# Each entry: (compiled regex, code, severity, human reason).
#
# The catalogue is curated from published jailbreaks (OWASP LLM Top 10,
# Simon Willison's corpus) plus patterns we care about for a Shopify
# merchant attack surface (product title injection, review injection).

_INPUT_PATTERNS: list[tuple[re.Pattern, str, Severity, str]] = [
    (
        re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules)", re.I),
        "instruction_override",
        Severity.CRITICAL,
        "Payload tries to override prior instructions.",
    ),
    (
        re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|the\s+)?(?:above|instructions?)", re.I),
        "instruction_override",
        Severity.CRITICAL,
        "Payload tries to disregard prior instructions.",
    ),
    (
        re.compile(r"(?:you\s+are\s+now|from\s+now\s+on)\s+(?:a|an)\s+", re.I),
        "role_override",
        Severity.CRITICAL,
        "Payload tries to re-role the assistant.",
    ),
    (
        re.compile(r"\b(?:jailbreak|DAN\s+mode|developer\s+mode|sudo\s+mode)\b", re.I),
        "jailbreak_token",
        Severity.CRITICAL,
        "Known jailbreak trigger phrase.",
    ),
    (
        re.compile(r"system\s*:\s*(?:you|do|ignore|reveal)", re.I),
        "fake_system_prefix",
        Severity.CRITICAL,
        "Payload tries to inject a fake system turn.",
    ),
    (
        re.compile(r"(?:reveal|print|show|dump)\s+(?:your|the)\s+(?:system\s+)?prompt", re.I),
        "prompt_disclosure_request",
        Severity.CRITICAL,
        "Asks the model to disclose its system prompt.",
    ),
    (
        re.compile(r"</?\s*(?:system|assistant|user)\s*>", re.I),
        "xml_role_tag",
        Severity.WARNING,
        "Embedded role tag — possible delimiter injection.",
    ),
    (
        re.compile(r"\\n\\nHuman:|\\n\\nAssistant:", re.I),
        "claude_turn_injection",
        Severity.CRITICAL,
        "Attempts to inject Claude conversational turns.",
    ),
    (
        re.compile(r"(?:pretend|act\s+as|role[-\s]*play)\s+(?:if|you|like|as)", re.I),
        "role_play_override",
        Severity.WARNING,
        "Role-play override attempt.",
    ),
    (
        re.compile(r"base64\s*:", re.I),
        "base64_smuggle",
        Severity.WARNING,
        "Possible base64-smuggled instruction.",
    ),
    (
        re.compile(r"(?:execute|run)\s+(?:this|the\s+following)\s+(?:code|command|shell)", re.I),
        "exec_request",
        Severity.CRITICAL,
        "Asks the model to execute code/commands.",
    ),
]


# ---------------------------------------------------------------------------
# Output patterns
# ---------------------------------------------------------------------------

_OUTPUT_PATTERNS: list[tuple[re.Pattern, str, Severity, str]] = [
    (
        re.compile(r"(?:as\s+an?\s+ai\s+language\s+model|i\s+am\s+an?\s+ai)", re.I),
        "meta_refusal",
        Severity.WARNING,
        "Model fell back to a meta AI disclaimer instead of answering.",
    ),
    (
        re.compile(r"\bi\s+cannot\s+(?:help|assist|provide|comply)", re.I),
        "hard_refusal",
        Severity.WARNING,
        "Model refused the request — show empty-state UI instead of the refusal.",
    ),
    (
        re.compile(r"my\s+(?:system\s+)?(?:prompt|instructions)\s+(?:are|is|say)", re.I),
        "prompt_leak",
        Severity.CRITICAL,
        "Model leaked part of its system prompt.",
    ),
    (
        re.compile(r"<\|(?:im_start|im_end|system|assistant|user)\|>", re.I),
        "chat_template_leak",
        Severity.CRITICAL,
        "Model output includes chat template tokens.",
    ),
    (
        re.compile(r"https?://(?:bit\.ly|tinyurl\.com|goo\.gl|t\.co|ow\.ly|is\.gd|buff\.ly)", re.I),
        "shortlink_url",
        Severity.WARNING,
        "Suspicious shortened URL in output.",
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_input(text: str) -> list[Violation]:
    """Return violations found in an *incoming* merchant-sourced string."""
    if not text:
        return []
    out: list[Violation] = []
    for pat, code, sev, reason in _INPUT_PATTERNS:
        m = pat.search(text)
        if m:
            out.append(Violation(code=code, severity=sev, match=m.group(0), reason=reason))
    return out


def scan_output(text: str) -> list[Violation]:
    """Return violations found in model output before we show it to the user."""
    if not text:
        return []
    out: list[Violation] = []
    for pat, code, sev, reason in _OUTPUT_PATTERNS:
        m = pat.search(text)
        if m:
            out.append(Violation(code=code, severity=sev, match=m.group(0), reason=reason))
    return out


def _is_strict_mode() -> bool:
    try:
        from app.core.feature_flags import is_enabled
        return is_enabled("llm_strict_safety")
    except Exception as exc:
        log.warning("llm_safety: feature flag check failed: %s", exc)
        return True  # fail closed


def assert_input_safe(text: str, *, context: str = "") -> None:
    """Raise LLMSafetyViolation on any CRITICAL input violation."""
    violations = scan_input(text)
    critical = [v for v in violations if v.severity == Severity.CRITICAL]
    if critical and _is_strict_mode():
        _audit(context, critical, direction="input")
        raise LLMSafetyViolation(critical)
    if violations:
        _audit(context, violations, direction="input", blocked=False)


def assert_output_safe(text: str, *, context: str = "") -> None:
    violations = scan_output(text)
    critical = [v for v in violations if v.severity == Severity.CRITICAL]
    if critical and _is_strict_mode():
        _audit(context, critical, direction="output")
        raise LLMSafetyViolation(critical)
    if violations:
        _audit(context, violations, direction="output", blocked=False)


@dataclass
class OutputClassification:
    ok: bool
    should_display: bool
    violations: list[Violation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "should_display": self.should_display,
            "violations": [v.to_dict() for v in self.violations],
        }


def assert_prompt_safe(text: str, *, context: str = "") -> None:
    """
    One-stop pre-flight check for outgoing prompts. Calls both:
      - llm_pii_guard.assert_clean  (leaked secrets)
      - llm_safety.assert_input_safe (prompt injection)

    Raises LLMSafetyViolation OR LLMPayloadViolation on failure.
    Call sites should catch both and return a graceful empty string.
    """
    try:
        from app.core.llm_pii_guard import assert_clean
        assert_clean(text, context=context)
    except Exception as exc:
        # PII guard raises its own exception type — re-raise for the caller
        raise
    assert_input_safe(text, context=context)


def classify_output(text: str) -> OutputClassification:
    """Soft classification — never raises. Use when caller wants to route
    to an empty-state UI instead of showing a tainted model output."""
    violations = scan_output(text)
    critical = any(v.severity == Severity.CRITICAL for v in violations)
    warning = any(v.severity == Severity.WARNING for v in violations)
    return OutputClassification(
        ok=not critical and not warning,
        should_display=not critical,  # warnings still display (e.g. hard refusal shown to user)
        violations=violations,
    )


def _audit(
    context: str,
    violations: list[Violation],
    *,
    direction: str,
    blocked: bool = True,
) -> None:
    """Log + write an alert so the security dashboard sees the event."""
    import logging
    log = logging.getLogger("llm_safety")
    codes = [v.code for v in violations]
    log.warning(
        "llm_safety %s %s context=%s codes=%s",
        direction,
        "blocked" if blocked else "flagged",
        context or "unknown",
        codes,
    )
    try:
        from app.services.alerting import write_alert
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            write_alert(
                db,
                severity="warning" if blocked else "info",
                source="llm_safety",
                alert_type=f"llm_safety_{direction}",
                summary=f"LLM safety {'block' if blocked else 'flag'}: {', '.join(codes)}",
                detail={"context": context, "violations": [v.to_dict() for v in violations]},
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        log.warning("llm_safety: audit alert write failed: %s", exc)
