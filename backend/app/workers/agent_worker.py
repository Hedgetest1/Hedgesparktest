import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.append("/opt/wishspark/backend")

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

def log(msg):
    print(f"[AGENT_WORKER] {datetime.now(timezone.utc).isoformat()} | {msg}", flush=True)


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

def run_cycle():
    started_at = datetime.utcnow()
    # This worker has no per-shop dimension — it selects the top-N
    # ProductOpportunity rows globally by priority_score.
    # shops_processed is always 0.
    # rows_written counts targets that completed all four steps without error
    # (create_sandbox_run, write_analysis, write_report, update_sandbox_status).
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
