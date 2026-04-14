"""
night_shift.py — Phase Ω⁵ Night Shift Agent API.

  GET  /pro/night-shift/latest        — latest cached report
  GET  /pro/night-shift/history       — archive of last N nights
  GET  /pro/night-shift/timeline      — autonomous actions taken while you slept
  POST /pro/night-shift/run            — force re-run for the caller (debug)
  POST /pro/night-shift/apply          — mark the suggested action as accepted
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["night_shift"])


@router.get("/pro/night-shift/latest")
def get_latest(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Return the most recent night shift report. If nothing is cached yet
    (e.g. first morning after enabling Pro), generate one on-demand so
    the morning card is never empty.
    """
    from app.services.night_shift_agent import get_latest_for_shop, generate_for_shop
    from app.core.feature_usage import track
    track("night_shift_agent", shop)
    doc = get_latest_for_shop(shop)
    if doc is None:
        doc = generate_for_shop(db, shop, force=False)
    return doc


@router.post("/pro/night-shift/run")
def force_run(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Force a fresh run, bypassing the per-day cache."""
    from app.services.night_shift_agent import generate_for_shop
    return generate_for_shop(db, shop, force=True)


@router.get("/pro/night-shift/history")
def get_history(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    limit: int = 14,
):
    """Return the most recent N nights from persistent archive."""
    from sqlalchemy import text
    rows = db.execute(
        text(
            """
            SELECT day, status, headline, sleep_confidence, sleep_confidence_label, generated_at
            FROM night_shift_reports
            WHERE shop_domain = :shop
            ORDER BY day DESC
            LIMIT :lim
            """
        ),
        {"shop": shop, "lim": max(1, min(60, limit))},
    ).fetchall()
    return {
        "shop_domain": shop,
        "reports": [
            {
                "day": r[0],
                "status": r[1],
                "headline": r[2],
                "sleep_confidence": r[3],
                "sleep_confidence_label": r[4],
                "generated_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ],
    }


@router.get("/pro/night-shift/timeline")
def get_timeline(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Merchant-facing timeline of autonomous actions HedgeSpark took on
    their store in the last 24 hours and 7 days. Reads the
    autonomous_actions table directly (source of truth for every
    decision the autonomous loop made) and groups the entries into
    "overnight" (last 24h) and "this week" (last 7d) buckets so the
    merchant can scan the recent work at a glance.

    Every row is keyed by the same (shop_domain, id) pair the
    autonomous loop uses internally, so nothing is invented and the
    merchant can always trace a timeline entry back to a real
    decision and its measured outcome.
    """
    from app.models.autonomous_action import AutonomousAction

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_24h = now_naive - timedelta(hours=24)
    cutoff_7d = now_naive - timedelta(days=7)

    # Single query, python-side bucketing — smaller code surface than two
    # near-identical queries and the row count is bounded by the 7-day
    # window so there's no scale concern.
    rows = (
        db.query(AutonomousAction)
        .filter(
            AutonomousAction.shop_domain == shop,
            AutonomousAction.created_at >= cutoff_7d,
        )
        .order_by(AutonomousAction.created_at.desc())
        .limit(200)
        .all()
    )

    def _serialize(row: AutonomousAction) -> dict:
        # Observed impact: prefer measurement_end (completed) over deployed_at,
        # fall back to created_at so the UI always has a timestamp to render.
        at_iso = (
            row.measurement_end.isoformat()
            if row.measurement_end
            else row.deployed_at.isoformat()
            if row.deployed_at
            else row.created_at.isoformat()
            if row.created_at
            else None
        )
        # Human-readable outcome color hint for the frontend without
        # hard-coding palette logic in the DB layer.
        if row.outcome == "positive":
            verdict = "win"
        elif row.outcome == "negative":
            verdict = "loss"
        elif row.outcome == "neutral":
            verdict = "neutral"
        elif row.status == "rolled_back":
            verdict = "rollback"
        elif row.status in ("measuring", "deployed"):
            verdict = "measuring"
        else:
            verdict = "pending"
        return {
            "id": row.id,
            "at": at_iso,
            "status": row.status,
            "action_type": row.action_type,
            "nudge_type": row.nudge_type,
            "signal_type": row.signal_type,
            "product_url": row.product_url,
            "decision_reason": row.decision_reason,
            "risk_level": row.risk_level,
            "lift_pct": row.lift_pct,
            "p_value": row.p_value,
            "visitors_measured": row.visitors_measured,
            "outcome": row.outcome,
            "verdict": verdict,
            "rollback_reason": row.rollback_reason,
        }

    overnight: list[dict] = []
    this_week: list[dict] = []
    for row in rows:
        anchor = row.created_at or row.deployed_at
        serialized = _serialize(row)
        if anchor and anchor >= cutoff_24h:
            overnight.append(serialized)
        this_week.append(serialized)

    # Aggregate what landed (positive outcomes) so the card hero can
    # brag without inventing a number.
    positive_lifts = [
        float(r["lift_pct"])
        for r in this_week
        if r["outcome"] == "positive" and r["lift_pct"] is not None
    ]
    avg_positive_lift_pct = (
        round(sum(positive_lifts) / len(positive_lifts), 2)
        if positive_lifts
        else None
    )
    wins_week = sum(1 for r in this_week if r["outcome"] == "positive")
    losses_week = sum(1 for r in this_week if r["outcome"] == "negative")
    neutral_week = sum(1 for r in this_week if r["outcome"] == "neutral")
    measuring_week = sum(1 for r in this_week if r["verdict"] == "measuring")

    return {
        "shop_domain": shop,
        "overnight": overnight,
        "this_week": this_week,
        "summary": {
            "actions_overnight": len(overnight),
            "actions_week": len(this_week),
            "wins_week": wins_week,
            "losses_week": losses_week,
            "neutral_week": neutral_week,
            "measuring_week": measuring_week,
            "avg_positive_lift_pct": avg_positive_lift_pct,
        },
    }


@router.post("/pro/night-shift/apply")
def apply_action(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Accept the suggested action from the latest report. For now this
    records intent and surfaces it to the action pipeline — execution
    happens through existing action_executor paths.
    """
    from app.services.night_shift_agent import get_latest_for_shop
    doc = get_latest_for_shop(shop)
    if not doc:
        raise HTTPException(404, "No night shift report available")
    top = doc.get("top_action")
    if not top:
        raise HTTPException(400, "No suggested action in the latest report")

    # Fire an audit trail entry via the canonical write_audit_log helper
    # so we stay on the same column contract the rest of the codebase uses.
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="worker",
            actor_name="night_shift_agent",
            action_type="apply_suggested_action",
            target_type=top.get("kind") or "night_shift_action",
            target_id=str(top.get("source") or top.get("label") or "unknown")[:256],
            shop_domain=shop,
            before_state=None,
            after_state=top,
            status="completed",
            approval_mode="human_approved",
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, "applied": top}
