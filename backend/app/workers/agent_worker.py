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
    state.last_run_at = datetime.utcnow()
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
    finished_at = datetime.utcnow()
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
    """Scan for new bug-worthy events, create candidates, auto-propose patches."""
    db = SessionLocal()
    try:
        from app.services.bugfix_pipeline import run_bug_triage, run_auto_propose, run_auto_apply

        # Phase A: Triage
        triage = run_bug_triage(db)
        db.commit()
        if triage["created"] > 0:
            log(f"bug_triage: created={triage['created']} deduped={triage['deduped']}")

        # Phase B: Auto-propose
        propose = run_auto_propose(db)
        db.commit()
        if propose["attempted"] > 0:
            log(f"auto_propose: proposed={propose['proposed']} failed={propose['failed']}")

        # Phase C: Auto-apply TIER_0 patches
        auto = run_auto_apply(db)
        db.commit()
        if auto["attempted"] > 0:
            log(f"auto_apply: applied={auto['applied']} failed={auto['failed']}")

        # Phase D: Auto-promote (branch → push → CI poll → PR)
        from app.services.promotion_pipeline import run_auto_promotion
        promo = run_auto_promotion(db)
        db.commit()
        if promo.get("pushed", 0) > 0 or promo.get("prs_created", 0) > 0:
            log(f"auto_promotion: pushed={promo['pushed']} prs={promo['prs_created']}")

    except Exception as exc:
        log(f"bug_triage error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def run_cycle():
    started_at = datetime.utcnow()

    # Phase 1: Orchestrator — reads alerts/state, executes safe actions
    _run_orchestrator()

    # Phase 2: Onboarding — ensure new merchants reach "ready" state
    _run_onboarding()

    # Phase 3: Bug triage — scan alerts/outcomes for code-fix candidates
    _run_bug_triage()

    # Phase 4: Sandbox analysis (original agent_worker logic)
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
