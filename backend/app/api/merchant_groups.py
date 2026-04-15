"""
merchant_groups.py — Phase Ω'' multi-store API.

  POST   /pro/groups                       — create
  GET    /pro/groups                       — list (by current shop's owner_email)
  POST   /pro/groups/{id}/members          — add shop
  DELETE /pro/groups/{id}/members/{shop}   — remove
  GET    /pro/groups/{id}/dashboard        — consolidated metrics
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session
from app.models.merchant import Merchant
from app.models.merchant_group import MerchantGroup

router = APIRouter(tags=["merchant_groups"])


class GroupCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    base_currency: str = Field(default="EUR", max_length=8)


class MemberAddIn(BaseModel):
    shop_domain: str = Field(..., max_length=255)
    label: str | None = Field(default=None, max_length=120)
    is_primary: bool = False


class GroupCreateResponse(BaseModel):
    id: int
    name: str
    owner_email: str


class GroupMemberRow(BaseModel):
    shop_domain: str
    label: str | None = None
    is_primary: bool | None = None


class GroupRow(BaseModel):
    id: int
    name: str
    description: str | None = None
    base_currency: str | None = None
    members: list[GroupMemberRow] = Field(default_factory=list)


class GroupListResponse(BaseModel):
    groups: list[GroupRow] = Field(default_factory=list)


class MemberAddResponse(BaseModel):
    id: int
    shop_domain: str
    label: str | None = None
    is_primary: bool | None = None


class GroupDashboardResponse(BaseModel):
    group_id: int
    name: str
    base_currency: str | None = None
    lookback_days: int | None = None
    members: list[dict[str, Any]] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    top_shop: dict[str, Any] | None = None
    generated_at: str | None = None
    error: str | None = None


def _owner_email_for(db: Session, shop: str) -> str:
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).one_or_none()
    if not m or not m.contact_email:
        raise HTTPException(status_code=400, detail="merchant_email_unknown")
    return m.contact_email


@router.post("/pro/groups", response_model=GroupCreateResponse)
def create_group_endpoint(
    payload: GroupCreateIn,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.merchant_groups import create_group, add_member
    email = _owner_email_for(db, shop)
    g = create_group(db, name=payload.name, owner_email=email,
                     description=payload.description, base_currency=payload.base_currency)
    add_member(db, g.id, shop, label="primary", is_primary=True)
    return {"id": g.id, "name": g.name, "owner_email": g.owner_email}


@router.get("/pro/groups", response_model=GroupListResponse)
def list_groups_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.merchant_groups import list_groups_for_owner, list_members
    email = _owner_email_for(db, shop)
    groups = list_groups_for_owner(db, email)
    return {
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "base_currency": g.base_currency,
                "members": [
                    {
                        "shop_domain": m.shop_domain,
                        "label": m.label,
                        "is_primary": m.is_primary,
                    }
                    for m in list_members(db, g.id)
                ],
            }
            for g in groups
        ]
    }


@router.post("/pro/groups/{group_id}/members", response_model=MemberAddResponse)
def add_member_endpoint(
    group_id: int,
    payload: MemberAddIn,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.merchant_groups import add_member
    g = db.get(MerchantGroup, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="not_found")
    email = _owner_email_for(db, shop)
    if g.owner_email != email:
        raise HTTPException(status_code=403, detail="forbidden")
    m = add_member(db, group_id, payload.shop_domain, label=payload.label, is_primary=payload.is_primary)
    return {"id": m.id, "shop_domain": m.shop_domain, "label": m.label, "is_primary": m.is_primary}


@router.delete("/pro/groups/{group_id}/members/{shop_domain}", response_model=OkResponse)
def remove_member_endpoint(
    group_id: int,
    shop_domain: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.merchant_groups import remove_member
    g = db.get(MerchantGroup, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="not_found")
    email = _owner_email_for(db, shop)
    if g.owner_email != email:
        raise HTTPException(status_code=403, detail="forbidden")
    ok = remove_member(db, group_id, shop_domain)
    if not ok:
        raise HTTPException(status_code=404, detail="member_not_found")
    return {"ok": True}


@router.get("/pro/groups/{group_id}/dashboard", response_model=GroupDashboardResponse)
def group_dashboard_endpoint(
    group_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    lookback_days: int = Query(30, ge=1, le=365),
):
    from app.services.merchant_groups import get_group_dashboard
    g = db.get(MerchantGroup, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="not_found")
    email = _owner_email_for(db, shop)
    if g.owner_email != email:
        raise HTTPException(status_code=403, detail="forbidden")
    return get_group_dashboard(db, group_id, lookback_days=lookback_days)
