"""
agency.py — Phase Ω'' white-label / agency mode service.

Roster + roll-up KPIs across an agency's client shops with revenue-share
calculation. Cached 5 min in Redis per agency_id.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.agency import Agency, AgencyClient

log = logging.getLogger("agency")

_CACHE_TTL_SECONDS = 5 * 60
_CACHE_KEY_PREFIX = "hs:agency:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_agency(db: Session, *, name: str, contact_email: str,
                  default_revshare_pct: float = 20.0,
                  brand_color: str | None = None,
                  logo_url: str | None = None,
                  custom_subdomain: str | None = None) -> Agency:
    a = Agency(
        name=name, contact_email=contact_email,
        default_revshare_pct=default_revshare_pct,
        brand_color=brand_color, logo_url=logo_url,
        custom_subdomain=custom_subdomain,
    )
    db.add(a)
    db.flush()
    return a


def get_agency_by_email(db: Session, contact_email: str) -> Agency | None:
    return (
        db.query(Agency)
        .filter(Agency.contact_email == contact_email)
        .one_or_none()
    )


def add_client(db: Session, agency_id: int, shop_domain: str,
               *, nickname: str | None = None,
               revshare_pct: float | None = None) -> AgencyClient:
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise ValueError("agency_not_found")
    pct = revshare_pct if revshare_pct is not None else agency.default_revshare_pct
    existing = (
        db.query(AgencyClient)
        .filter(AgencyClient.agency_id == agency_id, AgencyClient.shop_domain == shop_domain)
        .one_or_none()
    )
    if existing:
        existing.nickname = nickname or existing.nickname
        existing.revshare_pct = pct
        existing.status = "active"
        db.flush()
        return existing
    c = AgencyClient(
        agency_id=agency_id,
        shop_domain=shop_domain,
        nickname=nickname,
        revshare_pct=pct,
        status="active",
    )
    db.add(c)
    db.flush()
    return c


def remove_client(db: Session, agency_id: int, shop_domain: str) -> bool:
    c = (
        db.query(AgencyClient)
        .filter(AgencyClient.agency_id == agency_id, AgencyClient.shop_domain == shop_domain)
        .one_or_none()
    )
    if not c:
        return False
    c.status = "removed"
    db.flush()
    return True


def list_clients(db: Session, agency_id: int, *, include_removed: bool = False) -> list[AgencyClient]:
    q = db.query(AgencyClient).filter(AgencyClient.agency_id == agency_id)
    if not include_removed:
        q = q.filter(AgencyClient.status != "removed")
    return q.order_by(AgencyClient.id.desc()).all()


# ---------------------------------------------------------------------------
# Roll-up dashboard
# ---------------------------------------------------------------------------


def get_agency_dashboard(db: Session, agency_id: int, *, lookback_days: int = 30) -> dict:
    """
    Aggregate KPIs across the agency's active clients:
      * total client revenue
      * agency-billable revshare €
      * per-client breakdown (revenue, AOV, revshare €)
      * top performing client
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:dash:{agency_id}:{lookback_days}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception:
        rc = None

    agency = db.get(Agency, agency_id)
    if not agency:
        return {"error": "agency_not_found"}

    clients = list_clients(db, agency_id)
    if not clients:
        return {
            "agency_id": agency_id,
            "name": agency.name,
            "clients": [],
            "by_currency": {},
            "headline": None,
            "is_homogeneous": True,
            "primary_currency": None,
            "client_count": 0,
            "generated_at": _now().isoformat(),
        }

    cutoff = _now() - timedelta(days=lookback_days)
    shops = [c.shop_domain for c in clients]

    # Per-shop revenue in shop's OWN native currency. We aggregate by
    # currency in Python; never sum mixed currencies as if same number.
    rows = db.execute(text("""
        SELECT so.shop_domain,
               COALESCE(m.primary_currency, so.currency) AS currency,
               COALESCE(SUM(so.total_price), 0) AS revenue,
               COUNT(*) AS orders
        FROM shop_orders so
        LEFT JOIN merchants m ON m.shop_domain = so.shop_domain
        WHERE so.shop_domain = ANY(:shops) AND so.created_at >= :cut
          AND (m.primary_currency IS NULL OR so.currency = m.primary_currency)
        GROUP BY so.shop_domain, COALESCE(m.primary_currency, so.currency)
    """), {"shops": shops, "cut": cutoff}).fetchall()

    by_shop: dict[str, dict] = {}
    for r in rows:
        by_shop[r[0]] = {
            "currency": (r[1] or "").upper() or "UNKNOWN",
            "revenue": round(float(r[2] or 0), 2),
            "orders": int(r[3] or 0),
        }

    from app.services.multi_currency_rollup import ShopRow, aggregate_by_currency, headline_for

    rollup_input: list[ShopRow] = []
    breakdown = []
    # Revshare-by-currency: revshare must be denominated in the shop's
    # own currency, never folded across currencies.
    revshare_by_currency: dict[str, float] = {}
    for c in clients:
        info = by_shop.get(c.shop_domain, {"currency": "UNKNOWN", "revenue": 0.0, "orders": 0})
        revenue = info["revenue"]
        revshare = round(revenue * (c.revshare_pct / 100.0), 2)
        rollup_input.append(ShopRow(
            shop_domain=c.shop_domain,
            currency=info["currency"],
            revenue=revenue,
            orders=info["orders"],
        ))
        revshare_by_currency[info["currency"]] = (
            revshare_by_currency.get(info["currency"], 0.0) + revshare
        )
        breakdown.append({
            "shop_domain": c.shop_domain,
            "nickname": c.nickname,
            "status": c.status,
            "revshare_pct": c.revshare_pct,
            "currency": info["currency"],
            "revenue": revenue,
            "orders": info["orders"],
            "aov": round(revenue / info["orders"], 2) if info["orders"] else 0.0,
            "revshare": revshare,
        })

    rollup = aggregate_by_currency(rollup_input)

    breakdown.sort(key=lambda r: r["revenue"], reverse=True)
    top_client = next(
        (b for b in breakdown if b["currency"] == rollup["primary_currency"]),
        breakdown[0] if breakdown else None,
    )

    # Round revshare totals at boundary
    revshare_by_currency = {k: round(v, 2) for k, v in revshare_by_currency.items()}

    result = {
        "agency_id": agency_id,
        "name": agency.name,
        "lookback_days": lookback_days,
        "clients": breakdown,
        "by_currency": rollup["by_currency"],
        "revshare_by_currency": revshare_by_currency,
        "headline": headline_for(rollup),
        "is_homogeneous": rollup["is_homogeneous"],
        "primary_currency": rollup["primary_currency"],
        "client_count": len(clients),
        "top_client": top_client,
        "generated_at": _now().isoformat(),
    }

    if rc is not None:
        try:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
        except Exception as exc:
            log.warning("agency: cache write failed: %s", exc)

    return result
