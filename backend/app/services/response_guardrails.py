"""
response_guardrails.py — Hard safety gate for all merchant-facing automated text.

Every automated message (chatbot, email, auto-response) MUST pass through
validate_response() before being sent to a merchant.

Rules:
    1. No pricing improvisation (specific $ amounts, discount %, refund promises)
    2. No legal/GDPR/security claims beyond approved language
    3. No fake timelines ("will be fixed by Tuesday")
    4. No promises of fixes unless the fix is already applied
    5. No aggressive or condescending tone
    6. Automatic escalation signal for angry/legal/billing merchants

This module is deterministic. No LLM. No inference.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("response_guardrails")

# ---------------------------------------------------------------------------
# Forbidden phrase patterns — NEVER include in merchant-facing text
# ---------------------------------------------------------------------------

_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # Pricing / billing improvisation
    (r"\$\d+", "dollar_amount"),
    (r"€\d+", "euro_amount"),
    (r"\b\d+%\s*(?:off|discount|refund|cashback)\b", "discount_promise"),
    (r"\b(?:money[\s-]?back|full\s+refund|guaranteed\s+refund)\b", "refund_promise"),
    (r"\b(?:free\s+(?:forever|lifetime|permanently))\b", "perpetual_free_promise"),

    # Timeline guarantees
    (r"\b(?:will\s+be\s+(?:fixed|resolved|done|ready)\s+by\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|end\s+of\s+(?:day|week)))\b", "timeline_guarantee"),
    (r"\b(?:guarantee[ds]?\s+(?:to|that|it\s+will))\b", "guarantee_claim"),
    (r"\b(?:within\s+\d+\s*(?:hours?|minutes?|days?|business\s+days?))\b", "specific_timeline"),

    # Legal / GDPR / security claims
    (r"\b(?:we\s+are\s+(?:required|obligated|legally\s+bound))\b", "legal_obligation_claim"),
    (r"\b(?:gdpr[\s-]?compliant|fully\s+compliant|100%\s+(?:compliant|secure))\b", "compliance_claim"),
    (r"\b(?:legally\s+(?:binding|enforceable)|terms\s+(?:require|mandate))\b", "legal_binding_claim"),
    (r"\b(?:not?\s+(?:our|my)\s+(?:fault|responsibility|problem))\b", "blame_deflection"),
    (r"\b(?:lawyer|attorney|sue|lawsuit|litigation|court)\b", "legal_threat_language"),

    # Promise of unverified fixes
    (r"\b(?:this\s+(?:has\s+been|is\s+now)\s+(?:fixed|resolved|patched))\b", "unverified_fix_claim"),

    # Aggressive tone
    (r"\b(?:obviously|clearly\s+you|you\s+should\s+have|your\s+fault)\b", "condescending_tone"),
]

# ---------------------------------------------------------------------------
# Required disclaimers per context
# ---------------------------------------------------------------------------

_BILLING_DISCLAIMER = (
    "For billing questions, your Shopify admin panel is the authoritative source. "
    "We cannot modify charges directly."
)

_LEGAL_DISCLAIMER = (
    "This is informational only and does not constitute legal advice. "
    "For legal matters, please consult qualified counsel."
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class GuardrailResult:
    """Result of a guardrail check."""
    __slots__ = ("safe", "violations", "cleaned_text", "must_escalate")

    def __init__(self):
        self.safe: bool = True
        self.violations: list[str] = []
        self.cleaned_text: str = ""
        self.must_escalate: bool = False


def validate_response(
    text: str,
    context: str = "chatbot",
    classification: str | None = None,
) -> GuardrailResult:
    """
    Validate a merchant-facing response against all guardrails.

    Args:
        text: The response text to validate
        context: "chatbot" | "email" | "auto_response"
        classification: The inbound classification if applicable

    Returns:
        GuardrailResult with safe=True if text passes all checks.
        If safe=False, violations list explains why.
    """
    result = GuardrailResult()
    result.cleaned_text = text

    if not text or not text.strip():
        result.safe = False
        result.violations.append("empty_response")
        return result

    text_lower = text.lower()

    # Check forbidden patterns
    for pattern, violation_type in _FORBIDDEN_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            result.safe = False
            result.violations.append(f"forbidden:{violation_type}")

    # Classification-based escalation
    if classification in ("billing_or_legal", "complaint"):
        result.must_escalate = True

    # Length check — no auto-response should be longer than 500 chars
    if context == "auto_response" and len(text) > 500:
        result.safe = False
        result.violations.append("auto_response_too_long")

    if result.violations:
        log.warning(
            "response_guardrails: BLOCKED context=%s violations=%s text_preview=%r",
            context, result.violations, text[:80],
        )

    return result


def add_disclaimer(text: str, classification: str | None) -> str:
    """Append appropriate disclaimer based on classification."""
    if classification == "billing_or_legal":
        return f"{text}\n\n{_BILLING_DISCLAIMER}"
    if classification in ("complaint", "billing_or_legal"):
        return f"{text}\n\n{_LEGAL_DISCLAIMER}"
    return text


def hedge_timeline(text: str) -> str:
    """Replace hard timeline language with hedged versions."""
    replacements = [
        (r"\bin about (\d+) minutes?\b", r"typically within \1 minutes"),
        (r"\btakes seconds\b", "usually takes just a moment"),
        (r"\bin under (\d+) minutes?\b", r"typically under \1 minutes"),
        (r"\b(\d+)[- ]minute fix\b", r"usually a \1-minute fix"),
        (r"\bFirst insights appear in about (\d+) minutes?\b",
         r"First insights typically appear within \1 minutes"),
    ]
    result = text
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result
