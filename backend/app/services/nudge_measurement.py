"""
nudge_measurement.py — Nudge exposure, interaction, and outcome measurement.

Public interface
----------------
    record_nudge_event(
        db, shop_domain, nudge_id, visitor_id, product_url, event_type,
        metadata=None,
    ) -> NudgeEvent | None

    record_holdout_assignment(
        db, shop_domain, nudge_id, visitor_id, product_url,
    ) -> NudgeEvent | None

    get_nudge_stats(db, shop_domain, nudge_id) -> dict

    get_nudge_attribution(
        db, shop_domain, nudge_id, window_hours=24, exposed_visitors=None,
    ) -> dict

    get_nudge_full_report(db, shop_domain, nudge_id, window_hours=24) -> dict

    get_nudge_ab_report(db, shop_domain, nudge_id, window_hours=24) -> dict

    get_nudge_lift_report(db, shop_domain, nudge_id, window_hours=24) -> dict

event_type values
-----------------
  "shown"            — nudge rendered (one per visitor/session/nudge via client dedup)
  "dismissed"        — visitor dismissed the nudge
  "clicked"          — visitor clicked a CTA (reserved for future nudge types)
  "holdout_assigned" — server-assigned visitor to holdout (control) group;
                       nudge was suppressed for this visitor.
                       Written server-side only — never accepted from client POST.

Attribution model — v1 (observational, NOT causal)
----------------------------------------------------
A visitor is attributed as "purchased" if:
  1. They had a "shown" event for this nudge (visitor_id IS NOT NULL), AND
  2. They appear in visitor_purchase_sessions with confirmed_at within
     window_hours of their FIRST "shown" event for this nudge.

This is observational first-exposure attribution.
All outputs include attribution_note labeling the observational nature.

Revenue attribution model
--------------------------
Revenue is attributed per visitor by joining:
    nudge_events (visitor_id, event_type, created_at)
    → visitor_purchase_sessions (visitor_id → shopify_order_id, confirmed_at)
    → shop_orders (shopify_order_id → total_price, currency)

Join safety:
  - visitor_purchase_sessions.shopify_order_id has a UNIQUE constraint.
  - shop_orders.shopify_order_id has a UNIQUE constraint.
  - The vps → shop_orders join is always 1:1 — zero duplication.
  - A visitor placing 2 orders within the window produces 2 vps rows
    → 2 LEFT JOINs → SUM correctly accumulates both order values.
  - LEFT JOIN preserves vps rows with no shop_orders match (webhook lag or
    missing configuration) — they contribute 0 to revenue via COALESCE.

Revenue metrics:
  exposed_revenue  — total order value for attributed exposed purchasers
  holdout_revenue  — total order value for attributed holdout purchasers
  exposed_rpv      — exposed_revenue / exposed_count (revenue per eligible visitor)
  holdout_rpv      — holdout_revenue / holdout_count
  incremental_rpv  — exposed_rpv − holdout_rpv (nudge-attributable RPV)

  estimated_incremental_revenue = exposed_revenue − (holdout_rpv × exposed_count)

  Derivation: holdout_rpv is the control baseline — revenue an eligible visitor
  generates without the nudge.  Multiplied by exposed_count, this gives the
  counterfactual revenue the exposed group would have generated without the nudge.
  The difference is the estimated nudge contribution.

  This is more conservative than extrapolating incremental_rpv across the full
  eligible audience.  It stays within the observed experiment population.

  revenue_lift_pct = incremental_rpv / holdout_rpv × 100
  (analogous to CVR estimated_lift_pct — labeled "estimated")

Currency handling:
  All revenue figures are summed within their reported currency.
  When shop_orders contains multiple currencies, figures are summed without
  conversion and labeled currency="mixed".  This is documented clearly in
  all return dicts.

has_order_data flag:
  False when exposed_purchases > 0 but exposed_revenue == 0, indicating
  shop_orders is empty (webhook not configured or not yet received).
  When False, revenue figures are zero and should not be used.

Holdout attribution model — v1 (quasi-experimental)
----------------------------------------------------
A holdout visitor is attributed as "purchased" if:
  1. They had a "holdout_assigned" event for this nudge, AND
  2. They appear in visitor_purchase_sessions with confirmed_at within
     window_hours of their FIRST "holdout_assigned" event for this nudge.

Both groups use the same attribution window from their first qualifying event.
Both groups are drawn from the same eligible population (behavioral gate passed).
The only systematic difference between the groups is nudge exposure.

This is a quasi-experimental design — hash-based deterministic assignment is
pseudo-random, not truly random — but it is materially more credible than pure
observational post-exposure CVR or revenue.  All outputs are labeled accordingly.

Per-variant attribution
-----------------------
Because variant assignment is deterministic via hash(visitor_id + ":" + nudge_id),
each visitor always sees the same variant for a given nudge.  This means the
per-variant attribution join is clean:
  - First-exposure subquery groups by visitor_id
  - Variant is taken from MIN(event_meta->>'copy_variant') — stable per visitor
  - Attribution join checks purchases within window_hours of first_shown_at

Winner selection — simplified proportion test
---------------------------------------------
For v1 we compute a z-score for two proportions (post-exposure CVR per variant).
This is mathematically correct but labeled "observational_significance" because:
  - Attribution is observational (no holdout group → no causal isolation)
  - The experiment is not pre-registered or power-calculated

Decision labels:
  "insufficient_sample"       — any variant has < MIN_SAMPLE_PER_VARIANT exposures
  "no_significant_difference" — z-test p >= 0.10
  "provisional_leader"        — p < 0.10 (> 90% confident, one-tailed)
  "confident_leader"          — p < 0.05 (> 95% confident, one-tailed)

All labels include p-value and z-score for full transparency.
Agents can act on "confident_leader" to promote the winning variant.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.nudge_event import NudgeEvent

log = logging.getLogger(__name__)

# Client-submittable event types (accepted via POST /nudge/event)
CLIENT_EVENT_TYPES: frozenset[str] = frozenset({"shown", "dismissed", "clicked"})

# All valid event types (includes server-side-only events)
ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {"shown", "dismissed", "clicked", "holdout_assigned"}
)

DEFAULT_ATTRIBUTION_WINDOW_HOURS: int = 24

# Minimum per-group visitors before winner selection or lift estimation is attempted.
# Below this, variance in CVR and RPV is too high for any meaningful comparison.
MIN_SAMPLE_PER_GROUP: int = 30

# Backward-compatible alias — used by A/B variant selection code
MIN_SAMPLE_PER_VARIANT: int = MIN_SAMPLE_PER_GROUP


# ---------------------------------------------------------------------------
# Write path — client-triggered events (shown / dismissed / clicked)
# ---------------------------------------------------------------------------

def record_nudge_event(
    db:          Session,
    shop_domain: str,
    nudge_id:    int,
    visitor_id:  Optional[str],
    product_url: str,
    event_type:  str,
    metadata:    Optional[dict] = None,
) -> Optional[NudgeEvent]:
    """
    Persist one nudge measurement event.

    Accepts client event types only: shown, dismissed, clicked.
    For server-side holdout recording use record_holdout_assignment().

    Returns the created NudgeEvent on success, None on any error.
    Never raises — errors are logged and swallowed to preserve delivery.

    visitor_id = None: stored as NULL; contributes to aggregate counts
    but is excluded from attribution joins and variant stats (which
    require event_meta.copy_variant from a known-identity exposure).
    """
    if event_type not in CLIENT_EVENT_TYPES:
        log.warning(
            "nudge_measurement: rejected event_type=%r (not a client event) "
            "shop=%s nudge_id=%d",
            event_type, shop_domain, nudge_id,
        )
        return None

    if not visitor_id:
        log.warning(
            "nudge_measurement: %s event without visitor_id "
            "shop=%s nudge_id=%d product=%s — stored, excluded from attribution",
            event_type, shop_domain, nudge_id, product_url,
        )

    try:
        ev = NudgeEvent(
            shop_domain = shop_domain,
            nudge_id    = nudge_id,
            visitor_id  = visitor_id or None,
            product_url = product_url,
            event_type  = event_type,
            event_meta  = json.dumps(metadata) if metadata else None,
        )
        db.add(ev)
        db.flush()

        log.info(
            "nudge_measurement: recorded event_type=%s nudge_id=%d "
            "shop=%s product=%s visitor=%s variant=%s id=%d",
            event_type, nudge_id, shop_domain, product_url,
            (visitor_id[:8] + "…") if visitor_id else "none",
            (metadata or {}).get("copy_variant", "unknown"),
            ev.id,
        )
        return ev

    except Exception as exc:
        log.error(
            "nudge_measurement: failed to record event_type=%s nudge_id=%d shop=%s: %s",
            event_type, nudge_id, shop_domain, exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Write path — server-side holdout assignment recording
# ---------------------------------------------------------------------------

def record_holdout_assignment(
    db:          Session,
    shop_domain: str,
    nudge_id:    int,
    visitor_id:  str,
    product_url: str,
) -> Optional[NudgeEvent]:
    """
    Persist a server-side holdout assignment event.

    Called by GET /nudges/active when an eligible visitor is deterministically
    assigned to the holdout (control) group.  This event is never submitted by
    the client — it is written by the server as the authoritative record that
    this visitor was eligible but the nudge was suppressed.

    Multiple page loads by the same visitor produce multiple holdout_assigned
    events — this is intentional and mirrors how 'shown' events work.
    Stats queries use COUNT(DISTINCT visitor_id) to deduplicate.

    Returns the created NudgeEvent on success, None on any error.
    Never raises — errors are logged and swallowed to preserve delivery.
    """
    if not visitor_id:
        log.warning(
            "nudge_measurement: holdout_assigned without visitor_id — "
            "shop=%s nudge_id=%d product=%s — skipped (cannot attribute without identity)",
            shop_domain, nudge_id, product_url,
        )
        return None

    try:
        ev = NudgeEvent(
            shop_domain = shop_domain,
            nudge_id    = nudge_id,
            visitor_id  = visitor_id,
            product_url = product_url,
            event_type  = "holdout_assigned",
            event_meta  = json.dumps({"holdout": True}),
        )
        db.add(ev)
        db.flush()

        log.info(
            "nudge_measurement: holdout_assigned nudge_id=%d "
            "shop=%s product=%s visitor=%s id=%d",
            nudge_id, shop_domain, product_url,
            visitor_id[:8] + "…", ev.id,
        )
        return ev

    except Exception as exc:
        log.error(
            "nudge_measurement: failed to record holdout_assigned "
            "nudge_id=%d shop=%s visitor=%s: %s",
            nudge_id, shop_domain, visitor_id[:8] + "…", exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Read path — aggregate stats (all variants combined)
# ---------------------------------------------------------------------------

def get_nudge_stats(
    db:          Session,
    shop_domain: str,
    nudge_id:    int,
) -> dict:
    """
    Return per-event-type counts for a specific nudge (all variants combined).

    Uses COUNT(DISTINCT visitor_id) for unique visitor counts.
    Also returns total_events (including NULL visitor_id events) for audit.

    Returns:
        {
            "nudge_id":               int,
            "exposures":              int,
            "dismissals":             int,
            "clicks":                 int,
            "dismissal_rate":         float,
            "click_rate":             float,
            "total_shown_events":     int,
            "total_dismissed_events": int,
        }
    """
    try:
        rows = db.execute(
            text("""
                SELECT
                    event_type,
                    COUNT(DISTINCT CASE WHEN visitor_id IS NOT NULL
                                        THEN visitor_id END) AS identified_visitors,
                    COUNT(*)                                  AS total_events
                FROM nudge_events
                WHERE shop_domain = :shop
                  AND nudge_id    = :nudge_id
                GROUP BY event_type
            """),
            {"shop": shop_domain, "nudge_id": nudge_id},
        ).fetchall()

    except Exception as exc:
        log.error(
            "nudge_measurement: stats query failed shop=%s nudge_id=%d: %s",
            shop_domain, nudge_id, exc,
        )
        return _empty_stats(nudge_id)

    identified: dict[str, int] = {}
    total:      dict[str, int] = {}

    for row in rows:
        m  = row._mapping
        et = m["event_type"]
        identified[et] = int(m["identified_visitors"])
        total[et]      = int(m["total_events"])

    exposures  = identified.get("shown",     0)
    dismissals = identified.get("dismissed", 0)
    clicks     = identified.get("clicked",   0)

    return {
        "nudge_id":               nudge_id,
        "exposures":              exposures,
        "dismissals":             dismissals,
        "clicks":                 clicks,
        "dismissal_rate":         round(dismissals / exposures, 4) if exposures else 0.0,
        "click_rate":             round(clicks     / exposures, 4) if exposures else 0.0,
        "total_shown_events":     total.get("shown",     0),
        "total_dismissed_events": total.get("dismissed", 0),
    }


# ---------------------------------------------------------------------------
# Read path — aggregate outcome attribution (exposed group, with revenue)
# ---------------------------------------------------------------------------

def get_nudge_attribution(
    db:               Session,
    shop_domain:      str,
    nudge_id:         int,
    window_hours:     int           = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    exposed_visitors: Optional[int] = None,
) -> dict:
    """
    Compute observational post-exposure purchase attribution and revenue for a nudge.

    Attribution model:
      - Join first-exposure events → visitor_purchase_sessions → shop_orders
      - confirmed_at within window_hours of first_shown_at per visitor
      - Revenue = SUM(shop_orders.total_price) for all attributed orders
      - Observational only — not causal

    Revenue join:
      - visitor_purchase_sessions → shop_orders via shopify_order_id (1:1)
      - LEFT JOIN preserves purchases without order data (webhook missing)
      - SUM(total_price) correctly handles multiple orders per visitor

    Parameters
    ----------
    exposed_visitors : pre-computed from get_nudge_stats() to avoid a
        redundant query when both are called in sequence.
    """
    if exposed_visitors is None:
        exposed_visitors = get_nudge_stats(db, shop_domain, nudge_id)["exposures"]

    window_secs = window_hours * 3600

    try:
        result = db.execute(
            text("""
                WITH first_exposures AS (
                    SELECT visitor_id, MIN(created_at) AS first_shown_at
                    FROM nudge_events
                    WHERE shop_domain = :shop
                      AND nudge_id    = :nudge_id
                      AND event_type  = 'shown'
                      AND visitor_id  IS NOT NULL
                    GROUP BY visitor_id
                ),
                attributed_purchases AS (
                    SELECT
                        fe.visitor_id,
                        vps.shopify_order_id
                    FROM first_exposures fe
                    JOIN visitor_purchase_sessions vps
                      ON  vps.visitor_id  = fe.visitor_id
                      AND vps.shop_domain = :shop
                      AND vps.confirmed_at > fe.first_shown_at
                      AND vps.confirmed_at < fe.first_shown_at
                                             + (:window_secs * INTERVAL '1 second')
                )
                SELECT
                    COUNT(DISTINCT ap.visitor_id)       AS purchasers,
                    COALESCE(SUM(so.total_price), 0.0)  AS revenue,
                    COUNT(DISTINCT so.currency)         AS currency_count,
                    MIN(so.currency)                    AS sample_currency
                FROM attributed_purchases ap
                LEFT JOIN shop_orders so
                  ON  so.shopify_order_id = ap.shopify_order_id
                  AND so.shop_domain      = :shop
            """),
            {"shop": shop_domain, "nudge_id": nudge_id, "window_secs": window_secs},
        ).fetchone()

    except Exception as exc:
        log.error(
            "nudge_measurement: attribution query failed shop=%s nudge_id=%d: %s",
            shop_domain, nudge_id, exc,
        )
        return _empty_attribution(nudge_id, window_hours, exposed_visitors)

    if result is None:
        return _empty_attribution(nudge_id, window_hours, exposed_visitors)

    m              = result._mapping
    purchasers     = int(m["purchasers"])
    revenue        = float(m["revenue"] or 0.0)
    currency_count = int(m["currency_count"] or 0)
    sample_currency = str(m["sample_currency"]) if m["sample_currency"] else None
    cvr            = round(purchasers / exposed_visitors, 4) if exposed_visitors else 0.0
    currency, currency_note = _resolve_currency(currency_count, sample_currency)

    log.info(
        "nudge_measurement: attribution nudge_id=%d shop=%s window=%dh "
        "exposed=%d purchased=%d cvr=%.4f revenue=%.2f currency=%s",
        nudge_id, shop_domain, window_hours,
        exposed_visitors, purchasers, cvr, revenue, currency,
    )

    return {
        "nudge_id":                  nudge_id,
        "window_hours":              window_hours,
        "method":                    "observational_first_exposure",
        "attribution_note": (
            f"Observational only. Counts unique visitors who saw this nudge "
            f"and purchased within {window_hours}h of first exposure. "
            f"Does not imply causation — no holdout control group."
        ),
        "exposed_visitors":          exposed_visitors,
        "post_exposure_purchases":   purchasers,
        "post_exposure_cvr":         cvr,
        "purchase_session_revenue":  round(revenue, 2),
        "revenue_currency":          currency,
        "revenue_currency_note":     currency_note,
    }


# ---------------------------------------------------------------------------
# Read path — per-variant stats (A/B breakdown)
# ---------------------------------------------------------------------------

def get_nudge_variant_stats(
    db:          Session,
    shop_domain: str,
    nudge_id:    int,
) -> list[dict]:
    """
    Return per-variant exposure and dismissal counts.

    Extracts copy_variant from event_meta JSON using PostgreSQL's ->> operator.
    Only counts events where event_meta is present and copy_variant is set —
    events without variant data (legacy or missing event_meta) are excluded.

    Returns a list of dicts, one per observed variant:
        [
            {
                "variant_name": "high_interest",
                "exposures":    int,   # unique identified visitors shown this variant
                "dismissals":   int,   # unique identified visitors who dismissed
                "dismissal_rate": float,
            },
            ...
        ]
    """
    try:
        rows = db.execute(
            text("""
                SELECT
                    (event_meta::json->>'copy_variant')            AS variant_name,
                    event_type,
                    COUNT(DISTINCT visitor_id)                     AS unique_visitors
                FROM nudge_events
                WHERE shop_domain = :shop
                  AND nudge_id    = :nudge_id
                  AND visitor_id  IS NOT NULL
                  AND event_meta  IS NOT NULL
                  AND (event_meta::json->>'copy_variant') IS NOT NULL
                GROUP BY (event_meta::json->>'copy_variant'), event_type
            """),
            {"shop": shop_domain, "nudge_id": nudge_id},
        ).fetchall()

    except Exception as exc:
        log.error(
            "nudge_measurement: variant_stats query failed shop=%s nudge_id=%d: %s",
            shop_domain, nudge_id, exc,
        )
        return []

    # Reshape: {variant_name: {event_type: count}}
    variant_map: dict[str, dict[str, int]] = {}
    for row in rows:
        m    = row._mapping
        vn   = m["variant_name"]
        et   = m["event_type"]
        cnt  = int(m["unique_visitors"])
        if vn not in variant_map:
            variant_map[vn] = {}
        variant_map[vn][et] = cnt

    result = []
    for vn, counts in variant_map.items():
        exp  = counts.get("shown",     0)
        dis  = counts.get("dismissed", 0)
        clk  = counts.get("clicked",   0)
        result.append({
            "variant_name":   vn,
            "exposures":      exp,
            "dismissals":     dis,
            "clicks":         clk,
            "dismissal_rate": round(dis / exp, 4) if exp else 0.0,
            "click_rate":     round(clk / exp, 4) if exp else 0.0,
        })

    # Stable ordering: preserve AB_VARIANTS order, then alphabetical for unknowns
    from app.services.nudge_engine import AB_VARIANTS
    known_order = {v: i for i, v in enumerate(AB_VARIANTS)}
    result.sort(key=lambda x: known_order.get(x["variant_name"], 999))

    return result


# ---------------------------------------------------------------------------
# Read path — per-variant attribution
# ---------------------------------------------------------------------------

def get_nudge_variant_attribution(
    db:           Session,
    shop_domain:  str,
    nudge_id:     int,
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
) -> list[dict]:
    """
    Compute observational post-exposure purchase attribution per variant.

    Because variant assignment is deterministic (hash-based), each visitor
    always sees the same variant for a given nudge.  MIN(created_at) gives
    the first exposure, and MIN(event_meta->>'copy_variant') is stable
    (same visitor always has the same variant in event_meta).

    Returns a list of dicts per variant:
        [
            {
                "variant_name":             str,
                "exposed_visitors":         int,
                "post_exposure_purchases":  int,
                "post_exposure_cvr":        float,
            },
            ...
        ]
    """
    window_secs = window_hours * 3600

    try:
        rows = db.execute(
            text("""
                WITH first_exposures AS (
                    SELECT
                        visitor_id,
                        MIN(created_at)                        AS first_shown_at,
                        MIN(event_meta::json->>'copy_variant') AS variant_name
                    FROM nudge_events
                    WHERE shop_domain = :shop
                      AND nudge_id    = :nudge_id
                      AND event_type  = 'shown'
                      AND visitor_id  IS NOT NULL
                      AND event_meta  IS NOT NULL
                      AND (event_meta::json->>'copy_variant') IS NOT NULL
                    GROUP BY visitor_id
                ),
                purchases AS (
                    SELECT
                        fe.variant_name,
                        COUNT(DISTINCT vps.visitor_id) AS purchasers
                    FROM first_exposures fe
                    JOIN visitor_purchase_sessions vps
                      ON  vps.visitor_id  = fe.visitor_id
                      AND vps.shop_domain = :shop
                      AND vps.confirmed_at > fe.first_shown_at
                      AND vps.confirmed_at < fe.first_shown_at
                                             + (:window_secs * INTERVAL '1 second')
                    GROUP BY fe.variant_name
                ),
                exposures AS (
                    SELECT variant_name, COUNT(DISTINCT visitor_id) AS exposed
                    FROM first_exposures
                    GROUP BY variant_name
                )
                SELECT
                    e.variant_name,
                    e.exposed         AS exposed_visitors,
                    COALESCE(p.purchasers, 0) AS purchasers
                FROM exposures e
                LEFT JOIN purchases p USING (variant_name)
            """),
            {"shop": shop_domain, "nudge_id": nudge_id, "window_secs": window_secs},
        ).fetchall()

    except Exception as exc:
        log.error(
            "nudge_measurement: variant_attribution query failed shop=%s nudge_id=%d: %s",
            shop_domain, nudge_id, exc,
        )
        return []

    from app.services.nudge_engine import AB_VARIANTS
    known_order = {v: i for i, v in enumerate(AB_VARIANTS)}

    result = []
    for row in rows:
        m   = row._mapping
        exp = int(m["exposed_visitors"])
        pur = int(m["purchasers"])
        result.append({
            "variant_name":            m["variant_name"],
            "exposed_visitors":        exp,
            "post_exposure_purchases": pur,
            "post_exposure_cvr":       round(pur / exp, 4) if exp else 0.0,
        })

    result.sort(key=lambda x: known_order.get(x["variant_name"], 999))
    return result


# ---------------------------------------------------------------------------
# Read path — holdout group stats
# ---------------------------------------------------------------------------

def get_holdout_stats(
    db:          Session,
    shop_domain: str,
    nudge_id:    int,
) -> dict:
    """
    Return holdout group size for this nudge.

    Uses COUNT(DISTINCT visitor_id) — mirrors how exposed count is computed.
    Multiple page loads by the same holdout visitor produce multiple
    holdout_assigned events; dedup is handled here.

    Returns:
        {
            "holdout_count": int,   # unique identified visitors in holdout group
        }
    """
    try:
        result = db.execute(
            text("""
                SELECT COUNT(DISTINCT visitor_id) AS holdout_count
                FROM nudge_events
                WHERE shop_domain = :shop
                  AND nudge_id    = :nudge_id
                  AND event_type  = 'holdout_assigned'
                  AND visitor_id  IS NOT NULL
            """),
            {"shop": shop_domain, "nudge_id": nudge_id},
        ).fetchone()

    except Exception as exc:
        log.error(
            "nudge_measurement: holdout_stats query failed shop=%s nudge_id=%d: %s",
            shop_domain, nudge_id, exc,
        )
        return {"holdout_count": 0}

    holdout_count = int(result._mapping["holdout_count"]) if result else 0
    return {"holdout_count": holdout_count}


# ---------------------------------------------------------------------------
# Read path — holdout group attribution (with revenue)
# ---------------------------------------------------------------------------

def get_holdout_attribution(
    db:            Session,
    shop_domain:   str,
    nudge_id:      int,
    window_hours:  int           = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    holdout_count: Optional[int] = None,
) -> dict:
    """
    Compute purchase attribution and revenue for the holdout (control) group.

    Attribution model mirrors the exposed group:
      - Join first holdout_assigned event → visitor_purchase_sessions → shop_orders
      - confirmed_at within window_hours of first_assigned_at per visitor
      - Revenue = SUM(shop_orders.total_price) for all attributed orders

    This gives the baseline purchase rate and baseline revenue per visitor for
    eligible-but-suppressed visitors — the counterfactual estimate of "what would
    these visitors have generated without the nudge?"

    Parameters
    ----------
    holdout_count : pre-computed from get_holdout_stats() to avoid a
        redundant query when both are called in sequence.
    """
    if holdout_count is None:
        holdout_count = get_holdout_stats(db, shop_domain, nudge_id)["holdout_count"]

    window_secs = window_hours * 3600

    try:
        result = db.execute(
            text("""
                WITH first_holdouts AS (
                    SELECT visitor_id, MIN(created_at) AS first_assigned_at
                    FROM nudge_events
                    WHERE shop_domain = :shop
                      AND nudge_id    = :nudge_id
                      AND event_type  = 'holdout_assigned'
                      AND visitor_id  IS NOT NULL
                    GROUP BY visitor_id
                ),
                attributed_purchases AS (
                    SELECT
                        fh.visitor_id,
                        vps.shopify_order_id
                    FROM first_holdouts fh
                    JOIN visitor_purchase_sessions vps
                      ON  vps.visitor_id  = fh.visitor_id
                      AND vps.shop_domain = :shop
                      AND vps.confirmed_at > fh.first_assigned_at
                      AND vps.confirmed_at < fh.first_assigned_at
                                             + (:window_secs * INTERVAL '1 second')
                )
                SELECT
                    COUNT(DISTINCT ap.visitor_id)       AS purchasers,
                    COALESCE(SUM(so.total_price), 0.0)  AS revenue,
                    COUNT(DISTINCT so.currency)         AS currency_count,
                    MIN(so.currency)                    AS sample_currency
                FROM attributed_purchases ap
                LEFT JOIN shop_orders so
                  ON  so.shopify_order_id = ap.shopify_order_id
                  AND so.shop_domain      = :shop
            """),
            {"shop": shop_domain, "nudge_id": nudge_id, "window_secs": window_secs},
        ).fetchone()

    except Exception as exc:
        log.error(
            "nudge_measurement: holdout_attribution query failed shop=%s nudge_id=%d: %s",
            shop_domain, nudge_id, exc,
        )
        return _empty_holdout_attribution(nudge_id, window_hours, holdout_count)

    if result is None:
        return _empty_holdout_attribution(nudge_id, window_hours, holdout_count)

    m               = result._mapping
    purchasers      = int(m["purchasers"])
    revenue         = float(m["revenue"] or 0.0)
    currency_count  = int(m["currency_count"] or 0)
    sample_currency = str(m["sample_currency"]) if m["sample_currency"] else None
    holdout_cvr     = round(purchasers / holdout_count, 4) if holdout_count else 0.0
    currency, currency_note = _resolve_currency(currency_count, sample_currency)

    log.info(
        "nudge_measurement: holdout_attribution nudge_id=%d shop=%s window=%dh "
        "holdout=%d purchased=%d cvr=%.4f revenue=%.2f currency=%s",
        nudge_id, shop_domain, window_hours,
        holdout_count, purchasers, holdout_cvr, revenue, currency,
    )

    return {
        "nudge_id":          nudge_id,
        "window_hours":      window_hours,
        "holdout_count":     holdout_count,
        "holdout_purchases": purchasers,
        "holdout_cvr":       holdout_cvr,
        "holdout_revenue":   round(revenue, 2),
        "revenue_currency":  currency,
        "revenue_currency_note": currency_note,
    }


# ---------------------------------------------------------------------------
# Read path — revenue lift computation
# ---------------------------------------------------------------------------

def _compute_revenue_lift(
    exposed_count:    int,
    holdout_count:    int,
    exposed_revenue:  float,
    holdout_revenue:  float,
    exposed_purchases: int,
    holdout_purchases: int,
    exposed_currency:  str,
    holdout_currency:  str,
    window_hours:      int,
) -> dict:
    """
    Compute all revenue lift metrics from exposed and holdout group data.

    Metrics:
        exposed_rpv      = exposed_revenue / exposed_count
        holdout_rpv      = holdout_revenue / holdout_count
        incremental_rpv  = exposed_rpv − holdout_rpv
        revenue_lift_pct = incremental_rpv / holdout_rpv × 100

        estimated_incremental_revenue =
            exposed_revenue − (holdout_rpv × exposed_count)

        Derivation: holdout_rpv is the control baseline — revenue per eligible
        visitor without the nudge.  Multiplied by exposed_count, this gives the
        counterfactual revenue the exposed group would have generated without the
        nudge.  The difference is the estimated nudge contribution within the
        observed experiment population.

    has_order_data:
        False when purchases > 0 but revenue = 0, meaning no shop_orders rows
        matched (webhook not configured or not yet received for these orders).
        When False, all revenue figures are zero and must not be reported as real.

    Currency:
        If both groups use the same currency → use that currency.
        If they differ (possible if shop switched currencies mid-experiment) →
        label as "mixed" and note it.

    No formal revenue significance test:
        Revenue per visitor is a continuous random variable with unknown variance.
        Without per-visitor revenue distributions (available only with per-row
        queries), a reliable t-test requires additional data not available in the
        aggregate queries.  Use the CVR z-test (in the parent lift report) as the
        proxy for statistical confidence.  Revenue figures are presented with honest
        sample sufficiency labels only.

    Returns a self-contained revenue_lift dict suitable for direct inclusion in
    the lift report response.
    """
    # Currency resolution — use consistent label across both groups
    if exposed_currency == holdout_currency and exposed_currency not in ("unknown", "mixed"):
        currency = exposed_currency
        currency_note = None
    elif exposed_currency == "unknown" and holdout_currency == "unknown":
        currency = "unknown"
        currency_note = (
            "No order data available for either group. "
            "Ensure the Shopify orders/paid webhook is configured."
        )
    else:
        currency = "mixed"
        currency_note = (
            f"Revenue figures are summed across multiple currencies "
            f"({exposed_currency} / {holdout_currency}). "
            "Do not compare raw totals — use RPV ratios only."
        )

    # Order data health check
    exposed_has_data  = not (exposed_purchases > 0 and exposed_revenue == 0)
    holdout_has_data  = not (holdout_purchases > 0 and holdout_revenue == 0)
    has_order_data    = exposed_has_data and holdout_has_data

    # Sample sufficiency
    under_threshold = []
    if exposed_count < MIN_SAMPLE_PER_GROUP:
        under_threshold.append(f"exposed={exposed_count}")
    if holdout_count < MIN_SAMPLE_PER_GROUP:
        under_threshold.append(f"holdout={holdout_count}")
    sample_state = "insufficient" if under_threshold else "sufficient"

    # Revenue per visitor
    exposed_rpv = round(exposed_revenue / exposed_count, 4) if exposed_count else 0.0
    holdout_rpv = round(holdout_revenue / holdout_count, 4) if holdout_count else 0.0

    # Incremental RPV
    incremental_rpv = round(exposed_rpv - holdout_rpv, 4)

    # Revenue lift %
    if holdout_rpv > 0:
        revenue_lift_pct: Optional[float] = round(incremental_rpv / holdout_rpv * 100, 2)
    elif exposed_rpv > 0 and holdout_rpv == 0:
        revenue_lift_pct = None   # positive but baseline is zero — undefined ratio
    else:
        revenue_lift_pct = 0.0

    # Estimated incremental revenue within experiment population
    # = exposed_revenue − (holdout_rpv × exposed_count)
    # = what exposed group generated − what they'd have generated at holdout baseline
    if has_order_data and holdout_count > 0:
        counterfactual_revenue = holdout_rpv * exposed_count
        estimated_incremental_revenue: Optional[float] = round(
            exposed_revenue - counterfactual_revenue, 2
        )
    else:
        estimated_incremental_revenue = None

    log.info(
        "nudge_measurement: revenue_lift nudge exposed=%d holdout=%d "
        "exposed_rpv=%.2f holdout_rpv=%.2f incremental_rpv=%.2f "
        "lift_pct=%s estimated_incr=%.2f has_order_data=%s sample=%s currency=%s",
        exposed_count, holdout_count,
        exposed_rpv, holdout_rpv, incremental_rpv,
        f"{revenue_lift_pct:.1f}%" if revenue_lift_pct is not None else "n/a",
        estimated_incremental_revenue or 0,
        has_order_data, sample_state, currency,
    )

    # Revenue note for consumers
    revenue_note_parts = [
        "Revenue figures are quasi-experimental estimates based on hash-based "
        "holdout assignment (not a true RCT).",
        f"Attribution window: {window_hours}h from first qualifying event.",
        "estimated_incremental_revenue = exposed_revenue − (holdout_rpv × exposed_count). "
        "This is the estimated nudge contribution within the observed experiment population only.",
    ]
    if not has_order_data:
        revenue_note_parts.append(
            "WARNING: has_order_data=False — shop_orders is empty or no orders matched "
            "the attribution join. Revenue figures are zero. "
            "Verify that the Shopify orders/paid webhook (POST /webhooks/shopify/orders-paid) "
            "is active and delivering."
        )
    if sample_state == "insufficient":
        revenue_note_parts.append(
            f"Sample insufficient for reliable RPV comparison "
            f"(need ≥{MIN_SAMPLE_PER_GROUP} per group; under threshold: "
            f"{', '.join(under_threshold)})."
        )

    return {
        "has_order_data":                has_order_data,
        "exposed_revenue":               round(exposed_revenue, 2),
        "holdout_revenue":               round(holdout_revenue, 2),
        "exposed_rpv":                   exposed_rpv,
        "holdout_rpv":                   holdout_rpv,
        "incremental_rpv":               incremental_rpv,
        "revenue_lift_pct":              revenue_lift_pct,
        "estimated_incremental_revenue": estimated_incremental_revenue,
        "currency":                      currency,
        "currency_note":                 currency_note,
        "sample_state":                  sample_state,
        "min_sample_required":           MIN_SAMPLE_PER_GROUP,
        "revenue_note":                  " ".join(revenue_note_parts),
        # Agent-ready ranking signal: use incremental_rpv for nudge value ordering.
        # Positive = nudge adds revenue per eligible visitor.
        # Negative = nudge may be suppressing revenue (rare, investigate).
        # None = insufficient data or no order integration.
        "agent_ranking_signal": {
            "incremental_rpv":               incremental_rpv if has_order_data and sample_state == "sufficient" else None,
            "estimated_incremental_revenue": estimated_incremental_revenue,
            "revenue_lift_pct":              revenue_lift_pct if has_order_data and sample_state == "sufficient" else None,
        },
    }


# ---------------------------------------------------------------------------
# Read path — incremental lift estimation (CVR + Revenue)
# ---------------------------------------------------------------------------

def get_nudge_lift_report(
    db:           Session,
    shop_domain:  str,
    nudge_id:     int,
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
) -> dict:
    """
    Estimate incremental lift for a nudge using the holdout/control group.

    Combines CVR-based lift (proportion z-test) with revenue-based lift
    (RPV comparison, estimated incremental revenue) into a single report.

    Compares post-event behavior between:
      - Exposed group  — eligible visitors who received the nudge (shown events)
      - Holdout group  — eligible visitors who were suppressed (holdout_assigned events)

    Both groups are drawn from the same eligible population (behavioral gate passed).
    Both attribution windows are measured from the visitor's first qualifying event.
    The only systematic difference is whether the nudge was rendered.

    Returns:
        {
            "holdout_active":              bool,
            "exposed_count":               int,
            "holdout_count":               int,
            "exposed_purchases":           int,
            "holdout_purchases":           int,
            "exposed_cvr":                 float,
            "holdout_cvr":                 float,
            "estimated_lift_pct":          float | null,   # CVR lift
            "cvr_delta":                   float,
            "sample_state":                str,
            "min_sample_required":         int,
            "z_score":                     float,
            "p_value":                     float,
            "significance":                str,
            "method":                      "quasi_experimental_holdout",
            "attribution_note":            str,
            "window_hours":                int,
            "revenue_lift": {              # Revenue-weighted lift block
                "has_order_data":                bool,
                "exposed_revenue":               float,
                "holdout_revenue":               float,
                "exposed_rpv":                   float,
                "holdout_rpv":                   float,
                "incremental_rpv":               float,
                "revenue_lift_pct":              float | null,
                "estimated_incremental_revenue": float | null,
                "currency":                      str,
                "currency_note":                 str | null,
                "sample_state":                  str,
                "min_sample_required":           int,
                "revenue_note":                  str,
                "agent_ranking_signal": {
                    "incremental_rpv":               float | null,
                    "estimated_incremental_revenue": float | null,
                    "revenue_lift_pct":              float | null,
                },
            },
        }

    Honest labeling throughout:
      - "quasi_experimental_holdout" — not "randomized_controlled_trial"
      - "estimated_lift_pct" — not "proven_lift"
      - "estimated_incremental_revenue" — not "proven_causal_revenue"
      - revenue significance uses CVR z-test as proxy (labeled explicitly)

    When holdout_count = 0 (holdout not enabled or no data yet):
      returns holdout_active=False with all zeros and inactive revenue_lift.
    """
    holdout_stats = get_holdout_stats(db=db, shop_domain=shop_domain, nudge_id=nudge_id)
    holdout_count = holdout_stats["holdout_count"]

    if holdout_count == 0:
        return _inactive_lift_report(nudge_id, window_hours)

    exposed_stats = get_nudge_stats(db=db, shop_domain=shop_domain, nudge_id=nudge_id)
    exposed_count = exposed_stats["exposures"]

    holdout_attr = get_holdout_attribution(
        db            = db,
        shop_domain   = shop_domain,
        nudge_id      = nudge_id,
        window_hours  = window_hours,
        holdout_count = holdout_count,
    )
    exposed_attr = get_nudge_attribution(
        db               = db,
        shop_domain      = shop_domain,
        nudge_id         = nudge_id,
        window_hours     = window_hours,
        exposed_visitors = exposed_count,
    )

    exposed_purchases = exposed_attr["post_exposure_purchases"]
    holdout_purchases = holdout_attr["holdout_purchases"]
    exposed_cvr       = exposed_attr["post_exposure_cvr"]
    holdout_cvr       = holdout_attr["holdout_cvr"]
    exposed_revenue   = exposed_attr.get("purchase_session_revenue") or 0.0
    holdout_revenue   = holdout_attr.get("holdout_revenue") or 0.0
    exposed_currency  = exposed_attr.get("revenue_currency") or "unknown"
    holdout_currency  = holdout_attr.get("revenue_currency") or "unknown"

    # -----------------------------------------------------------------------
    # CVR lift
    # -----------------------------------------------------------------------
    under_threshold = []
    if exposed_count < MIN_SAMPLE_PER_GROUP:
        under_threshold.append(f"exposed={exposed_count}")
    if holdout_count < MIN_SAMPLE_PER_GROUP:
        under_threshold.append(f"holdout={holdout_count}")
    sample_state = "insufficient" if under_threshold else "sufficient"

    if exposed_cvr > 0 and holdout_cvr > 0:
        estimated_lift_pct: Optional[float] = round(
            (exposed_cvr - holdout_cvr) / holdout_cvr * 100, 2
        )
    elif exposed_cvr > 0 and holdout_cvr == 0:
        estimated_lift_pct = None  # positive but baseline is zero — undefined
    elif exposed_cvr == 0 and holdout_cvr == 0:
        estimated_lift_pct = 0.0
    else:
        estimated_lift_pct = round(
            (exposed_cvr - holdout_cvr) / holdout_cvr * 100, 2
        )

    cvr_delta = round(exposed_cvr - holdout_cvr, 4)

    if sample_state == "sufficient":
        z, p = _two_prop_z_test(exposed_count, exposed_purchases,
                                holdout_count, holdout_purchases)
        z = round(z, 4)
        p = round(p, 4)
        if p < 0.05:
            significance = f"p={p} — >95% confidence (one-tailed, quasi-experimental)"
        elif p < 0.10:
            significance = f"p={p} — >90% confidence (one-tailed, quasi-experimental)"
        else:
            significance = f"p={p} — no meaningful difference yet"
    else:
        z = 0.0
        p = 1.0
        significance = (
            f"Insufficient sample — need ≥{MIN_SAMPLE_PER_GROUP} per group. "
            f"Under threshold: {', '.join(under_threshold)}."
        )

    log.info(
        "nudge_measurement: lift_report nudge_id=%d shop=%s window=%dh "
        "exposed=%d holdout=%d exposed_cvr=%.4f holdout_cvr=%.4f "
        "cvr_lift=%.2f%% z=%.3f p=%.4f "
        "exposed_rev=%.2f holdout_rev=%.2f currency=%s sample=%s",
        nudge_id, shop_domain, window_hours,
        exposed_count, holdout_count, exposed_cvr, holdout_cvr,
        estimated_lift_pct or 0, z, p,
        exposed_revenue, holdout_revenue, exposed_currency, sample_state,
    )

    # -----------------------------------------------------------------------
    # Revenue lift block
    # -----------------------------------------------------------------------
    revenue_lift = _compute_revenue_lift(
        exposed_count     = exposed_count,
        holdout_count     = holdout_count,
        exposed_revenue   = exposed_revenue,
        holdout_revenue   = holdout_revenue,
        exposed_purchases = exposed_purchases,
        holdout_purchases = holdout_purchases,
        exposed_currency  = exposed_currency,
        holdout_currency  = holdout_currency,
        window_hours      = window_hours,
    )

    return {
        "holdout_active":      True,
        "exposed_count":       exposed_count,
        "holdout_count":       holdout_count,
        "exposed_purchases":   exposed_purchases,
        "holdout_purchases":   holdout_purchases,
        "exposed_cvr":         exposed_cvr,
        "holdout_cvr":         holdout_cvr,
        "estimated_lift_pct":  estimated_lift_pct,
        "cvr_delta":           cvr_delta,
        "sample_state":        sample_state,
        "min_sample_required": MIN_SAMPLE_PER_GROUP,
        "z_score":             z,
        "p_value":             p,
        "significance":        significance,
        "method":              "quasi_experimental_holdout",
        "attribution_note": (
            "Quasi-experimental holdout design. Both groups passed the behavioral "
            "eligibility gate. Assignment is deterministic via "
            "MD5(visitor_id:holdout:nudge_id) % 100 — pseudo-random, not a true RCT. "
            f"CVR lift is estimated directionally. Attribution window: {window_hours}h "
            "from first qualifying event per visitor. "
            "Revenue lift uses the same window and join chain. "
            "Do not claim proven causation — claim 'estimated incremental lift'."
        ),
        "window_hours":   window_hours,
        "revenue_lift":   revenue_lift,
    }


# ---------------------------------------------------------------------------
# Winner selection — proportion z-test
# ---------------------------------------------------------------------------

def _normal_cdf(z: float) -> float:
    """Standard normal CDF via error function (stdlib only — no scipy needed)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _two_prop_z_test(n1: int, k1: int, n2: int, k2: int) -> tuple[float, float]:
    """
    One-tailed z-test for two proportions: H1 = p1 > p2.

    Returns (z_score, one_tailed_p_value).
    Safe for edge cases (n=0, p=0, p=1, se=0).
    """
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1     = k1 / n1
    p2     = k2 / n2
    p_pool = (k1 + k2) / (n1 + n2)
    if p_pool <= 0.0 or p_pool >= 1.0:
        return 0.0, 1.0
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n2))
    if se < 1e-10:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # One-tailed: P(Z > z) = 1 - Phi(z)
    p = 1.0 - _normal_cdf(z)
    return z, p


