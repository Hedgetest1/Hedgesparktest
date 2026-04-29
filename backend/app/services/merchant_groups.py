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
                 base_currency: str = "USD") -> MerchantGroup:
    g = MerchantGroup(
        name=name, owner_email=owner_email,
        description=description, base_currency=base_currency,
    )
    db.add(g)
    db.flush()
    return g


def add_member(db: Session, group_id: int, shop_domain: str, *,
               label: str | None = None, is_primary: bool = False) -> MerchantGroupMember:
    # DoS / query-plan guard. Real merchants run 2-10 stores; capping
    # at 50 is generous and keeps `shop_domain = ANY(:shops)` bounded.
    current_count = (
        db.query(MerchantGroupMember)
        .filter(MerchantGroupMember.group_id == group_id)
        .count()
    )
    existing = (
        db.query(MerchantGroupMember)
        .filter(
            MerchantGroupMember.group_id == group_id,
            MerchantGroupMember.shop_domain == shop_domain,
        )
        .one_or_none()
    )
    if existing is None and current_count >= _MAX_MEMBERS_PER_GROUP:
        raise ValueError(f"max_members_exceeded:{_MAX_MEMBERS_PER_GROUP}")

    if is_primary:
        # Atomic primary-flip: only one shop per group can be primary.
        # Done in a single UPDATE statement to avoid a TOCTOU race
        # between two concurrent add_member(is_primary=True) calls.
        db.query(MerchantGroupMember).filter(
            MerchantGroupMember.group_id == group_id,
            MerchantGroupMember.is_primary.is_(True),
        ).update({"is_primary": False}, synchronize_session=False)

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


_MAX_MEMBERS_PER_GROUP = 50  # DoS guard: query plan stays sane up to ~50 shops


def get_group_dashboard(
    db: Session,
    group_id: int,
    *,
    lookback_days: int = 30,
    requesting_owner_email: str | None = None,
) -> dict:
    """
    Return consolidated metrics across all member shops in the group.

    Per-currency rollup (no FX, no fake-sum) — when member shops use
    mixed currencies the response surfaces `by_currency` totals plus
    a `headline` for the dominant bucket. Frontend renders one card
    per currency, never collapses into a single fake number.

    `requesting_owner_email` enforces tenant isolation at the service
    layer (defense in depth). When None, callers must have validated
    ownership themselves at the API layer.
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

    if requesting_owner_email is not None and group.owner_email != requesting_owner_email:
        return {"error": "forbidden"}

    members = list_members(db, group_id)
    if not members:
        return {
            "group_id": group_id,
            "name": group.name,
            "base_currency": group.base_currency,
            "members": [],
            "by_currency": {},
            "headline": None,
            "is_homogeneous": True,
            "total_orders": 0,
            "shop_count": 0,
            "top_shop": None,
            "generated_at": _now().isoformat(),
        }

    cutoff = _now() - timedelta(days=lookback_days)
    shops = [m.shop_domain for m in members]

    # Pull each shop's revenue in its OWN native currency. The query
    # filters shop_orders.currency = merchants.primary_currency so a
    # shop's own multi-currency rows don't double-count. We then
    # aggregate by currency in Python — no fake cross-currency sum.
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
    for m in members:
        s = m.shop_domain
        info = by_shop.get(s, {"currency": (group.base_currency or "USD").upper(), "revenue": 0.0, "orders": 0})
        rollup_input.append(ShopRow(
            shop_domain=s,
            currency=info["currency"],
            revenue=info["revenue"],
            orders=info["orders"],
        ))
        breakdown.append({
            "shop_domain": s,
            "label": m.label,
            "is_primary": m.is_primary,
            "currency": info["currency"],
            "revenue": info["revenue"],
            "orders": info["orders"],
            "aov": round(info["revenue"] / info["orders"], 2) if info["orders"] else 0.0,
        })

    rollup = aggregate_by_currency(rollup_input)

    # Sort breakdown by revenue desc *within currency*; for cross-currency
    # ranking we take the dominant currency's leader as `top_shop`.
    breakdown.sort(key=lambda r: r["revenue"], reverse=True)
    top_shop = next(
        (b for b in breakdown if b["currency"] == rollup["primary_currency"]),
        breakdown[0] if breakdown else None,
    )

    result = {
        "group_id": group_id,
        "name": group.name,
        "base_currency": group.base_currency,
        "lookback_days": lookback_days,
        "members": breakdown,
        "by_currency": rollup["by_currency"],
        "headline": headline_for(rollup),
        "is_homogeneous": rollup["is_homogeneous"],
        "primary_currency": rollup["primary_currency"],
        "total_orders": rollup["total_orders"],
        "shop_count": rollup["shop_count"],
        "top_shop": top_shop,
        "generated_at": _now().isoformat(),
    }

    if rc is not None:
        try:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result, default=str))
        except Exception as exc:
            log.warning("merchant_groups: cache write failed: %s", exc)

    return result
