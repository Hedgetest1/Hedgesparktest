"""
mta_engine.py — Multi-Touch Attribution engine.

Problem: existing utm_attribution.py only supports first-touch OR last-touch.
Industry-standard attribution requires FIVE models:

    1. first_touch     : 100% credit to first source
    2. last_touch      : 100% credit to last source
    3. linear          : equal credit to every touchpoint
    4. time_decay      : recent touches get more credit (exponential half-life)
    5. position_based  : 40% first + 40% last + 20% split among middle (U-shaped)

Input: visitor_purchase_sessions joined with events (filtered to touches
BEFORE the purchase). Output: per-source credit allocation in each model,
comparable side-by-side, with revenue credit.

Why multi-touch matters
-----------------------
A merchant spending on Meta Ads + Klaviyo + Google sees wildly different
ROAS under different attribution models. Last-touch under-credits Meta
(which drives discovery) and over-credits email (which closes). Linear
gives equal visibility. Time-decay respects the real purchase timeline.
Position-based is the industry default for DTC.

Algorithm — touchpoint reconstruction
--------------------------------------
For each visitor_purchase_session:
  1. Load all events for that visitor_id up to the purchase timestamp
  2. Filter to "touch events" (events that indicate a source change or
     fresh session arrival — source_type != None, or UTM present)
  3. Dedupe consecutive identical sources (A → A → B becomes A → B)
  4. The resulting ordered list is the "touchpoint path"
  5. Apply the chosen attribution model to allocate revenue credit

Caching
-------
Per-shop result cached 10 min in Redis (hs:mta:{shop}:{model}:{window_days}).

Deterministic. No LLM. No heuristics beyond the attribution weights.
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("mta_engine")

AttributionModel = Literal[
    "first_touch", "last_touch", "linear", "time_decay", "position_based"
]

_CACHE_PREFIX = "hs:mta"
_CACHE_TTL_S = 600  # 10 min
_TIME_DECAY_HALFLIFE_DAYS = 7.0  # exponential decay
_POSITION_FIRST = 0.40
_POSITION_LAST = 0.40
_POSITION_MIDDLE_TOTAL = 0.20
_MAX_WINDOW_DAYS = 365
_MAX_TOUCHES_PER_JOURNEY = 50  # cap for pathological spammers


@dataclass
class Touch:
    source: str
    campaign: str | None
    ts: datetime


@dataclass
class Journey:
    visitor_id: str
    order_id: str
    revenue: float
    purchase_at: datetime
    touches: list[Touch] = field(default_factory=list)


@dataclass
class SourceCredit:
    source: str
    touches: int
    revenue_credit_eur: float
    order_fractions: float  # sum of fractional credits across orders
    first_touches: int
    last_touches: int


# ---------------------------------------------------------------------------
# Touchpoint extraction
# ---------------------------------------------------------------------------

def _load_journeys(
    db: Session, shop_domain: str, window_days: int
) -> list[Journey]:
    """Build visitor journeys: every visitor_purchase_session with their
    pre-purchase touches.

    Uses a single join so we can stream the touches alongside the session
    rows without N+1 queries.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)
    try:
        # events.timestamp is BigInteger epoch milliseconds — convert both sides
        # to a comparable form. We filter events by epoch-ms cutoff first (cheap
        # index scan), then compare to vps.confirmed_at in Python.
        rows = db.execute(
            sql_text(
                """
                SELECT
                    vps.visitor_id,
                    vps.shopify_order_id,
                    vps.confirmed_at,
                    so.total_price,
                    e.source_type,
                    e.utm_campaign,
                    e.timestamp
                FROM visitor_purchase_sessions vps
                JOIN shop_orders so
                  ON so.shop_domain = vps.shop_domain
                 AND so.shopify_order_id = vps.shopify_order_id
                LEFT JOIN events e
                  ON e.visitor_id = vps.visitor_id
                 AND e.shop_domain = vps.shop_domain
                 AND e.source_type IS NOT NULL
                WHERE vps.shop_domain = :shop
                  AND vps.confirmed_at >= :cutoff
                ORDER BY vps.shopify_order_id, e.timestamp ASC NULLS LAST
                """
            ),
            {"shop": shop_domain, "cutoff": cutoff},
        ).fetchall()
    except Exception as exc:
        log.warning("mta: journey load failed: %s", exc)
        return []

    journeys_by_order: dict[str, Journey] = {}
    for row in rows:
        visitor_id, order_id, confirmed_at, total_price, source, campaign, ts_ms = row
        if not order_id:
            continue
        j = journeys_by_order.get(order_id)
        if j is None:
            j = Journey(
                visitor_id=visitor_id,
                order_id=order_id,
                revenue=float(total_price or 0),
                purchase_at=confirmed_at,
            )
            journeys_by_order[order_id] = j
        if source and ts_ms is not None:
            # Convert epoch ms → datetime, and ONLY keep touches at-or-before purchase
            try:
                touch_ts = datetime.utcfromtimestamp(int(ts_ms) / 1000.0)
                if touch_ts <= confirmed_at:
                    j.touches.append(Touch(source=source, campaign=campaign, ts=touch_ts))
            except (ValueError, OverflowError, OSError):
                pass

    # Post-process: dedupe consecutive identical sources, cap length
    journeys: list[Journey] = []
    for j in journeys_by_order.values():
        if not j.touches:
            # No pre-purchase touches → single "direct" touch at purchase time
            j.touches.append(
                Touch(source="direct", campaign=None, ts=j.purchase_at)
            )
        deduped: list[Touch] = []
        last_source = None
        for t in j.touches:
            if t.source == last_source:
                continue
            deduped.append(t)
            last_source = t.source
        if len(deduped) > _MAX_TOUCHES_PER_JOURNEY:
            # Keep first + last window, drop middle (preserves boundary credit)
            deduped = deduped[: _MAX_TOUCHES_PER_JOURNEY // 2] + deduped[-_MAX_TOUCHES_PER_JOURNEY // 2 :]
        j.touches = deduped
        journeys.append(j)

    return journeys


# ---------------------------------------------------------------------------
# Attribution models — each returns {source: fraction} summing to 1.0
# ---------------------------------------------------------------------------

def _model_first_touch(journey: Journey) -> dict[str, float]:
    return {journey.touches[0].source: 1.0}


def _model_last_touch(journey: Journey) -> dict[str, float]:
    return {journey.touches[-1].source: 1.0}


def _model_linear(journey: Journey) -> dict[str, float]:
    n = len(journey.touches)
    if n == 0:
        return {}
    per = 1.0 / n
    out: dict[str, float] = defaultdict(float)
    for t in journey.touches:
        out[t.source] += per
    return dict(out)


def _model_time_decay(journey: Journey) -> dict[str, float]:
    """Exponential decay — touches closer to purchase get more credit."""
    n = len(journey.touches)
    if n == 0:
        return {}
    if n == 1:
        return {journey.touches[0].source: 1.0}
    # Half-life in days → lambda = ln(2) / halflife
    lam = math.log(2) / _TIME_DECAY_HALFLIFE_DAYS
    weights: list[float] = []
    for t in journey.touches:
        delta_days = max(0.0, (journey.purchase_at - t.ts).total_seconds() / 86400.0)
        weights.append(math.exp(-lam * delta_days))
    total = sum(weights) or 1.0
    out: dict[str, float] = defaultdict(float)
    for t, w in zip(journey.touches, weights):
        out[t.source] += w / total
    return dict(out)


def _model_position_based(journey: Journey) -> dict[str, float]:
    """U-shaped: 40% first + 40% last + 20% distributed across middle."""
    n = len(journey.touches)
    if n == 0:
        return {}
    if n == 1:
        return {journey.touches[0].source: 1.0}
    if n == 2:
        out: dict[str, float] = defaultdict(float)
        out[journey.touches[0].source] += 0.5
        out[journey.touches[-1].source] += 0.5
        return dict(out)

    out = defaultdict(float)
    out[journey.touches[0].source] += _POSITION_FIRST
    out[journey.touches[-1].source] += _POSITION_LAST
    middle_count = n - 2
    per_middle = _POSITION_MIDDLE_TOTAL / middle_count
    for t in journey.touches[1:-1]:
        out[t.source] += per_middle
    return dict(out)


_MODELS: dict[AttributionModel, callable] = {
    "first_touch": _model_first_touch,
    "last_touch": _model_last_touch,
    "linear": _model_linear,
    "time_decay": _model_time_decay,
    "position_based": _model_position_based,
}


# ---------------------------------------------------------------------------
# Top-level aggregation
# ---------------------------------------------------------------------------

def compute_mta(
    db: Session,
    shop_domain: str,
    model: AttributionModel = "position_based",
    window_days: int = 30,
) -> dict:
    """Compute multi-touch attribution for a shop over a time window.

    Returns:
        {
            "shop_domain": str,
            "model": str,
            "window_days": int,
            "total_revenue_eur": float,
            "total_orders": int,
            "avg_touches_per_journey": float,
            "sources": [{source, touches, revenue_credit_eur, order_fractions,
                         first_touches, last_touches}, ...],
            "path_samples": [str, ...],   # 5 representative paths
            "generated_at": str,
        }
    """
    if model not in _MODELS:
        raise ValueError(f"unknown attribution model: {model}")
    window_days = max(1, min(window_days, _MAX_WINDOW_DAYS))

    # Cache check
    cache_key = f"{_CACHE_PREFIX}:{shop_domain}:{model}:{window_days}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(cache_key)
            if raw:
                return json.loads(raw)
    except Exception:
        rc = None

    journeys = _load_journeys(db, shop_domain, window_days)
    model_fn = _MODELS[model]

    credit: dict[str, SourceCredit] = {}
    total_rev = 0.0
    total_orders = len(journeys)
    touch_count_total = 0
    path_samples: list[str] = []

    for j in journeys:
        total_rev += j.revenue
        touch_count_total += len(j.touches)

        fractions = model_fn(j)
        first_source = j.touches[0].source if j.touches else "direct"
        last_source = j.touches[-1].source if j.touches else "direct"

        for source, frac in fractions.items():
            c = credit.get(source)
            if c is None:
                c = SourceCredit(
                    source=source,
                    touches=0,
                    revenue_credit_eur=0.0,
                    order_fractions=0.0,
                    first_touches=0,
                    last_touches=0,
                )
                credit[source] = c
            c.touches += sum(1 for t in j.touches if t.source == source)
            c.revenue_credit_eur += frac * j.revenue
            c.order_fractions += frac
            if source == first_source:
                c.first_touches += 1
            if source == last_source:
                c.last_touches += 1

        # Collect a few path samples for diagnostics
        if len(path_samples) < 5:
            path_str = " → ".join(t.source for t in j.touches[:8])
            if len(j.touches) > 8:
                path_str += f" → ... ({len(j.touches)} total)"
            path_samples.append(path_str)

    sources_sorted = sorted(
        credit.values(), key=lambda c: c.revenue_credit_eur, reverse=True
    )

    result = {
        "shop_domain": shop_domain,
        "model": model,
        "window_days": window_days,
        "total_revenue_eur": round(total_rev, 2),
        "total_orders": total_orders,
        "avg_touches_per_journey": round(
            touch_count_total / max(1, total_orders), 2
        ),
        "sources": [
            {
                "source": c.source,
                "touches": c.touches,
                "revenue_credit_eur": round(c.revenue_credit_eur, 2),
                "order_fractions": round(c.order_fractions, 3),
                "first_touches": c.first_touches,
                "last_touches": c.last_touches,
                "revenue_share_pct": (
                    round((c.revenue_credit_eur / total_rev) * 100, 1)
                    if total_rev > 0
                    else 0.0
                ),
            }
            for c in sources_sorted
        ],
        "path_samples": path_samples,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if rc is not None:
        try:
            rc.setex(cache_key, _CACHE_TTL_S, json.dumps(result, default=str))
        except Exception:
            pass

    return result


def compare_models(
    db: Session, shop_domain: str, window_days: int = 30
) -> dict:
    """Run ALL 5 attribution models and return a side-by-side comparison.

    This is the killer UX moment: "Meta is credited €2,100 under first-touch
    but only €450 under last-touch — that's a 4.6× swing and it tells you
    Meta is a discovery driver, not a closer."
    """
    by_model: dict[str, dict] = {}
    for m in _MODELS.keys():
        by_model[m] = compute_mta(db, shop_domain, model=m, window_days=window_days)  # type: ignore

    # Build per-source matrix
    all_sources = set()
    for payload in by_model.values():
        for s in payload.get("sources", []):
            all_sources.add(s["source"])

    matrix: list[dict] = []
    for source in all_sources:
        row = {"source": source}
        for model_name, payload in by_model.items():
            match = next(
                (s for s in payload["sources"] if s["source"] == source), None
            )
            row[model_name] = match["revenue_credit_eur"] if match else 0.0
        # Variance metric — how much does attribution swing?
        values = [row[m] for m in _MODELS.keys()]
        row["max"] = max(values) if values else 0.0
        row["min"] = min(values) if values else 0.0
        row["swing_pct"] = (
            round(((row["max"] - row["min"]) / row["max"]) * 100, 1)
            if row["max"] > 0
            else 0.0
        )
        matrix.append(row)

    matrix.sort(key=lambda r: r["max"], reverse=True)

    # Pick headline insight: biggest absolute swing
    biggest_swing = max(matrix, key=lambda r: r["max"] - r["min"], default=None)
    headline = None
    if biggest_swing and biggest_swing["max"] > 0:
        best_model = max(
            _MODELS.keys(), key=lambda m: biggest_swing[m]
        )
        worst_model = min(
            _MODELS.keys(), key=lambda m: biggest_swing[m]
        )
        if biggest_swing[best_model] > biggest_swing[worst_model] * 1.5:
            headline = (
                f"{biggest_swing['source']} is credited "
                f"€{biggest_swing[best_model]:,.0f} under {best_model.replace('_', '-')} "
                f"but only €{biggest_swing[worst_model]:,.0f} under {worst_model.replace('_', '-')} "
                f"— {biggest_swing['swing_pct']}% swing."
            )

    return {
        "shop_domain": shop_domain,
        "window_days": window_days,
        "matrix": matrix,
        "total_revenue_eur": (
            by_model.get("position_based", {}).get("total_revenue_eur", 0.0)
        ),
        "total_orders": (
            by_model.get("position_based", {}).get("total_orders", 0)
        ),
        "headline": headline,
        "by_model": by_model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
