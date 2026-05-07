"""merchant_brain.py — Brain Vero v0.1: the conductor.

# operator-filter: ok — tick_all_active_merchants intentionally walks
# the full Merchant population to coordinate per-shop decisions for
# every active install (operator/dev shops included for now; v0.2 may
# narrow to billing_active=true once paying merchants land).

Founder direttiva 2026-05-07: "vince se Brain fa davvero il Brain e fa
lavorare mani, cuore, gambe". The pre-2026-05-07 brain (bugfix_pipeline
+ invariant_monitor + agent_worker) was an immune system that fought
itself: 1496 candidates → 2 applied (0.13%), 93% no_effect actions.

Brain Vero is the per-merchant coordination loop:

    SENSE        cross-subsystem signals (RAR, churn, events, orders,
                 last action, last chat, slo state for this shop's
                 hot routes)
        ↓
    SYNTHESIZE   1-paragraph narrative tying the signals together
                 ("merchant X has €Y at risk + Z stalled checkouts;
                  no action in 4 days; chatbot dark for 8 days")
        ↓
    DECIDE       pick one action (or no_action) from a tiered menu;
                 deterministic rule-table for v0.1, LLM-driven later
        ↓
    COORDINATE   dispatch to existing limbs (email_orchestrator,
                 orchestrator, nudge_composer, merchant_chatbot)
        ↓
    LEARN        record decision + expected metric + window;
                 evaluate_pending_outcomes() closes the loop after
                 outcome_window_hours

The merchant_brain owns its own table (brain_decisions). Frequency:
1 tick per active merchant per agent_worker cycle when MERCHANT_BRAIN_
ENABLED=1 (default off; opt-in per project_pipeline_closed_until_
merchants un-park ceremony).

This is v0.1 — minimal viable conductor. v0.2 adds LLM-driven
synthesis. v0.3 adds pattern memory (learn across merchants).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_brain_enabled() -> bool:
    """Default OFF. Founder flips MERCHANT_BRAIN_ENABLED=1 in the
    un-park ceremony alongside the brain enrichers (per pipeline_state
    doctrine §25.6). Off → tick is a no-op; the brain decisions table
    stays empty until the ceremony fires."""
    return os.getenv("MERCHANT_BRAIN_ENABLED", "0").strip().lower() in (
        "1", "true", "yes",
    )


# ---------------------------------------------------------------------------
# State dataclass — what the synthesizer sees
# ---------------------------------------------------------------------------

@dataclass
class MerchantState:
    shop_domain: str
    rars_total_eur: float
    churn_risk_level: str   # "critical"/"high"/"medium"/"low"/"unknown"
    recent_orders_7d: int
    recent_events_24h: int
    hours_since_install: float
    last_action_age_hours: float | None
    last_chat_age_hours: float | None
    last_brain_decision_age_hours: float | None
    has_email_in_queue: bool


# ---------------------------------------------------------------------------
# SENSE — gather cross-subsystem signals for one merchant
# ---------------------------------------------------------------------------

def _sense(db: Session, shop_domain: str) -> MerchantState:
    """Read the current merchant state. Cheap queries — index-backed.
    Order: most-cached signals first (RAR via redis), then DB hits."""
    # RAR (Redis-cached, last value)
    rars_total = 0.0
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(f"hs:rars_history:v1:{shop_domain}")
            if raw:
                history = json.loads(raw)
                if history:
                    rars_total = float(history[-1].get("total_at_risk_eur") or 0)
    except Exception as exc:
        # Promoted from log.debug 2026-05-07 per audit_exception_debug:
        # Redis read failure is fire-and-forget for a single signal but
        # systemic if recurring; warn-level lets ops trace silent drift.
        log.warning("brain.sense: rars read failed %s: %s", shop_domain, exc)

    # Churn risk (compute report — cached by churn predictor) and lift
    # this shop's risk_level from the merchants[] array. Each query is
    # wrapped in a SAVEPOINT so a single failure doesn't abort the
    # outer transaction (would block the subsequent INSERT into
    # brain_decisions — observed live 2026-05-07 v0.1 smoke).
    churn_level = "unknown"
    try:
        with db.begin_nested():
            from app.services.merchant_churn_predictor import compute_churn_report
            report = compute_churn_report(db)
            for m in (report.get("merchants") or []):
                if m.get("shop_domain") == shop_domain:
                    churn_level = (m.get("risk_level") or "unknown").lower()
                    break
    except Exception as exc:
        # Promoted from log.debug 2026-05-07 per audit_exception_debug:
        # churn predictor is the most expensive sense signal; failure
        # means we ship a decision with churn_level="unknown" which
        # silently downgrades the rule-fire path. Warn-level lets ops
        # see if churn predictor is systemically failing.
        log.warning("brain.sense: churn lookup failed %s: %s",
                    shop_domain, exc)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_7d = now - timedelta(days=7)
    cutoff_24h = now - timedelta(hours=24)

    # Each DB read wrapped in a SAVEPOINT so a single column/table
    # mismatch doesn't abort the outer txn (would break the brain_
    # decisions INSERT downstream — observed live 2026-05-07 v0.1).
    def _safe_scalar(sql: str, params: dict) -> Any | None:
        try:
            with db.begin_nested():
                return db.execute(text(sql), params).scalar()
        except Exception:
            return None

    orders_7d = int(_safe_scalar(
        "SELECT COUNT(*) FROM shop_orders WHERE shop_domain=:s "
        "AND created_at >= :c",
        {"s": shop_domain, "c": cutoff_7d},
    ) or 0)
    # events table uses `timestamp` (DateTime) — not `created_at`.
    events_24h = int(_safe_scalar(
        "SELECT COUNT(*) FROM events WHERE shop_domain=:s "
        "AND timestamp >= :c",
        {"s": shop_domain, "c": cutoff_24h},
    ) or 0)
    hours_since_install = float(_safe_scalar(
        "SELECT EXTRACT(EPOCH FROM (NOW() - installed_at))/3600 "
        "FROM merchants WHERE shop_domain=:s",
        {"s": shop_domain},
    ) or 0)
    last_action_age_raw = _safe_scalar(
        "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))/3600 "
        "FROM action_tasks WHERE shop_domain=:s",
        {"s": shop_domain},
    )
    last_action_age = float(last_action_age_raw) if last_action_age_raw is not None else None
    last_brain_age_raw = _safe_scalar(
        "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(decision_at)))/3600 "
        "FROM brain_decisions WHERE shop_domain=:s",
        {"s": shop_domain},
    )
    last_brain_age = float(last_brain_age_raw) if last_brain_age_raw is not None else None

    # Email in queue (best-effort — email_orchestrator buffer is in-memory)
    try:
        from app.services.email_orchestrator import _pending_intents
        has_email_in_queue = any(
            intent.shop_domain == shop_domain for intent in _pending_intents
        )
    except Exception:
        has_email_in_queue = False

    return MerchantState(
        shop_domain=shop_domain,
        rars_total_eur=rars_total,
        churn_risk_level=churn_level,
        recent_orders_7d=orders_7d,
        recent_events_24h=events_24h,
        hours_since_install=hours_since_install,
        last_action_age_hours=last_action_age,
        last_chat_age_hours=None,  # v0.2 wires chat history
        last_brain_decision_age_hours=last_brain_age,
        has_email_in_queue=has_email_in_queue,
    )


# ---------------------------------------------------------------------------
# SYNTHESIZE — narrative tying signals together
# ---------------------------------------------------------------------------

def _synthesize(state: MerchantState) -> str:
    """Deterministic narrative for v0.1. v0.2 will add LLM-driven
    cross-signal synthesis grounded on the same state dict."""
    parts = []
    if state.rars_total_eur > 0:
        parts.append(f"€{state.rars_total_eur:,.0f} at risk")
    if state.churn_risk_level in ("critical", "high"):
        parts.append(f"{state.churn_risk_level} churn risk")
    if state.recent_orders_7d == 0 and state.hours_since_install > 168:
        parts.append("0 orders in 7d (installed >1w ago)")
    elif state.recent_orders_7d > 0:
        parts.append(f"{state.recent_orders_7d} orders/7d")
    if state.recent_events_24h == 0:
        parts.append("0 events/24h (tracker silent?)")
    if state.last_action_age_hours is not None:
        parts.append(
            f"last action {state.last_action_age_hours:.0f}h ago"
            if state.last_action_age_hours < 1000
            else "no recent action"
        )
    else:
        parts.append("no action ever")
    if not parts:
        return f"{state.shop_domain}: no signal worth narrative"
    return f"{state.shop_domain}: " + " · ".join(parts)


# ---------------------------------------------------------------------------
# DECIDE — pick one action (or no_action) from tiered menu
# ---------------------------------------------------------------------------

@dataclass
class BrainDecisionDraft:
    action_kind: str
    action_payload: dict
    rationale: str
    expected_outcome_metric: str
    outcome_window_hours: int
    baseline_value: float | None


def _decide(state: MerchantState) -> BrainDecisionDraft:
    """Rule-table for v0.1. Each rule names: action, rationale,
    expected metric, window. v0.2 plugs in LLM-driven decision with
    these rules as the safety floor."""
    # Cooldown: don't fire decisions more often than once per 6h per merchant
    if (state.last_brain_decision_age_hours is not None
            and state.last_brain_decision_age_hours < 6):
        return BrainDecisionDraft(
            action_kind="no_action_cooldown",
            action_payload={},
            rationale=(
                f"last decision {state.last_brain_decision_age_hours:.1f}h "
                "ago; cooldown 6h"
            ),
            expected_outcome_metric="cooldown_pending",
            outcome_window_hours=24,
            baseline_value=None,
        )

    # Rule 1: critical churn risk → retention outreach
    if state.churn_risk_level == "critical":
        return BrainDecisionDraft(
            action_kind="retention_outreach_email",
            action_payload={"urgency": "high"},
            rationale=(
                f"churn={state.churn_risk_level}, "
                f"orders_7d={state.recent_orders_7d}"
            ),
            expected_outcome_metric="merchant_re_engaged_7d",
            outcome_window_hours=168,
            baseline_value=float(state.recent_orders_7d),
        )

    # Rule 2: high RAR + stale → recovery digest
    if state.rars_total_eur > 1000 and (
        state.last_action_age_hours is None
        or state.last_action_age_hours > 72
    ):
        return BrainDecisionDraft(
            action_kind="recovery_digest",
            action_payload={"rars_focus_eur": state.rars_total_eur},
            rationale=(
                f"rars={state.rars_total_eur:.0f}, "
                f"last_action={state.last_action_age_hours}h"
            ),
            expected_outcome_metric="rars_delta_7d",
            outcome_window_hours=168,
            baseline_value=state.rars_total_eur,
        )

    # Rule 3: tracker silent post-install → re-engagement check
    if (state.recent_events_24h == 0 and state.hours_since_install > 24
            and state.recent_orders_7d == 0):
        return BrainDecisionDraft(
            action_kind="re_engagement_check",
            action_payload={
                "hours_since_install": state.hours_since_install,
            },
            rationale="0 events/24h post-install — tracker dark",
            expected_outcome_metric="events_24h_resumed",
            outcome_window_hours=24,
            baseline_value=0.0,
        )

    # Rule 4: high RAR but no churn signal → proactive nudge composer
    if state.rars_total_eur > 500 and state.recent_events_24h > 50:
        return BrainDecisionDraft(
            action_kind="proactive_nudge_compose",
            action_payload={"rars": state.rars_total_eur},
            rationale=(
                f"rars={state.rars_total_eur:.0f} + active "
                f"({state.recent_events_24h} events/24h)"
            ),
            expected_outcome_metric="cvr_delta_7d",
            outcome_window_hours=168,
            baseline_value=None,
        )

    # Default: no action this tick
    return BrainDecisionDraft(
        action_kind="no_action_no_signal",
        action_payload={},
        rationale="no rule fired — merchant healthy or insufficient data",
        expected_outcome_metric="none",
        outcome_window_hours=24,
        baseline_value=None,
    )


# ---------------------------------------------------------------------------
# COORDINATE — dispatch to existing limbs
# ---------------------------------------------------------------------------

def _coordinate(
    db: Session, state: MerchantState, decision: BrainDecisionDraft
) -> tuple[str | None, dict]:
    """Dispatch to the limb. Returns (limb_name, response_dict). v0.1
    is conservative: every limb call is wrapped in try/except + the
    brain records the response so failures are observable in
    brain_decisions, not as silent ops_alerts.

    LIMBS USED:
      - email_orchestrator.submit_intent → retention/recovery/re-engagement
      - nudge_composer.compose (deferred — v0.2 wiring)
      - orchestrator.execute_action (deferred — v0.2 wiring)
    """
    if decision.action_kind in ("no_action_cooldown", "no_action_no_signal"):
        return None, {}

    # v0.1 RECORDS decisions; LIMB DISPATCH deferred to v0.2.
    #
    # Rationale: shipping the SENSE→SYNTHESIZE→DECIDE→LEARN spine first
    # gives us a measurable substrate. Limb wiring (email_orchestrator
    # for retention_outreach, nudge_composer, orchestrator action_tasks)
    # requires per-action template registration + per-action holdout
    # contract. v0.2 sprint scope: register email templates, wire
    # nudge_composer, plumb action_tasks dispatch.
    #
    # The brain_decisions ledger captures every decision with rationale
    # so we can audit "would this have actioned correctly?" before
    # turning on dispatch — same pattern as the bugfix_pipeline's
    # adversarial-review-before-apply gate.
    return None, {"deferred_to": "v0.2_limb_wiring"}


# ---------------------------------------------------------------------------
# LEARN — record decision + (separate cycle) evaluate pending outcomes
# ---------------------------------------------------------------------------

def _record(
    db: Session,
    state: MerchantState,
    synthesis: str,
    decision: BrainDecisionDraft,
    limb: str | None,
    limb_response: dict,
) -> int:
    """Persist a brain_decisions row. Returns the new id."""
    from app.models.brain_decision import BrainDecision
    sense_dict = {
        "rars_total_eur": state.rars_total_eur,
        "churn_risk_level": state.churn_risk_level,
        "recent_orders_7d": state.recent_orders_7d,
        "recent_events_24h": state.recent_events_24h,
        "hours_since_install": state.hours_since_install,
        "last_action_age_hours": state.last_action_age_hours,
        "last_brain_decision_age_hours": state.last_brain_decision_age_hours,
    }
    row = BrainDecision(
        shop_domain=state.shop_domain,
        sense_snapshot=sense_dict,
        synthesis=synthesis,
        action_kind=decision.action_kind,
        action_payload=decision.action_payload,
        rationale=decision.rationale,
        limb_dispatched=limb,
        limb_response=limb_response,
        expected_outcome_metric=decision.expected_outcome_metric,
        outcome_window_hours=decision.outcome_window_hours,
        baseline_value=decision.baseline_value,
    )
    db.add(row)
    db.flush()
    return row.id


# ---------------------------------------------------------------------------
# Public API: tick + evaluate_pending_outcomes
# ---------------------------------------------------------------------------

def tick(db: Session, shop_domain: str) -> dict:
    """One full SENSE→SYNTHESIZE→DECIDE→COORDINATE→LEARN cycle for
    one merchant. Returns a summary dict for diagnostics."""
    if not is_brain_enabled():
        return {"shop": shop_domain, "skipped": "brain_disabled"}

    state = _sense(db, shop_domain)
    synthesis = _synthesize(state)
    decision = _decide(state)
    limb, limb_response = _coordinate(db, state, decision)
    decision_id = _record(db, state, synthesis, decision, limb, limb_response)
    db.commit()
    return {
        "shop": shop_domain,
        "decision_id": decision_id,
        "action_kind": decision.action_kind,
        "synthesis": synthesis,
        "limb": limb,
    }


def tick_all_active_merchants(db: Session, max_shops: int = 100) -> dict:
    """Run a brain tick across every active merchant. Cap at
    max_shops per cycle to bound work. Returns aggregate summary."""
    if not is_brain_enabled():
        return {"skipped": "brain_disabled", "ticks": 0}

    from app.models.merchant import Merchant
    shops = [
        m.shop_domain for m in db.query(Merchant).filter(
            Merchant.install_status == "active",
        ).limit(max_shops).all()
    ]
    actions: dict[str, int] = {}
    for shop in shops:
        try:
            res = tick(db, shop)
            actions[res.get("action_kind", "unknown")] = (
                actions.get(res.get("action_kind", "unknown"), 0) + 1
            )
        except Exception as exc:
            log.warning("brain.tick failed for %s: %s", shop, exc)
            db.rollback()
    return {"ticks": len(shops), "by_action": actions}


def evaluate_pending_outcomes(db: Session, max_evaluate: int = 50) -> dict:
    """Close the LEARN loop: for decisions whose outcome window has
    elapsed, measure the metric delta and stamp outcome_status.

    Called from agent_worker on its cycle. Bounded at max_evaluate
    rows per call so a backlog doesn't blow the cycle budget."""
    if not is_brain_enabled():
        return {"skipped": "brain_disabled", "evaluated": 0}

    from app.models.brain_decision import BrainDecision
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pending = db.query(BrainDecision).filter(
        BrainDecision.outcome_status.is_(None),
        # outcome_window has elapsed
        BrainDecision.decision_at <= (
            now - timedelta(hours=1)  # at least 1h old to allow signals to land
        ),
    ).limit(max_evaluate).all()

    evaluated = 0
    for d in pending:
        window_elapsed = (
            now - d.decision_at
        ).total_seconds() / 3600.0 >= (d.outcome_window_hours or 24)
        if not window_elapsed:
            continue

        status = _measure(db, d)
        d.outcome_status = status
        d.outcome_evaluated_at = now
        evaluated += 1
    if evaluated > 0:
        db.commit()
    return {"evaluated": evaluated}


