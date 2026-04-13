"""
agency.py — Phase Ω'' white-label agency API.

  POST   /agency/register           — create an agency from a contact_email
  POST   /agency/clients            — add a client shop
  GET    /agency/clients            — list active clients
  DELETE /agency/clients/{shop}     — remove a client
  GET    /agency/dashboard          — consolidated KPIs + revshare €

Auth: agency endpoints accept an `X-Agency-Email` header. Production
should swap this for a signed agency JWT — left as the next step
because the founder hasn't approved a separate auth flow yet.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(tags=["agency"])


class AgencyRegisterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    contact_email: str = Field(..., min_length=3, max_length=200)
    default_revshare_pct: float = Field(default=20.0, ge=0, le=100)
    brand_color: str | None = None
    logo_url: str | None = None
    custom_subdomain: str | None = None


class ClientAddIn(BaseModel):
    shop_domain: str = Field(..., min_length=1, max_length=200)
    nickname: str | None = Field(default=None, max_length=200)
    revshare_pct: float | None = Field(default=None, ge=0, le=100)


def _resolve_agency(db: Session, email: str | None):
    if not email:
        raise HTTPException(status_code=401, detail="agency_email_required")
    from app.services.agency import get_agency_by_email
    a = get_agency_by_email(db, email)
    if not a:
        raise HTTPException(status_code=404, detail="agency_not_found")
    return a


@router.post("/agency/register")
def register_agency(
    payload: AgencyRegisterIn,
    db: Session = Depends(get_db),
):
    from app.services.agency import create_agency, get_agency_by_email
    if get_agency_by_email(db, payload.contact_email):
        raise HTTPException(status_code=409, detail="already_registered")
    a = create_agency(
        db,
        name=payload.name,
        contact_email=payload.contact_email,
        default_revshare_pct=payload.default_revshare_pct,
        brand_color=payload.brand_color,
        logo_url=payload.logo_url,
        custom_subdomain=payload.custom_subdomain,
    )
    return {"id": a.id, "name": a.name, "contact_email": a.contact_email}


@router.post("/agency/clients")
def add_client_endpoint(
    payload: ClientAddIn,
    db: Session = Depends(get_db),
    x_agency_email: str | None = Header(default=None),
):
    a = _resolve_agency(db, x_agency_email)
    from app.services.agency import add_client
    c = add_client(
        db, a.id,
        shop_domain=payload.shop_domain,
        nickname=payload.nickname,
        revshare_pct=payload.revshare_pct,
    )
    return {
        "id": c.id,
        "shop_domain": c.shop_domain,
        "nickname": c.nickname,
        "revshare_pct": c.revshare_pct,
        "status": c.status,
    }


@router.get("/agency/clients")
def list_clients_endpoint(
    db: Session = Depends(get_db),
    x_agency_email: str | None = Header(default=None),
):
    a = _resolve_agency(db, x_agency_email)
    from app.services.agency import list_clients
    rows = list_clients(db, a.id)
    return {
        "clients": [
            {
                "id": c.id,
                "shop_domain": c.shop_domain,
                "nickname": c.nickname,
                "revshare_pct": c.revshare_pct,
                "status": c.status,
            }
            for c in rows
        ]
    }


@router.delete("/agency/clients/{shop_domain}")
def remove_client_endpoint(
    shop_domain: str,
    db: Session = Depends(get_db),
    x_agency_email: str | None = Header(default=None),
):
    a = _resolve_agency(db, x_agency_email)
    from app.services.agency import remove_client
    ok = remove_client(db, a.id, shop_domain)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@router.get("/agency/dashboard")
def agency_dashboard(
    db: Session = Depends(get_db),
    x_agency_email: str | None = Header(default=None),
    lookback_days: int = Query(30, ge=1, le=365),
):
    a = _resolve_agency(db, x_agency_email)
    from app.services.agency import get_agency_dashboard
    return get_agency_dashboard(db, a.id, lookback_days=lookback_days)