def _compute_winner(variant_attribution: list[dict]) -> dict:
    """
    Determine which copy variant is leading based on post-exposure CVR.

    Uses a one-tailed proportion z-test between the leading and trailing
    variant.  All significance is labeled "observational" — not causal.

    Decision labels:
      "insufficient_sample"       — any variant has < MIN_SAMPLE_PER_VARIANT exposures
      "no_significant_difference" — p >= 0.10
      "provisional_leader"        — p < 0.10 (90%+ confidence, one-tailed)
      "confident_leader"          — p < 0.05 (95%+ confidence, one-tailed)

    Returns:
        {
            "decision":         str,
            "winner_variant":   str | null,
            "runner_up":        str | null,
            "cvr_delta":        float,
            "z_score":          float,
            "p_value":          float,
            "significance":     str,   # human-readable confidence label
            "sample_sizes":     {variant_name: exposures},
            "min_sample_required": int,
            "method":           "observational_proportion_z_test",
            "note":             str,
        }
    """
    if len(variant_attribution) < 2:
        return {
            "decision":            "no_comparison_possible",
            "winner_variant":      None,
            "runner_up":           None,
            "cvr_delta":           0.0,
            "z_score":             0.0,
            "p_value":             1.0,
            "significance":        "n/a",
            "sample_sizes":        {v["variant_name"]: v["exposed_visitors"]
                                    for v in variant_attribution},
            "min_sample_required": MIN_SAMPLE_PER_VARIANT,
            "method":              "observational_proportion_z_test",
            "note":                "Fewer than 2 variants with measurement data.",
        }

    sample_sizes = {v["variant_name"]: v["exposed_visitors"] for v in variant_attribution}

    # Check minimum sample requirement
    under_threshold = [
        vn for vn, n in sample_sizes.items()
        if n < MIN_SAMPLE_PER_VARIANT
    ]
    if under_threshold:
        return {
            "decision":            "insufficient_sample",
            "winner_variant":      None,
            "runner_up":           None,
            "cvr_delta":           0.0,
            "z_score":             0.0,
            "p_value":             1.0,
            "significance":        "n/a",
            "sample_sizes":        sample_sizes,
            "min_sample_required": MIN_SAMPLE_PER_VARIANT,
            "method":              "observational_proportion_z_test",
            "note": (
                f"Minimum {MIN_SAMPLE_PER_VARIANT} exposures required per variant. "
                f"Variants below threshold: {under_threshold}."
            ),
        }

    # Sort by CVR descending to find leader vs trailer
    ranked = sorted(variant_attribution, key=lambda v: v["post_exposure_cvr"], reverse=True)
    leader  = ranked[0]
    trailer = ranked[-1]

    n1, k1 = leader["exposed_visitors"],  leader["post_exposure_purchases"]
    n2, k2 = trailer["exposed_visitors"], trailer["post_exposure_purchases"]

    z, p = _two_prop_z_test(n1, k1, n2, k2)

    cvr_delta = round(leader["post_exposure_cvr"] - trailer["post_exposure_cvr"], 4)

    if p < 0.05:
        decision      = "confident_leader"
        significance  = f"p={round(p, 3)} — >95% confidence (one-tailed, observational)"
    elif p < 0.10:
        decision      = "provisional_leader"
        significance  = f"p={round(p, 3)} — >90% confidence (one-tailed, observational)"
    else:
        decision      = "no_significant_difference"
        significance  = f"p={round(p, 3)} — no meaningful difference yet"

    log.info(
        "nudge_measurement: winner_check nudge variants=%s decision=%s "
        "leader=%s cvr_delta=%.4f z=%.3f p=%.4f",
        [v["variant_name"] for v in ranked],
        decision, leader["variant_name"], cvr_delta, z, p,
    )

    return {
        "decision":            decision,
        "winner_variant":      leader["variant_name"] if decision != "no_significant_difference" else None,
        "runner_up":           trailer["variant_name"],
        "cvr_delta":           cvr_delta,
        "z_score":             round(z, 4),
        "p_value":             round(p, 4),
        "significance":        significance,
        "sample_sizes":        sample_sizes,
        "min_sample_required": MIN_SAMPLE_PER_VARIANT,
        "method":              "observational_proportion_z_test",
        "note": (
            "Significance is observational — based on post-exposure purchase attribution, "
            "not a randomized controlled experiment with a holdout group. "
            "A 'confident_leader' decision is safe for autonomous variant promotion "
            "but does not prove causation."
        ),
    }


