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
from app.sandbox.sandbox_executor import create_sandbox_run, update_sandbox_status
from app.services.product_intelligence_engine import build_product_intelligence

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def log(msg):
    print(f"[AGENT_WORKER] {datetime.now(timezone.utc).isoformat()} | {msg}")


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


def run_cycle():
    log("starting agent cycle")

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

        except Exception as e:
            log(f"error: {e}")

    log("cycle finished")


def main():
    log("agent worker started")

    while True:
        run_cycle()
        log("sleeping 15 minutes")
        time.sleep(900)


if __name__ == "__main__":
    main()
