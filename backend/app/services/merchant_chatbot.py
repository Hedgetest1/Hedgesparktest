"""
merchant_chatbot.py — Domain-bounded merchant support chatbot.

Strictly limited to HedgeSpark product support, debugging, and guidance.
NOT a general-purpose assistant. NOT casual chitchat.

Flow:
  1. Classify incoming message
  2. Run relevant diagnostics (setup audit, billing, etc.)
  3. Generate response (direct answer / guided troubleshooting / escalation)
  4. Create support incident for non-trivial issues
  5. Trigger safe autonomous repair when available

All responses are merchant-safe. No internal stack traces. No secrets.

Unified pipeline integration:
  - OpsAlert dedup: reuses existing unresolved alerts instead of creating duplicates
  - SupportIncident dedup: reuses active incidents for same (shop, area) within 1 hour
  - BugFixCandidate linking: links incidents to existing candidates when found
  - Repair claims: acquires distributed lock before any repair attempt
  - Status transitions: open → triaged (alert linked) → investigating (candidate created) → resolved (fix applied)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.support_incident import SupportIncident

log = logging.getLogger("merchant_chatbot")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

VALID_CLASSIFICATIONS = (
    "product_question", "setup_help", "bug_report", "billing_access_issue",
    "integration_issue", "data_quality_issue", "feature_request", "out_of_scope",
    "unclassified",
)

VALID_SEVERITIES = ("low", "medium", "high", "critical")

VALID_AREAS = (
    "dashboard", "tracker", "klaviyo", "resend", "billing",
    "shopify_auth", "webhooks", "script_tags", "plan_access",
    "signals", "nudges", "unknown",
)


@dataclass
class MessageClassification:
    classification: str = "product_question"
    severity: str = "low"
    confidence: str = "medium"
    affected_area: str = "unknown"


# Keyword patterns for deterministic classification (no LLM needed)
_PATTERNS: list[tuple[str, dict]] = [
    # Billing / plan / access issues — CRITICAL path
    (r"(paid|payment|charged|billing|invoice|subscription)", {"classification": "billing_access_issue", "affected_area": "billing", "severity": "high"}),
    (r"(pro .*(locked|blocked|not.*(work|show|appear|access)))", {"classification": "billing_access_issue", "affected_area": "plan_access", "severity": "high"}),
    (r"(upgrade|downgrade|plan|tier|lite).*(not|wrong|still|locked|broken)", {"classification": "billing_access_issue", "affected_area": "plan_access", "severity": "high"}),
    (r"(free|trial).*(expired|ended|over)", {"classification": "billing_access_issue", "affected_area": "billing", "severity": "medium"}),

    # Shopify install / auth
    (r"(install|reinstall|uninstall).*(fail|error|stuck|problem|nothing|broken)", {"classification": "setup_help", "affected_area": "shopify_auth", "severity": "high"}),
    (r"(oauth|auth|connect|token).*(fail|error|invalid|expired|broken)", {"classification": "setup_help", "affected_area": "shopify_auth", "severity": "high"}),
    (r"(shopify).*(says|shows).*(connected|installed).*(but|dead|not|nothing)", {"classification": "setup_help", "affected_area": "shopify_auth", "severity": "high"}),

    # Tracker / events / data
    (r"(track|pixel|events?).*(not|dead|missing|zero|broken|stopped)", {"classification": "bug_report", "affected_area": "tracker", "severity": "high"}),
    (r"(nothing|no data|empty|blank).*(appear|show|display|work|visible|dashboard)", {"classification": "bug_report", "affected_area": "tracker", "severity": "high"}),
    (r"(events?).*(not.*(coming|firing|arriving|recording))", {"classification": "bug_report", "affected_area": "tracker", "severity": "high"}),
    (r"(events?).*(aren.?t.*(coming|firing|arriving))", {"classification": "bug_report", "affected_area": "tracker", "severity": "high"}),

    # Webhooks / script tags
    (r"(webhook).*(miss|fail|error|not|broken|stale)", {"classification": "integration_issue", "affected_area": "webhooks", "severity": "high"}),
    (r"(script.?tag|tracker.?script).*(miss|fail|not|broken)", {"classification": "integration_issue", "affected_area": "script_tags", "severity": "high"}),

    # Klaviyo
    (r"(klaviyo).*(not|fail|error|broken|dead|fire|connect|miss)", {"classification": "integration_issue", "affected_area": "klaviyo", "severity": "medium"}),

    # Signals / data quality
    (r"(signal|alert|insight|score).*(wrong|incorrect|weird|off|bad|strange)", {"classification": "data_quality_issue", "affected_area": "signals", "severity": "medium"}),
    (r"(data|number|metric|stat).*(wrong|incorrect|off|mismatch|inaccurate)", {"classification": "data_quality_issue", "affected_area": "dashboard", "severity": "medium"}),

    # Nudges
    (r"(nudge|popup|notification|banner).*(not|broken|fail|wrong|miss)", {"classification": "bug_report", "affected_area": "nudges", "severity": "medium"}),

    # Resend / email
    (r"(email|resend|digest).*(not|fail|miss|broken|send)", {"classification": "integration_issue", "affected_area": "resend", "severity": "medium"}),

    # Dashboard display issues
    (r"(dashboard|page|screen|ui).*(broken|blank|error|crash|load|slow)", {"classification": "bug_report", "affected_area": "dashboard", "severity": "medium"}),

    # Setup help
    (r"(how.*(set.?up|enable|configure|connect|start|install|use))", {"classification": "setup_help", "affected_area": "unknown", "severity": "low"}),
    (r"(setup|onboard|getting.?started|first.?time)", {"classification": "setup_help", "affected_area": "unknown", "severity": "low"}),

    # Feature requests
    (r"(can you add|feature.?request|would be nice|wish you had|please add)", {"classification": "feature_request", "affected_area": "unknown", "severity": "low"}),

    # Product questions
    (r"(what (does|is)|how does|explain|tell me about|what.*mean)", {"classification": "product_question", "affected_area": "unknown", "severity": "low"}),
    (r"(included|available|support).*(plan|tier|feature)", {"classification": "product_question", "affected_area": "plan_access", "severity": "low"}),
    (r"(price|pricing|cost|how much)", {"classification": "product_question", "affected_area": "billing", "severity": "low"}),
]

# Out-of-scope detection
_OUT_OF_SCOPE = [
    r"\b(recipe|cook|weather|sports|game|movie|music|joke)\b",
    r"\b(hello|hi there|how are you)\b",
    r"(who (are|is) (you|claude|gpt|ai|openai|anthropic))",
    r"(write (me|a) (poem|story|essay|code|script))",
    r"\b(translate|calculate|math|homework)\b",
]


def classify_message(message: str) -> MessageClassification:
    """
    Classify a merchant message into a support category.
    Deterministic keyword matching — no LLM needed.
    """
    text = message.lower().strip()

    # Check out-of-scope first
    for pattern in _OUT_OF_SCOPE:
        if re.search(pattern, text):
            return MessageClassification(
                classification="out_of_scope",
                severity="low",
                confidence="high",
                affected_area="unknown",
            )

    # Try each pattern
    for pattern, attrs in _PATTERNS:
        if re.search(pattern, text):
            return MessageClassification(
                classification=attrs.get("classification", "product_question"),
                severity=attrs.get("severity", "low"),
                confidence="high",
                affected_area=attrs.get("affected_area", "unknown"),
            )

    # Default: treat as product question if it's short, bug report if it sounds frustrated
    if re.search(r"(broken|not working|dead|stuck|fail|error|bug|crash|wrong)", text):
        return MessageClassification(
            classification="bug_report",
            severity="medium",
            confidence="medium",
            affected_area="unknown",
        )

    # Short messages with no pattern match: likely greeting or vague
    if len(text) < 15:
        return MessageClassification(classification="product_question", confidence="low")

    # Substantive unclassified messages (15+ chars, no pattern match):
    # These are real merchant input that didn't match any known pattern.
    # Route as "unclassified" — still creates incident, gets operator attention.
    return MessageClassification(
        classification="unclassified",
        confidence="low",
        affected_area="unknown",
        severity="low",
    )


# ---------------------------------------------------------------------------
# Diagnostics — reuse existing setup audit, billing, onboarding
# ---------------------------------------------------------------------------

@dataclass
class DiagnosticResult:
    """Results from running diagnostics on merchant state."""
    setup_status: str = "unknown"  # degraded / needs_repair / lite_ready / pro_active
    degraded_reasons: list = field(default_factory=list)
    billing_ok: bool = True
    plan: str = "lite"
    billing_active: bool = False
    onboarding_status: str = "unknown"
    onboarding_error: str | None = None
    klaviyo_status: str = "unknown"
    webhook_ok: bool = True
    tracker_ok: bool = True
    entitlement_mismatch: bool = False
    repair_attempted: bool = False
    repair_result: str | None = None


def run_diagnostics(
    db: Session, shop_domain: str,
    affected_area: str | None = None,
    severity: str = "low",
) -> DiagnosticResult:
    """Run targeted diagnostics based on affected area and severity."""
    result = DiagnosticResult()

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant:
        result.setup_status = "degraded"
        result.degraded_reasons = ["merchant_not_found"]
        return result

    result.plan = merchant.plan or "lite"
    result.billing_active = merchant.billing_active or False
    result.onboarding_status = merchant.onboarding_status or "unknown"
    result.onboarding_error = merchant.onboarding_error
    result.klaviyo_status = merchant.klaviyo_connection_status or "not_connected"

    # Run setup audit (fast mode — DB only)
    try:
        from app.services.setup_audit import compute_audit_fast
        audit = compute_audit_fast(db, shop_domain)
        result.setup_status = audit.readiness
        result.degraded_reasons = audit.degraded_reasons or []
        result.webhook_ok = audit.checks.webhook_ok
        result.tracker_ok = audit.checks.tracker_ok
    except Exception as exc:
        log.warning("chatbot diagnostics: setup audit failed: %s", exc)

    # Deep Shopify diagnostics for high-severity setup/integration issues
    # Only when fast audit shows missing components AND severity warrants API call
    # Gated by repair claim to prevent concurrent repairs with orchestrator
    _DEEP_AREAS = ("shopify_auth", "webhooks", "script_tags", "tracker")
    if (
        severity in ("high", "critical")
        and affected_area in _DEEP_AREAS
        and (not result.webhook_ok or not result.tracker_ok)
        and merchant.access_token
    ):
        from app.core.repair_claim import try_claim_repair, release_repair_claim
        # Always claim "webhooks" — matches orchestrator's claim key.
        # Deep check may repair both webhooks and tracker in one call.
        claim_area = "webhooks"
        if try_claim_repair(shop_domain, claim_area):
            try:
                _run_deep_shopify_check(db, merchant, result)
            finally:
                release_repair_claim(shop_domain, claim_area)
        else:
            log.info("chatbot: repair claim denied for %s:%s — another repair in progress",
                     shop_domain, claim_area)
            result.repair_attempted = False
            result.repair_result = "repair_in_progress_by_other"

    # Entitlement mismatch detection
    if merchant.plan == "pro" and not merchant.billing_active:
        result.entitlement_mismatch = True
        result.degraded_reasons.append("plan_pro_but_billing_inactive")
    if merchant.billing_active and merchant.plan != "pro":
        result.entitlement_mismatch = True
        result.degraded_reasons.append("billing_active_but_plan_not_pro")

    return result


def _run_deep_shopify_check(db: Session, merchant: Merchant, result: DiagnosticResult):
    """
    Live Shopify API check for webhook/tracker status.
    Only called for high-severity issues where fast audit shows missing components.
    Updates merchant DB record if live state differs from stored state.
    Skips blocklisted shops (legacy/dev placeholders).
    """
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    if merchant.shop_domain in _ONBOARDING_BLOCKLIST:
        log.info("chatbot: skipping deep check for blocklisted shop=%s", merchant.shop_domain)
        return

    try:
        from app.core.token_crypto import decrypt_token
        from app.core.tracker_version import get_tracker_url
        import os

        token = decrypt_token(merchant.access_token)
        app_url = os.getenv("SHOPIFY_APP_URL", "https://api.hedgesparkhq.com")
        tracker_url = get_tracker_url() or f"{app_url}/tracker.js"

        # Check webhook live state
        if not result.webhook_ok:
            try:
                from app.services.shopify_admin import ensure_orders_webhook
                wh_id, created = ensure_orders_webhook(
                    merchant.shop_domain, token, app_url
                )
                if wh_id:
                    merchant.webhook_id = str(wh_id)
                    result.webhook_ok = True
                    if "webhook_missing" in result.degraded_reasons:
                        result.degraded_reasons.remove("webhook_missing")
                    result.repair_attempted = True
                    result.repair_result = "webhook_repaired" if created else "webhook_confirmed"
                    log.info("chatbot deep check: webhook %s for %s",
                             "repaired" if created else "confirmed", merchant.shop_domain)
            except Exception as exc:
                log.warning("chatbot deep check: webhook check failed for %s: %s",
                           merchant.shop_domain, type(exc).__name__)

        # Check tracker live state
        if not result.tracker_ok:
            try:
                from app.services.shopify_admin import ensure_tracker_script_tag
                st_id, created = ensure_tracker_script_tag(
                    merchant.shop_domain, token, tracker_url
                )
                if st_id:
                    merchant.script_tag_id = str(st_id)
                    result.tracker_ok = True
                    if "tracker_missing" in result.degraded_reasons:
                        result.degraded_reasons.remove("tracker_missing")
                    result.repair_attempted = True
                    existing = result.repair_result or ""
                    result.repair_result = f"{existing}{',' if existing else ''}tracker_{'repaired' if created else 'confirmed'}"
                    log.info("chatbot deep check: tracker %s for %s",
                             "repaired" if created else "confirmed", merchant.shop_domain)
            except Exception as exc:
                log.warning("chatbot deep check: tracker check failed for %s: %s",
                           merchant.shop_domain, type(exc).__name__)

        db.flush()
    except Exception as exc:
        log.warning("chatbot deep check: token decrypt failed for %s: %s",
                   merchant.shop_domain, type(exc).__name__)


def attempt_safe_repair(db: Session, shop_domain: str, diagnostic: DiagnosticResult) -> DiagnosticResult:
    """
    Attempt autonomous safe repair for known fixable issues.
    Only triggers TIER_0-equivalent safe operations.
    Gated by repair claim to prevent concurrent repairs.
    """
    if diagnostic.setup_status in ("degraded", "needs_repair"):
        # Try onboarding retry for non-ready merchants
        if diagnostic.onboarding_status in ("pending", "failed"):
            from app.core.repair_claim import try_claim_repair, release_repair_claim
            if not try_claim_repair(shop_domain, "onboarding"):
                log.info("chatbot: onboarding repair claim denied for %s — another repair in progress", shop_domain)
                diagnostic.repair_result = "repair_in_progress_by_other"
                return diagnostic
            try:
                from app.services.onboarding import run_onboarding
                merchant_row = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
                if not merchant_row:
                    diagnostic.repair_result = "merchant_not_found"
                    return diagnostic
                onboard_result = run_onboarding(db, merchant_row)
                if onboard_result.status == "ready":
                    diagnostic.repair_attempted = True
                    diagnostic.repair_result = "onboarding_completed"
                    diagnostic.setup_status = "lite_ready"
                    log.info("chatbot: auto-repair onboarding success for %s", shop_domain)
                else:
                    diagnostic.repair_attempted = True
                    diagnostic.repair_result = f"onboarding_{onboard_result.status}: {onboard_result.error or 'unknown'}"
            except Exception as exc:
                diagnostic.repair_attempted = True
                diagnostic.repair_result = f"onboarding_error: {type(exc).__name__}"
                log.warning("chatbot: auto-repair onboarding failed for %s: %s", shop_domain, exc)
            finally:
                release_repair_claim(shop_domain, "onboarding")

    return diagnostic


# ---------------------------------------------------------------------------
# Response engine — uses chat_voice for variation + personality
# ---------------------------------------------------------------------------

from app.services.chat_voice import _pick, closing

# Product knowledge base (deterministic — no LLM needed for known topics)
_PRODUCT_ANSWERS: dict[str, str] = {
    "signal": "Signals are automated insights HedgeSpark detects from your store data \u2014 like high-intent visitors, cart abandonment patterns, or pricing opportunities. Each signal includes a confidence level and a recommended action.",
    "nudge": "Nudges are smart on-site messages shown to visitors based on their behavior \u2014 social proof, urgency cues, return visitor recognition. You configure them in the Nudges section.",
    "tracker": "The HedgeSpark tracker is a lightweight JavaScript snippet on your store. It captures visitor behavior \u2014 page views, cart actions, purchase intent \u2014 and powers all insights and signals.",
    "klaviyo": "Klaviyo integration pushes high-intent visitor events from HedgeSpark directly to your Klaviyo account for email/SMS targeting. Connect it in Settings \u2192 Integrations.",
    "plan": "HedgeSpark offers Lite (€39) with full $0-60 competitor parity plus the Revenue at Risk Score, and Pro (€99) with the moat intelligence layer — Causal Lift, Night Shift Agent, Anomaly Fusion, Counterfactual, Revenue Autopsy, Nudge DNA, MTA, Session replay. You can upgrade anytime from the dashboard.",
    "pro": "Pro unlocks: Funnel & Session analysis, AI Nudges, Advanced Attribution, Cohort Analysis, Click Heatmaps, and more. Upgrade from the dashboard.",
    "attribution": "Attribution tracks which traffic sources and campaigns drive the most valuable visitors and conversions in your store.",
    "funnel": "Funnel analysis shows where visitors drop off in your purchase flow \u2014 from landing to checkout \u2014 so you can identify and fix conversion bottlenecks.",
    "heatmap": "Heatmaps show where visitors click most on your store pages, helping you optimize layout and product placement.",
    "live": "Live Radar shows real-time visitor activity on your store \u2014 who\u2019s browsing, what they\u2019re looking at, and their intent score.",
    "brief": "The Daily Brief is an AI-generated summary of your store\u2019s performance, key signals, and recommended actions for the day.",
    "revenue": "Revenue Radar tracks your store\u2019s revenue trends, identifies at-risk products, and surfaces pricing opportunities.",
    "webhook": "Webhooks are automated notifications from Shopify to HedgeSpark when events happen (orders, uninstalls). They\u2019re set up automatically during installation.",
    "script_tag": "Script tags are how the tracker gets loaded on your store pages. Installed automatically \u2014 if missing, the system can repair it.",
    "setup": "After installing HedgeSpark, the system automatically registers webhooks, installs the tracker script, and starts collecting data. First insights appear within hours of your first visitors.",
    "pixel": "The HedgeSpark pixel tracks purchase events on your checkout confirmation page. It powers revenue attribution and conversion measurement.",
    "purchase": "Purchase tracking connects Shopify orders to visitor behavior. This enables revenue attribution, conversion measurement, and ROI analysis for your traffic sources.",
    "session": "Session Replay shows how individual visitors navigate your store \u2014 pages viewed, time spent, scroll depth, and cart interactions.",
    "cohort": "Cohort Analysis groups visitors by behavior (new vs. returning) and shows how each group converts over time.",
}


@dataclass
class ChatResponse:
    """Structured response from the chatbot."""
    message: str
    classification: str
    severity: str
    affected_area: str
    incident_created: bool = False
    incident_id: int | None = None
    repair_attempted: bool = False
    repair_result: str | None = None
    diagnostic_summary: dict | None = None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def process_message(db: Session, shop_domain: str, message: str) -> ChatResponse:
    """
    Process a merchant chat message end-to-end.
    Classify → load context → diagnose → respond → create incident if needed.
    """
    import app.services.chat_voice as voice

    # 1. Classify
    cls = classify_message(message)

    # 2. Handle out-of-scope immediately
    if cls.classification == "out_of_scope":
        return ChatResponse(
            message=_pick(voice.OUT_OF_SCOPE, message, shop_domain),
            classification=cls.classification,
            severity=cls.severity,
            affected_area=cls.affected_area,
        )

    # 3. Load store context (powers contextualized responses)
    from app.services.store_context import get_store_context
    store = get_store_context(db, shop_domain)

    # 4. Detect follow-up to recent incident (conversation memory)
    is_followup = False
    recent_incident = None
    followup_patterns = re.search(
        r"(still (broken|not working|same|down|failing|dead|off))|"
        r"(same (issue|problem|bug|error))|"
        r"(didn.?t (work|fix|help|change|resolve))|"
        r"(happening again|not fixed|still see|still have)",
        message.lower(),
    )
    if followup_patterns:
        recent_incident = _find_active_incident(db, shop_domain, cls.affected_area)
        if not recent_incident:
            # Try with broader search (any active incident for this shop in last 2h)
            cutoff = _now() - timedelta(hours=2)
            recent_incident = (
                db.query(SupportIncident)
                .filter(
                    SupportIncident.shop_domain == shop_domain,
                    SupportIncident.status.in_(["open", "triaged", "investigating"]),
                    SupportIncident.created_at >= cutoff,
                )
                .order_by(SupportIncident.created_at.desc())
                .first()
            )
        if recent_incident:
            is_followup = True

    # 5. Run diagnostics for non-trivial issues
    diagnostic = None
    needs_diagnostics = cls.classification in (
        "bug_report", "setup_help", "billing_access_issue",
        "integration_issue", "data_quality_issue",
    )

    if needs_diagnostics:
        diagnostic = run_diagnostics(db, shop_domain, cls.affected_area, cls.severity)

    # 6. Check if there's already an active bugfix candidate for this area
    existing_candidate = _find_active_candidate_for_area(db, cls.affected_area)
    already_being_fixed = existing_candidate is not None

    # 6b. Check for performance questions (insight engine)
    # Runs for product_question AND unclassified — catches "how is my store doing" etc.
    from app.services.store_insight_engine import answer_performance_question
    perf_answer = None
    if cls.classification in ("product_question", "unclassified"):
        try:
            perf_answer = answer_performance_question(db, shop_domain, message)
        except Exception as exc:
            log.warning("merchant_chatbot: process_message failed: %s", exc)

    # 7. Generate response (store-aware, context-driven)
    if perf_answer:
        response_text = perf_answer
    elif is_followup and recent_incident:
        # Conversation memory: acknowledge the follow-up
        area = recent_incident.affected_area or cls.affected_area
        response_text = (
            f"I see this is still happening \u2014 I\u2019m looking at it again. "
            f"Your earlier report (incident #{recent_incident.id}, area: {area}) is still active."
        )
        if diagnostic and diagnostic.setup_status in ("degraded", "needs_repair"):
            response_text += " I\u2019m re-running diagnostics now."
        elif already_being_fixed:
            response_text += " A fix is already in the pipeline for this."
        else:
            response_text += " I\u2019ll escalate this for a deeper look."
        cls.severity = "high"  # follow-ups are escalated
    elif cls.classification == "product_question":
        response_text = _answer_product_question(message, shop_domain, db, store)
    elif cls.classification == "setup_help":
        response_text = _respond_setup_help(message, diagnostic, shop_domain, store)
    elif cls.classification == "bug_report":
        response_text = _respond_bug_report(message, diagnostic, cls.affected_area, shop_domain, store)
    elif cls.classification == "billing_access_issue":
        response_text = _respond_billing_issue(message, diagnostic, shop_domain)
    elif cls.classification == "integration_issue":
        response_text = _respond_integration_issue(message, diagnostic, cls.affected_area, shop_domain)
    elif cls.classification == "data_quality_issue":
        response_text = _respond_data_quality(message, diagnostic, shop_domain, store)
    elif cls.classification == "feature_request":
        response_text = _pick(voice.FEATURE_REQUEST, message, shop_domain)
    elif cls.classification == "unclassified":
        # β5 — LLM fallback with RAG. Tries Haiku grounded on merchant
        # snapshot; silently falls back to deterministic template on any
        # failure, budget exhaustion, or hallucination detection.
        try:
            from app.services.chatbot_llm_fallback import try_llm_fallback
            llm_result = try_llm_fallback(db, shop_domain=shop_domain, message=message)
            if llm_result.success and llm_result.answer:
                response_text = llm_result.answer
            else:
                response_text = _pick(voice.UNCLASSIFIED, message, shop_domain)
        except Exception as exc:
            log.warning("merchant_chatbot: process_message failed: %s", exc)
            response_text = _pick(voice.UNCLASSIFIED, message, shop_domain)
    else:
        response_text = _pick(voice.GENERIC_FALLBACK, message, shop_domain)

    # 8. If already being fixed, inform merchant
    if already_being_fixed and cls.classification in ("bug_report", "integration_issue") and not is_followup:
        response_text += "\n\n" + _pick(voice.ALREADY_BEING_FIXED, message, shop_domain)

    # 9. Attempt safe repair for fixable issues (only if NOT already being fixed)
    repair_attempted = False
    repair_result = None
    deep_check_skipped = diagnostic.repair_result == "repair_in_progress_by_other" if diagnostic else False
    if diagnostic and cls.severity in ("high", "critical") and not already_being_fixed:
        if diagnostic.setup_status in ("degraded", "needs_repair"):
            diagnostic = attempt_safe_repair(db, shop_domain, diagnostic)
            repair_attempted = diagnostic.repair_attempted
            repair_result = diagnostic.repair_result
            if repair_attempted and diagnostic.repair_result and "completed" in diagnostic.repair_result:
                response_text += "\n\n" + _pick(voice.REPAIR_TRIGGERED, message, shop_domain)
        if diagnostic.repair_result == "repair_in_progress_by_other" or deep_check_skipped:
            response_text += "\n\n" + voice.REPAIR_IN_PROGRESS

    # 10. Append soft closing (deterministic, not on every message)
    # Use closing on diagnostic/bug/integration responses, not on product questions
    if cls.classification in ("bug_report", "integration_issue", "data_quality_issue", "unclassified"):
        response_text += "\n\n" + closing(message, shop_domain)

    # 8. Create incident for non-trivial issues (with dedup)
    incident_created = False
    incident_id = None
    should_create_incident = cls.classification in (
        "bug_report", "billing_access_issue", "integration_issue",
        "data_quality_issue", "feature_request", "unclassified",
    ) or cls.severity in ("high", "critical")

    if should_create_incident:
        # Dedup: check for active incident for same (shop, area) within 1 hour
        incident = _find_active_incident(db, shop_domain, cls.affected_area)
        if incident:
            # Reuse existing incident
            incident_created = False
            incident_id = incident.id
            log.info("chatbot: reusing existing incident id=%d for %s:%s",
                     incident.id, shop_domain, cls.affected_area)
            # Retry pipeline routing if the first attempt failed (no alert linked)
            if incident.linked_ops_alert_id is None and cls.severity in ("high", "critical"):
                _route_to_pipeline(db, incident, cls, diagnostic, existing_candidate)
        else:
            incident = _create_incident(db, shop_domain, message, cls, response_text, diagnostic)
            incident_created = True
            incident_id = incident.id

            # Route to autonomous pipeline (with dedup)
            _route_to_pipeline(db, incident, cls, diagnostic, existing_candidate)

        if cls.severity in ("high", "critical"):
            tracked_msg = _pick(voice.INCIDENT_TRACKED, message, shop_domain).replace("{id}", str(incident_id))
            response_text += f"\n\n{tracked_msg}"

    # 9. Audit log
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db, actor_type="merchant", actor_name=shop_domain,
            action_type="support_chat",
            target_type="support_incident" if incident_created else "chat",
            target_id=str(incident_id) if incident_id else None,
            shop_domain=shop_domain,
            after_state={
                "classification": cls.classification,
                "severity": cls.severity,
                "affected_area": cls.affected_area,
                "incident_created": incident_created,
                "repair_attempted": repair_attempted,
                "already_being_fixed": already_being_fixed,
            },
            status="completed",
        )
    except Exception as exc:
        log.warning("chatbot: audit log write failed for %s: %s", shop_domain, exc)

    return ChatResponse(
        message=response_text,
        classification=cls.classification,
        severity=cls.severity,
        affected_area=cls.affected_area,
        incident_created=incident_created,
        incident_id=incident_id,
        repair_attempted=repair_attempted,
        repair_result=repair_result,
        diagnostic_summary=_safe_diagnostic_summary(diagnostic) if diagnostic else None,
    )


# ---------------------------------------------------------------------------
# Response generators (per classification)
# ---------------------------------------------------------------------------

def _answer_product_question(message: str, shop_domain: str = "", db: Session | None = None, store=None) -> str:
    """Answer product questions with store-specific context when available."""
    import app.services.chat_voice as voice
    text = message.lower()

    # Try store-contextualized answer first
    for keyword, base_answer in _PRODUCT_ANSWERS.items():
        if keyword not in text:
            continue

        if not store or not store.has_data:
            return base_answer

        # Contextualize based on keyword + store state
        if keyword == "signal" and store.active_signals_count > 0:
            ctx = f"Your store currently has {store.active_signals_count} products with active signals."
            if store.top_signal_summary:
                ctx += f" Top right now: {store.top_signal_summary}."
            return f"{base_answer}\n\n{ctx}"

        if keyword in ("revenue", "brief") and store.has_revenue:
            return f"{base_answer}\n\nYour store did {store.orders_7d} orders in the last 7 days."

        if keyword in ("tracker", "pixel") and store.visitors_7d > 0:
            return f"{base_answer}\n\nYour tracker is active \u2014 {store.visitors_7d} visitors tracked in the last 7 days."

        if keyword == "nudge" and store.plan != "pro":
            return f"{base_answer}\n\nNudges are a Pro feature. You\u2019re currently on {store.plan.title()}."

        if keyword in ("funnel", "heatmap", "session", "cohort", "attribution") and store.plan != "pro":
            return f"{base_answer}\n\nThis is a Pro feature. Upgrade from the dashboard to unlock it."

        if keyword == "plan":
            return f"{base_answer}\n\nYou\u2019re currently on the {store.plan.title()} plan."

        return base_answer

    # Fallback with store awareness
    if store and store.has_data:
        return (
            f"I can explain any part of HedgeSpark. Your store has "
            f"{store.visitors_7d} visitors this week"
            f"{f' and {store.orders_7d} orders' if store.has_revenue else ''}. "
            f"What are you curious about?"
        )
    return _pick(voice.PRODUCT_QUESTION_FALLBACK, message, shop_domain)


def _respond_setup_help(message: str, diagnostic: DiagnosticResult | None, shop_domain: str, store=None) -> str:
    """Respond to setup/onboarding issues with concrete status + store context."""
    import app.services.chat_voice as voice

    if not diagnostic:
        return _pick(voice.SETUP_CHECKING, message, shop_domain)

    if diagnostic.setup_status == "pro_active":
        base = _pick(voice.SETUP_ALL_GOOD_PRO, message, shop_domain)
        if store and store.has_data:
            base += f"\n\nYour store is tracking {store.visitors_7d} visitors this week."
        return base

    if diagnostic.setup_status == "lite_ready":
        base = _pick(voice.SETUP_ALL_GOOD_LITE, message, shop_domain)
        if store and store.has_data:
            base += f"\n\n{store.visitors_7d} visitors tracked in the last 7 days."
        elif store and not store.has_data:
            base += "\n\nNo visitor data yet \u2014 data starts flowing once your first visitors arrive."
        return base

    parts = ["I\u2019ve checked your store setup and found some issues:"]

    if "merchant_not_found" in diagnostic.degraded_reasons:
        parts.append("\u2022 Your store doesn\u2019t appear to be registered yet. Try reinstalling the app from the Shopify App Store.")
        return "\n".join(parts)

    if "install_inactive" in diagnostic.degraded_reasons:
        parts.append("\u2022 The app appears to be uninstalled. You\u2019ll need to reinstall from the Shopify App Store.")
        return "\n".join(parts)

    if "token_missing" in diagnostic.degraded_reasons or "token_decrypt_failed" in diagnostic.degraded_reasons:
        parts.append("\u2022 There\u2019s an authentication issue with your Shopify connection. Try reinstalling the app \u2014 this will refresh the connection.")
        return "\n".join(parts)

    if not diagnostic.webhook_ok:
        parts.append("\u2022 Webhook registration is missing \u2014 I\u2019m triggering an automatic repair.")
    if not diagnostic.tracker_ok:
        parts.append("\u2022 Tracker script is missing \u2014 I\u2019m triggering a reinstall.")

    if diagnostic.onboarding_status == "failed" and diagnostic.onboarding_error:
        parts.append(f"\u2022 Onboarding hit an issue: {diagnostic.onboarding_error}")

    parts.append("\nI\u2019m checking if I can repair these automatically.")
    return "\n".join(parts)


def _respond_bug_report(message: str, diagnostic: DiagnosticResult | None, affected_area: str, shop_domain: str = "", store=None) -> str:
    """Respond to bug reports with relevant diagnostic context."""
    import app.services.chat_voice as voice

    if diagnostic and diagnostic.setup_status in ("degraded", "needs_repair"):
        return _respond_setup_help(message, diagnostic, shop_domain, store)

    if affected_area == "tracker":
        if diagnostic and not diagnostic.tracker_ok:
            return _pick(voice.BUG_REPORT_TRACKER_MISSING, message, shop_domain)
        return _pick(voice.BUG_REPORT_TRACKER_OK, message, shop_domain)

    if affected_area == "dashboard":
        return _pick(voice.BUG_REPORT_DASHBOARD, message, shop_domain)

    if affected_area == "nudges":
        if diagnostic and diagnostic.plan != "pro":
            return voice.BUG_REPORT_NUDGES_NOT_PRO
        return _pick(voice.BUG_REPORT_NUDGES_PRO, message, shop_domain)

    return _pick(voice.BUG_REPORT_GENERIC, message, shop_domain)


def _respond_billing_issue(message: str, diagnostic: DiagnosticResult | None, shop_domain: str = "") -> str:
    """Respond to billing/access issues with concrete plan status."""
    import app.services.chat_voice as voice

    if not diagnostic:
        return _pick(voice.BILLING_CHECKING, message, shop_domain)

    if diagnostic.entitlement_mismatch:
        if "plan_pro_but_billing_inactive" in diagnostic.degraded_reasons:
            return voice.ENTITLEMENT_PRO_NO_BILLING
        if "billing_active_but_plan_not_pro" in diagnostic.degraded_reasons:
            return voice.ENTITLEMENT_BILLING_NOT_PRO
        return voice.ENTITLEMENT_PRO_NO_BILLING

    if diagnostic.billing_active and diagnostic.plan == "pro":
        text = message.lower()
        if re.search(r"(locked|blocked|can.?t access|not.*show)", text):
            return _pick(voice.BILLING_PRO_LOCKED, message, shop_domain)
        return _pick(voice.BILLING_PRO_HEALTHY, message, shop_domain)

    if not diagnostic.billing_active and diagnostic.plan == "lite":
        return _pick(voice.BILLING_STARTER, message, shop_domain)

    return f"Your current plan: {diagnostic.plan}, billing active: {diagnostic.billing_active}. If this doesn\u2019t match what you expect, I\u2019ve logged it for investigation."


def _respond_integration_issue(message: str, diagnostic: DiagnosticResult | None, affected_area: str, shop_domain: str = "") -> str:
    """Respond to integration issues (Klaviyo, webhooks, etc.)."""
    import app.services.chat_voice as voice

    if affected_area == "klaviyo":
        if diagnostic and diagnostic.klaviyo_status == "not_connected":
            return _pick(voice.INTEGRATION_KLAVIYO_NOT_CONNECTED, message, shop_domain)
        if diagnostic and diagnostic.klaviyo_status == "invalid_key":
            return voice.INTEGRATION_KLAVIYO_INVALID
        if diagnostic and diagnostic.klaviyo_status == "connected":
            return _pick(voice.INTEGRATION_KLAVIYO_CONNECTED, message, shop_domain)
        return _pick(voice.INTEGRATION_KLAVIYO_GENERIC, message, shop_domain)

    if affected_area == "webhooks":
        if diagnostic and not diagnostic.webhook_ok:
            return _pick(voice.INTEGRATION_WEBHOOK_MISSING, message, shop_domain)
        return _pick(voice.INTEGRATION_WEBHOOK_OK, message, shop_domain)

    if affected_area == "script_tags":
        if diagnostic and not diagnostic.tracker_ok:
            return _pick(voice.INTEGRATION_SCRIPT_MISSING, message, shop_domain)
        return _pick(voice.INTEGRATION_SCRIPT_OK, message, shop_domain)

    if affected_area == "resend":
        return _pick(voice.INTEGRATION_EMAIL, message, shop_domain)

    return _pick(voice.INTEGRATION_GENERIC, message, shop_domain)


def _respond_data_quality(message: str, diagnostic: DiagnosticResult | None, shop_domain: str = "", store=None) -> str:
    """Respond to data quality concerns with store-specific context."""
    import app.services.chat_voice as voice

    if diagnostic and diagnostic.setup_status in ("degraded", "needs_repair"):
        return _pick(voice.DATA_QUALITY_DEGRADED, message, shop_domain)

    base = _pick(voice.DATA_QUALITY_HEALTHY, message, shop_domain)

    # Add store-specific data context
    if store and store.has_data:
        context_parts = []
        if store.visitors_7d > 0:
            context_parts.append(f"I\u2019m seeing {store.visitors_7d} visitors in the last 7 days")
        if store.has_revenue:
            context_parts.append(f"{store.orders_7d} orders tracked")
        if store.cart_rate is not None:
            context_parts.append(f"cart rate at {store.cart_rate:.1%}")
        if context_parts:
            base += f"\n\nFor reference, your current numbers: {', '.join(context_parts)}."

    return base


# ---------------------------------------------------------------------------
# Pipeline awareness — check existing state before acting
# ---------------------------------------------------------------------------

def _find_active_candidate_for_area(db, affected_area: str):
    """
    Check if there's already an active BugFixCandidate originated from a
    merchant-reported bug for this affected area.

    Only matches candidates created from support_incident sources where the
    context explicitly mentions the same affected_area. This avoids false
    matches against system-level candidates (worker crashes, outcome failures)
    that happen to contain area keywords in their source_ref.
    """
    if not affected_area or affected_area == "unknown":
        return None

    from app.models.bugfix_candidate import BugFixCandidate

    # Only match candidates from support_incident source whose context
    # includes this specific affected_area. This is precise — no fuzzy ILIKE.
    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed", "approved", "applying"]),
            BugFixCandidate.source_type == "support_incident",
            BugFixCandidate.context_json.ilike(f'%"affected_area": "{affected_area}"%'),
        )
        .order_by(BugFixCandidate.created_at.desc())
        .first()
    )
    return candidates


def _find_active_incident(db: Session, shop_domain: str, affected_area: str | None):
    """
    Find an existing active incident for same (shop, area) within 1 hour.
    Returns the incident if found, None otherwise.
    """
    if not affected_area:
        return None

    cutoff = _now() - timedelta(hours=1)
    return (
        db.query(SupportIncident)
        .filter(
            SupportIncident.shop_domain == shop_domain,
            SupportIncident.affected_area == affected_area,
            SupportIncident.status.in_(["open", "triaged", "investigating"]),
            SupportIncident.created_at >= cutoff,
        )
        .order_by(SupportIncident.created_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Incident creation + pipeline routing
# ---------------------------------------------------------------------------

def _create_incident(
    db: Session, shop_domain: str, message: str,
    cls: MessageClassification, response_text: str,
    diagnostic: DiagnosticResult | None,
) -> SupportIncident:
    """Create a structured support incident."""
    incident = SupportIncident(
        shop_domain=shop_domain,
        source="merchant_chat",
        original_message=message[:2000],  # cap
        classification=cls.classification,
        severity=cls.severity,
        confidence=cls.confidence,
        affected_area=cls.affected_area,
        status="open",
        response_text=response_text[:2000],
    )
    db.add(incident)
    db.flush()

    log.info(
        "chatbot: incident created id=%d shop=%s class=%s severity=%s area=%s",
        incident.id, shop_domain, cls.classification, cls.severity, cls.affected_area,
    )
    return incident


def _find_existing_alert(
    db: Session, shop_domain: str | None, alert_type: str,
    affected_area: str | None = None, lookback_hours: int = 24,
):
    """Find an existing unresolved OpsAlert for this shop + alert_type + area."""
    from app.models.ops_alert import OpsAlert
    q = db.query(OpsAlert).filter(
        OpsAlert.alert_type == alert_type,
        OpsAlert.resolved == False,
        OpsAlert.created_at >= _now() - timedelta(hours=lookback_hours),
    )
    if shop_domain:
        q = q.filter(OpsAlert.shop_domain == shop_domain)
    # For merchant_reported_bug alerts, match on affected_area in detail JSON
    # to prevent different bugs from the same merchant being collapsed
    if affected_area and alert_type == "merchant_reported_bug":
        q = q.filter(OpsAlert.detail.ilike(f'%"affected_area": "{affected_area}"%'))
    return q.first()


def _route_to_pipeline(
    db: Session, incident: SupportIncident,
    cls: MessageClassification, diagnostic: DiagnosticResult | None,
    existing_candidate=None,
):
    """
    Route incident to autonomous pipeline when appropriate.
    Deduplicates OpsAlerts and links to existing BugFixCandidates.
    Transitions incident status: open → triaged (when alert linked).
    """
    alert = None

    # Entitlement mismatches → ops alert (with dedup)
    if diagnostic and diagnostic.entitlement_mismatch:
        try:
            alert = _find_existing_alert(db, incident.shop_domain, "entitlement_mismatch")
            if not alert:
                from app.services.alerting import write_alert
                alert = write_alert(
                    db, severity="warning", source="merchant_chatbot",
                    alert_type="entitlement_mismatch",
                    summary=f"Plan/billing mismatch for {incident.shop_domain}: plan={diagnostic.plan} billing_active={diagnostic.billing_active}",
                    shop_domain=incident.shop_domain,
                )
            incident.linked_ops_alert_id = alert.id
            incident.status = "triaged"
        except Exception as exc:
            log.warning("chatbot: entitlement alert routing failed for incident=%d: %s",
                        incident.id, exc)

    # High-severity bugs with known affected area → ops alert (with dedup)
    if cls.classification == "bug_report" and cls.severity in ("high", "critical"):
        try:
            alert = _find_existing_alert(
                db, incident.shop_domain, "merchant_reported_bug",
                affected_area=cls.affected_area,
            )
            if not alert:
                from app.services.alerting import write_alert
                alert = write_alert(
                    db, severity="warning", source="merchant_chatbot",
                    alert_type="merchant_reported_bug",
                    summary=f"Merchant reported bug: {incident.original_message[:200]}",
                    shop_domain=incident.shop_domain,
                    detail={"affected_area": cls.affected_area, "incident_id": incident.id},
                )
            else:
                log.info("chatbot: reusing existing alert id=%d for %s:%s",
                         alert.id, incident.shop_domain, cls.affected_area)
            incident.linked_ops_alert_id = alert.id
            incident.status = "triaged"
        except Exception as exc:
            log.warning("chatbot: bug report alert routing failed for incident=%d: %s",
                        incident.id, exc)

    # Link to existing bugfix candidate if one exists for this area
    if existing_candidate:
        incident.linked_bugfix_candidate_id = existing_candidate.id
        incident.status = "investigating"

    db.flush()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_diagnostic_summary(diagnostic: DiagnosticResult) -> dict:
    """Return a merchant-safe subset of diagnostics (no internal details)."""
    return {
        "setup_status": diagnostic.setup_status,
        "plan": diagnostic.plan,
        "billing_active": diagnostic.billing_active,
        "onboarding": diagnostic.onboarding_status,
        "webhook_ok": diagnostic.webhook_ok,
        "tracker_ok": diagnostic.tracker_ok,
        "klaviyo": diagnostic.klaviyo_status,
        "repair_attempted": diagnostic.repair_attempted,
        "repair_result": diagnostic.repair_result,
    }


def get_incident_history(db: Session, shop_domain: str, limit: int = 20) -> list[dict]:
    """Get recent incidents for a merchant."""
    from sqlalchemy import desc
    incidents = (
        db.query(SupportIncident)
        .filter(SupportIncident.shop_domain == shop_domain)
        .order_by(desc(SupportIncident.created_at))
        .limit(limit)
        .all()
    )
    return [
        {
            "id": i.id,
            "created_at": i.created_at.isoformat() + "Z" if i.created_at else None,
            "classification": i.classification,
            "severity": i.severity,
            "affected_area": i.affected_area,
            "status": i.status,
            "message_preview": (i.original_message or "")[:100],
            "response_preview": (i.response_text or "")[:100],
        }
        for i in incidents
    ]


# ---------------------------------------------------------------------------
# Billing / entitlement hardening checks (callable outside chatbot)
# ---------------------------------------------------------------------------

def check_entitlement_health(db: Session, shop_domain: str) -> dict:
    """
    Run entitlement health check for a merchant.
    Detects plan/billing mismatches, stale states, etc.
    Returns: {"healthy": bool, "issues": [...]}
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant:
        return {"healthy": False, "issues": ["merchant_not_found"]}

    issues = []

    # Pro plan but billing inactive
    if merchant.plan == "pro" and not merchant.billing_active:
        issues.append("plan_pro_billing_inactive")

    # Billing active but not pro
    if merchant.billing_active and merchant.plan != "pro":
        issues.append("billing_active_plan_not_pro")

    # Has charge ID but no confirmation
    if merchant.billing_charge_id and not merchant.billing_confirmed_at and merchant.billing_active:
        issues.append("charge_id_without_confirmation")

    # Uninstalled but billing still active
    if merchant.install_status == "uninstalled" and merchant.billing_active:
        issues.append("uninstalled_billing_active")

    return {"healthy": len(issues) == 0, "issues": issues}