# ---------------------------------------------------------------------------
# Combined reports
# ---------------------------------------------------------------------------

def get_nudge_full_report(
    db:           Session,
    shop_domain:  str,
    nudge_id:     int,
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
) -> dict:
    """
    Aggregate stats + attribution.  Two DB round trips.
    Backward-compatible with callers before A/B was implemented.
    """
    stats       = get_nudge_stats(db=db, shop_domain=shop_domain, nudge_id=nudge_id)
    attribution = get_nudge_attribution(
        db               = db,
        shop_domain      = shop_domain,
        nudge_id         = nudge_id,
        window_hours     = window_hours,
        exposed_visitors = stats["exposures"],
    )
    return {
        "nudge_id":    nudge_id,
        "stats":       stats,
        "attribution": attribution,
    }


def get_nudge_ab_report(
    db:           Session,
    shop_domain:  str,
    nudge_id:     int,
    window_hours: int = DEFAULT_ATTRIBUTION_WINDOW_HOURS,
) -> dict:
    """
    Full A/B measurement report: aggregate + per-variant + winner selection.

    Five DB round trips (aggregate stats, aggregate attribution, variant stats,
    variant attribution) — intentionally separated for clarity and error isolation.

    Returns:
        {
            "nudge_id":       int,
            "stats":          { aggregate exposure/dismissal counts },
            "attribution":    { aggregate post-exposure attribution + revenue },
            "ab_experiment":  {
                "is_active":       bool,
                "variants":        [per-variant stats + attribution merged],
                "winner":          { decision, winner_variant, p_value, ... },
                "window_hours":    int,
                "attribution_method": "observational_first_exposure",
            }
        }
    """
    stats       = get_nudge_stats(db=db, shop_domain=shop_domain, nudge_id=nudge_id)
    attribution = get_nudge_attribution(
        db               = db,
        shop_domain      = shop_domain,
        nudge_id         = nudge_id,
        window_hours     = window_hours,
        exposed_visitors = stats["exposures"],
    )

    variant_stats = get_nudge_variant_stats(db=db, shop_domain=shop_domain, nudge_id=nudge_id)
    variant_attr  = get_nudge_variant_attribution(
        db=db, shop_domain=shop_domain, nudge_id=nudge_id, window_hours=window_hours,
    )

    # Merge variant stats + attribution by variant_name
    attr_by_name = {va["variant_name"]: va for va in variant_attr}
    merged_variants = []
    for vs in variant_stats:
        vn = vs["variant_name"]
        va = attr_by_name.get(vn, {})
        merged_variants.append({
            "variant_name":            vn,
            "exposures":               vs["exposures"],
            "dismissals":              vs["dismissals"],
            "clicks":                  vs["clicks"],
            "dismissal_rate":          vs["dismissal_rate"],
            "click_rate":              vs["click_rate"],
            "post_exposure_purchases": va.get("post_exposure_purchases", 0),
            "post_exposure_cvr":       va.get("post_exposure_cvr", 0.0),
        })

    # Winner selection — only when we have data for 2+ variants
    winner = _compute_winner(variant_attr) if len(variant_attr) >= 2 else {
        "decision":            "no_variant_data",
        "winner_variant":      None,
        "runner_up":           None,
        "note": (
            "No per-variant attribution data yet — exposures may lack "
            "copy_variant in event_meta."
        ),
        "min_sample_required": MIN_SAMPLE_PER_VARIANT,
        "method":              "observational_proportion_z_test",
    }

    return {
        "nudge_id":    nudge_id,
        "stats":       stats,
        "attribution": attribution,
        "ab_experiment": {
            "is_active":          len(merged_variants) > 0,
            "variants":           merged_variants,
            "winner":             winner,
            "window_hours":       window_hours,
            "attribution_method": "observational_first_exposure",
            "note": (
                "Per-variant stats require copy_variant to be present in "
                "nudge_events.event_meta (populated by spark-nudge.js v4+). "
                "Events without event_meta are counted in aggregate totals only."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_currency(currency_count: int, sample_currency: Optional[str]) -> tuple[str, Optional[str]]:
    """
    Return (currency_label, currency_note) from query aggregates.

    currency_count = COUNT(DISTINCT so.currency) from the revenue query.
    sample_currency = MIN(so.currency) — one representative currency.

    Rules:
      0 currencies → no order data → "unknown", note is None
      1 currency   → clean → use that currency, note is None
      >1 currencies → mixed → "mixed", note explains the issue
    """
    if currency_count == 0:
        return "unknown", None
    if currency_count == 1:
        return (sample_currency or "unknown"), None
    return "mixed", (
        f"Orders in {currency_count} different currencies. "
        "Revenue figures are summed without conversion — use RPV ratios, not raw totals."
    )


def _empty_stats(nudge_id: int) -> dict:
    return {
        "nudge_id":               nudge_id,
        "exposures":              0,
        "dismissals":             0,
        "clicks":                 0,
        "dismissal_rate":         0.0,
        "click_rate":             0.0,
        "total_shown_events":     0,
        "total_dismissed_events": 0,
    }


def _empty_attribution(nudge_id: int, window_hours: int, exposed_visitors: int = 0) -> dict:
    return {
        "nudge_id":                  nudge_id,
        "window_hours":              window_hours,
        "method":                    "observational_first_exposure",
        "attribution_note":          "Attribution query failed — data unavailable.",
        "exposed_visitors":          exposed_visitors,
        "post_exposure_purchases":   0,
        "post_exposure_cvr":         0.0,
        "purchase_session_revenue":  0.0,
        "revenue_currency":          "unknown",
        "revenue_currency_note":     None,
    }


def _empty_holdout_attribution(
    nudge_id: int, window_hours: int, holdout_count: int = 0
) -> dict:
    return {
        "nudge_id":              nudge_id,
        "window_hours":          window_hours,
        "holdout_count":         holdout_count,
        "holdout_purchases":     0,
        "holdout_cvr":           0.0,
        "holdout_revenue":       0.0,
        "revenue_currency":      "unknown",
        "revenue_currency_note": None,
    }


def _inactive_revenue_lift(window_hours: int) -> dict:
    """Return a revenue_lift block indicating holdout is not yet active."""
    note = (
        "Holdout not enabled or no holdout_assigned events recorded yet. "
        "Enable holdout via PATCH /pro/nudges/{id}/holdout to begin "
        "revenue lift measurement."
    )
    return {
        "has_order_data":                False,
        "exposed_revenue":               0.0,
        "holdout_revenue":               0.0,
        "exposed_rpv":                   0.0,
        "holdout_rpv":                   0.0,
        "incremental_rpv":               0.0,
        "revenue_lift_pct":              None,
        "estimated_incremental_revenue": None,
        "currency":                      "unknown",
        "currency_note":                 None,
        "sample_state":                  "insufficient",
        "min_sample_required":           MIN_SAMPLE_PER_GROUP,
        "revenue_note":                  note,
        "agent_ranking_signal": {
            "incremental_rpv":               None,
            "estimated_incremental_revenue": None,
            "revenue_lift_pct":              None,
        },
    }


def _inactive_lift_report(nudge_id: int, window_hours: int) -> dict:
    """Return a lift report indicating holdout is not yet active."""
    return {
        "holdout_active":      False,
        "exposed_count":       0,
        "holdout_count":       0,
        "exposed_purchases":   0,
        "holdout_purchases":   0,
        "exposed_cvr":         0.0,
        "holdout_cvr":         0.0,
        "estimated_lift_pct":  None,
        "cvr_delta":           0.0,
        "sample_state":        "insufficient",
        "min_sample_required": MIN_SAMPLE_PER_GROUP,
        "z_score":             0.0,
        "p_value":             1.0,
        "significance":        "n/a — holdout not enabled or no data yet",
        "method":              "quasi_experimental_holdout",
        "attribution_note": (
            "Holdout is not enabled for this nudge (holdout_pct = 0) or no "
            "holdout_assigned events have been recorded yet. Enable holdout via "
            "PATCH /pro/nudges/{id}/holdout to begin lift measurement."
        ),
        "window_hours":   window_hours,
        "revenue_lift":   _inactive_revenue_lift(window_hours),
    }
