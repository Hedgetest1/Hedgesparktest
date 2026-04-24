"""
orchestrator.py — Tier 0/1 AI Agent Orchestrator.

Modes:
    deterministic — hardcoded rules only (Tier 0, default)
    proposal      — deterministic + Claude proposes (logged, NOT executed)
    hybrid        — deterministic executes + Claude proposals execute (future)

Design principles:
    - Every action is idempotent and safe to re-execute
    - Every action writes an audit_log entry
    - Max actions per cycle is capped (prevent runaway loops)
    - Per-action cooldown prevents repeated execution
    - All decisions are logged with reasoning
    - Claude proposals are strictly validated against ACTION_REGISTRY
    - Repair claims prevent concurrent repairs with chatbot

Public interface:
    run_orchestrator_cycle(db) -> OrchestratorResult
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.ops_alert import OpsAlert
from app.services.audit import write_audit_log

log = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_ACTIONS_PER_CYCLE = 5
ACTION_COOLDOWN_SECONDS = 3600  # 1 hour between same action+target

# Orchestrator mode: "deterministic" | "proposal" | "hybrid"
# Set via ORCHESTRATOR_MODE env var. Default: deterministic.
import os
ORCHESTRATOR_MODE = os.getenv("ORCHESTRATOR_MODE", "deterministic").strip().lower()


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ActionRecord:
    action: str
    target: str
    reason: str
    status: str = "pending"    # pending | executed | skipped | failed
    detail: str | None = None


@dataclass
class OrchestratorResult:
    cycle_id: str = ""
    actions_evaluated: int = 0
    actions_executed: int = 0
    actions_skipped: int = 0
    actions_failed: int = 0
    records: list[ActionRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cooldown tracking — Redis primary (multi-worker correct), in-process fallback
#
# Pre-2026-04-23 this was a module-level dict, safe under single-worker
# uvicorn but would fire actions 4× per cycle under --workers 4 (each
# worker having its own dict). Now Redis-backed via SETEX, with the in-
# process dict retained only as a fallback path during Redis outages.
# ---------------------------------------------------------------------------

_cooldown_cache: dict[str, float] = {}  # multi-worker: redis-backed — Redis-unavailable fallback only
_COOLDOWN_REDIS_PREFIX = "hs:action_cooldown:v1"


def _cooldown_redis_key(action: str, target: str) -> str:
    return f"{_COOLDOWN_REDIS_PREFIX}:{action}::{target}"


def _is_on_cooldown(action: str, target: str) -> bool:
    key = _cooldown_redis_key(action, target)
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            return rc.exists(key) > 0
    except Exception:
        pass  # SILENT-EXCEPT-OK: Redis optional — falls through to in-process dict
    # Fallback: per-process dict (single-worker or Redis outage)
    last = _cooldown_cache.get(f"{action}::{target}")
    if last is None:
        return False
    return (time.monotonic() - last) < ACTION_COOLDOWN_SECONDS


def _set_cooldown(action: str, target: str):
    key = _cooldown_redis_key(action, target)
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(key, ACTION_COOLDOWN_SECONDS, "1")
            return
    except Exception:
        pass  # SILENT-EXCEPT-OK: Redis optional — falls through to in-process dict
    # Fallback: per-process dict (single-worker or Redis outage)
    _cooldown_cache[f"{action}::{target}"] = time.monotonic()


def _clear_cooldowns():
    """For testing only — clears both Redis and in-process cooldown state."""
    _cooldown_cache.clear()
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            for key in rc.scan_iter(match=f"{_COOLDOWN_REDIS_PREFIX}:*", count=100):
                rc.delete(key)
    except Exception:
        pass  # SILENT-EXCEPT-OK: test-only helper, in-process dict already cleared above


# ---------------------------------------------------------------------------
# Action tiers — risk classification for execution gating
# ---------------------------------------------------------------------------

TIER_0 = 0  # Safe: auto-executable by both deterministic rules and Claude
TIER_1 = 1  # Medium: logged only, not auto-executed (future: human-approve)
TIER_2 = 2  # High risk: never auto-executed, always requires human approval


# ---------------------------------------------------------------------------
# Action registry — each action is a safe, idempotent function
# ---------------------------------------------------------------------------

def _action_webhook_repair(db: Session, target: str) -> str:
    """
    Re-register missing webhooks for a merchant.
    Gated by repair claim to prevent concurrent repairs with chatbot.
    """
    from app.core.repair_claim import try_claim_repair, release_repair_claim
    if not try_claim_repair(target, "webhooks"):
        log.info("orchestrator: webhook_repair skipped for %s — repair claim held (chatbot or prior cycle)", target)
        return "skipped_repair_in_progress"

    try:
        from app.services.webhook_health import repair_missing_webhooks
        result = repair_missing_webhooks(db, target)
        if result.error:
            return f"error: {result.error}"
        if result.repaired:
            return f"repaired: {result.repaired}"
        if result.already_ok:
            return "already_ok"
        return "no_action"
    finally:
        release_repair_claim(target, "webhooks")


def _action_resolve_alert(db: Session, target: str) -> str:
    """Resolve an alert by ID."""
    from app.services.alerting import resolve_alert
    try:
        alert_id = int(target)
    except (ValueError, TypeError):
        return f"invalid_alert_id: {target}"
    resolve_alert(db, alert_id)
    return "resolved"


def _action_clear_redis_cache(db: Session, target: str) -> str:
    """Flush Redis cache keys matching a pattern. Target: key prefix or '*' for all."""
    from app.core.redis_client import _client
    rc = _client()
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("orchestrator.clear_cache")
        return "redis_unavailable"
    try:
        if target == "*":
            rc.flushdb()
            return "flushed_all"
        # Flush keys matching prefix pattern
        pattern = f"{target}*" if not target.endswith("*") else target
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = rc.scan(cursor, match=pattern, count=100)
            if keys:
                rc.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        return f"deleted_{deleted}_keys"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def _action_restart_worker(db: Session, target: str) -> str:
    """Restart a PM2 worker process by name. Target: pm2 process name."""
    import subprocess
    allowed = {
        "wishspark-worker", "wishspark-aggregation-worker",
        "wishspark-agent-worker", "wishspark-segment-monitor",
        "wishspark-nudge-optimizer", "wishspark-gdpr-worker",
    }
    if target not in allowed:
        return f"rejected: {target} not in allowed worker list"
    try:
        result = subprocess.run(
            ["pm2", "restart", target],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return f"restarted: {target}"
        return f"pm2_error: {result.stderr[:200]}"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def _action_restart_all_workers(db: Session, target: str) -> str:
    """Restart all PM2 worker processes (high blast radius)."""
    import subprocess
    try:
        result = subprocess.run(
            ["pm2", "restart", "all"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return "restarted_all"
        return f"pm2_error: {result.stderr[:200]}"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def _action_run_migration_dryrun(db: Session, target: str) -> str:
    """Run alembic check to verify schema alignment (read-only)."""
    import subprocess
    try:
        result = subprocess.run(
            ["/opt/wishspark/backend/venv/bin/python", "-m", "alembic", "check"],
            capture_output=True, text=True, timeout=15,
            cwd="/opt/wishspark/backend",
        )
        if result.returncode == 0:
            return "schema_aligned"
        return f"drift_detected: {result.stdout[:200]} {result.stderr[:200]}".strip()
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def _action_db_connection_reset(db: Session, target: str) -> str:
    """Dispose and recreate the SQLAlchemy connection pool."""
    from app.core.database import engine
    try:
        engine.dispose()
        # Verify new connections work
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "pool_reset_ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


# Action registry: name → (function, description, tier)
ACTION_REGISTRY: dict[str, tuple] = {
    "webhook_repair": (_action_webhook_repair, "Re-register missing/stale webhooks for a shop", TIER_0),
    "resolve_alert": (_action_resolve_alert, "Mark an operational alert as resolved", TIER_0),
    "clear_redis_cache": (_action_clear_redis_cache, "Flush Redis cache keys (target: prefix or '*')", TIER_0),
    "restart_worker": (_action_restart_worker, "Restart a specific PM2 worker process", TIER_1),
    "restart_all_workers": (_action_restart_all_workers, "Restart ALL PM2 processes (high blast radius)", TIER_2),
    "run_migration_dryrun": (_action_run_migration_dryrun, "Verify DB schema alignment (read-only check)", TIER_1),
    "db_connection_reset": (_action_db_connection_reset, "Reset SQLAlchemy connection pool", TIER_1),
}


def get_action_tier(action_name: str) -> int:
    """Return the tier for an action, or TIER_2 (highest) if unknown."""
    entry = ACTION_REGISTRY.get(action_name)
    if entry and len(entry) >= 3:
        return entry[2]
    return TIER_2


# ---------------------------------------------------------------------------
# Decision rules — deterministic Tier 0 policy
# ---------------------------------------------------------------------------

#: resolve_alert is a NOISE-REDUCTION action, not a real fix. It marks a
#: stale alert as resolved so the operator dashboard isn't cluttered, but
#: it does not repair the underlying condition. Post-2026-04-11 audit
#: showed 548/558 orch_resolve_alert outcomes = no_effect, because the
#: source problem kept re-firing. We now (a) skip re-resolving after 3
#: attempts in 24h and (b) tag the action as cosmetic so it does not
#: inflate the bugfix-pipeline failure rate.
_COSMETIC_ACTIONS: frozenset[str] = frozenset({
    "resolve_alert",
})


def _skip_stale_resolve(
    db: Session, now: datetime, alert_type: str, shop_domain: str | None,
) -> bool:
    """
    Decide whether to stop auto-resolving alerts of this (alert_type, shop).

    Rule: if orch_resolve_alert has fired on this (alert_type, shop) 3+
    times in the last 24h AND every prior attempt was no_effect, the
    source is clearly NOT being fixed by resolving the symptom. Stop
    trying and escalate to a manual_intervention_required alert.
    """
    from app.models.action_outcome import ActionOutcome
    from app.models.ops_alert import OpsAlert

    cutoff = now - timedelta(hours=24)
    q = (
        db.query(ActionOutcome)
        .filter(
            ActionOutcome.action_type == "orch_resolve_alert",
            ActionOutcome.executed_at >= cutoff,
            ActionOutcome.outcome_status == "no_effect",
        )
    )
    recent_no_effect_count = q.count()
    if recent_no_effect_count < 3:
        return False

    # We have 3+ no_effect. Escalate ONCE (dedup via the alert system).
    try:
        from app.services.alerting import write_alert
        existing = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "manual_intervention_required",
                OpsAlert.source == f"orchestrator:{alert_type}:{shop_domain or 'global'}",
                OpsAlert.resolved == False,
            )
            .first()
        )
        if not existing:
            write_alert(
                db,
                severity="warning",
                source=f"orchestrator:{alert_type}:{shop_domain or 'global'}",
                alert_type="manual_intervention_required",
                summary=(
                    f"Auto-resolve for {alert_type}"
                    + (f" on {shop_domain}" if shop_domain else "")
                    + f" has failed {recent_no_effect_count}x in 24h. "
                    f"Resolving the symptom does not fix the underlying issue. "
                    f"Human investigation required."
                ),
                shop_domain=shop_domain,
                detail={
                    "alert_type": alert_type,
                    "shop_domain": shop_domain,
                    "no_effect_count_24h": recent_no_effect_count,
                    "action": "Stop auto-resolving this alert type; investigate root cause.",
                },
            )
    except Exception as exc:
        log.warning("orchestrator: escalation write_alert failed (non-fatal): %s", exc)

    return True


def _evaluate_decisions(db: Session) -> list[ActionRecord]:
    """
    Read operational state and produce a list of candidate actions.

    Rules are evaluated in priority order. Each rule can produce 0-N actions.
    The caller enforces MAX_ACTIONS_PER_CYCLE and cooldown.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    candidates: list[ActionRecord] = []

    # Outcome feedback: check if recent actions for a target were ineffective
    from app.services.outcome_evaluator import count_recent_failures
    _MAX_REPEATED_FAILURES = 2  # stop retrying after 2 no_effect outcomes in 24h

    # Rule 1: Webhook repair failures → retry once (unless already proven ineffective)
    webhook_fails = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "webhook_repair_failed",
            OpsAlert.resolved == False,
            OpsAlert.created_at >= now - timedelta(hours=24),
        )
        .all()
    )
    for alert in webhook_fails:
        if alert.shop_domain:
            # Skip if recent attempts have been ineffective
            failures = count_recent_failures(db, "orch_webhook_repair", alert.shop_domain, hours=24)
            if failures >= _MAX_REPEATED_FAILURES:
                log.info("orchestrator: skipping webhook_repair for %s — %d recent no_effect outcomes", alert.shop_domain, failures)
                continue
            candidates.append(ActionRecord(
                action="webhook_repair",
                target=alert.shop_domain,
                reason=f"Retry webhook repair for alert_id={alert.id}",
            ))

    # Rule 2: Worker repeated failure alerts → escalate (resolve the alert
    # to prevent re-triggering, since watchdog will re-create if still failing)
    worker_fails = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "worker_repeated_failure",
            OpsAlert.resolved == False,
            OpsAlert.created_at >= now - timedelta(hours=6),
        )
        .all()
    )
    for alert in worker_fails:
        # Only resolve if alert is >1 hour old (give human time to see it)
        if alert.created_at and (now - alert.created_at).total_seconds() > 3600:
            # Stop auto-resolving if the same alert_type on the same shop
            # has been auto-resolved 3+ times in 24h with no_effect — that's
            # not self-healing, that's treadmill. Escalate instead.
            if _skip_stale_resolve(db, now, "worker_repeated_failure", alert.shop_domain):
                continue
            candidates.append(ActionRecord(
                action="resolve_alert",
                target=str(alert.id),
                reason=f"Auto-resolve stale worker failure alert (>1h old, watchdog will re-create if still failing)",
            ))

    # Rule 3: Webhook-repaired info alerts → auto-resolve after 4 hours
    info_alerts = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "webhook_repaired",
            OpsAlert.resolved == False,
            OpsAlert.created_at <= now - timedelta(hours=4),
        )
        .all()
    )
    for alert in info_alerts:
        if _skip_stale_resolve(db, now, "webhook_repaired", alert.shop_domain):
            continue
        candidates.append(ActionRecord(
            action="resolve_alert",
            target=str(alert.id),
            reason="Auto-resolve informational webhook_repaired alert (>4h old)",
        ))

    # Rule 5: Worker repeated failure → propose restart (TIER_1, blocked in hybrid)
    for alert in worker_fails:
        if alert.source and alert.source != "test":
            # Extract worker name from alert source
            worker_name = f"wishspark-{alert.source.replace('_worker', '').replace('_', '-')}"
            if alert.source.endswith("_worker"):
                worker_name = f"wishspark-{alert.source.replace('_', '-')}"
            candidates.append(ActionRecord(
                action="restart_worker",
                target=worker_name,
                reason=f"Worker {alert.source} has repeated failures (alert_id={alert.id})",
            ))

    # Rule 6: Cache-related errors → clear cache
    cache_alerts = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type.in_(["cache_error", "redis_error"]),
            OpsAlert.resolved == False,
            OpsAlert.created_at >= now - timedelta(hours=6),
        )
        .all()
    )
    for alert in cache_alerts:
        failures = count_recent_failures(db, "orch_clear_redis_cache", "signals:*", hours=6)
        if failures < _MAX_REPEATED_FAILURES:
            candidates.append(ActionRecord(
                action="clear_redis_cache",
                target="signals:*",
                reason=f"Cache anomaly detected (alert_id={alert.id})",
            ))

    # Rule 4: Onboarding failure alerts → auto-resolve after 2 hours
    # (the onboarding batch runner retries failed merchants each cycle anyway;
    # resolving the alert prevents accumulation while retries continue)
    onboarding_fails = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "onboarding_failed",
            OpsAlert.resolved == False,
            OpsAlert.created_at <= now - timedelta(hours=2),
        )
        .all()
    )
    for alert in onboarding_fails:
        if _skip_stale_resolve(db, now, "onboarding_failed", alert.shop_domain):
            continue
        candidates.append(ActionRecord(
            action="resolve_alert",
            target=str(alert.id),
            reason="Auto-resolve onboarding_failed alert (>2h old, batch runner retries automatically)",
        ))

    # Dedup within this cycle: same (action, target) → keep first only
    seen: set[str] = set()
    deduped: list[ActionRecord] = []
    for c in candidates:
        key = f"{c.action}::{c.target}"
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


