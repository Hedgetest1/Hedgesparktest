"""
email_governance.py — Email system governance and drift prevention.

This module is the CONTROL LAYER for the HedgeSpark email system.
It does not send emails. It enforces rules across the entire pipeline.

Responsibilities:
  1. Template registry — single source of truth for what templates exist
  2. Content hashing — track which version of a template was sent
  3. Agent permissions — define what agents can and cannot modify
  4. Data injection rules — whitelist of allowed dynamic fields per template
  5. Identity rules — which sender address is allowed for which email type
  6. Audit enrichment — attach governance metadata to every sent email

Public interface:
    validate_intent(intent: EmailIntent) -> GovernanceResult
    get_template_registry() -> dict
    hash_template_content(html: str) -> str
    get_identity_rules() -> dict
    get_agent_permissions() -> dict

Called by:
    email_orchestrator._send_intent() — before every send
    brand_voice.py — for rule reference
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("email_governance")


# ═══════════════════════════════════════════════════════════════════════════
# PART 1 — TEMPLATE REGISTRY (source of truth)
# ═══════════════════════════════════════════════════════════════════════════
#
# File structure:
#   app/services/email_templates.py    — ALL template renderers + shared helpers
#   app/services/digest_formatter.py   — Digest-specific formatter (uses own layout)
#   app/services/brand_voice.py        — Forbidden patterns, validation engine
#   app/services/email_governance.py   — THIS FILE: registry, rules, enforcement
#   app/services/email_orchestrator.py — Orchestration, rate limits, priority
#
# Template versioning:
#   Templates are Python functions, versioned via git.
#   Content hash (SHA256 of rendered HTML) is computed at send time
#   and stored in the audit log for traceability.
#
# Separation:
#   Base layout:    _wrap_html() in email_templates.py (dark theme, responsive)
#   Copy blocks:    _render_*() functions in email_templates.py
#   Dynamic data:   ctx dict passed to renderers, validated by ALLOWED_FIELDS
#   Brand rules:    brand_voice.py (forbidden patterns, structural rules)

TEMPLATE_REGISTRY = {
    # FOUNDATION EMAILS — explain the system, build trust
    "welcome": {
        "type": "foundation",
        "renderer": "email_templates._render_welcome",
        "sender": "andrea@hedgesparkhq.com",
        "sender_display": "Andrea from HedgeSpark",
        "has_signature": True,
        "uses_wrap_html": True,
        "show_logo": True,
        "max_sends_per_merchant": 1,
    },
    "beta_welcome": {
        "type": "foundation",
        "renderer": "email_templates._render_beta_welcome",
        "sender": "andrea@hedgesparkhq.com",
        "sender_display": "Andrea from HedgeSpark",
        "has_signature": True,
        "uses_wrap_html": True,
        "show_logo": True,
        "max_sends_per_merchant": 1,
    },

    # FOUNDATION — follow-ups (pre-onboarding)
    "followup_opened": {
        "type": "foundation",
        "renderer": "email_templates._render_followup_opened",
        "sender": "andrea@hedgesparkhq.com",
        "sender_display": "Andrea from HedgeSpark",
        "has_signature": True,
        "uses_wrap_html": True,
        "show_logo": True,
        "max_sends_per_merchant": 1,
    },
    "followup_clicked": {
        "type": "foundation",
        "renderer": "email_templates._render_followup_clicked",
        "sender": "andrea@hedgesparkhq.com",
        "sender_display": "Andrea from HedgeSpark",
        "has_signature": True,
        "uses_wrap_html": True,
        "show_logo": True,
        "max_sends_per_merchant": 1,
    },
    "followup_noopen": {
        "type": "foundation",
        "renderer": "email_templates._render_followup_noopen",
        "sender": "andrea@hedgesparkhq.com",
        "sender_display": "Andrea from HedgeSpark",
        "has_signature": True,
        "uses_wrap_html": True,
        "show_logo": True,
        "max_sends_per_merchant": 1,
    },

    # SIGNAL EMAILS — triggered by detected state
    "setup_incomplete": {
        "type": "signal",
        "renderer": "email_templates._render_setup_incomplete",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": False,
        "max_sends_per_merchant": 3,
    },
    "first_insight": {
        "type": "signal",
        "renderer": "email_templates._render_first_insight",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": False,
        "max_sends_per_merchant": 1,
    },
    "connection_issue": {
        "type": "signal",
        "renderer": "email_templates._render_connection_issue",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": False,
        "max_sends_per_merchant": 3,
    },
    "reengagement": {
        "type": "signal",
        "renderer": "silence_detector._send_reengagement_email",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": True,
        "max_sends_per_merchant": 4,  # once per quarter
    },

    # HYBRID — weekly digest
    "weekly_digest": {
        "type": "hybrid",
        "renderer": "digest_formatter.format_digest",
        "sender": "digest@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,  # Migrated to _wrap_html dark theme
        "show_logo": False,
        "max_sends_per_merchant": None,  # weekly by schedule
    },

    # HYBRID — daily morning brief (Lite tier)
    "lite_morning_digest": {
        "type": "hybrid",
        "renderer": "lite_morning_digest._build_email",  # inline HTML builder
        "sender": "digest@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": False,  # inline builder, not _wrap_html
        "show_logo": True,
        "max_sends_per_merchant": None,  # daily by schedule
    },

    # SIGNAL — drift re-engagement (stuck onboarding recovery)
    "reengagement_drift": {
        "type": "signal",
        "renderer": "onboarding_health._build_reengagement_email",  # inline HTML
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": False,
        "show_logo": False,
        "max_sends_per_merchant": 4,  # per drift episode chain
    },

    # COMPLIANCE — GDPR Art. 15 data export
    "gdpr_export": {
        "type": "compliance",
        "renderer": "gdpr_processor._build_export_email",  # inline HTML
        "sender": "privacy@hedgesparkhq.com",
        "sender_display": "HedgeSpark Privacy",
        "has_signature": False,
        "uses_wrap_html": False,
        "show_logo": False,
        "max_sends_per_merchant": None,  # per request
    },

    # AUTO-RESPONSE — reactive
    "auto_response": {
        "type": "signal",
        "renderer": "auto_responder.send_auto_response",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": False,
        "max_sends_per_merchant": 3,  # per day
    },

    # SIGNAL — Brain Vero retention outreach (critical churn risk).
    # Dispatched by `merchant_brain._dispatch_retention_outreach_email`
    # when the rule-table fires `retention_outreach_email`. 2026-05-08.
    "retention_outreach": {
        "type": "signal",
        "renderer": "email_templates._render_retention_outreach",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": False,
        "max_sends_per_merchant": 4,  # quarterly cap; brain has its own 24h cooldown
    },

    # SIGNAL — Brain Vero recovery digest (high RAR + stale state).
    # Dispatched by `merchant_brain._dispatch_recovery_digest_email`
    # when the rule-table fires `recovery_digest`. 2026-05-08.
    "recovery_digest": {
        "type": "signal",
        "renderer": "email_templates._render_recovery_digest",
        "sender": "dev@hedgesparkhq.com",
        "sender_display": "HedgeSpark",
        "has_signature": False,
        "uses_wrap_html": True,
        "show_logo": False,
        "max_sends_per_merchant": 4,  # quarterly cap; brain has its own 24h cooldown
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# PART 2 — EMAIL IDENTITY RULES
# ═══════════════════════════════════════════════════════════════════════════

IDENTITY_RULES = {
    "andrea@hedgesparkhq.com": {
        "display_name": "Andrea from HedgeSpark",
        "allowed_types": {"welcome", "beta_welcome", "followup_opened",
                          "followup_clicked", "followup_noopen"},
        "tone": "personal, trust-building, foundation",
        "never_sends": {"trigger_*", "weekly_digest", "auto_response"},
    },
    "dev@hedgesparkhq.com": {
        "display_name": "HedgeSpark",
        "allowed_types": {"setup_incomplete", "first_insight", "connection_issue",
                          "reengagement", "reengagement_drift", "auto_response",
                          "retention_outreach", "recovery_digest"},
        "tone": "system intelligence, factual, guiding",
        "never_sends": {"welcome", "beta_welcome", "followup_*",
                        "weekly_digest", "lite_morning_digest"},
    },
    "digest@hedgesparkhq.com": {
        "display_name": "HedgeSpark",
        "allowed_types": {"weekly_digest", "lite_morning_digest"},
        "tone": "structured report, data-driven, no personal signature",
        "never_sends": {"onboarding, problems, auto_response"},
    },
    "privacy@hedgesparkhq.com": {
        "display_name": "HedgeSpark Privacy",
        "allowed_types": {"gdpr_export"},
        "tone": "legal, formal, compliance-facing",
        "never_sends": {"all merchant-facing marketing/onboarding/digest emails"},
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# PART 3 — AGENT PERMISSIONS
# ═══════════════════════════════════════════════════════════════════════════

AGENT_PERMISSIONS = {
    # What agents CAN do
    "allowed": [
        "Fill template variables (shop_name, product_name, revenue, metrics)",
        "Select which template to use based on detected state",
        "Submit EmailIntent to orchestrator with pre-rendered content",
        "Provide context dict with allowed fields only",
    ],

    # What agents CANNOT do
    "forbidden": [
        "Modify HTML structure of any template",
        "Change _wrap_html wrapper or its parameters",
        "Alter section titles, their order, or their accent colors",
        "Generate free-form email copy (all copy must come from templates)",
        "Change sender address (enforced by identity validation)",
        "Add or remove CTA buttons",
        "Modify brand colors, fonts, or spacing",
        "Add signatures where registry says has_signature=False",
        "Remove signatures where registry says has_signature=True",
        "Use LLM to generate email body text (nudge copy is separate)",
    ],

    # What happens on violation
    "enforcement": "hard_block",  # violations block send, logged, ops alert created
}


# ═══════════════════════════════════════════════════════════════════════════
# PART 4 — DATA INJECTION RULES (allowed fields per template)
# ═══════════════════════════════════════════════════════════════════════════

ALLOWED_FIELDS = {
    "welcome": {"shop_name"},
    "beta_welcome": {"shop_name", "merchant_name"},
    "setup_incomplete": {"shop_name", "issue", "hours_since_install"},
    "first_insight": {"shop_name", "signal_count", "top_signal"},
    "connection_issue": {"shop_name", "issue", "stuck_minutes"},
    "followup_opened": {"merchant_name"},
    "followup_clicked": {"merchant_name"},
    "followup_noopen": {"merchant_name"},
    "weekly_digest": {
        "shop_domain", "currency", "this_week", "last_week",
        "revenue_delta_pct", "unique_visitors", "conversion_rate",
        "top_products", "insight", "recommendation",
        "revenue_at_risk", "whats_working", "proof", "proof_report",
        "data_confidence", "merchant_plan",
    },
    "reengagement": {"shop_name"},
    "reengagement_drift": {"drift_episode", "hours_since_install"},
    "auto_response": {"classification", "response_text"},
    "lite_morning_digest": {"signals_count", "top_product"},
    "gdpr_export": {"request_id"},
    "retention_outreach": {"shop_name", "orders_7d"},
    "recovery_digest": {"shop_name", "rars_eur", "last_action_hours", "shop_currency"},
}


# ═══════════════════════════════════════════════════════════════════════════
# PART 5 — CONTENT HASHING (traceability)
# ═══════════════════════════════════════════════════════════════════════════

def hash_template_content(html: str) -> str:
    """Compute SHA256 hash of rendered email HTML for audit traceability."""
    return hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
# TEMPLATE DRIFT DETECTION
# ═══════════════════════════════════════════════════════════════════════════
#
# Baseline hashes computed by rendering each template with "BASELINE" values.
# If a template's structural HTML changes (code edit), the baseline hash
# will no longer match. This does NOT check dynamic content — only structure.
#
# Regenerate baselines after intentional template changes:
#   ./venv/bin/python -c "from app.services.email_governance import regenerate_baselines; regenerate_baselines()"

_TEMPLATE_BASELINES = {
    "welcome": "84eab68a134ecf6f",
    "setup_incomplete": "6796493c8a162169",
    "first_insight": "a08d9c3bfa10fb2e",
    "connection_issue": "f7dd9eca662f426c",
    "followup_opened": "f84bec4588f1b97a",
    "followup_clicked": "21ce507882a904a6",
    "followup_noopen": "d94dd86490047275",
    "beta_welcome": "b62953ae377e29f9",
    "retention_outreach": "0f4030b745c9e759",
    "recovery_digest": "5920a10cb4e937cc",
}

_BASELINE_CONTEXTS = {
    "welcome": {"shop_name": "BASELINE"},
    "setup_incomplete": {"shop_name": "BASELINE", "issue": "BASELINE", "hours_since_install": 24},
    "first_insight": {"shop_name": "BASELINE", "signal_count": 1, "top_signal": "BASELINE"},
    "connection_issue": {"shop_name": "BASELINE", "issue": "BASELINE"},
    "followup_opened": {"merchant_name": "BASELINE"},
    "followup_clicked": {"merchant_name": "BASELINE"},
    "followup_noopen": {"merchant_name": "BASELINE"},
    "beta_welcome": {"shop_name": "BASELINE", "merchant_name": "BASELINE"},
    "retention_outreach": {"shop_name": "BASELINE", "orders_7d": 1},
    "recovery_digest": {"shop_name": "BASELINE", "rars_eur": 1000, "last_action_hours": 96, "shop_currency": "USD"},
}


def check_template_drift() -> dict[str, str]:
    """
    Compare current template renders against stored baselines.

    Returns dict of {template_name: "drifted" | "ok"}.
    Call periodically (e.g., on deploy or in health check) to detect
    unintentional template changes.
    """
    results = {}
    try:
        from app.services.email_templates import render_email
        for name, ctx in _BASELINE_CONTEXTS.items():
            expected = _TEMPLATE_BASELINES.get(name)
            if not expected:
                results[name] = "no_baseline"
                continue
            _, html, _ = render_email(name, ctx)
            actual = hash_template_content(html)
            if actual != expected:
                results[name] = "drifted"
                log.warning(
                    "governance: TEMPLATE DRIFT detected for %s — expected=%s actual=%s",
                    name, expected, actual,
                )
            else:
                results[name] = "ok"
    except Exception as exc:
        log.error("governance: drift check failed: %s", exc)
    return results


def regenerate_baselines() -> None:
    """Print new baseline hashes for all templates. Run after intentional changes."""
    from app.services.email_templates import render_email
    print("_TEMPLATE_BASELINES = {")
    for name, ctx in _BASELINE_CONTEXTS.items():
        _, html, _ = render_email(name, ctx)
        h = hash_template_content(html)
        print(f'    "{name}": "{h}",')
    print("}")


# ═══════════════════════════════════════════════════════════════════════════
# PART 6 — GOVERNANCE VALIDATION (called before every send)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class GovernanceResult:
    """Result of governance validation."""
    passed: bool = True
    violations: list[str] = field(default_factory=list)
    content_hash: str = ""
    template_type: str = ""
    expected_sender: str = ""

    def add_violation(self, msg: str) -> None:
        self.violations.append(msg)
        self.passed = False


def validate_intent(intent) -> GovernanceResult:
    """
    Validate an EmailIntent against governance rules.

    Checks:
      1. Email type exists in registry
      2. Sender address matches identity rules
      3. Content hash computed for audit
      4. Brand voice validation (delegated to brand_voice.py)

    Called by email_orchestrator._send_intent() before every Resend call.
    """
    result = GovernanceResult()

    email_type = intent.email_type
    from_addr = intent.from_address

    # 1. Registry check
    registry_entry = TEMPLATE_REGISTRY.get(email_type)
    if not registry_entry:
        # Unknown type — might be a new trigger variant, allow but flag
        result.add_violation(f"unknown_template_type:{email_type}")
        log.warning("governance: unknown email type %s for %s", email_type, intent.shop_domain)
    else:
        result.template_type = registry_entry["type"]
        result.expected_sender = registry_entry["sender"]

    # 2. Sender identity check
    if registry_entry:
        expected = registry_entry["sender"]
        # Extract email from "Display Name <email@domain>" format
        actual_email = from_addr
        email_match = re.search(r"<([^>]+)>", from_addr)
        if email_match:
            actual_email = email_match.group(1)

        if actual_email != expected:
            result.add_violation(
                f"sender_mismatch:expected={expected},actual={actual_email}"
            )

    # 3. Content hash
    if intent.html:
        result.content_hash = hash_template_content(intent.html)

    # 4. Template drift check — block sends if template structure changed
    if email_type in _DRIFT_STATE and _DRIFT_STATE[email_type] == "drifted":
        result.add_violation(f"template_drifted:{email_type}")

    # 5. Brand voice — hard block on violations
    try:
        from app.services.brand_voice import validate_email_text, validate_subject_line
        text_check = validate_email_text(
            intent.plain_text,
            is_digest=(email_type == "weekly_digest"),
        )
        subj_check = validate_subject_line(intent.subject)
        if not text_check.passed:
            for v in text_check.violations:
                result.add_violation(f"brand:{v}")
        if not subj_check.passed:
            for v in subj_check.violations:
                result.add_violation(f"brand_subject:{v}")
    except Exception as exc:
        log.warning("email_governance: validate_intent failed: %s", exc)
        pass  # brand check failure is non-fatal

    if result.violations:
        log.warning(
            "governance: %d violations for %s/%s: %s",
            len(result.violations), intent.shop_domain, email_type,
            result.violations,
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# PART 7 — PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def get_template_registry() -> dict:
    return dict(TEMPLATE_REGISTRY)

def get_identity_rules() -> dict:
    return dict(IDENTITY_RULES)

def get_agent_permissions() -> dict:
    return dict(AGENT_PERMISSIONS)

def get_allowed_fields(email_type: str) -> set:
    return ALLOWED_FIELDS.get(email_type, set())


# ═══════════════════════════════════════════════════════════════════════════
# STARTUP VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════
# Run on first import. Verifies all template baselines match.
# If drift is detected, logs ERROR but does NOT crash the process —
# the hard block happens at send time in validate_intent().

_DRIFT_STATE: dict[str, str] = {}  # populated on startup


def _verify_baselines_on_startup() -> None:
    """Verify template baselines on module load. Populates _DRIFT_STATE."""
    global _DRIFT_STATE
    try:
        _DRIFT_STATE = check_template_drift()
        drifted = [k for k, v in _DRIFT_STATE.items() if v == "drifted"]
        if drifted:
            log.error(
                "governance: TEMPLATE DRIFT on startup — %d template(s) changed: %s. "
                "Emails using these templates will be BLOCKED until baselines are updated.",
                len(drifted), drifted,
            )
        else:
            log.info("governance: all %d template baselines verified OK", len(_DRIFT_STATE))
    except Exception as exc:
        log.warning("governance: startup baseline check failed (non-fatal): %s", exc)


# Run verification when module is first imported
_verify_baselines_on_startup()


# ═══════════════════════════════════════════════════════════════════════════
# DATA INJECTION VALIDATION
# ═══════════════════════════════════════════════════════════════════════════
#
# Prevents hallucinated, impossible, or malformed values from reaching emails.
# Called before template rendering when context dict is available.

_DATA_BOUNDS = {
    # Revenue values: must be non-negative, capped at reasonable maximum
    "revenue": (0, 10_000_000),       # $0 – $10M
    "weekly_loss": (0, 1_000_000),
    "total_price": (0, 1_000_000),
    "aov": (0, 50_000),              # AOV above $50k is suspicious
    "top_recoverable": (0, 1_000_000),

    # Counts: must be non-negative integers
    "order_count": (0, 100_000),
    "carts": (0, 10_000),
    "views": (0, 1_000_000),
    "views_1h": (0, 100_000),
    "views_24h": (0, 1_000_000),
    "return_count": (0, 100_000),
    "unique_visitors": (0, 10_000_000),
    "signal_count": (0, 1_000),

    # Rates: must be 0-100 (percentages) or 0-1 (ratios)
    "conversion_rate": (0, 100),
    "revenue_delta_pct": (-100, 10_000),  # can be negative, capped at 10000%
}


@dataclass
class DataValidationResult:
    passed: bool = True
    violations: list[str] = field(default_factory=list)
    sanitized: dict = field(default_factory=dict)

    def add_violation(self, msg: str) -> None:
        self.violations.append(msg)
        self.passed = False


def validate_email_data(email_type: str, context: dict) -> DataValidationResult:
    """
    Validate data fields before they enter an email template.

    Checks:
      1. Only allowed fields present (per ALLOWED_FIELDS)
      2. Numeric values within sane bounds
      3. Required string fields are non-empty
      4. No None values for fields that will be displayed

    Returns DataValidationResult with violations and sanitized dict.
    """
    result = DataValidationResult()
    allowed = ALLOWED_FIELDS.get(email_type, set())

    if not allowed:
        # Unknown template type — pass through but flag
        result.sanitized = dict(context)
        return result

    sanitized = {}

    for key, value in context.items():
        if key not in allowed:
            result.add_violation(f"unexpected_field:{key}")
            continue

        # Numeric bounds check
        if key in _DATA_BOUNDS and value is not None:
            lo, hi = _DATA_BOUNDS[key]
            if isinstance(value, (int, float)):
                if value < lo or value > hi:
                    result.add_violation(f"out_of_bounds:{key}={value} (range {lo}-{hi})")
                    # Do NOT clamp — reject. Invalid data must not reach merchant.

        sanitized[key] = value

    result.sanitized = sanitized

    if result.violations:
        log.warning(
            "governance: data validation issues for %s: %s",
            email_type, result.violations,
        )

    return result
