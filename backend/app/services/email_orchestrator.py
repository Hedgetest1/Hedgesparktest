"""
email_orchestrator.py — Centralized email orchestration system.

Every email producer in the system submits an EmailIntent instead of
sending directly. The orchestrator collects intents per merchant,
resolves conflicts, enforces rate limits, merges compatible messages,
and flushes the winning intent(s) as actual sends.

This prevents:
  - Multiple emails to the same merchant on the same day
  - Low-priority emails drowning out high-priority ones
  - Spam-like frequency from independent producers
  - Silent merchants receiving zero communications

Architecture:
  1. COLLECT — producers call submit_intent() during their scan phase
  2. RESOLVE — resolve_intents() picks winners per merchant
  3. FLUSH  — flush_intents() sends the winners via Resend

All three steps happen within a single agent_worker cycle.

Public interface:
    submit_intent(db, intent: EmailIntent) -> str        # returns intent_id
    resolve_and_flush(db) -> dict                         # run the full cycle
    get_pending_intents(shop_domain) -> list[EmailIntent]  # diagnostic

Rate limits:
    - Max 1 email per merchant per 24 hours (hard)
    - Max 2 emails per merchant per 7 days (hard)
    - CRITICAL (P0) can override weekly cap (not daily)
    - Auto-response emails bypass rate limits (they're replies)

Priority tiers (highest wins):
    P0 — CRITICAL:   connection_issue, billing problems
    P1 — REVENUE:    revenue triggers, proof reports
    P2 — ENGAGEMENT: weekly digest, first_insight
    P3 — LIFECYCLE:  welcome, setup_incomplete, followup
    P4 — WINBACK:    reengagement, silence detection

Merge rules:
    - Same-day digest + proof → merged into enriched digest
    - Same-day lifecycle + revenue trigger → revenue trigger wins, lifecycle deferred
    - Same-tier intents → most recent signal wins
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("email_orchestrator")


# ---------------------------------------------------------------------------
# Priority tiers
# ---------------------------------------------------------------------------

class Priority(IntEnum):
    CRITICAL    = 0   # connection_issue, billing
    REVENUE     = 1   # revenue triggers, proof
    ENGAGEMENT  = 2   # digest, first_insight
    LIFECYCLE   = 3   # welcome, setup_incomplete, followup
    WINBACK     = 4   # reengagement

    @classmethod
    def from_email_type(cls, email_type: str) -> "Priority":
        _MAP = {
            # P0 — Critical
            "connection_issue":       cls.CRITICAL,
            "gdpr_export":            cls.CRITICAL,  # GDPR Art. 15 — 30-day deadline
            # P2 — Engagement (dashboard content — digest@ channel)
            "weekly_digest":          cls.ENGAGEMENT,
            "lite_morning_digest":    cls.ENGAGEMENT,
            "first_insight":          cls.ENGAGEMENT,
            # P3 — Lifecycle
            "welcome":                cls.LIFECYCLE,
            "beta_welcome":           cls.LIFECYCLE,
            "setup_incomplete":       cls.LIFECYCLE,
            "followup_noopen":        cls.LIFECYCLE,
            "followup_opened":        cls.LIFECYCLE,
            "followup_clicked":       cls.LIFECYCLE,
            # P4 — Winback
            "reengagement":           cls.WINBACK,
            "reengagement_drift":     cls.WINBACK,
            "retention_outreach":     cls.WINBACK,   # Brain Vero — critical churn outreach
            # P1 — Revenue (money-at-risk frame)
            "recovery_digest":        cls.REVENUE,   # Brain Vero — RAR-focused recovery
        }
        return _MAP.get(email_type, cls.LIFECYCLE)


# ---------------------------------------------------------------------------
# Rate limit constants
# ---------------------------------------------------------------------------

_MAX_PER_DAY = 1     # hard cap: 1 email per merchant per 24h
_MAX_PER_WEEK = 2    # hard cap: 2 emails per merchant per 7 days (premium, not noisy)
_REDIS_PREFIX = "hs:email_orch:"
_INTENT_TTL = 3600   # intents expire after 1 hour (single cycle)

# Email types that bypass rate limits (auto-responses to merchant-initiated contact)
_BYPASS_RATE_LIMIT = {"auto_response"}

# Email types that can be merged into a digest
_DIGEST_MERGEABLE = {"first_insight"}

# Email types that should NEVER send standalone — always merge into digest or drop
# These are low-value as standalone sends; they only justify inbox space inside a digest
_DOWNGRADE_TO_DIGEST = {"first_insight", "connection_issue", "reengagement"}

# Email types that justify a standalone send outside the weekly digest
_STANDALONE_WORTHY = {
    "weekly_digest",           # The primary value channel (Pro)
    "lite_morning_digest",     # Daily brief push channel (Lite) — Gap A
    "welcome",                 # First impression — always standalone
    "beta_welcome",            # Beta-cohort first impression
    "setup_incomplete",        # Onboarding blocker — time-sensitive
    "reengagement_drift",      # Stuck-onboarding recovery — time-sensitive
    "gdpr_export",             # GDPR Art. 15 — legal deadline
}


# ---------------------------------------------------------------------------
# EmailIntent dataclass
# ---------------------------------------------------------------------------

@dataclass
class EmailIntent:
    """A request to send an email, not yet approved by the orchestrator."""
    shop_domain: str
    email_type: str
    to_email: str
    subject: str
    html: str
    plain_text: str = ""
    from_address: str = "HedgeSpark <dev@hedgesparkhq.com>"

    # Orchestration metadata
    priority: Priority = field(default=Priority.LIFECYCLE)
    ttl_hours: int = 24          # intent expires if not sent within TTL
    mergeable: bool = False      # can this be folded into another email?
    merge_section: str = ""      # HTML snippet for merge (appended to digest)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Producer context (for diagnostics)
    producer: str = ""           # e.g. "revenue_triggers", "merchant_digest"
    context: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.priority == Priority.LIFECYCLE:
            # Auto-detect priority from email_type
            self.priority = Priority.from_email_type(self.email_type)


# ---------------------------------------------------------------------------
# In-memory intent buffer (per agent_worker cycle)
# ---------------------------------------------------------------------------

_pending_intents: list[EmailIntent] = []  # multi-worker: accept-degrade — submit_intent callers are singleton workers only (no uvicorn-API producer)


def submit_intent(db: Session, intent: EmailIntent) -> str:
    """
    Submit an email intent to the orchestrator.

    Called by individual producers instead of sending directly.
    Returns the intent_id for tracking.
    """
    _pending_intents.append(intent)
    log.info(
        "email_orch: intent submitted id=%s shop=%s type=%s priority=%s producer=%s",
        intent.intent_id, intent.shop_domain, intent.email_type,
        intent.priority.name, intent.producer,
    )
    return intent.intent_id


def send_immediate(db: Session, intent: EmailIntent) -> dict:
    """
    Submit + resolve + send in one synchronous call.

    For real-time paths (auto-response, followup) that need low latency
    but MUST still pass through full governance.

    Pipeline: governance → suppression check → rate limit → atomic guard → send.
    Does NOT go through the batch queue. Does NOT wait for flush cycle.

    Returns {"status": "sent"|"blocked"|"failed", "reason": str|None, "resend_id": str|None}
    """
    log.info(
        "email_orch: immediate send id=%s shop=%s type=%s producer=%s",
        intent.intent_id, intent.shop_domain, intent.email_type, intent.producer,
    )

    # Full governance + send (same path as batch flush)
    if _send_intent(db, intent):
        return {"status": "sent", "reason": None, "resend_id": intent.intent_id}
    else:
        return {"status": "blocked", "reason": "governance_or_guard", "resend_id": None}


def get_pending_intents(shop_domain: str | None = None) -> list[EmailIntent]:
    """Get pending intents, optionally filtered by shop."""
    if shop_domain:
        return [i for i in _pending_intents if i.shop_domain == shop_domain]
    return list(_pending_intents)


def clear_intents() -> None:
    """Clear the intent buffer (called after flush or on error)."""
    _pending_intents.clear()


# ---------------------------------------------------------------------------
# Resolution + Flush
# ---------------------------------------------------------------------------

def resolve_and_flush(db: Session) -> dict:
    """
    Process all pending intents: resolve conflicts, enforce rate limits, send.

    Returns:
        {
            "total_intents":  int,
            "merchants":      int,
            "sent":           int,
            "deferred":       int,
            "rate_limited":   int,
            "suppressed":     int,
            "merged":         int,
        }
    """
    summary = {
        "total_intents": len(_pending_intents),
        "merchants": 0,
        "sent": 0,
        "deferred": 0,
        "rate_limited": 0,
        "suppressed": 0,
        "merged": 0,
    }

    if not _pending_intents:
        return summary

    # Group by merchant
    by_shop: dict[str, list[EmailIntent]] = {}
    for intent in _pending_intents:
        by_shop.setdefault(intent.shop_domain, []).append(intent)

    summary["merchants"] = len(by_shop)

    for shop, intents in by_shop.items():
        result = _resolve_merchant(db, shop, intents)
        summary["sent"] += result["sent"]
        summary["deferred"] += result["deferred"]
        summary["rate_limited"] += result["rate_limited"]
        summary["suppressed"] += result["suppressed"]
        summary["merged"] += result["merged"]

    log.info(
        "email_orch: cycle complete — %d intents, %d merchants, "
        "%d sent, %d deferred, %d rate_limited, %d suppressed, %d merged",
        summary["total_intents"], summary["merchants"],
        summary["sent"], summary["deferred"], summary["rate_limited"],
        summary["suppressed"], summary["merged"],
    )

    clear_intents()
    return summary


def _resolve_merchant(
    db: Session,
    shop: str,
    intents: list[EmailIntent],
) -> dict:
    """
    Resolve intents for a single merchant. This is the SINGLE POINT OF CONTROL
    for all outbound merchant communication.

    Pipeline:
      1. Hard suppression (bounce/complaint)
      2. Merchant pause check
      3. Intent validation (reject redundant, downgrade low-value)
      4. Rate limit enforcement
      5. Conflict detection (no contradictory messages)
      6. Priority resolution (pick winner)
      7. Merge compatible intents into winner
      8. Send
    """
    result = {"sent": 0, "deferred": 0, "rate_limited": 0, "suppressed": 0, "merged": 0}

    if not intents:
        return result

    # ── Step 0: Operator/dev-shop guard ──
    # Founder direttiva 2026-05-06: dev tenants (hedgespark-dev.myshopify.com,
    # any future operator shops) MUST NEVER receive merchant-facing email.
    # Belt-and-suspenders: check both shop_domain AND each intent's
    # to_email — a dev tenant might be misconfigured (billing_active flip)
    # but the founder's address is hardcoded in operator_blocklist.
    from app.core.operator_blocklist import is_operator_dev_shop, is_operator_email
    if is_operator_dev_shop(shop):
        result["suppressed"] = len(intents)
        for i in intents:
            _log_suppressed(db, i, "operator_dev_shop_blocked")
        log.info(
            "email_orchestrator: blocked %d intent(s) for operator dev shop=%s",
            len(intents), shop,
        )
        return result
    # Address-level fallback gate (defense in depth)
    leaked = [i for i in intents if is_operator_email(i.to_email)]
    if leaked:
        for i in leaked:
            _log_suppressed(db, i, "operator_email_address_blocked")
        log.warning(
            "email_orchestrator: blocked %d intent(s) targeting operator "
            "email(s) for shop=%s — review producer logic",
            len(leaked), shop,
        )
        # Strip the leaked intents and continue with the rest
        intents = [i for i in intents if not is_operator_email(i.to_email)]
        result["suppressed"] += len(leaked)
        if not intents:
            return result

    # ── Step 1: Hard suppression (bounce/complaint — permanent) ──
    if _is_suppressed(db, shop):
        result["suppressed"] = len(intents)
        for i in intents:
            _log_suppressed(db, i, "email_suppressed")
        return result

    # ── Step 2: Merchant pause check ──
    if _is_merchant_paused(db, shop):
        result["suppressed"] = len(intents)
        for i in intents:
            _log_suppressed(db, i, "merchant_paused")
        return result

    # ── Step 3: Adaptive engagement check ──
    # should_send_email returns (False, reason) for FOUR cases — all of
    # which must suppress the send, not just `complained`:
    #   - "complained"               (Resend complaint, hard block)
    #   - "never_opened"             (after threshold, blocked)
    #   - "low_open_rate:X%"         (after threshold, blocked)
    #   - "new_merchant_weekly_cap"  (rate-limit during onboarding)
    # Bug 2026-05-08 brutal audit: previous code only honored "complained"
    # — the other three were silently ignored. Bypass intents (in
    # _BYPASS_RATE_LIMIT, e.g. auto-responses) skip this gate by design,
    # so we only check the gate against the top NON-bypass intent.
    from app.services.email_performance import should_send_email
    intents.sort(key=lambda i: i.priority)
    top = intents[0]
    if top.email_type not in _BYPASS_RATE_LIMIT:
        should, reason = should_send_email(db, shop, top.email_type)
        if not should:
            # All non-bypass intents get suppressed; bypass intents (if any)
            # still get a chance below.
            non_bypass = [i for i in intents if i.email_type not in _BYPASS_RATE_LIMIT]
            for i in non_bypass:
                _log_suppressed(db, i, f"adaptive:{reason}")
            result["suppressed"] = len(non_bypass)
            # Filter out non-bypass so subsequent steps only process
            # bypass intents (auto-responses, transactional, etc).
            intents = [i for i in intents if i.email_type in _BYPASS_RATE_LIMIT]
            if not intents:
                return result

    # ── Step 4: Intent validation — reject and downgrade ──
    bypass = [i for i in intents if i.email_type in _BYPASS_RATE_LIMIT]
    normal = [i for i in intents if i.email_type not in _BYPASS_RATE_LIMIT]

    # Send bypass intents (auto-responses) with their own rate limit
    for i in bypass:
        if _send_intent(db, i):
            result["sent"] += 1
        else:
            result["suppressed"] += 1

    if not normal:
        return result

    # 4a. Deduplicate — only one intent per email_type per merchant
    seen_types: dict[str, EmailIntent] = {}
    deduped: list[EmailIntent] = []
    for i in normal:
        if i.email_type in seen_types:
            result["suppressed"] += 1
            _log_suppressed(db, i, "duplicate_type")
        else:
            seen_types[i.email_type] = i
            deduped.append(i)
    normal = deduped

    # 4b. Downgrade low-value intents — if digest exists, fold them in
    has_digest = any(i.email_type == "weekly_digest" for i in normal)
    if has_digest:
        kept: list[EmailIntent] = []
        for i in normal:
            if i.email_type in _DOWNGRADE_TO_DIGEST:
                # Mark as mergeable into digest instead of standalone
                i.mergeable = True
                i.merge_section = _build_merge_snippet(i)
                result["merged"] += 1
                log.info(
                    "email_orch: downgraded %s to digest merge for %s",
                    i.email_type, shop,
                )
                # Find digest and append merge section
                for d in normal:
                    if d.email_type == "weekly_digest" and d.html:
                        if "<!--MERGE_POINT-->" in d.html:
                            d.html = d.html.replace(
                                "<!--MERGE_POINT-->",
                                i.merge_section + "<!--MERGE_POINT-->",
                            )
                        else:
                            d.html = d.html.replace(
                                "</div></body>",
                                i.merge_section + "</div></body>",
                            )
                        break
            else:
                kept.append(i)
        normal = kept
    else:
        # No digest this cycle — drop downgrade-only intents entirely
        kept = []
        for i in normal:
            if i.email_type in _DOWNGRADE_TO_DIGEST:
                result["suppressed"] += 1
                _log_suppressed(db, i, "no_digest_to_merge_into")
            else:
                kept.append(i)
        normal = kept

    if not normal:
        return result

    # 4c. Reject intents not worthy of standalone send (except digest)
    final: list[EmailIntent] = []
    for i in normal:
        if i.email_type in _STANDALONE_WORTHY or i.email_type == "weekly_digest":
            final.append(i)
        else:
            result["suppressed"] += 1
            _log_suppressed(db, i, f"not_standalone_worthy:{i.email_type}")
    normal = final

    if not normal:
        return result

    # ── Step 5: Rate limit enforcement ──
    recent_count = _recent_send_count(db, shop, days=1)
    weekly_count = _recent_send_count(db, shop, days=7)

    if recent_count >= _MAX_PER_DAY:
        result["rate_limited"] += len(normal)
        for i in normal:
            _log_suppressed(db, i, "rate_limit_daily")
        return result

    if weekly_count >= _MAX_PER_WEEK:
        # Exception: CRITICAL priority (P0) can override weekly cap
        critical = [i for i in normal if i.priority == Priority.CRITICAL]
        non_critical = [i for i in normal if i.priority != Priority.CRITICAL]
        for i in non_critical:
            result["rate_limited"] += 1
            _log_suppressed(db, i, "rate_limit_weekly")
        normal = critical
        if not normal:
            return result

    # ── Step 6: Conflict detection ──
    normal = _resolve_conflicts(normal)

    # ── Step 7: Priority resolution — pick winner ──
    normal.sort(key=lambda i: (i.priority, i.created_at))
    winner = normal[0]

    # Defer losers
    for i in normal[1:]:
        if i.mergeable and i.merge_section and winner.email_type == "weekly_digest":
            # Already merged in step 4b
            pass
        else:
            result["deferred"] += 1
            _log_suppressed(db, i, f"deferred_by:{winner.email_type}")

    # ── Step 8: Send the winner ──
    if _send_intent(db, winner):
        result["sent"] = 1
    else:
        result["suppressed"] += 1

    return result


def _build_merge_snippet(intent: EmailIntent) -> str:
    """Build a compact HTML snippet from a downgraded intent for digest merging."""
    type_labels = {
        "first_insight": "New Insight",
        "connection_issue": "Connection Alert",
        "reengagement": "Activity Update",
    }
    label = type_labels.get(intent.email_type, intent.email_type.replace("_", " ").title())
    # Strip down to just the core message
    return f"""
    <div style="margin:16px 0;padding:12px 16px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;font-size:13px;line-height:1.5">
        <strong style="color:#0c4a6e">{label}</strong>
        <p style="margin:4px 0 0;color:#1e293b">{intent.subject}</p>
    </div>
    """


def _resolve_conflicts(intents: list[EmailIntent]) -> list[EmailIntent]:
    """
    Remove conflicting intents that would send contradictory messages.

    Rules:
      - setup_incomplete + connection_issue → keep only setup_incomplete (same root cause)
      - revenue trigger + reengagement → keep only revenue trigger (conflicting signals)
      - multiple revenue triggers → keep highest priority one
    """
    types = {i.email_type for i in intents}

    drop = set()

    # Same root cause: setup stuck vs connection lost — keep the onboarding one
    if "setup_incomplete" in types and "connection_issue" in types:
        drop.add("connection_issue")

    if not drop:
        return intents

    kept = []
    for i in intents:
        if i.email_type in drop:
            log.info("email_orch: conflict resolved — dropped %s for %s", i.email_type, i.shop_domain)
        else:
            kept.append(i)
    return kept


def _is_merchant_paused(db: Session, shop: str) -> bool:
    """Check if merchant has paused all communications."""
    try:
        row = db.execute(
            text("SELECT email_paused FROM merchants WHERE shop_domain = :shop"),
            {"shop": shop},
        ).first()
        return bool(row and row[0]) if row else False
    except Exception as exc:
        # Fail-open — don't block sends on query error. Logged at WARNING
        # so the fail-open firing is observable (pre-2026-04-23 this path
        # was silent and an outage could have disabled the pause-respect
        # gate without any signal).
        log.warning(
            "email_orch: _is_merchant_paused fail-open (shop=%s): %s",
            shop, type(exc).__name__,
        )
        return False


# ---------------------------------------------------------------------------
# Send mechanics
# ---------------------------------------------------------------------------

def _send_intent(db: Session, intent: EmailIntent) -> bool:
    """Actually send an email intent via Resend. Returns True on success.

    HARD BLOCKS (email is NOT sent):
      - Governance violation (sender mismatch, brand violations)
      - Email budget exhausted
      - Atomic send guard fails (parallel execution)
      - Empty/missing HTML or recipient

    Fail-safe: on any governance check failure (import error, etc),
    the email is SKIPPED and logged. We never send an unvalidated email.
    """
    # ── Pre-flight: reject obviously broken intents ──
    if not intent.to_email or not intent.html:
        _log_suppressed(db, intent, "missing_recipient_or_html")
        return False

    # ── DNS deliverability pre-gate ──
    # When Resend has the @hedgesparkhq.com domain in `failed` state,
    # `send_email()` short-circuits with DNS_SUPPRESSED and returns None —
    # which the downstream block then logs as generic `send_failed` AND
    # heal-detection: email_send_failed is a per-attempt event log; retry path lives in orchestrator state machine, not alert lifecycle
    # fires a `write_alert(email_send_failed)` per intent. During a multi-
    # day DNS outage (2026-04-12 → 21) that pattern would dump one alert
    # per send attempt into ops_alerts, crowding out real signal. Catch
    # it here instead: single `dns_gate_closed` suppressed row, no
    # per-intent alert — the hourly `email_dns_status_check` already
    # fires one Telegram alert on the flip, which is the true signal.
    # Fail-open on any unexpected error (never silence real sends).
    try:
        from app.services.email_deliverability import (
            is_domain_verified,
            uses_org_domain,
        )
        if uses_org_domain(intent.from_address) and not is_domain_verified():
            log.warning(
                "email_orch: DNS_GATE_CLOSED shop=%s type=%s — suppressing "
                "(Resend domain verification failed; see /ops/email-health)",
                intent.shop_domain, intent.email_type,
            )
            _log_suppressed(db, intent, "dns_gate_closed")
            return False
    except Exception as exc:
        log.warning("email_orch: dns pre-gate error (fail-open): %s", exc)

    # ── Governance validation — ALL violations are hard blocks ──
    governance_hash = ""
    try:
        from app.services.email_governance import validate_intent as _gov_validate
        gov = _gov_validate(intent)
        governance_hash = gov.content_hash
        if not gov.passed:
            log.error(
                "email_orch: GOVERNANCE BLOCKED %s for %s: %s",
                intent.email_type, intent.shop_domain, gov.violations,
            )
            _log_suppressed(db, intent, f"governance:{gov.violations[0]}")
            return False
    except Exception as exc:
        # Governance check FAILURE = fail-closed. Do not send unvalidated email.
        log.error("email_orch: governance check failed, blocking send: %s", exc)
        _log_suppressed(db, intent, "governance_check_error")
        return False

    # ── Budget check ──
    try:
        from app.core.resend_usage import get_resend_usage, RESEND_MONTHLY_LIMIT
        usage = get_resend_usage(db)
        if usage["sent"] >= RESEND_MONTHLY_LIMIT:
            _log_suppressed(db, intent, "email_budget_exhausted")
            return False
    except Exception as exc:
        # FAIL-CLOSED: budget infrastructure failure must NOT trigger
        # uncapped email sending. The Resend monthly limit is real money;
        # an over-spend incident is unrecoverable, while a transient
        # budget-check outage is a 5-minute fix. Pre-2026-05-08 this
        # logged "fail-open" and proceeded — the exact class of bug §2.9
        # (every LLM/email cap is a north-star invariant) forbids.
        log.error(
            "email_orchestrator: budget check failed — REJECTING send "
            "(fail-CLOSED for cost protection): %s", exc,
        )
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="email_orchestrator",
                alert_type="budget_check_failed_fail_closed",
                summary=(
                    "Resend budget check failed; email sends paused on "
                    "fail-closed semantics. Investigate & restore."
                ),
                shop_domain=intent.shop_domain,
                detail={"error": str(exc)[:300]},
            )
        except Exception as alert_exc:
            # Best-effort alert path; the load-bearing fix is the
            # `return False` below — refusing to send is the primary
            # behavior. Don't let an alerting failure block that.
            log.debug(
                "email_orchestrator: budget-failure alert write failed "
                "(non-fatal): %s", alert_exc,
            )
        _log_suppressed(db, intent, "email_budget_check_unavailable")
        return False

    # ── Atomic send guard — prevent duplicate sends on parallel execution ──
    if not _claim_send_slot(intent.shop_domain, intent.email_type):
        _log_suppressed(db, intent, "duplicate_send_guard")
        return False

    from app.core.email import send_email

    resend_id = send_email(
        to=intent.to_email,
        subject=intent.subject,
        html=intent.html,
        text=intent.plain_text,
        from_address=intent.from_address,
    )

    if resend_id:
        _log_sent(db, intent, resend_id)

        # Record in performance memory
        try:
            from app.services.email_performance import record_email_event
            record_email_event(db, intent.shop_domain, intent.email_type, "sent")
        except Exception as exc:
            log.warning("email_orchestrator: performance record failed: %s", exc)

        # Update Redis rate-limit counter
        _increment_send_counter(intent.shop_domain)

        log.info(
            "email_orch: SENT id=%s shop=%s type=%s priority=%s resend=%s content_hash=%s",
            intent.intent_id, intent.shop_domain, intent.email_type,
            intent.priority.name, resend_id, governance_hash,
        )
        # heal-detection: success → resolve any prior email_send_failed
        # alert for this email_type. Born 2026-05-07.
        try:
            from app.services.alerting import auto_resolve_alerts
            auto_resolve_alerts(
                db,
                source=f"email_orchestrator:{intent.email_type}",
                alert_type="email_send_failed",
            )
        except Exception as exc:
            log.debug("email_orch: heal-detection failed: %s", exc)
        return True

    _log_suppressed(db, intent, "send_failed")

    # Feed delivery failures into the self-healing pipeline.
    # send_email returning empty resend_id means Resend rejected the
    # request — broken template, bad sender, network failure. The
    # generic Rule 7 catch-all triages recurring instances.
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            source=f"email_orchestrator:{intent.email_type}",
            alert_type="email_send_failed",
            severity="warning",
            shop_domain=intent.shop_domain,
            summary=(
                f"Resend rejected email type={intent.email_type} "
                f"to={intent.to_email} (producer={intent.producer})"
            ),
            detail={
                "intent_id": intent.intent_id,
                "email_type": intent.email_type,
                "to_email": intent.to_email,
                "producer": intent.producer,
            },
        )
    except Exception as exc:
        log.debug("email_orch: write_alert failed (non-fatal): %s", exc)

    return False


# ---------------------------------------------------------------------------
# Atomic send guard — prevents duplicate sends on parallel execution
# ---------------------------------------------------------------------------

_SEND_GUARD_TTL = 300  # 5 minutes — enough to cover one send cycle


def _claim_send_slot(shop: str, email_type: str) -> bool:
    """
    Atomic SET NX guard. Returns True if this is the first attempt to send
    this email_type to this shop in the current window. Returns False if
    another process already claimed it.

    Fail-open on Redis unavailability — better to risk a duplicate than
    to silence all emails.
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("email_orchestrator.dupe_guard")
            return True  # Redis down — fail-open
        key = f"{_REDIS_PREFIX}guard:{shop}:{email_type}"
        result = rc.set(key, "1", nx=True, ex=_SEND_GUARD_TTL)
        if not result:
            log.warning(
                "email_orch: DUPLICATE GUARD blocked %s for %s (parallel execution detected)",
                email_type, shop,
            )
        return bool(result)
    except Exception as exc:
        # Fail-open so Redis outage doesn't silence all emails. Logged
        # at WARNING — the dupe-guard is a soft guarantee that depends
        # on Redis; when it fails we want to know.
        log.warning(
            "email_orch: _claim_send_slot fail-open (shop=%s type=%s): %s",
            shop, email_type, type(exc).__name__,
        )
        return True