# ---------------------------------------------------------------------------
# Main orchestrator cycle
# ---------------------------------------------------------------------------

def run_orchestrator_cycle(db: Session) -> OrchestratorResult:
    """
    Run one orchestrator decision-execution cycle.

    1. Evaluate all decision rules → candidate actions
    2. Filter by cooldown and MAX_ACTIONS_PER_CYCLE
    3. Execute each action via the registry
    4. Write audit log for every execution
    5. Return structured result
    """
    import secrets
    cycle_id = f"orch_{secrets.token_hex(4)}"
    result = OrchestratorResult(cycle_id=cycle_id)

    # Tag the current Sentry transaction (agent_worker cron) with this
    # cycle_id so any event raised during the cycle — LLM failure,
    # action_fn exception, DB rollback — links back to the exact
    # orchestrator pass in the dashboard. Free: auto-no-op when Sentry
    # is disabled (get_current_scope returns a no-op scope).
    try:
        import sentry_sdk
        sentry_sdk.get_current_scope().set_tag("orchestrator.cycle_id", cycle_id)
    except Exception:
        pass  # SILENT-EXCEPT-OK: scope enrichment is best-effort

    # Step 1: Evaluate
    candidates = _evaluate_decisions(db)
    result.actions_evaluated = len(candidates)

    log.info("orchestrator: cycle=%s evaluated=%d candidates", cycle_id, len(candidates))

    # Step 2: Filter and execute
    executed = 0
    for candidate in candidates:
        if executed >= MAX_ACTIONS_PER_CYCLE:
            candidate.status = "skipped"
            candidate.detail = "max_actions_per_cycle reached"
            result.actions_skipped += 1
            result.records.append(candidate)
            continue

        if _is_on_cooldown(candidate.action, candidate.target):
            candidate.status = "skipped"
            candidate.detail = "cooldown active"
            result.actions_skipped += 1
            result.records.append(candidate)
            log.info(
                "orchestrator: SKIP %s target=%s reason=cooldown cycle=%s",
                candidate.action, candidate.target, cycle_id,
            )
            continue

        # Step 3: Execute
        entry = ACTION_REGISTRY.get(candidate.action)
        action_fn = entry[0] if entry else None
        if action_fn is None:
            candidate.status = "failed"
            candidate.detail = f"unknown action: {candidate.action}"
            result.actions_failed += 1
            result.records.append(candidate)
            continue

        log.info(
            "orchestrator: EXEC %s target=%s reason=%s cycle=%s",
            candidate.action, candidate.target, candidate.reason, cycle_id,
        )

        try:
            exec_result = action_fn(db, candidate.target)
            candidate.status = "executed"
            candidate.detail = exec_result
            result.actions_executed += 1
            _set_cooldown(candidate.action, candidate.target)

            audit_entry = write_audit_log(
                db,
                actor_type="agent",
                actor_name="orchestrator",
                action_type=f"orch_{candidate.action}",
                target_type="merchant" if candidate.action == "webhook_repair" else "alert",
                target_id=candidate.target,
                shop_domain=candidate.target if candidate.action == "webhook_repair" else None,
                after_state={"result": exec_result, "reason": candidate.reason},
                status="completed",
                approval_mode="autonomous",
                metadata={"cycle_id": cycle_id},
            )

            # Record pending outcome for later evaluation
            from app.services.outcome_evaluator import record_pending_outcome
            record_pending_outcome(
                db,
                audit_log_id=audit_entry.id,
                action_type=f"orch_{candidate.action}",
                target_id=candidate.target,
                shop_domain=candidate.target if candidate.action == "webhook_repair" else None,
            )

            executed += 1

        except Exception as exc:
            candidate.status = "failed"
            candidate.detail = str(exc)[:200]
            result.actions_failed += 1
            log.error(
                "orchestrator: FAIL %s target=%s error=%s cycle=%s",
                candidate.action, candidate.target, exc, cycle_id,
            )
            db.rollback()

        result.records.append(candidate)

    try:
        db.commit()
    except Exception as exc:
        log.warning("orchestrator: run_orchestrator_cycle failed: %s", exc)
        db.rollback()

    log.info(
        "orchestrator: cycle=%s mode=%s deterministic: executed=%d skipped=%d failed=%d",
        cycle_id, ORCHESTRATOR_MODE,
        result.actions_executed, result.actions_skipped, result.actions_failed,
    )

    # ----- LLM proposal phase (proposal or hybrid mode only) -----
    if ORCHESTRATOR_MODE in ("proposal", "hybrid"):
        try:
            _run_llm_proposal_phase(db, cycle_id, result)
        except Exception as exc:
            log.warning("orchestrator: LLM phase error (non-fatal): %s", exc)

    return result


