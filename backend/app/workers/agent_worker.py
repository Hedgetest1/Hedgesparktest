import logging
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.append("/opt/wishspark/backend")

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

        # Phase B2: Evaluate merge outcomes
        from app.services.merge_intelligence import evaluate_merge_outcomes
        merge_eval = evaluate_merge_outcomes(db)
        db.commit()
        if merge_eval["evaluated"] > 0:
            log(f"merge_eval: healthy={merge_eval['healthy']} regressed={merge_eval['regressed']}")
    except Exception as exc:
        log(f"orchestrator error (non-fatal): {exc}")
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


def _run_bug_triage():
    """Scan for new bug-worthy events, create candidates, auto-propose, auto-apply, auto-promote."""
    db = SessionLocal()
    try:
        from app.services.bugfix_pipeline import run_bug_triage, run_auto_propose, run_auto_apply
        from app.services.promotion_pipeline import run_auto_promotion

        # Each phase is isolated — one failure does not kill the others
        for phase_name, phase_fn in [
            ("triage", lambda: run_bug_triage(db)),
            ("auto_propose", lambda: run_auto_propose(db)),
            ("auto_apply", lambda: run_auto_apply(db)),
            ("auto_promotion", lambda: run_auto_promotion(db)),
        ]:
            try:
                result = phase_fn()
                db.commit()
                # Log only when something happened
                if isinstance(result, dict):
                    interesting = {k: v for k, v in result.items() if v and k not in ("scanned",)}
                    if interesting:
                        log(f"{phase_name}: {interesting}")
            except Exception as exc:
                log(f"{phase_name} error (non-fatal): {exc}")
                db.rollback()

    finally:
        db.close()