# ---------------------------------------------------------------------------
# Rate-limit tracking (Redis + DB fallback)
# ---------------------------------------------------------------------------

def _recent_send_count(db: Session, shop: str, days: int) -> int:
    """Count emails sent to this merchant in the last N days."""
    # Try Redis first (fast path)
    count = _redis_send_count(shop, days)
    if count is not None:
        return count

    # DB fallback
    try:
        row = db.execute(
            text("""
                SELECT COUNT(*)::int FROM merchant_emails
                WHERE shop_domain = :shop
                  AND status = 'sent'
                  AND created_at >= NOW() - make_interval(days => :days)
            """),
            {"shop": shop, "days": days},
        ).scalar()
        return int(row or 0)
    except Exception as exc:
        # Fail-safe 0 so one DB error doesn't block email sends, but log
        # so we notice if rate-limit counting is silently broken at scale.
        log.warning(
            "email_orch: _recent_send_count fail-safe (shop=%s days=%d): %s",
            shop, days, type(exc).__name__,
        )
        return 0


def _redis_send_count(shop: str, days: int) -> int | None:
    """Get send count from Redis. Returns None on miss/error."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if not rc:
            return None
        key = f"{_REDIS_PREFIX}sends:{shop}:{days}d"
        val = rc.get(key)
        return int(val) if val is not None else None
    except Exception as exc:
        log.warning(
            "email_orchestrator: send counter read failed shop=%s days=%d (%s): %s",
            shop, days, type(exc).__name__, str(exc)[:200],
        )
        return None


def _increment_send_counter(shop: str) -> None:
    """Increment daily and weekly send counters in Redis."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if not rc:
            return

        pipe = rc.pipeline(transaction=False)

        # Daily counter (24h TTL)
        day_key = f"{_REDIS_PREFIX}sends:{shop}:1d"
        pipe.incr(day_key)
        pipe.expire(day_key, 86400)

        # Weekly counter (7d TTL)
        week_key = f"{_REDIS_PREFIX}sends:{shop}:7d"
        pipe.incr(week_key)
        pipe.expire(week_key, 604800)

        pipe.execute()
    except Exception as exc:
        log.warning("email_orchestrator: send counter increment failed: %s", exc)


