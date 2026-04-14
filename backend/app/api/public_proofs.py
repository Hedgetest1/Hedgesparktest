"""
public_proofs.py — Public-facing proof page API + share creation.

GET  /public/proof/{token}    — unauthenticated, returns proof for rendering
POST /public/proof/{token}/event — track CTA clicks, installs
POST /pro/shares              — authenticated, creates a new share
GET  /pro/shares              — authenticated, lists merchant's shares
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["shares"])


# ── Public endpoints (no auth) ──

@router.get("/public/proof/{token}")
def get_public_proof(token: str, db: Session = Depends(get_db)):
    """Public proof page data. No authentication required."""
    from app.services.share_engine import get_public_proof
    result = get_public_proof(db, token)
    if not result:
        raise HTTPException(404, "Proof not found or expired")
    return result


class ShareEventPayload(BaseModel):
    event_type: str = Field(..., max_length=32)  # "click_cta" or "install"
    channel: str | None = Field(None, max_length=64)
    referrer: str | None = Field(None, max_length=2048)


@router.post("/public/proof/{token}/event")
def track_share_event(token: str, payload: ShareEventPayload, db: Session = Depends(get_db)):
    """Track a share event (CTA click, install attribution)."""
    if payload.event_type not in ("click_cta", "install", "share"):
        raise HTTPException(400, "Invalid event_type")
    from app.services.share_engine import track_share_action
    track_share_action(db, token, payload.event_type, payload.channel, payload.referrer)
    return {"ok": True}


# ── Authenticated endpoints (Pro merchants) ──

class CreateSharePayload(BaseModel):
    nudge_id: int | None = None
    window_hours: int = 168


@router.post("/pro/shares")
def create_share(
    payload: CreateSharePayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Create a shareable proof link."""
    from app.services.share_engine import generate_share
    result = generate_share(db, shop, payload.nudge_id, payload.window_hours)
    if not result:
        raise HTTPException(404, "No proof data available to share")
    return result


@router.get("/pro/shares")
def list_shares(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """List merchant's active shares."""
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT share_token, headline, proof_type, view_count, click_cta_count,
                   installs_attributed, created_at, expires_at
            FROM public_proof_shares
            WHERE shop_domain = :shop
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at DESC
            LIMIT 20
        """),
        {"shop": shop},
    ).fetchall()

    return [
        {
            "share_token": r[0],
            "share_url": f"https://app.hedgesparkhq.com/proof/{r[0]}",
            "headline": r[1],
            "proof_type": r[2],
            "views": r[3],
            "cta_clicks": r[4],
            "installs": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "expires_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]
