"""
merchant_groups.py — Phase Ω'' multi-store consolidation service.

CRUD + consolidated metrics across all member shops in a group. Built on
existing tables — no new aggregation pipeline. Pulls per-shop metrics
on-demand and folds them into one report.

Cached 5 min in Redis per group_id since the underlying queries are
expensive at 10k merchants × N members.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.merchant_group import MerchantGroup, MerchantGroupMember

log = logging.getLogger("merchant_groups")

_CACHE_TTL_SECONDS = 5 * 60
_CACHE_KEY_PREFIX = "hs:mgroup:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_group(db: Session, *, name: str, owner_email: str,
                 description: str | None = None,
                 base_currency: str = "EUR") -> MerchantGroup:
    g = MerchantGroup(
        name=name, owner_email=owner_email,
        description=description, base_currency=base_currency,
    )
    db.add(g)
    db.flush()
    return g


def add_member(db: Session, group_id: int, shop_domain: str, *,
               label: str | None = None, is_primary: bool = False) -> MerchantGroupMember:
    existing = (
        db.query(MerchantGroupMember)
        .filter(
            MerchantGroupMember.group_id == group_id,
            MerchantGroupMember.shop_domain == shop_domain,
        )
        .one_or_none()
    )
    if existing:
        existing.label = label or existing.label
        if is_primary:
            existing.is_primary = True
        db.flush()
        return existing
    m = MerchantGroupMember(
        group_id=group_id,
        shop_domain=shop_domain,
        label=label,
        is_primary=is_primary,
    )
    db.add(m)
    db.flush()
    return m


def remove_member(db: Session, group_id: int, shop_domain: str) -> bool:
    m = (
        db.query(MerchantGroupMember)
        .filter(
            MerchantGroupMember.group_id == group_id,
            MerchantGroupMember.shop_domain == shop_domain,
        )
        .one_or_none()
    )
    if not m:
        return False
    db.delete(m)
    db.flush()
    return True


def list_groups_for_owner(db: Session, owner_email: str) -> list[MerchantGroup]:
    return (
        db.query(MerchantGroup)
        .filter(MerchantGroup.owner_email == owner_email)
        .order_by(MerchantGroup.id.desc())
        .all()
    )


def list_members(db: Session, group_id: int) -> list[MerchantGroupMember]:
    return (
        db.query(MerchantGroupMember)
        .filter(MerchantGroupMember.group_id == group_id)
        .order_by(MerchantGroupMember.is_primary.desc(), MerchantGroupMember.id.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Consolidated metrics
# ---------------------------------------------------------------------------


def get_group_dashboard(db: Session, group_id: int, *, lookback_days: int = 30) -> dict:
    """
    Return consolidated metrics across all member shops in the group:
      * total revenue (last N days)
      * total orders
      * AOV
      * per-shop breakdown
      * top contributing shop
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:dash:{group_id}:{lookback_days}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception:
        rc = None

    group = db.get(MerchantGroup, group_id)
    if not group:
        return {"error": "group_not_found"}

    members = list_members(db, group_id)
    if not members:
        return {
            "group_id": group_id,
            "name": group.name,
            "members": [],
            "totals": {"revenue_eur": 0, "orders": 0, "aov_eur": 0},
            "generated_at": _now().isoformat(),
        }

    cutoff = _now() - timedelta(days=lookback_days)
    shops = [m.shop_domain for m in members]

    # TODO(currency): multi-shop aggregation needs per-shop currency
    rows = db.execute(text("""
        SELECT shop_domain,
               COALESCE(SUM(total_price), 0) AS revenue,
               COUNT(*) AS orders
        FROM shop_orders
        WHERE shop_domain = ANY(:shops) AND created_at >= :cut
        GROUP BY shop_domain
    """), {"shops": shops, "cut": cutoff}).fetchall()

    by_shop: dict[str, dict] = {}
    for r in rows:
        by_shop[r[0]] = {
            "revenue_eur": round(float(r[1] or 0), 2),
            "orders": int(r[2] or 0),
        }

    breakdown = []
    total_rev = 0.0
    total_orders = 0
    for m in members:
        s = m.shop_domain
        info = by_shop.get(s, {"revenue_eur": 0.0, "orders": 0})
        total_rev += info["revenue_eur"]
        total_orders += info["orders"]
        breakdown.append({
            "shop_domain": s,
            "label": m.label,
            "is_primary": m.is_primary,
            "revenue_eur": info["revenue_eur"],
            "orders": info["orders"],
            "aov_eur": round(info["revenue_eur"] / info["orders"], 2) if info["orders"] else 0.0,
        })

    breakdown.sort(key=lambda r: r["revenue_eur"], reverse=True)
    top_shop = breakdown[0] if breakdown else None

    result = {
        "group_id": group_id,
        "name": group.name,
        "base_currency": group.base_currency,
        "lookback_days": lookback_days,
        "members": breakdown,
        "totals": {
            "revenue_eur": round(total_rev, 2),
            "orders": total_orders,
            "aov_eur": round(total_rev / total_orders, 2) if total_orders else 0.0,
        },
        "top_shop": top_shop,
        "generated_at": _now().isoformat(),
    }

    if rc is not None:
        try:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
        except Exception:
            pass

    return result