# ---------------------------------------------------------------------------
# Suppression checks
# ---------------------------------------------------------------------------

def _is_suppressed(db: Session, shop: str) -> bool:
    """Check if merchant email is suppressed (bounce/complaint)."""
    try:
        from app.services.email_journey import get_journey
        journey = get_journey(db, shop)
        if journey and journey.email_suppressed:
            return True
    except Exception as exc:
        log.warning("email_orchestrator: suppression check failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _log_sent(db: Session, intent: EmailIntent, resend_id: str) -> None:
    """Record a successful send in the audit table."""
    # session-rollback: ok — caller chain is all worker-loop entry points (agent_worker._run_email_orchestrator_flush, merchant_digest, silence_detector, merchant_brain, onboarding_health). Worker pattern: `db = SessionLocal()` → `try: ... db.commit() except: db.rollback() finally: db.close()`. Audit-log is best-effort side-write; failure leaves PendingRollbackError but outer worker `except Exception` catches the next failure and rolls back. Session owned by worker cycle, never crosses requests.
    try:
        from app.models.merchant_email import MerchantEmail
        entry = MerchantEmail(
            shop_domain=intent.shop_domain,
            email_type=intent.email_type,
            to_email=intent.to_email,
            subject=intent.subject,
            status="sent",
            resend_id=resend_id if isinstance(resend_id, str) else None,
        )
        db.add(entry)
        db.flush()
    except Exception as exc:
        log.warning("email_orch: audit log failed intent=%s: %s", intent.intent_id, exc)


def _log_suppressed(db: Session, intent: EmailIntent, reason: str) -> None:
    """Record a suppressed intent in the audit table."""
    # session-rollback: ok — same caller fan-in as _log_sent: worker-loop entry points (agent_worker._run_email_orchestrator_flush etc). Identical session lifecycle: own SessionLocal per cycle, rollback-on-exception, close-in-finally. No cross-cycle bleed.
    try:
        from app.models.merchant_email import MerchantEmail
        entry = MerchantEmail(
            shop_domain=intent.shop_domain,
            email_type=intent.email_type,
            to_email=intent.to_email,
            subject=intent.subject,
            status="suppressed",
            suppressed_by=f"orchestrator:{reason}",
        )
        db.add(entry)
        db.flush()
    except Exception as exc:
        log.warning("email_orch: suppressed log failed intent=%s: %s", intent.intent_id, exc)


# ---------------------------------------------------------------------------
# Context memory — engagement-aware decisions
# ---------------------------------------------------------------------------

def get_merchant_email_context(db: Session, shop: str) -> dict:
    """
    Build engagement context for a merchant.

    Returns:
        {
            "last_sent_at":       datetime | None,
            "last_opened_at":     datetime | None,
            "emails_7d":          int,
            "emails_30d":         int,
            "engagement_level":   str,  # active | passive | dormant | new
            "is_suppressed":      bool,
        }
    """
    try:
        row = db.execute(
            text("""
                SELECT
                    MAX(CASE WHEN status = 'sent' THEN created_at END) AS last_sent,
                    COUNT(CASE WHEN status = 'sent'
                               AND created_at >= NOW() - INTERVAL '7 days' THEN 1 END) AS sent_7d,
                    COUNT(CASE WHEN status = 'sent'
                               AND created_at >= NOW() - INTERVAL '30 days' THEN 1 END) AS sent_30d
                FROM merchant_emails
                WHERE shop_domain = :shop
            """),
            {"shop": shop},
        ).first()

        last_sent = row[0] if row else None
        sent_7d = int(row[1] or 0) if row else 0
        sent_30d = int(row[2] or 0) if row else 0

    except Exception as exc:
        # Fail-safe empty context — the caller uses this for display /
        # governance telemetry, not for hard gating. Log so silent
        # degradation is observable.
        log.warning(
            "email_orch: get_merchant_email_context fail-safe (shop=%s): %s",
            shop, type(exc).__name__,
        )
        last_sent = None
        sent_7d = 0
        sent_30d = 0

    # Get last opened from email_performance
    last_opened = None
    try:
        opened_row = db.execute(
            text("""
                SELECT MAX(last_opened_at) FROM merchant_email_stats
                WHERE shop_domain = :shop
            """),
            {"shop": shop},
        ).scalar()
        last_opened = opened_row
    except Exception as exc:
        log.warning("email_orchestrator: last_opened query failed: %s", exc)

    # Classify engagement
    if sent_30d == 0:
        engagement = "new"
    elif last_opened and last_sent:
        days_since_open = (datetime.now(timezone.utc).replace(tzinfo=None) - last_opened).days
        if days_since_open <= 7:
            engagement = "active"
        elif days_since_open <= 30:
            engagement = "passive"
        else:
            engagement = "dormant"
    elif sent_30d >= 3:
        engagement = "dormant"  # sent but never opened
    else:
        engagement = "passive"

    return {
        "last_sent_at": last_sent,
        "last_opened_at": last_opened,
        "emails_7d": sent_7d,
        "emails_30d": sent_30d,
        "engagement_level": engagement,
        "is_suppressed": _is_suppressed(db, shop),
    }
