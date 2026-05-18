import logging
log = logging.getLogger("agent_worker")
import os
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.core.sentry_init import init_sentry, cron_monitor
init_sentry(component="agent_worker")

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="agent_worker")

from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.models.product_opportunity import ProductOpportunity
from app.models.price_intelligence import PriceIntelligence
from app.models.worker_log import WorkerLog
from app.models.worker_state import WorkerState
from app.sandbox.sandbox_executor import create_sandbox_run, update_sandbox_status
from app.services.product_intelligence_engine import build_product_intelligence

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKER_NAME = "agent_worker"
SLEEP_SECONDS = 900     # 15 minutes between cycles


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log = logging.getLogger("worker.agent")

def log(msg):
    _log.info(msg)


# ---------------------------------------------------------------------------
# WorkerState helpers
# ---------------------------------------------------------------------------

def _load_state(db) -> WorkerState:
    """
    Return the WorkerState row for this worker, creating it if absent.
    last_watermark is unused by this worker; last_run_at is the only
    field updated each cycle.
    """
    state = (
        db.query(WorkerState)
        .filter(WorkerState.worker_name == WORKER_NAME)
        .first()
    )
    if state is None:
        state = WorkerState(
            worker_name=WORKER_NAME,
            last_watermark=None,
            last_run_at=None,
        )
        db.add(state)
        db.commit()
        db.refresh(state)
        log("created new worker_state row (first run)")
    return state


def _save_state(db, state: WorkerState) -> None:
    state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


# ---------------------------------------------------------------------------
# WorkerLog helper
# ---------------------------------------------------------------------------

def _write_log(
    db,
    started_at: datetime,
    shops_processed: int,
    rows_written: int,
    errors: int,
    error_detail: str | None,
) -> None:
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    entry = WorkerLog(
        worker_name=WORKER_NAME,
        started_at=started_at,
        finished_at=finished_at,
        shops_processed=shops_processed,
        rows_written=rows_written,
        errors=errors,
        error_detail=error_detail,
        duration_ms=duration_ms,
    )
    db.add(entry)
    db.commit()


# ---------------------------------------------------------------------------
# Business logic (unchanged)
# ---------------------------------------------------------------------------

def fetch_targets():
    db = SessionLocal()
    try:
        opportunities = db.query(ProductOpportunity).order_by(ProductOpportunity.priority_score.desc()).limit(3).all()
        targets = []

        for opp in opportunities:
            price = db.query(PriceIntelligence).filter(
                PriceIntelligence.product_url == opp.product_url
            ).first()

            targets.append({
                "goal": "analyze product opportunity",
                "product_name": opp.product_url,
                "product_url": opp.product_url,
                "avg_intent_score": float(opp.avg_intent_score or 0),
                "confidence": float(opp.priority_score or 0),
                "recommended_action": opp.recommended_action or "NONE",
                "price_opportunity": getattr(price, "price_opportunity", "UNKNOWN") if price else "UNKNOWN",
            })

        if not targets:
            targets = [
                {"goal": "analyze pricing strategy"},
                {"goal": "analyze product opportunity"},
                {"goal": "analyze conversion opportunity"},
            ]

        return targets
    finally:
        db.close()


def write_report(run_path: str, goal: str, analysis: dict):
    report_path = Path(run_path) / "report.md"

    content = f"""# WishSpark Sandbox Report

Generated at: {datetime.now(timezone.utc).isoformat()} UTC

## Goal
{goal}

## Analysis Summary
{analysis.get("summary", "No summary available")}

## Commercial Priority
{analysis.get("commercial_priority", "UNKNOWN")}

## Recommended Action
{analysis.get("recommended_action", "NONE")}

## Status
planned
"""

    report_path.write_text(content, encoding="utf-8")


