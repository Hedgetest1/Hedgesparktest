"""
community_marketplace.py — Phase Ω''' marketplace API.

  GET    /pro/marketplace/templates          — list (vertical-aware)
  POST   /pro/marketplace/templates          — publish
  POST   /pro/marketplace/templates/{id}/clone   — clone into the merchant's account
  POST   /pro/marketplace/templates/{id}/upvote  — upvote (+1)
  DELETE /pro/marketplace/templates/{id}     — unpublish (author only)
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["community_marketplace"])


class PublishIn(BaseModel):
    template_type: Literal["nudge", "rule"]
    title: str = Field(..., min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    vertical: str = Field(default="other", max_length=32)
    payload: dict
    author_label: str | None = Field(default=None, max_length=120)


class MarketplaceTemplatesListResponse(BaseModel):
    templates: list[dict[str, Any]] = Field(default_factory=list)


class PublishResponse(BaseModel):
    id: int
    title: str
    template_type: str


class CloneResponse(BaseModel):
    ok: bool
    error: str | None = None
    template_id: int | None = None
    template_type: str | None = None
    title: str | None = None
    payload: dict[str, Any] | None = None
    first_clone: bool | None = None


@router.get("/pro/marketplace/templates", response_model=MarketplaceTemplatesListResponse)
def list_templates_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    template_type: str | None = Query(default=None, pattern="^(nudge|rule)$"),
    vertical: str | None = None,
    sort: str = Query(default="popular", pattern="^(popular|recent|upvotes)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    from app.services.community_marketplace import list_templates
    from app.core.feature_usage import track
    track("community_marketplace", shop)
    # If the caller didn't specify a vertical, infer from their shop classification
    if not vertical:
        try:
            from app.services.vertical_classifier import get_vertical
            vertical = get_vertical(db, shop)
        except Exception:
            vertical = None
    return {"templates": list_templates(db, template_type=template_type, vertical=vertical, sort=sort, limit=limit)}


@router.post("/pro/marketplace/templates", response_model=PublishResponse)
def publish_endpoint(
    payload: PublishIn,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.community_marketplace import publish
    try:
        t = publish(
            db,
            author_shop=shop,
            template_type=payload.template_type,
            title=payload.title,
            description=payload.description,
            vertical=payload.vertical,
            payload=payload.payload,
            author_label=payload.author_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": t.id, "title": t.title, "template_type": t.template_type}


@router.post("/pro/marketplace/templates/{template_id}/clone", response_model=CloneResponse)
def clone_endpoint(
    template_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.community_marketplace import clone_template
    out = clone_template(db, template_id, shop)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("error", "not_found"))
    return out


@router.post("/pro/marketplace/templates/{template_id}/upvote", response_model=OkResponse)
def upvote_endpoint(
    template_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.community_marketplace import upvote
    if not upvote(db, template_id):
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@router.delete("/pro/marketplace/templates/{template_id}", response_model=OkResponse)
def unpublish_endpoint(
    template_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.community_marketplace import unpublish
    ok = unpublish(db, template_id, shop)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found_or_forbidden")
    return {"ok": True}