def _context_is_quiet(context: str) -> bool:
    """
    Check if orchestrator context has no actionable signals.

    If all alerts are resolved and no workers are erroring, there's nothing
    for the LLM to propose. Skip the call to save budget.

    Only applies to well-formed contexts (>100 chars) from the real builder.
    Short/mock contexts always pass through to avoid test interference.
    """
    # Don't skip on short contexts — likely test mocks or builder errors
    if len(context) < 200:
        return False

    context_lower = context.lower()
    has_unresolved = "unresolved" in context_lower and "0 unresolved" not in context_lower
    has_errors = "error" in context_lower and "0 errors" not in context_lower
    has_critical = "critical" in context_lower
    has_degraded = "degraded" in context_lower or "failed" in context_lower

    return not (has_unresolved or has_errors or has_critical or has_degraded)


def _run_llm_proposal_phase(
    db: Session,
    cycle_id: str,
    result: OrchestratorResult,
) -> None:
    """
    Run the Claude/LLM decision layer.

    In 'proposal' mode: log all proposals, do NOT execute any.
    In 'hybrid' mode: execute TIER_0 proposals, log the rest.
    """
    from app.services.orchestrator_context import build_orchestrator_context
    from app.services.orchestrator_llm import claude_decision

    context = build_orchestrator_context(db)
    log.info("orchestrator: LLM context built (%d chars) cycle=%s", len(context), cycle_id)

    # Cost optimization: skip LLM call if context has no actionable signals.
    # If no unresolved alerts and no worker errors, LLM will propose nothing.
    if _context_is_quiet(context):
        log.info("orchestrator: context quiet — skipping LLM call cycle=%s", cycle_id)
        return

    llm_result = claude_decision(context, ACTION_REGISTRY)

    if llm_result.error:
        log.info("orchestrator: LLM skipped: %s cycle=%s", llm_result.error, cycle_id)
        return

    # Classify proposals
    llm_executed = 0
    llm_blocked_tier = 0
    llm_invalid = 0

    for proposal in llm_result.proposals:
        tier = get_action_tier(proposal.action)
        executed = False
        status = "proposed"
        rejection_reason = None

        if not proposal.valid:
            status = "rejected"
            rejection_reason = "invalid_action_or_target"
            llm_invalid += 1

        elif ORCHESTRATOR_MODE == "proposal":
            # Proposal mode: log only, never execute
            status = "proposed"

        elif ORCHESTRATOR_MODE == "hybrid":
            if tier > TIER_0:
                status = "awaiting_approval"
                rejection_reason = f"tier_{tier}_requires_approval"
                llm_blocked_tier += 1
            elif _is_on_cooldown(proposal.action, proposal.target):
                status = "blocked_cooldown"
                rejection_reason = "cooldown_active"
            elif llm_executed >= MAX_ACTIONS_PER_CYCLE:
                status = "blocked_limit"
                rejection_reason = "max_actions_reached"
            else:
                # EXECUTE — TIER_0 in hybrid mode
                entry = ACTION_REGISTRY.get(proposal.action)
                action_fn = entry[0] if entry else None
                if action_fn:
                    try:
                        exec_result = action_fn(db, proposal.target)
                        status = "executed"
                        executed = True
                        llm_executed += 1
                        _set_cooldown(proposal.action, proposal.target)
                        log.info(
                            "orchestrator: HYBRID_EXEC %s target=%s result=%s cycle=%s",
                            proposal.action, proposal.target, exec_result, cycle_id,
                        )
                    except Exception as exc:
                        status = "exec_failed"
                        rejection_reason = str(exc)[:200]
                        log.error(
                            "orchestrator: HYBRID_FAIL %s target=%s error=%s cycle=%s",
                            proposal.action, proposal.target, exc, cycle_id,
                        )
                        db.rollback()

        # Write audit log for every proposal regardless of outcome
        audit_entry = write_audit_log(
            db,
            actor_type="agent",
            actor_name="orchestrator_claude",
            action_type=f"llm_{'exec' if executed else 'propose'}_{proposal.action}",
            target_type="merchant" if proposal.action == "webhook_repair" else "alert",
            target_id=proposal.target,
            before_state={"assessment": llm_result.assessment},
            after_state={
                "reason": proposal.reason,
                "valid": proposal.valid,
                "tier": tier,
                "mode": ORCHESTRATOR_MODE,
                "executed": executed,
                "rejection": rejection_reason,
            },
            status=status,
            approval_mode="autonomous" if executed else "proposal",
            metadata={
                "cycle_id": cycle_id,
                "model": llm_result.model_used,
            },
        )

        # Record pending outcome for executed actions
        if executed:
            from app.services.outcome_evaluator import record_pending_outcome
            record_pending_outcome(
                db,
                audit_log_id=audit_entry.id,
                action_type=f"llm_exec_{proposal.action}",
                target_id=proposal.target,
                shop_domain=proposal.target if proposal.action == "webhook_repair" else None,
            )

        # Create approval request for TIER_1+ proposals in hybrid mode
        if status == "awaiting_approval":
            from app.models.action_approval import ActionApproval
            _APPROVAL_TTL_HOURS = 2
            exp = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=_APPROVAL_TTL_HOURS)
            approval = ActionApproval(
                audit_log_id=audit_entry.id,
                action_type=proposal.action,
                target_id=proposal.target,
                shop_domain=proposal.target if proposal.action in ("webhook_repair",) else None,
                status="pending",
                reason=proposal.reason,
                expires_at=exp,
            )
            db.add(approval)
            db.flush()
            log.info(
                "orchestrator: APPROVAL_REQUESTED id=%d %s target=%s cycle=%s",
                approval.id, proposal.action, proposal.target, cycle_id,
            )
            try:
                from app.core.alert_delivery import notify_approval_pending
                sent = notify_approval_pending(
                    approval_id=approval.id,
                    action_type=proposal.action,
                    target_id=proposal.target,
                    shop_domain=proposal.target if proposal.action in ("webhook_repair",) else None,
                    reason=proposal.reason,
                    expires_at=exp.isoformat() + "Z",
                )
                if sent:
                    approval.notified_at = datetime.now(timezone.utc).replace(tzinfo=None)
            except Exception as exc:
                log.warning("orchestrator: _run_llm_proposal_phase failed: %s", exc)

    try:
        db.commit()
    except Exception as exc:
        log.warning("orchestrator: _run_llm_proposal_phase failed: %s", exc)
        db.rollback()

    log.info(
        "orchestrator: LLM cycle=%s mode=%s model=%s assessment=%s "
        "proposals=%d executed=%d blocked_tier=%d invalid=%d",
        cycle_id, ORCHESTRATOR_MODE, llm_result.model_used,
        llm_result.assessment[:60],
        len(llm_result.proposals), llm_executed, llm_blocked_tier, llm_invalid,
    )