def write_analysis(run_path: str, analysis: dict):
    analysis_path = Path(run_path) / "analysis.json"
    analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def _run_orchestrator():
    """Run the AI orchestrator — decision/execution + outcome evaluation."""
    db = SessionLocal()
    try:
        # Phase A: Decision + execution
        from app.services.orchestrator import run_orchestrator_cycle
        result = run_orchestrator_cycle(db)
        if result.actions_executed > 0 or result.actions_failed > 0:
            log(f"orchestrator: exec={result.actions_executed} skip={result.actions_skipped} fail={result.actions_failed}")

        # Phase B: Evaluate outcomes from previous cycles
        from app.services.outcome_evaluator import evaluate_pending_outcomes
        eval_result = evaluate_pending_outcomes(db)
        db.commit()
        if eval_result.evaluated > 0:
            log(f"outcomes: evaluated={eval_result.evaluated} success={eval_result.success} no_effect={eval_result.no_effect}")

    except Exception as exc:
        log(f"orchestrator error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_merchant_brain_tick():
    """MerchantBrain v0.1 — per-merchant coordination cycle.
    Default OFF; flipped on by MERCHANT_BRAIN_ENABLED=1 in the
    pre-merchant un-park ceremony. Bounded: max 100 shops per cycle.

    Born 2026-05-07 closing founder direttiva "shippa Brain Vero"
    — the conductor pivots brain from immune-system-on-self to
    merchant-outcome loop. The 5-step cycle (sense → synthesize →
    decide → coordinate → learn) lives in
    `app.services.merchant_brain`; this worker phase runs the tick
    across active shops + closes pending outcome windows.
    """
    db = SessionLocal()
    try:
        from app.services.merchant_brain import (
            is_brain_enabled,
            tick_all_active_merchants,
            evaluate_pending_outcomes as brain_evaluate_pending,
            enrich_dispatched_decisions as brain_enrich_dispatched,
        )
        if not is_brain_enabled():
            return
        result = tick_all_active_merchants(db, max_shops=100)
        if result.get("ticks", 0) > 0:
            by_action = result.get("by_action", {})
            log(f"merchant_brain: ticks={result['ticks']} {dict(by_action)}")
        # Enrich dispatched decisions with merchant_emails resend_id +
        # send_status. Closes the brain → actual-delivery observability
        # gap (Competitor-CTO audit, 2026-05-08).
        enrich_result = brain_enrich_dispatched(db, max_enrich=100)
        if enrich_result.get("enriched", 0) > 0:
            log(f"merchant_brain: enriched={enrich_result['enriched']}")
        # Close LEARN loop for decisions whose outcome window elapsed
        eval_result = brain_evaluate_pending(db, max_evaluate=50)
        if eval_result.get("evaluated", 0) > 0:
            log(f"merchant_brain: outcomes_evaluated={eval_result['evaluated']}")
    except Exception as exc:
        log(f"merchant_brain error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_onboarding():
    """Run pending merchant onboarding."""
    db = SessionLocal()
    try:
        from app.services.onboarding import run_pending_onboarding
        summary = run_pending_onboarding(db)
        if summary["processed"] > 0:
            log(f"onboarding: ready={summary['ready']} failed={summary['failed']}")
    except Exception as exc:
        log(f"onboarding error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_onboarding_health():
    """Check onboarding pipeline health + funnel friction detection."""
    db = SessionLocal()
    try:
        from app.services.onboarding_health import write_onboarding_alerts, run_drift_action_loop
        result = write_onboarding_alerts(db)
        if result["alerts_written"] > 0:
            log(f"onboarding_health: alerts={result['alerts_written']} stuck={result['stuck']} pixel_abandon={result['pixel_abandon']}")

        # Drift action loop (A2): closes the H6 detect-only gap by
        # actually re-engaging drifters via email_orchestrator. Honors
        # per-shop weekly cooldown internally so it's safe to call
        # every cycle.
        try:
            drift_loop = run_drift_action_loop(db)
            if drift_loop.get("sent") or drift_loop.get("escalated"):
                log(
                    f"onboarding_health: drift_loop sent={drift_loop['sent']} "
                    f"escalated={drift_loop['escalated']} "
                    f"drifters={drift_loop['drifters']}"
                )
            db.commit()
        except Exception as exc:
            log(f"onboarding_health: drift loop error (non-fatal): {exc}")
            db.rollback()
    except Exception as exc:
        log(f"onboarding_health error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()

    # Funnel friction detection (separate session for isolation)
    db2 = SessionLocal()
    try:
        from app.services.onboarding_funnel import run_friction_detection
        friction = run_friction_detection(db2)
        if friction["friction_signals"] > 0 or friction["alerts_written"] > 0:
            log(f"onboarding_funnel: signals={friction['friction_signals']} alerts={friction['alerts_written']} insights={friction['insights']}")
    except Exception as exc:
        log(f"onboarding_funnel error (non-fatal): {exc}")
        db2.rollback()
    finally:
        db2.close()


def _run_scaling_intelligence():
    """Daily snapshot + scaling forecast/recommendations (cooldown-protected)."""
    from app.services.scaling_intelligence import (
        should_capture_snapshot, capture_daily_snapshot, mark_snapshot_captured,
        should_generate_recommendations, generate_recommendations, mark_recommendations_generated,
        build_forecast,
    )

    # Daily snapshot
    if should_capture_snapshot():
        db = SessionLocal()
        try:
            capture_daily_snapshot(db)
            db.commit()
            mark_snapshot_captured()
        except Exception as exc:
            log(f"scaling snapshot error (non-fatal): {exc}")
            db.rollback()
        finally:
            db.close()

    # Recommendations (daily)
    if should_generate_recommendations():
        db = SessionLocal()
        try:
            recs = generate_recommendations(db)
            db.commit()
            mark_recommendations_generated()

            # Notify via Telegram for significant recommendations (with reviewer context)
            if recs:
                try:
                    from app.services.telegram_agent import send_scaling_alert, is_configured
                    if is_configured():
                        forecast = build_forecast(db)
                        # Only send full alert if forecast has real data
                        if forecast.get("status") == "ok":
                            for r in recs[:2]:  # max 2 notifications
                                send_scaling_alert(r, forecast)
                        else:
                            from app.services.telegram_agent import send_message
                            send_message(
                                f"*Scaling* \u2014 {len(recs)} recommendation(s) generated\n\n"
                                f"Forecast: insufficient data ({forecast.get('snapshots_available', 0)}/{forecast.get('minimum_required', 5)} days)\n"
                                f"Projections not yet reliable. Use /scaling for details."
                            )
                except Exception as exc:
                    log(f"scaling telegram error (non-fatal): {exc}")

        except Exception as exc:
            log(f"scaling recommendations error (non-fatal): {exc}")
            db.rollback()
        finally:
            db.close()


# Sprint A P2 fix 2026-05-11: bound entitlement scan + Redis cursor.
# Prior behavior: unbounded `.all()` over every active merchant + 1
# OpsAlert SELECT per shop = 20k queries/cycle at 10k merchants in
# the 25-min agent cron deadline. Now: 200 shops/cycle bounded by
# round-robin cursor (mirrors segment_monitor_worker pattern, 50
# cycles × 15min = 12.5h full-fleet coverage at 10k); per-shop
# OpsAlert SELECT collapsed into ONE pre-fetch SELECT before loop.
_ENTITLEMENT_MAX_PER_CYCLE = 200
_ENTITLEMENT_CURSOR_KEY = "hs:entitlement_scan:cursor"


def _entitlement_load_cursor() -> int:
    """Load round-robin cursor from Redis. 0 on miss/error (fresh start)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("entitlement_scan.load_cursor.redis_down")
            return 0
        raw = rc.get(_ENTITLEMENT_CURSOR_KEY)
        return int(raw) if raw else 0
    except Exception as exc:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("entitlement_scan.load_cursor.exception")
        log.warning("agent_worker: _entitlement_load_cursor failed: %s", exc)
        return 0


def _entitlement_save_cursor(pos: int) -> None:
    """Persist round-robin cursor. 24h TTL (long enough that a
    long-paused worker still resumes correctly)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.set(_ENTITLEMENT_CURSOR_KEY, str(pos), ex=86400)
    except Exception:
        pass  # SILENT-EXCEPT-OK: cursor save is best-effort; on next cycle we restart from prior cursor (worst case: re-scan same batch).


def _run_entitlement_health_scan():
    """Scan active merchants for plan/billing mismatches. Bounded by
    `_ENTITLEMENT_MAX_PER_CYCLE` shops per cycle, round-robin via
    Redis cursor. See Sprint A P2 fix doctrine above."""
    db = SessionLocal()
    try:
        from app.models.merchant import Merchant
        from app.core.operator_blocklist import operator_dev_shops
        from app.services.merchant_chatbot import check_entitlement_health
        from app.models.ops_alert import OpsAlert

        # Sorted list for cursor stability — same shop_domain order
        # every cycle so the cursor advances deterministically.
        all_shops = [
            r[0] for r in
            db.query(Merchant.shop_domain)
            .filter(
                Merchant.install_status == "active",
                ~Merchant.shop_domain.in_(operator_dev_shops()),
            )
            .order_by(Merchant.shop_domain.asc())
            .all()
        ]

        # Round-robin batch: at <= MAX_PER_CYCLE total, scan everything.
        if len(all_shops) <= _ENTITLEMENT_MAX_PER_CYCLE:
            batch = all_shops
            next_cursor = 0
        else:
            start = _entitlement_load_cursor() % len(all_shops)
            end = start + _ENTITLEMENT_MAX_PER_CYCLE
            if end <= len(all_shops):
                batch = all_shops[start:end]
                next_cursor = end % len(all_shops)
            else:
                # wrap
                batch = all_shops[start:] + all_shops[: end - len(all_shops)]
                next_cursor = end - len(all_shops)

        # Pre-fetch existing unresolved entitlement_mismatch alerts in
        # ONE query — collapses the per-shop OpsAlert SELECT (was 1
        # query per shop with mismatch). Returns set of shop_domains
        # that already have an open alert.
        already_alerted = {
            r[0] for r in
            db.query(OpsAlert.shop_domain)
            .filter(
                OpsAlert.alert_type == "entitlement_mismatch",
                OpsAlert.resolved.is_(False),
                OpsAlert.shop_domain.in_(batch),
            )
            .all()
        }

        issues_found = 0
        for shop_domain in batch:
            health = check_entitlement_health(db, shop_domain)
            if not health["healthy"]:
                issues_found += 1
                if shop_domain in already_alerted:
                    continue  # dedup hit — alert already open
                try:
                    from app.services.alerting import write_alert
                    # heal-detection: entitlement_mismatch alerts are
                    # opt-out — the entitlement_scan cycle re-runs every
                    # 15min, and a fixed mismatch (plan/billing change)
                    # generates no new alert (already_alerted dedup), so
                    # the persisting alert clears via manual resolve OR
                    # the L2 retention sweep. No auto-heal handler needed.
                    write_alert(
                        db, severity="warning", source="entitlement_scan",
                        alert_type="entitlement_mismatch",
                        summary=f"Entitlement mismatch: {health['issues']}",
                        shop_domain=shop_domain,
                    )
                except Exception as exc:
                    log.warning(
                        "agent_worker: entitlement write_alert failed "
                        "for %s: %s", shop_domain, exc,
                    )

        if issues_found > 0:
            log(
                f"entitlement_scan: {issues_found} mismatches found "
                f"across {len(batch)} merchants (of {len(all_shops)} "
                f"total, cursor advanced to {next_cursor})"
            )
        db.commit()
        _entitlement_save_cursor(next_cursor)
    except Exception as exc:
        log(f"entitlement_scan error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _today_rome() -> str:
    """Return today's date string in Europe/Rome timezone."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


def _run_daily_digest():
    """
    Send daily health digest to Telegram — ONCE per calendar day (Europe/Rome).

    Scheduled for 08:00+ Rome (B7 gate): previous behaviour was "first
    cycle after Rome midnight" which often fired at ~00:05 and was noisy.

    Silence policy (B6, founder Option B): if the state is quiet (no
    attention items AND overall health is healthy), skip the send but
    still mark the day as done so we don't recompute on every cycle.

    Dedup: stored in worker_state.last_digest_date (DB column).
    Survives PM2 restarts, Redis resets, process crashes.

    Audit log (2026-04-18, B1 residue closure): every state transition
    writes an audit_log row with actor_name='agent_worker' and
    action_type='daily_digest_decision' so hit-rate / silence-rate /
    failure-rate can be measured with a SQL query.
    """
    from app.services.telegram_agent import (
        send_daily_digest,
        is_digest_quiet,
        is_configured,
    )
    from app.services.audit import write_audit_log
    if not is_configured():
        return

    # B7 — 08:00 Rome gate.
    from zoneinfo import ZoneInfo as _ZI
    if datetime.now(_ZI("Europe/Rome")).hour < 8:
        return

    today = _today_rome()

    db = SessionLocal()
    try:
        from sqlalchemy import text
        # Check if already sent (or silenced) today — DB is source of truth.
        row = db.execute(text(
            "SELECT last_digest_date FROM worker_state WHERE worker_name = 'agent_worker'"
        )).fetchone()

        if row and row[0] == today:
            return  # already decided for today

        # B6 — Silence policy (Option B). Mark today as done so the
        # decision isn't recomputed every cycle.
        if is_digest_quiet(db):
            db.execute(text(
                "UPDATE worker_state SET last_digest_date = :today "
                "WHERE worker_name = 'agent_worker'"
            ), {"today": today})
            try:
                write_audit_log(
                    db,
                    actor_type="worker",
                    actor_name="agent_worker",
                    action_type="daily_digest_decision",
                    target_type="digest",
                    target_id=today,
                    status="silenced_quiet",
                    metadata={"date_rome": today},
                )
            except Exception as exc:
                log(f"daily_digest audit log (silenced) failed: {exc}")
            db.commit()
            log(f"daily_digest: silenced for {today} — quiet state")
            return

        # Send digest
        sent = send_daily_digest(db)

        if sent:
            db.execute(text(
                "UPDATE worker_state SET last_digest_date = :today WHERE worker_name = 'agent_worker'"
            ), {"today": today})
            try:
                write_audit_log(
                    db,
                    actor_type="worker",
                    actor_name="agent_worker",
                    action_type="daily_digest_decision",
                    target_type="digest",
                    target_id=today,
                    status="sent",
                    metadata={"date_rome": today},
                )
            except Exception as exc:
                log(f"daily_digest audit log (sent) failed: {exc}")
            db.commit()
            log(f"daily_digest: sent for {today}")
        else:
            # Retryable failure — no worker_state update, but DO record the
            # attempt so failure-rate is measurable. The audit row carries
            # the attempt timestamp; repeated cycles will write repeated
            # rows until the send succeeds (bounded by the Rome-day dedup).
            try:
                write_audit_log(
                    db,
                    actor_type="worker",
                    actor_name="agent_worker",
                    action_type="daily_digest_decision",
                    target_type="digest",
                    target_id=today,
                    status="send_failed",
                    metadata={"date_rome": today},
                )
                db.commit()
            except Exception as exc:
                log(f"daily_digest audit log (failed) failed: {exc}")
            log("daily_digest: send failed — will retry next cycle")
    except Exception as exc:
        log(f"daily_digest error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_daily_digest failed: %s", exc)
    finally:
        db.close()


def _run_breach_classifier():
    """Classify security alerts as breach candidates and start the
    GDPR Art. 33 72h clock automatically."""
    db = SessionLocal()
    try:
        from app.services.breach_notification import process_breach_candidates
        report = process_breach_candidates(db)
        if report.get("new_response_alerts", 0) > 0:
            log(
                f"breach_notification: raised {report['new_response_alerts']} "
                f"new breach_response_required alert(s) — "
                f"{report['classified']} classified"
            )
    except Exception as exc:
        log(f"breach_notification error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_breach_classifier failed: %s", exc)
    finally:
        db.close()


def _run_audit_log_integrity_check():
    """
    Daily audit_log hash-chain verification. Rate-limited via Redis.

    Atomic claim: SET NX on the day-keyed lock so two worker replicas
    don't both pass the check and both run the expensive chain walk.
    Previously the pair get() + setex() left a race window.
    """
    today = _today_rome()
    redis_key = f"hs:audit_log_check:day:{today}"
    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception as exc:
        log.warning("agent_worker: _run_audit_log_integrity_check failed: %s", exc)
        rc = None
    if rc is not None:
        try:
            # Claim the day-slot atomically; if we can't, another process owns it
            if not rc.set(redis_key, "1", nx=True, ex=48 * 3600):
                return
        except Exception as exc:
            # FAIL-CLOSED on Redis hiccup. Pre-2026-05-08 this proceeded
            # (fail-open) which meant during a Redis outage every worker
            # cycle could re-run the expensive enforce_chain_integrity()
            # walk. With 4 workers + a 15-min cycle that's 96 chain walks/day
            # vs the intended 1/day. Skip this cycle; next 15-min tick
            # will retry. Audit-log integrity is daily-grain and
            # tolerates 15-min slip.
            log.warning(
                "agent_worker: _run_audit_log_integrity_check Redis claim "
                "failed; SKIPPING this cycle (fail-CLOSED): %s", exc,
            )
            return

    db = SessionLocal()
    try:
        from app.services.audit import enforce_chain_integrity
        result = enforce_chain_integrity(db)
        if result["violations"]:
            log(
                f"audit_log: TAMPERING DETECTED "
                f"{len(result['violations'])} violations"
            )
    except Exception as exc:
        log(f"audit_log integrity error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_audit_log_integrity_check failed: %s", exc)
    finally:
        db.close()


def _run_invariant_monitor():
    """
    Post-merge structural invariant check — runs registered audits
    (backend/scripts/audit_*.py) against the live source tree. A
    failure here means the invariant broke AFTER passing preflight,
    which typically indicates a hook bypass (--no-verify), a merge
    conflict resolution, or an emergency fix. Emits ops_alerts the
    bugfix pipeline can react to.

    No Redis lock: cheap (sub-second), idempotent, safe to run on
    multiple replicas. The alerting.write_alert dedup windows
    (5-min acute + 24h chronic aggregation) handle repeat suppression.
    """
    db = SessionLocal()
    try:
        from app.services.invariant_monitor import run_invariant_check
        summary = run_invariant_check(db)
        if summary.get("failed", 0) > 0:
            log(
                f"invariant_monitor: {summary['failed']}/{summary['checked']} "
                f"audits failing, {summary['alerts_written']} alerts written"
            )
        db.commit()
    except Exception as exc:
        log(f"invariant_monitor error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as rollback_exc:
            log.warning(
                "agent_worker: invariant_monitor rollback failed: %s", rollback_exc
            )
    finally:
        db.close()


def _run_uninstall_erasure_watchdog():
    """Self-heal missing shop/redact webhooks — GDPR Art. 17 belt-and-braces."""
    db = SessionLocal()
    try:
        from app.services.uninstall_erasure import run_uninstall_erasure_watchdog
        report = run_uninstall_erasure_watchdog(db)
        if report.get("self_healed", 0) > 0:
            log(
                f"uninstall_erasure: SELF-HEALED {report['self_healed']} "
                f"shop(s) whose 48h grace elapsed without shop/redact"
            )
    except Exception as exc:
        log(f"uninstall_erasure error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_uninstall_erasure_watchdog failed: %s", exc)
    finally:
        db.close()


def _run_security_heartbeat():
    """Hourly synthetic probes of the security surface. Self-rate-limited."""
    db = SessionLocal()
    try:
        from app.services.security_heartbeat import run_security_heartbeat
        report = run_security_heartbeat(db)
        if report.get("failed", 0) > 0:
            log(
                f"security_heartbeat: FAILED={report['failed']} "
                f"passed={report['passed']} total={report['total']}"
            )
    except Exception as exc:
        log(f"security_heartbeat error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_security_heartbeat failed: %s", exc)
    finally:
        db.close()


def _run_gdpr_sla_enforcement():
    """Per-cycle guardrail — every pending GdprRequest is checked
    against its deadline. Missing deadlines fire CRITICAL alerts.
    Dedup via ops_alert row identity so we don't flood the channel.
    """
    db = SessionLocal()
    try:
        from app.services.gdpr_sla import enforce_sla
        report = enforce_sla(db)
        if report.get("new_alerts", 0) > 0:
            log(
                f"gdpr_sla: BREACH violations={report['violations']} "
                f"new_alerts={report['new_alerts']}"
            )
    except Exception as exc:
        log(f"gdpr_sla error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_gdpr_sla_enforcement failed: %s", exc)
    finally:
        db.close()


def _run_data_retention():
    """GDPR Art. 5(1)(e) retention sweep — runs ONCE per calendar day
    (Europe/Rome) so we never keep visitor behavioral data past its TTL.

    Kill switch: DATA_RETENTION_PAUSED=1.
    Tunables: DATA_RETENTION_EVENTS_DAYS, DATA_RETENTION_VPS_DAYS.
    """
    today = _today_rome()
    redis_key = f"hs:data_retention:day:{today}"
    rc = None
    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception as exc:
        log.warning("agent_worker: _run_data_retention failed: %s", exc)
        rc = None
    if rc is not None:
        try:
            if not rc.set(redis_key, "1", nx=True, ex=48 * 3600):
                return
        except Exception as exc:
            log.warning("agent_worker: _run_data_retention failed: %s", exc)

    db = SessionLocal()
    try:
        from app.services.data_retention import run_retention_sweep
        report = run_retention_sweep(db)
        if report.get("events_deleted") or report.get("vps_deleted"):
            log(
                f"data_retention: events_deleted={report['events_deleted']} "
                f"vps_deleted={report['vps_deleted']}"
            )
    except Exception as exc:
        log(f"data_retention error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_data_retention failed: %s", exc)
    finally:
        db.close()


def _run_merchant_digest():
    """Send weekly email digests to eligible merchants (Monday only, Europe/Rome)."""
    from app.services.merchant_digest import _is_monday_rome, run_merchant_digest_cycle

    if not _is_monday_rome():
        return

    db = SessionLocal()
    try:
        summary = run_merchant_digest_cycle(db)
        db.commit()
        if summary["sent"] > 0 or summary["failed"] > 0:
            log(f"merchant_digest: sent={summary['sent']} failed={summary['failed']} no_data={summary['no_data']}")
    except Exception as exc:
        log(f"merchant_digest error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_lite_morning_digest():
    """Send daily morning brief email to Lite merchants (08:00–09:59 Europe/Rome).

    Closes Gap A of the €39-ready sprint: turns the Lite brief from
    pure-pull (merchant must log in) into push (email lands in inbox).
    Per-merchant dedup via Redis keeps it to one send per calendar day
    even when the 15-min agent worker cycle fires multiple times within
    the send window.
    """
    from app.services.lite_morning_digest import (
        _is_morning_rome,
        run_lite_morning_digest_cycle,
    )

    if not _is_morning_rome():
        return

    db = SessionLocal()
    try:
        summary = run_lite_morning_digest_cycle(db)
        db.commit()
        if summary["sent"] > 0 or summary["failed"] > 0:
            log(
                f"lite_morning_digest: sent={summary['sent']} "
                f"failed={summary['failed']} skipped={summary['skipped']}"
            )
    except Exception as exc:
        log(f"lite_morning_digest error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()

def _run_lifecycle_emails():
    """
    Send lifecycle emails for merchants needing attention.

    Three trigger types, each with its own dedup/cooldown:
      1. setup_incomplete — merchant installed >24h ago, still not ready
      2. first_insight    — first opportunity_signal appeared (once per shop)
      3. connection_issue — merchant stuck in degraded/failed state >2h

    All dedup is handled inside submit_lifecycle_intent — safe to call every cycle.
    """
    # worker-loop-cursor: ok — the stuck/degraded `.limit()` scans run
    # over the TRANSIENT failed-onboarding population (active merchants
    # stuck pending/failed >24h / degraded >2h). In healthy operation
    # that set is near-zero (onboarding completes in minutes); a value
    # large enough to exceed the per-cycle limit is itself a louder,
    # separately-alarmed onboarding-pipeline incident. submit_lifecycle_
    # intent dedup makes re-selecting the same rows incorrectness-safe
    # (no double-send), so the only scale effect is nudge LATENCY under
    # an already-alarmed pathology — a per-query round-robin cursor
    # here is over-eng (§2 r10) for an alarm-dominated scenario, unlike
    # billing_sync (every Pro merchant, perpetually in-set, revenue
    # integrity) which got the cursor.
    db = SessionLocal()
    try:
        from app.services.merchant_email_service import submit_lifecycle_intent
        from app.models.merchant import Merchant
        from datetime import timedelta
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        intents_queued = 0

        # --- 1. Setup incomplete: installed >24h ago, not ready ---
        # Operator/dev tenant exclusion (founder direttiva 2026-05-06).
        from app.core.operator_blocklist import operator_dev_shops
        install_cutoff = now - timedelta(hours=24)
        stuck_merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.onboarding_status.in_(["pending", "failed"]),
                Merchant.installed_at < install_cutoff,
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
                ~Merchant.shop_domain.in_(operator_dev_shops()),
            )
            .limit(10)
            .all()
        )
        for m in stuck_merchants:
            hours = int((now - m.installed_at).total_seconds() / 3600) if m.installed_at else 24
            result = submit_lifecycle_intent(db, m.shop_domain, "setup_incomplete", {
                "issue": m.onboarding_error or "setup is incomplete",
                "hours_since_install": hours,
            })
            if result["status"] == "queued":
                intents_queued += 1
            db.commit()

        # --- 2. First insight: shops with signals that haven't received this email ---
        # Single query collapses the prior pattern (DISTINCT outer + 2
        # per-shop sub-queries for top signal + count). merchants.shop_domain
        # is unique → JOIN does not multiply rows.
        from sqlalchemy import text
        first_insight_shops = db.execute(text("""
            SELECT
                os.shop_domain,
                COUNT(*)                                              AS signal_count,
                (array_agg(os.signal_type  ORDER BY os.signal_strength DESC NULLS LAST))[1] AS top_signal_type,
                (array_agg(os.explanation  ORDER BY os.signal_strength DESC NULLS LAST))[1] AS top_explanation
            FROM opportunity_signals os
            JOIN merchants m ON m.shop_domain = os.shop_domain
            WHERE m.install_status = 'active'
              AND m.contact_email IS NOT NULL
              AND m.contact_email != ''
              AND os.expires_at > now()
              AND os.shop_domain NOT IN (
                  SELECT me.shop_domain FROM merchant_emails me
                  WHERE me.email_type = 'first_insight' AND me.status = 'sent'
              )
            GROUP BY os.shop_domain
            LIMIT 10
        """)).fetchall()

        from app.core.query_count_monitor import worker_scope
        for row in first_insight_shops:
            shop = row[0]
            signal_count = int(row[1] or 1)
            top_signal_type = row[2]
            top_explanation = row[3]

            ctx = {"signal_count": signal_count}
            if top_signal_type or top_explanation:
                ctx["top_signal"] = (
                    top_explanation
                    or top_signal_type
                    or "a product showing unusual visitor behavior"
                )

            # Worker-scope query monitor: per-shop iteration gets a
            # fresh count so a future N+1 regression in the lifecycle
            # path surfaces at runtime, paired with the static
            # audit_n_plus_one preflight check.
            with worker_scope("agent_worker.first_insight", shop):
                result = submit_lifecycle_intent(db, shop, "first_insight", ctx)
                if result["status"] == "queued":
                    intents_queued += 1
                db.commit()

        # --- 3. Connection issue: stuck merchants (degraded/failed >2h) ---
        # Operator/dev tenant exclusion (founder direttiva 2026-05-06).
        stuck_cutoff = now - timedelta(hours=2)
        degraded_merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.onboarding_status.in_(["failed"]),
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
                ~Merchant.shop_domain.in_(operator_dev_shops()),
            )
            .limit(5)
            .all()
        )
        for m in degraded_merchants:
            result = submit_lifecycle_intent(db, m.shop_domain, "connection_issue", {
                "issue": m.onboarding_error or "the connection to your store was lost",
            })
            if result["status"] == "queued":
                intents_queued += 1
            db.commit()

        if intents_queued > 0:
            log(f"lifecycle_emails: {intents_queued} intents queued for orchestrator")

    except Exception as exc:
        log(f"lifecycle_emails error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_followup_emails():
    """
    Send 48h follow-up emails to merchants who received a beta invite
    but haven't engaged. Deterministic variant selection based on journey state.

    The followup worker commits per-merchant internally — no outer commit needed.
    """
    db = SessionLocal()
    try:
        from app.services.followup_worker import run_followup_cycle
        result = run_followup_cycle(db)
        # No outer commit — run_followup_cycle commits per-merchant
        if result["sent"] > 0 or result["failed"] > 0:
            log(f"followup_emails: eligible={result['eligible']} sent={result['sent']} skipped={result['skipped']} failed={result['failed']}")
    except Exception as exc:
        log(f"followup_emails error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_inbound_actions():
    """Execute routing actions from classified inbound emails. Closes the merchant reply loop."""
    db = SessionLocal()
    try:
        from app.services.inbound_action_executor import run_inbound_actions, run_low_severity_escalation
        result = run_inbound_actions(db)
        db.commit()
        esc = run_low_severity_escalation(db)
        db.commit()
        total = result["processed"] + result["incidents_created"] + result["feedback_logged"]
        if total > 0 or esc["escalated"] > 0:
            log(f"inbound_actions: processed={result['processed']} incidents={result['incidents_created']} feedback={result['feedback_logged']} escalated={esc['escalated']}")
    except Exception as exc:
        log(f"inbound_actions error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_silence_detection():
    """Detect merchants with 0 events over 14 days while still active. Trigger re-engagement."""
    db = SessionLocal()
    try:
        from app.services.silence_detector import run_silence_detection
        result = run_silence_detection(db)
        db.commit()
        if result["detected"] > 0:
            log(f"silence_detection: detected={result['detected']} alerted={result['alerted']}")
    except Exception as exc:
        log(f"silence_detection error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_action_agent():
    """Claim and execute pending action tasks — creates nudges for auto-executable actions."""
    db = SessionLocal()
    try:
        from app.services.action_agent import run_action_cycle
        result = run_action_cycle(db)
        db.commit()
        if result["executed"] > 0 or result["approval_queued"] > 0:
            log(f"action_agent: claimed={result['claimed']} executed={result['executed']} approval={result['approval_queued']} failed={result['failed']}")
    except Exception as exc:
        log(f"action_agent error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_action_learning():
    """Evaluate action outcomes and feed learning back into ranking."""
    db = SessionLocal()
    try:
        from app.services.action_learning import evaluate_pending_outcomes
        result = evaluate_pending_outcomes(db)
        db.commit()
        if result["evaluated"] > 0:
            log(f"action_learning: evaluated={result['evaluated']} success={result['success']} no_effect={result['no_effect']}")
    except Exception as exc:
        log(f"action_learning error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_email_orchestrator_flush():
    """
    Flush pending email intents through the orchestrator.

    Called after all email-producing phases (digest, lifecycle, followup,
    silence, revenue triggers). Resolves conflicts, enforces rate limits,
    merges compatible messages, and sends the winners.
    """
    from app.services.email_orchestrator import get_pending_intents, resolve_and_flush, clear_intents

    pending = get_pending_intents()
    if not pending:
        return

    db = SessionLocal()
    try:
        result = resolve_and_flush(db)
        db.commit()
        log(
            f"email_orchestrator: intents={result['total_intents']} "
            f"merchants={result['merchants']} sent={result['sent']} "
            f"deferred={result['deferred']} rate_limited={result['rate_limited']} "
            f"merged={result['merged']}"
        )
    except Exception as exc:
        log(f"email_orchestrator flush error (non-fatal): {exc}")
        db.rollback()
        clear_intents()  # Don't let failed intents accumulate
    finally:
        db.close()


def _run_billing_sync():
    """Verify Pro merchant billing state with Shopify. Runs weekly (Sunday only)."""
    from datetime import timezone as _tz
    now_dt = datetime.now(_tz.utc).replace(tzinfo=None)
    if now_dt.weekday() != 6:  # Sunday only
        return

    db = SessionLocal()
    try:
        from app.services.billing_sync import run_billing_sync
        result = run_billing_sync(db)
        db.commit()
        if result["checked"] > 0:
            log(f"billing_sync: checked={result['checked']} deactivated={result['deactivated']}")
    except Exception as exc:
        log(f"billing_sync error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _check_sentry_webhook_dark():
    """Check Sentry webhook health — alert if webhook goes dark."""
    db2 = SessionLocal()
    try:
        _check_sentry_webhook_health(db2)
        db2.commit()
    except Exception as exc:
        log(f"sentry_webhook_health error (non-fatal): {exc}")
        db2.rollback()
    finally:
        db2.close()


def _check_sentry_webhook_health(db):
    """Alert if Sentry webhook intake has gone dark while email is still receiving."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func
    from app.models.sentry_incident import SentryIncident

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_6h = now - timedelta(hours=6)

    # Count recent incidents by source
    source_counts = dict(
        db.query(SentryIncident.source_type, func.count(SentryIncident.id))
        .filter(SentryIncident.created_at >= cutoff_6h)
        .group_by(SentryIncident.source_type)
        .all()
    )

    webhook_count = source_counts.get("sentry_webhook", 0)
    email_count = source_counts.get("email", 0)

    # Alert condition: email is receiving but webhook is not
    # This means Sentry is sending alerts but webhook integration is broken/misconfigured
    if email_count > 0 and webhook_count == 0:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="agent_worker",
            alert_type="sentry_webhook_dark",
            summary=(
                f"Sentry webhook intake is DARK — {email_count} incidents via email fallback "
                f"in last 6h but 0 via webhook. Check Sentry webhook configuration."
            ),
            detail={
                "email_count_6h": email_count,
                "webhook_count_6h": webhook_count,
                "window_hours": 6,
            },
        )


def _run_on_alert_responder():
    """A8: autonomous triage of new critical ops_alerts.

    FRAMEWORK ONLY as of 2026-04-18 — the poll + context-packet logic
    ships behind env flag ON_ALERT_RESPONDER_ENABLED=0 (default).
    When enabled, Claude will triage each unresolved critical alert
    within the same agent_worker cycle it lands in (rather than
    waiting for the 08:00 daily brief).

    Flipping the flag to 1 requires founder sign-off on the money-spend
    scope (~€1-3/mo LLM estimated) per `on_alert_responder.py` docstring.
    """
    db = SessionLocal()
    try:
        from app.services.on_alert_responder import run as _run_responder
        report = _run_responder(db)
        if report.get("alerts_found", 0) > 0:
            log(
                f"on_alert_responder: mode={report['mode']} "
                f"found={report['alerts_found']} "
                f"contexts_built={report['contexts_built']} "
                f"llm_calls={report['llm_calls_made']}"
            )
    except Exception as exc:
        log(f"on_alert_responder error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_on_alert_responder failed: %s", exc)
    finally:
        db.close()


def _run_sentry_poller():
    """Pull active Sentry issues into the triage pipeline.

    The Sentry alert-rules YAML emails the founder on bursts/regressions
    but does NOT forward to /webhooks/sentry/inbound. Without this poll,
    those events stay in Sentry and never reach SentryIncident →
    consume_triage_queue → BugFixCandidate. Polling closes the loop with
    one path that doesn't depend on Gmail forwarding or per-rule webhook
    actions. Cooldown + dedup are handled inside the poller.
    """
    db = SessionLocal()
    try:
        from app.services.sentry_poller import poll_recent_issues
        result = poll_recent_issues(db)
        db.commit()
        if result.get("forwarded", 0) > 0 or result.get("parse_errors", 0) > 0:
            log(
                f"sentry_poller: status={result.get('status')} "
                f"polled={result.get('polled', 0)} "
                f"forwarded={result.get('forwarded', 0)} "
                f"stale={result.get('skipped_stale', 0)} "
                f"low_volume={result.get('skipped_low_volume', 0)} "
                f"parse_errors={result.get('parse_errors', 0)}"
            )
    except Exception as exc:
        log(f"sentry_poller error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception:
            pass  # SILENT-EXCEPT-OK: rollback after a poll error is best-effort
    finally:
        db.close()


def _run_sentry_triage():
    """Pull fresh Sentry issues + generate diagnostic triage packets.
    The bugfix-candidate creation phases (consume_triage_queue +
    reevaluate_skipped_families) were dropped with the old-brain
    Stage 2-E supersession — Sentry incidents are still ingested
    and analyzed for /ops view, but no longer feed the dead
    auto-fix pipeline."""
    _run_sentry_poller()

    db = SessionLocal()
    try:
        from app.services.sentry_triage import run_triage_generation
        result = run_triage_generation(db)
        db.commit()
        if result["generated"] > 0 or result["errors"] > 0:
            log(f"sentry_triage: generated={result['generated']} errors={result['errors']}")
    except Exception as exc:
        log(f"sentry_triage generation error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_stale_alert_cleanup():
    """Auto-resolve alerts older than 72h to prevent historical backlog spam."""
    db = SessionLocal()
    try:
        from app.services.alerting import resolve_stale_alerts
        resolved = resolve_stale_alerts(db)
        db.commit()
        if resolved > 0:
            log(f"stale_alert_cleanup: auto-resolved {resolved} alerts older than 48h")
    except Exception as exc:
        log(f"stale_alert_cleanup error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_stale_alert_cleanup failed: %s", exc)
    finally:
        db.close()


def _run_cto_health_check():
    """
    Phase 0: CTO Signal Layer.

    Runs FIRST every cycle. Pure operational intelligence:
    - Synthesizes health dimensions with trend detection
    - Stores state in Redis for /ops/system-health + circuit breaker + daily digest
    - Logs one-line summary for PM2 visibility
    - Telegram: ONLY for CRITICAL (system on fire). Everything else goes in daily digest.

    Strict boundaries:
    - OBSERVES only. Never prescribes, never patches, never overrides.
    - No LLM calls. No heavy queries. No side effects.
    """
    try:
        from app.services.system_health_synthesizer import (
            synthesize_health, format_telegram_signal, send_telegram_signal,
        )
        db = SessionLocal()
        try:
            health = synthesize_health(db)

            # One-line structured log (always)
            dims = " ".join(
                f"{d.name}={'OK' if d.status == 'healthy' else d.status.upper()}"
                for d in health.dimensions
            )
            log(f"[CTO] {health.overall_status.upper()} | {dims}")

            # Store in Redis (consumed by /ops/system-health, circuit breaker, daily digest)
            from app.core.redis_client import cache_set
            cache_set("hs:system_health", health.to_dict(), 900)

            # Telegram: ONLY for CRITICAL. Degraded/healthy goes in daily digest.
            if health.overall_status == "critical":
                send_telegram_signal(health)

        finally:
            db.close()
    except Exception as exc:
        log(f"[CTO] error (non-fatal): {exc}")


def _run_approval_expiry_sweep():
    """
    Expire stale pending action_approvals.

    Approvals are created with expires_at; they're flipped to 'expired' lazily
    on read by /approvals. Zombies accumulate if /approvals is never called.
    This sweep guarantees bounded lifetime regardless of operator attention.

    Runs every cycle (15 min) — cheap single-statement UPDATE.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import text as sql_text
        result = db.execute(sql_text(
            "UPDATE action_approvals SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at < now() "
            "RETURNING id"
        ))
        expired_ids = [r[0] for r in result.fetchall()]
        if expired_ids:
            try:
                from app.services.alerting import write_alert
                write_alert(
                    db,
                    alert_type="approval_expired_unhandled",
                    source="agent_worker",
                    severity="warning",
                    detail={
                        "expired_count": len(expired_ids),
                        "approval_ids": expired_ids[:10],
                        "message": (
                            f"{len(expired_ids)} action approval(s) expired without "
                            f"operator review. Check /approvals or Telegram /incidents."
                        ),
                    },
                )
            except Exception as exc:
                log.warning("agent_worker: _run_approval_expiry_sweep failed: %s", exc)
        db.commit()
        if expired_ids:
            log(f"approval_expiry_sweep: expired={len(expired_ids)}, escalated to ops_alert")
    except Exception as exc:
        log(f"approval_expiry_sweep error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_approval_expiry_sweep failed: %s", exc)
    finally:
        db.close()


def _run_approved_reminders():
    """
    Send Telegram reminders for action_approvals that are pending.

    Runs every cycle (15 min). Sends reminder every 30 min per approval
    via Redis cooldown key. Includes tappable Approve button.

    Stops reminding when action is approved, expired, or executed.
    """
    try:
        from app.core.redis_client import cache_get, cache_set
        db = SessionLocal()
        try:
            from sqlalchemy import text as sql_text

            from app.services.telegram_agent import send_message_with_buttons, is_configured
            if not is_configured():
                return

            from datetime import datetime, timezone

            # Pending action approvals (TIER_1 orchestrator actions)
            pending_actions = db.execute(sql_text("""
                SELECT id, action_type, target_id, reason, created_at::text, expires_at::text
                FROM action_approvals
                WHERE status = 'pending'
                ORDER BY created_at ASC
            """)).fetchall()

            for action in pending_actions:
                cooldown_key = f"hs:approval_reminder:{action.id}"
                if cache_get(cooldown_key) is not None:
                    continue

                expires_note = ""
                if action.expires_at:
                    try:
                        exp = datetime.fromisoformat(action.expires_at)
                        mins_left = int((exp - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 60)
                        if mins_left <= 0:
                            expires_note = " *EXPIRED*"
                        else:
                            expires_note = f" (expires in {mins_left}m)"
                    except Exception as exc:
                        log.warning("agent_worker: _run_approved_reminders failed: %s", exc)

                send_message_with_buttons(
                    f"*Action Approval #{action.id} pending*{expires_note}\n\n"
                    f"Action: {action.action_type}\n"
                    f"Target: {action.target_id or 'N/A'}\n"
                    f"Reason: {(action.reason or '')[:120]}\n\n"
                    f"Tap to approve and execute:",
                    [[{"text": f"Approve #{action.id}", "callback_data": f"/approve {action.id}"}]],
                )

                cache_set(cooldown_key, True, 1800)
                log(f"approved_reminder: sent for action #{action.id}")

        finally:
            db.close()
    except Exception as exc:
        log(f"approved_reminder error (non-fatal): {exc}")


def _run_regulatory_feed_monitor():
    """Fetch RSS feeds from worldwide DPAs and regulatory bodies.
    Keyword-matches new items and emits regulatory_update alerts.
    24h internal cooldown — safe to call every cycle."""
    try:
        from app.services.regulatory_feed_monitor import run_feed_monitor
        report = run_feed_monitor()
        if report.get("skipped"):
            return
        if report.get("items_new", 0) > 0:
            log(
                f"regulatory_feed: {report['items_new']} new items from "
                f"{report['feeds_checked']} feeds, {report['items_stored']} stored"
            )
    except Exception as exc:
        log(f"regulatory_feed error (non-fatal): {exc}")


def _run_regulatory_watch():
    """Worldwide regulatory compliance audit — deterministic checks
    against the live codebase and system state. Emits compliance_gap
    alerts that the bugfix pipeline can triage. 6h internal cooldown."""
    db = SessionLocal()
    try:
        from app.services.regulatory_watch import run_regulatory_audit
        report = run_regulatory_audit(db)
        if report.get("skipped"):
            return
        if report.get("failed", 0) > 0 or report.get("auto_resolved", 0) > 0:
            log(
                f"regulatory_watch: {report['passed']}/{report['total_rules']} passed, "
                f"{report['failed']} failed, {report.get('new_alerts', 0)} new alerts, "
                f"{report.get('auto_resolved', 0)} auto-resolved"
            )
    except Exception as exc:
        log(f"regulatory_watch error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_regulatory_watch failed: %s", exc)
    finally:
        db.close()


_STANDBY_REDIS_KEY = "hs:self_heal_standby"
# Phases that actively change code/state — gated by standby.
# Detection/observation phases (health, cleanup, digest) still run so the
# system remains observable while paused.
_STANDBY_SKIPPED_PHASES = {
    "bug_triage", "auto_propose", "auto_apply", "auto_merge",
    "evolution_audit", "evolution_conversion", "model_upgrade_scan",
    "pipeline_self_upgrade",
}


def is_self_heal_in_standby() -> bool:
    """Return True if the self-heal pipeline is in operator-requested standby.

    Controlled via Redis key `hs:self_heal_standby`. When set, code-mutating
    phases are skipped but observation/health phases continue, so the
    founder can verify health while changes are paused.
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("agent_worker.standby_read")
            return False
        return rc.exists(_STANDBY_REDIS_KEY) > 0
    except Exception as exc:
        log.warning("agent_worker: is_self_heal_in_standby failed: %s", exc)
        return False


def set_self_heal_standby(enabled: bool, reason: str = "") -> bool:
    """Enter or exit self-heal standby. Returns True on success.

    Standby is a soft pause: triage / propose / apply / merge / evolution
    phases are skipped. Health checks, digests, cleanup, and the heartbeat
    continue so the founder can monitor the paused state.
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("agent_worker.standby_write")
            return False
        if enabled:
            import json as _json
            payload = _json.dumps({
                "reason": reason or "standby",
                "entered_at": datetime.now(timezone.utc).isoformat(),
            })
            # REDIS-PERSIST-OK: operator self-heal standby flag — clears
            # only on explicit resume. A TTL would auto-resume the
            # pipeline mid-incident, which is the exact opposite of
            # what an operator hitting the kill switch wants.
            rc.set(_STANDBY_REDIS_KEY, payload)
        else:
            rc.delete(_STANDBY_REDIS_KEY)
        return True
    except Exception as exc:
        log(f"set_self_heal_standby error: {exc}")
        return False


def _run_analytics_retention():
    """β6: prune analytics_events older than retention window.
    Runs at most once per day — checked via Redis key."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None and rc.exists("hs:event_bus:cleanup_today"):
            return
    except Exception as exc:
        log.warning("agent_worker: _run_analytics_retention failed: %s", exc)
        rc = None

    db = SessionLocal()
    try:
        from app.services.event_bus import cleanup_old_events
        deleted = cleanup_old_events(db)
        if deleted > 0:
            log(f"event_bus_cleanup: deleted {deleted} old rows")
        if rc is not None:
            try:
                rc.setex("hs:event_bus:cleanup_today", 86400, "1")
            except Exception as exc:
                log.warning("agent_worker: _run_analytics_retention failed: %s", exc)
    except Exception as exc:
        log(f"event_bus_cleanup error (non-fatal): {exc}")
    finally:
        db.close()


def _run_worker_watchdog():
    """α5: resurrect PM2 workers that have fallen behind."""
    db = SessionLocal()
    try:
        from app.services.worker_watchdog import run_watchdog
        report = run_watchdog(db)
        db.commit()
        if report.get("stale", 0) > 0:
            log(
                f"worker_watchdog: stale={report['stale']} "
                f"restarted={report['restarted']} "
                f"cooldown={report['on_cooldown']}"
            )
    except Exception as exc:
        log(f"worker_watchdog error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception as exc:
            log.warning("agent_worker: _run_worker_watchdog failed: %s", exc)
    finally:
        db.close()


def _run_dashboard_asset_probe():
    """Runtime probe that detects served-HTML-vs-built-chunks drift.
    Born 2026-04-18 late after the landing rendered as unstyled white
    because a rebuild replaced on-disk chunks while the PM2 process
    kept its old in-memory manifest. Probe owns its own 5min cooldown
    via should_run(). Alert is deduped by UTC hour (Redis SETNX)."""
    from app.workers.tasks import dashboard_asset_probe_task
    if not dashboard_asset_probe_task.should_run():
        return
    try:
        dashboard_asset_probe_task.run()
    except Exception as exc:
        log(f"dashboard_asset_probe error (non-fatal): {exc}")
    finally:
        dashboard_asset_probe_task.mark_done()


def _run_email_dns_status_check():
    """Hourly: refresh Resend domain verification cache + flip detect.

    Born 2026-04-22 after 10 days of silent email suppression against
    hedgesparkhq.com. DNS verification lives outside our control (it
    depends on DNS records set at the registrar), so the self-healing
    pipeline cannot repair it — but it CAN surface the exact moment
    the founder's fix lands by refreshing the Resend status cache and
    firing a Telegram alert on `failed → verified` flip. Symmetric
    alert fires on `verified → failed` so a future regression surfaces
    within one hour instead of rotting for days.

    Self-gated to ~1 hour via the task module's should_run()."""
    from app.workers.tasks import email_dns_status_task
    if not email_dns_status_task.should_run():
        return
    try:
        email_dns_status_task.run()
    except Exception as exc:
        log(f"email_dns_status error (non-fatal): {exc}")
    finally:
        email_dns_status_task.mark_done()


def _run_dashboard_auto_remediation():
    """Deterministic auto-remediation for any unresolved
    `dashboard_asset_drift` alert. Runs `pm2 restart wishspark-dashboard
    --update-env`, re-probes assets, resolves origin alert on success
    or escalates on failure. Hourly rate-limited (max 3/hour).

    Kept separate from `_run_on_alert_responder` because the remedy is
    a shell command, not an LLM call — no budget gate, default ON."""
    from app.services.dashboard_auto_remediation import attempt
    db = SessionLocal()
    try:
        report = attempt(db)
        if report.get("action") in ("remediated", "escalated"):
            log(
                f"dashboard_auto_remediation: action={report['action']} "
                f"alert_id={report.get('alert_id')} "
                f"restart_ok={report.get('restart_ok')}"
            )
    except Exception as exc:
        log(f"dashboard_auto_remediation error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception:
            pass  # SILENT-EXCEPT-OK: rollback best-effort after primary error already logged
    finally:
        db.close()


@cron_monitor(slug="agent_worker_cycle", interval_minutes=15, max_runtime_minutes=25)
def run_cycle():
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    standby = is_self_heal_in_standby()
    if standby:
        log("run_cycle: SELF-HEAL STANDBY active — mutating phases skipped")

    # Phase 0-pre-1: Dashboard asset probe — 5 min cadence, catches the
    # "stale Next.js in-memory manifest" class of bugs that slip past
    # HTTP-200-on-/ monitors. Paired with auto-remediation below.
    _run_dashboard_asset_probe()

    # Phase 0-pre-1b: Deterministic auto-remediation for any unresolved
    # dashboard_asset_drift alert. Shell-only (pm2 restart), no LLM.
    _run_dashboard_auto_remediation()

    # Phase 0-pre-1c: Hourly Resend DNS verification poll. Fires
    # 🟢 / 🔴 Telegram alert on verified ↔ failed flip so founder
    # knows the instant a DNS fix lands (or breaks again).
    _run_email_dns_status_check()

    # Phase 0-pre0: Worker watchdog (α5) — resurrect stale workers FIRST
    _run_worker_watchdog()

    # Phase 0-pre1: Analytics retention (β6) — daily-gated, cheap
    _run_analytics_retention()

    # Phase 0-pre: Auto-resolve stale alerts (tiered cleanup) — runs always
    _run_stale_alert_cleanup()

    # Phase 0: CTO-level health synthesis (runs first, sets context)
    _run_cto_health_check()

    # Phase 0b: Remind operator about approved-but-not-applied candidates
    _run_approved_reminders()

    # Phase 0c: Expire stale pending action_approvals (bounded lifetime)
    _run_approval_expiry_sweep()

    # Phase 1: Orchestrator — reads alerts/state, executes safe actions
    _run_orchestrator()

    # Phase 1b: Brain Vero — per-merchant SENSE→SYNTHESIZE→DECIDE→
    # COORDINATE→LEARN cycle (`app/services/merchant_brain.py`).
    # Default OFF via MERCHANT_BRAIN_ENABLED=0 (un-park ceremony flips
    # on). Bounded: max 100 shops per cycle, 6h decision cooldown per
    # shop. Replaces the old immune-system-on-self brain (Stage 2-E
    # supersession 2026-05-07).
    _run_merchant_brain_tick()

    # Phase 2: Onboarding — ensure new merchants reach "ready" state
    _run_onboarding()

    # Phase 2b: Onboarding health — detect stuck merchants, pixel abandonment, slow activation
    _run_onboarding_health()

    # Phase 6: Entitlement health scan
    _run_entitlement_health_scan()

    # Phase 7: Daily snapshot + scaling recommendations
    _run_scaling_intelligence()

    # Phase 7c: Daily health digest to Telegram (24h cooldown)
    _run_daily_digest()

    # Phase 7d: Weekly merchant email digest (Monday, Europe/Rome)
    _run_merchant_digest()

    # Phase 7d-lite: Daily Lite morning brief email (08:00-09:59 Europe/
    # Rome) — pushes the brief so Lite merchants don't have to log in
    # to see it. Closes Gap A of the €39-ready sprint.
    _run_lite_morning_digest()

    # Phase 7d-ter: GDPR data retention sweep — daily, Europe/Rome.
    # Deletes events/visitor_purchase_sessions past their retention TTL.
    _run_data_retention()

    # Phase 7d-quater: GDPR SLA enforcement — every cycle. Emits CRITICAL
    # ops_alerts for any GdprRequest past its computed deadline
    # (shop_redact 48h, customer requests 30d).
    _run_gdpr_sla_enforcement()

    # Phase 7d-quinquies: Security synthetic heartbeat — hourly
    # self-attack probes. Fails loudly if OAuth / webhook / consent /
    # ops auth / export session rejections are missing.
    _run_security_heartbeat()

    # Phase 7d-sexies: Uninstall erasure watchdog — belt-and-braces
    # GDPR Art. 17 guarantee. Creates a shop_redact GdprRequest for any
    # uninstalled shop whose 48h grace has elapsed without Shopify
    # delivering shop/redact.
    _run_uninstall_erasure_watchdog()

    # Phase 7d-septies: Audit log integrity check — daily chain walk
    # that emits a CRITICAL alert on any tampering evidence.
    _run_audit_log_integrity_check()

    # Phase 7d-septies-bis: Invariant monitor — runs critical preflight
    # audits against the live source tree on every cycle. Closes the
    # "someone bypassed pre-commit hook" hole: if a structural
    # regression lands in main via --no-verify or merge conflict, this
    # emits an ops_alert within 15min. Rule 7 in bug_triage (≥3
    # recurrences) then auto-creates a BugFixCandidate after ~45min,
    # and the standard self-healing pipeline handles the rest. Pure
    # detect-only — never attempts to fix.
    _run_invariant_monitor()

    # Phase 7d-octies: Breach notification classifier — turns known
    # breach signatures into `breach_response_required` alerts with
    # GDPR Art. 33 clock started. Runs every cycle; dedup is row-level.
    _run_breach_classifier()

    # Phase 7d-nonies: Regulatory watch — worldwide compliance audit.
    # Deterministic checks against the live codebase + state. Emits
    # compliance_gap alerts that feed into the bugfix pipeline. 6h cooldown.
    _run_regulatory_watch()

    # Phase 7d-decies: Regulatory feed monitor — fetches RSS feeds from
    # worldwide DPAs and regulatory bodies. Keyword-matches new items
    # and emits regulatory_update alerts. 24h cooldown.
    _run_regulatory_feed_monitor()

    # Phase 7e: Lifecycle emails (setup_incomplete, first_insight, connection_issue)
    _run_lifecycle_emails()

    # Phase 7f: 48h follow-up emails (beta invite follow-ups)
    _run_followup_emails()

    # Phase 7g: Inbound email action executor — close the merchant reply loop
    _run_inbound_actions()

    # Phase 7h: Churn / silence detection — catch merchants going dark
    _run_silence_detection()

    # Phase 7i: Billing sync — verify Pro merchant charges with Shopify
    _run_billing_sync()

    # Phase 7j: Action execution agent — close the action loop
    _run_action_agent()

    # Phase 7k: Action learning — measure outcomes, feed back into scoring
    _run_action_learning()

    # Phase 7m: Email orchestrator flush — resolve conflicts across all email producers
    _run_email_orchestrator_flush()

    # Phase 7n: Sentry incident triage — generate AI debugging packets
    _run_sentry_triage()

    _run_on_alert_responder()

    # Phase 7k (post Stage 2-E supersession): sentry webhook dark check.
    _check_sentry_webhook_dark()

    # Phase 8: Sandbox analysis (original agent_worker logic)
    # This worker has no per-shop dimension — it selects the top-N
    # ProductOpportunity rows globally by priority_score.
    rows_written = 0
    errors = 0
    last_error: str | None = None

    log("starting agent cycle")

    # Separate session used only for worker_state and worker_log writes.
    # fetch_targets() manages its own session internally.
    db = SessionLocal()

    try:
        state = _load_state(db)

        targets = fetch_targets()
        log(f"planned targets: {len(targets)}")

        for target in targets:
            try:
                goal = target.get("goal", "analyze product opportunity")

                run = create_sandbox_run(
                    goal=goal,
                    payload=target,
                )

                run_id = run["run_id"]
                run_path = run["sandbox_path"]

                log(f"created sandbox run {run_id}")

                analysis = build_product_intelligence(goal=goal, payload=target)
                write_analysis(run_path, analysis)
                log(f"analysis written for {run_id}")

                write_report(run_path, goal, analysis)
                log(f"report written for {run_id}")

                update_sandbox_status(run_id, "planned")
                log("sandbox status updated to planned")

                rows_written += 1

            except Exception as e:
                errors += 1
                last_error = str(e)
                log(f"error: {e}")

        _save_state(db, state)
        log(f"cycle finished — rows_written={rows_written} errors={errors}")

    except Exception as exc:
        errors += 1
        last_error = str(exc)
        log(f"cycle-level error: {exc}")
        raise   # worker_log is written by finally, then main() re-raises for PM2

    finally:
        try:
            _write_log(
                db,
                started_at=started_at,
                shops_processed=0,
                rows_written=rows_written,
                errors=errors,
                error_detail=last_error,
            )
        except Exception as exc:
            log(f"worker_log write error (non-fatal): {exc}")
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log("agent worker started")

    while True:
        from app.core.distributed_lock import worker_lock
        from app.core.metrics import track_worker_cycle

        with worker_lock(WORKER_NAME, ttl_seconds=SLEEP_SECONDS + 60) as acquired:
            if not acquired:
                log("another instance holds the lock — skipping cycle")
            else:
                try:
                    with track_worker_cycle(WORKER_NAME):
                        run_cycle()
                except Exception as exc:
                    log(f"unhandled exception: {exc}")
                    raise

        log(f"sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
