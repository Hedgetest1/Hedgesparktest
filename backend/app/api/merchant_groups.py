"""
merchant_groups.py — Multi-store consolidation API (Lite-accessible).

  POST   /merchant/groups                       — create
  GET    /merchant/groups                       — list (by current shop's owner_email)
  POST   /merchant/groups/{id}/members          — add shop
  DELETE /merchant/groups/{id}/members/{shop}   — remove
  GET    /merchant/groups/{id}/dashboard        — consolidated metrics

Originally gated to Pro tier (Phase Ω'') and never wired into the UI;
flipped to Lite per the $0-60 parity doctrine on 2026-04-29 (Putler $29
ships multi-store; we ship at $39 and beat the $129 tier on quality).

Tenant isolation: every endpoint resolves the current shop's
contact_email and asserts `group.owner_email == that email` before
acting. Service layer also enforces this for defense-in-depth.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.models.merchant import Merchant
from app.models.merchant_group import MerchantGroup

router = APIRouter(tags=["merchant_groups"])


class GroupCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    # When omitted, the endpoint resolves to the creator's shop native
    # currency via get_shop_currency(). A USD merchant creating a group
    # without an explicit preference should get USD, not EUR.
    base_currency: str | None = Field(default=None, max_length=8)


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
    by_currency: dict[str, dict[str, Any]] = Field(default_factory=dict)
    headline: dict[str, Any] | None = None
    is_homogeneous: bool = True
    primary_currency: str | None = None
    total_orders: int = 0
    shop_count: int = 0
    top_shop: dict[str, Any] | None = None
    generated_at: str | None = None
    error: str | None = None


def _owner_email_for(db: Session, shop: str) -> str:
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).one_or_none()
    if not m or not m.contact_email:
        raise HTTPException(status_code=400, detail="merchant_email_unknown")
    return m.contact_email


@router.post("/merchant/groups", response_model=GroupCreateResponse)
def create_group_endpoint(
    payload: GroupCreateIn,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    from app.services.merchant_groups import create_group, add_member
    from app.services.revenue_metrics import get_shop_currency
    email = _owner_email_for(db, shop)
    # Default group base currency to the creator's shop native currency
    # when the client doesn't specify. Falls back to USD for brand-new
    # shops with no resolvable currency (consistent with the rest of
    # the dashboard). Fixes an EUR-only default that broke USD merchants.
    base_ccy = payload.base_currency or get_shop_currency(db, shop) or "USD"
    g = create_group(db, name=payload.name, owner_email=email,
                     description=payload.description, base_currency=base_ccy)
    add_member(db, g.id, shop, label="primary", is_primary=True)
    return {"id": g.id, "name": g.name, "owner_email": g.owner_email}


@router.get("/merchant/groups", response_model=GroupListResponse)
def list_groups_endpoint(
    shop: str = Depends(require_merchant_session),
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


@router.post("/merchant/groups/{group_id}/members", response_model=MemberAddResponse)
def add_member_endpoint(
    group_id: int,
    payload: MemberAddIn,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    from app.services.merchant_groups import add_member
    g = db.get(MerchantGroup, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="not_found")
    email = _owner_email_for(db, shop)
    if g.owner_email != email:
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        m = add_member(db, group_id, payload.shop_domain, label=payload.label, is_primary=payload.is_primary)
    except ValueError as exc:
        if str(exc).startswith("max_members_exceeded"):
            raise HTTPException(status_code=409, detail=str(exc))
        raise
    return {"id": m.id, "shop_domain": m.shop_domain, "label": m.label, "is_primary": m.is_primary}


@router.delete("/merchant/groups/{group_id}/members/{shop_domain}", response_model=OkResponse)
def remove_member_endpoint(
    group_id: int,
    shop_domain: str,
    shop: str = Depends(require_merchant_session),
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


@router.get("/merchant/groups/{group_id}/dashboard", response_model=GroupDashboardResponse)
def group_dashboard_endpoint(
    group_id: int,
    shop: str = Depends(require_merchant_session),
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
    return get_group_dashboard(
        db, group_id,
        lookback_days=lookback_days,
        requesting_owner_email=email,
    )
