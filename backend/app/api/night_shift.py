"""
night_shift.py — Phase Ω⁵ Night Shift Agent API.

  GET  /pro/night-shift/latest        — latest cached report
  POST /pro/night-shift/run            — force re-run for the caller (debug)
  POST /pro/night-shift/apply          — mark the suggested action as accepted
"""
from __future__ import annotations

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

    # Fire an audit trail entry; existing orchestrator layers can pick it up
    try:
        from sqlalchemy import text
        db.execute(
            text(
                """
                INSERT INTO audit_log (shop_domain, actor, action, target, detail, created_at)
                VALUES (:shop, 'night_shift_agent', 'apply_suggested_action',
                        :target, :detail, NOW())
                """
            ),
            {
                "shop": shop,
                "target": top.get("kind") or "night_shift_action",
                "detail": __import__("json").dumps(top),
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, "applied": top}