def _measure(db: Session, decision) -> str:
    """Return outcome_status for this decision. Conservative —
    'evaluation_failed' on any introspection error so we don't
    inflate effective counts."""
    metric = decision.expected_outcome_metric or ""
    try:
        if metric == "cooldown_pending":
            return "neutral"
        if metric == "events_24h_resumed":
            # events table column is `timestamp` (not `created_at`)
            n = int(db.execute(
                text("SELECT COUNT(*) FROM events WHERE shop_domain=:s "
                     "AND timestamp >= :c"),
                {"s": decision.shop_domain,
                 "c": decision.decision_at},
            ).scalar() or 0)
            decision.measured_value = float(n)
            return "effective" if n > 0 else "ineffective"
        if metric == "rars_delta_7d":
            # baseline is decision.baseline_value; compare to current RAR
            from app.core.redis_client import _client
            rc = _client()
            current = 0.0
            if rc is not None:
                raw = rc.get(f"hs:rars_history:v1:{decision.shop_domain}")
                if raw:
                    history = json.loads(raw)
                    if history:
                        current = float(history[-1].get(
                            "total_at_risk_eur") or 0)
            decision.measured_value = current
            base = decision.baseline_value or 0
            if base <= 0:
                return "neutral"
            delta_pct = ((current - base) / base) * 100
            return "effective" if delta_pct < -5 else (
                "ineffective" if delta_pct > 5 else "neutral")
        if metric == "merchant_re_engaged_7d":
            # baseline = orders_7d at decision; compare to current
            n = int(db.execute(
                text("SELECT COUNT(*) FROM shop_orders WHERE shop_domain=:s "
                     "AND created_at >= NOW() - INTERVAL '7 days'"),
                {"s": decision.shop_domain},
            ).scalar() or 0)
            decision.measured_value = float(n)
            base = decision.baseline_value or 0
            return "effective" if n > base else (
                "ineffective" if n < base else "neutral")
        # Unknown / not-yet-implemented metric
        return "neutral"
    except Exception as exc:
        log.warning("brain.measure failed decision=%s: %s", decision.id, exc)
        return "evaluation_failed"