def _run_bugfix_outcome_eval():
    """Evaluate applied bugfix outcomes (48h delay, closed-loop learning)."""
    db = SessionLocal()
    try:
        from app.services.evolution_outcomes import evaluate_bugfix_outcomes
        summary = evaluate_bugfix_outcomes(db)
        db.commit()
        if summary["evaluated"] > 0:
            log(f"bugfix_outcomes: evaluated={summary['evaluated']} effective={summary['effective']} ineffective={summary['ineffective']}")
    except Exception as exc:
        log(f"bugfix_outcomes error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_evolution_audit():
    """Run weekly evolution audit if cooldown expired."""
    from app.services.evolution_engine import should_run_audit, run_evolution_audit, mark_audit_run
    if not should_run_audit():
        return
    db = SessionLocal()
    try:
        summary = run_evolution_audit(db)
        db.commit()
        mark_audit_run()
        if summary["new"] > 0:
            log(f"evolution: new={summary['new']} deduped={summary['deduped']}")
    except Exception as exc:
        log(f"evolution error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_model_upgrade_scan():
    """Scan for model upgrade candidates (weekly, config-driven)."""
    from app.services.model_upgrade_agent import should_run_scan, scan_for_upgrades, mark_scan_run
    if not should_run_scan():
        return
    db = SessionLocal()
    try:
        summary = scan_for_upgrades(db)
        db.commit()
        mark_scan_run()
        if summary["created"] > 0:
            log(f"model_upgrade: created={summary['created']}")
    except Exception as exc:
        log(f"model_upgrade error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_meta_review():
    """Run weekly meta-review for strategic prioritization."""
    from app.services.meta_reviewer import should_run_meta_review, run_meta_review, mark_meta_review_run
    if not should_run_meta_review():
        return
    db = SessionLocal()
    try:
        result = run_meta_review(db)
        db.commit()
        mark_meta_review_run()
        if result.get("status") == "completed":
            review = result.get("review", {})
            log(f"meta_review: focus={review.get('weekly_focus_area', '?')} proposals={result.get('review', {}).get('priorities', []).__len__()} conflicts={len(review.get('conflicts', []))}")
        elif result.get("reason"):
            log(f"meta_review: skipped ({result['reason']})")
    except Exception as exc:
        log(f"meta_review error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_evolution_conversion():
    """Convert eligible LEVEL_1 evolution proposals into bugfix candidates."""
    db = SessionLocal()
    try:
        from app.services.evolution_converter import convert_eligible_proposals
        summary = convert_eligible_proposals(db)
        db.commit()
        if summary["converted"] > 0:
            log(f"evolution_convert: converted={summary['converted']} dedup={summary['skipped_dedup']}")
    except Exception as exc:
        log(f"evolution_convert error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_evolution_gc():
    """Run daily evolution proposal garbage collection if cooldown expired."""
    from app.services.evolution_gc import should_run_gc, run_evolution_gc, mark_gc_run
    if not should_run_gc():
        return
    db = SessionLocal()
    try:
        summary = run_evolution_gc(db)
        db.commit()
        mark_gc_run()
        total = summary["obsolete"] + summary["resolved_indirectly"] + summary["needs_revalidation"]
        if total > 0:
            log(f"evolution_gc: obsolete={summary['obsolete']} resolved={summary['resolved_indirectly']} revalidate={summary['needs_revalidation']}")
    except Exception as exc:
        log(f"evolution_gc error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_monthly_evolution_audit():
    """Run monthly Opus evolution audit if cooldown expired. Send Telegram summary."""
    from app.services.monthly_evolution_audit import should_run_monthly_audit, run_monthly_opus_audit, mark_monthly_audit_run
    if not should_run_monthly_audit():
        return
    db = SessionLocal()
    try:
        result = run_monthly_opus_audit(db)
        db.commit()

        status = result.get("status", "skipped")
        actually_ran = status == "completed" and result["proposals_created"] > 0

        # Only mark cooldown if audit actually executed (LLM was called)
        if status != "skipped":
            mark_monthly_audit_run()

        if actually_ran:
            log(f"monthly_opus_audit: created={result['proposals_created']} cycle={result.get('cycle')}")
        elif status == "skipped":
            log(f"monthly_opus_audit: skipped — {result.get('reason', 'unknown')}")

        # Send Telegram summary ONLY after real execution
        try:
            from app.services.telegram_agent import send_monthly_report, send_message, is_configured
            if is_configured():
                if actually_ran:
                    from app.services.system_summary import build_system_summary
                    summary = build_system_summary(db)
                    send_monthly_report(result.get("proposals", []), summary)
                elif status == "skipped":
                    send_message(
                        f"*Monthly Opus Audit* \u2014 skipped\n\n"
                        f"Reason: {result.get('reason', 'unknown')}\n"
                        f"No proposals generated. No LLM call was made."
                    )
        except Exception as exc:
            log(f"telegram report error (non-fatal): {exc}")

    except Exception as exc:
        log(f"monthly_opus_audit error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


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
                    from app.services.telegram_agent import send_scaling_alert, send_reviewer_verdict, is_configured
                    if is_configured():
                        forecast = build_forecast(db)
                        # Only send full alert if forecast has real data
                        if forecast.get("status") == "ok":
                            for r in recs[:2]:  # max 2 notifications
                                send_scaling_alert(r, forecast)
                                # Attach reviewer assessment
                                try:
                                    from app.services.reviewer_layer import review_entity
                                    rec_id = r.get("id")
                                    if rec_id:
                                        assessment = review_entity(db, "scaling_recommendation", rec_id)
                                        if assessment:
                                            db.flush()
                                            send_reviewer_verdict(assessment, entity_title=r.get("title"))
                                except Exception:
                                    pass
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


def _run_entitlement_health_scan():
    """Scan all active merchants for plan/billing mismatches. Create ops alerts for issues."""
    db = SessionLocal()
    try:
        from app.models.merchant import Merchant
        from app.services.merchant_chatbot import check_entitlement_health

        merchants = (
            db.query(Merchant)
            .filter(Merchant.install_status == "active")
            .all()
        )

        issues_found = 0
        for m in merchants:
            health = check_entitlement_health(db, m.shop_domain)
            if not health["healthy"]:
                issues_found += 1
                try:
                    from app.services.alerting import write_alert
                    # Dedup: check if alert already exists for this shop
                    from app.models.ops_alert import OpsAlert
                    existing = (
                        db.query(OpsAlert)
                        .filter(
                            OpsAlert.shop_domain == m.shop_domain,
                            OpsAlert.alert_type == "entitlement_mismatch",
                            OpsAlert.resolved_at.is_(None),
                        )
                        .first()
                    )
                    if not existing:
                        write_alert(
                            db, severity="warning", source="entitlement_scan",
                            alert_type="entitlement_mismatch",
                            summary=f"Entitlement mismatch: {health['issues']}",
                            shop_domain=m.shop_domain,
                        )
                except Exception:
                    pass

        if issues_found > 0:
            log(f"entitlement_scan: {issues_found} mismatches found across {len(merchants)} merchants")
        db.commit()
    except Exception as exc:
        log(f"entitlement_scan error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_brain_refresh():
    """Refresh the project brain snapshot if cooldown expired (daily)."""
    from app.services.project_brain import should_refresh_brain, build_full_snapshot, mark_brain_refreshed
    if not should_refresh_brain():
        return
    db = SessionLocal()
    try:
        snapshot = build_full_snapshot(db)
        db.commit()
        mark_brain_refreshed()
        log(f"brain_refresh: id={snapshot.id} files={snapshot.total_files} critical={snapshot.critical_files}")
    except Exception as exc:
        log(f"brain_refresh error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def run_cycle():
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Phase 1: Orchestrator — reads alerts/state, executes safe actions
    _run_orchestrator()

    # Phase 2: Onboarding — ensure new merchants reach "ready" state
    _run_onboarding()

    # Phase 3: Bug triage — scan alerts/outcomes for code-fix candidates
    _run_bug_triage()

    # Phase 3b: Bugfix outcome evaluation (closed-loop learning)
    _run_bugfix_outcome_eval()

    # Phase 4: Evolution audit (weekly) + meta-review + convert eligible proposals + model upgrade scan
    _run_evolution_audit()
    _run_meta_review()
    _run_evolution_conversion()
    _run_model_upgrade_scan()

    # Phase 4b: Evolution GC (daily) — clean stale/duplicate/resolved proposals
    _run_evolution_gc()

    # Phase 5: Monthly Opus evolution audit (30-day cooldown)
    _run_monthly_evolution_audit()

    # Phase 6: Entitlement health scan
    _run_entitlement_health_scan()

    # Phase 7: Daily snapshot + scaling recommendations
    _run_scaling_intelligence()

    # Phase 7b: Project brain refresh (daily)
    _run_brain_refresh()

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
        try:
            run_cycle()
        except Exception as exc:
            # Unhandled exception — log and let PM2 restart the process.
            log(f"unhandled exception: {exc}")
            raise

        log(f"sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
