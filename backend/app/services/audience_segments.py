"""
audience_segments.py — Live behavioral audience segmentation per product.

Public interface
----------------
    segment_product_visitors(
        db:          Session,
        shop_domain: str,
        product_url: str,
        hours:       int = 72,
    ) -> dict

    Return a segmentation report for a single product URL, classifying active
    (unconverted) visitors into hot / warm / cold segments based on their
    behavioral_index relative to the shop's empirical conversion calibration.

Segmentation logic
------------------
Thresholds depend on whether the shop has sufficient empirical calibration data.

EMPIRICAL mode (calibration.is_empirical = True):
    Hot   — behavioral_index >= converter_behavioral_mean
            Visitor's behavior matches or exceeds the historical buyer fingerprint
    Warm  — non_converter_mean <= behavioral_index < converter_mean
            Above the non-buyer baseline, not yet at buyer level
    Cold  — behavioral_index < non_converter_mean
            At or below the typical non-buyer engagement level

FALLBACK mode (calibration.is_empirical = False, insufficient attribution data):
    Hot   — behavioral_index >= 0.55  (scroll ~75% + dwell ~60s equivalent)
    Warm  — 0.20 <= behavioral_index < 0.55
    Cold  — behavioral_index < 0.20

Visitor exclusion
-----------------
Visitors already in visitor_purchase_sessions are excluded from all segments.
They are customers, not prospects — segmenting them would inflate counts and
contaminate the revenue window estimate.

Revenue window
--------------
For each segment:
    estimated_revenue_window = visitor_count × cvr_estimate × aov

This is a probabilistic estimate, not a guarantee.  The code comments label
explicitly what is empirical vs approximate.

visitor_ids
-----------
Returned as pseudonymous UUID strings (the hedgespark_visitor_id from
localStorage in spark-tracker.js).  These are NOT personally identifiable
on their own — they are random browser identifiers with no linkage to name,
email, or Shopify customer records unless a future enrichment step adds that.

They are returned in the Pro response because they are the operational input
for future agent actions: "target these specific visitors with this nudge."
Maximum 500 visitors per query to bound response size.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.empirical_calibration import (
    _compute_behavioral_index,
    _DEFAULT_SHOP_CVR,
    _MIN_DISCRIMINABILITY,
    compute_empirical_probability_direct,
    get_or_train_model,
)
from app.services.revenue_metrics import get_shop_aov

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Active visitor window: visitors who engaged with the product in this window
# are considered "in the decision phase" and segmentable.
_DEFAULT_ACTIVE_HOURS: int = 72

# Maximum visitors returned per query — bounds response size and query time
_MAX_VISITORS: int = 500

# Fallback segment thresholds (when calibration.is_empirical = False)
_HOT_THRESHOLD_FALLBACK:  float = 0.55
_WARM_THRESHOLD_FALLBACK: float = 0.20

# Per-segment CVR multipliers for fallback mode (applied to base_cvr or DEFAULT_SHOP_CVR)
# Grounded in typical e-commerce conversion lift patterns:
#   Hot: highly engaged visitors convert ~4× more than shop average
#   Warm: around shop average conversion rate
#   Cold: ~15% of shop average (minimal engagement, low intent)
_FALLBACK_CVR_MULTIPLIERS: dict[str, float] = {
    "hot":  4.0,
    "warm": 1.0,
    "cold": 0.15,
}


# ---------------------------------------------------------------------------
# Main segmentation function
# ---------------------------------------------------------------------------

def segment_product_visitors(
    db: Session,
    shop_domain: str,
    product_url: str,
    hours: int = _DEFAULT_ACTIVE_HOURS,
) -> dict[str, Any]:
    """
    Compute live audience segments for a product page.

    Parameters
    ----------
    db          Active SQLAlchemy session.
    shop_domain Merchant shop domain.
    product_url Canonical product path, e.g. /products/ceramic-vase.
    hours       Active visitor window in hours (default 72, max 168).

    Returns
    -------
    dict with the full segment report — see module docstring for schema.
    Never raises — all errors degrade gracefully with appropriate labels.
    """
    hours = max(1, min(hours, 168))
    since_dt = datetime.utcnow() - timedelta(hours=hours)
    since_ms = int(since_dt.timestamp() * 1000)

    log.info(
        "audience_segments: shop=%s product=%s window=%dh since=%s",
        shop_domain, product_url, hours, since_dt.isoformat(),
    )

    # ------------------------------------------------------------------ #
    # 1. Load calibration and AOV — both have safe fallbacks              #
    # ------------------------------------------------------------------ #
    calibration = get_or_train_model(db, shop_domain)
    aov, aov_source = _resolve_aov(db, shop_domain)

    # ------------------------------------------------------------------ #
    # 2. Fetch active, unconverted visitors for this product              #
    # ------------------------------------------------------------------ #
    try:
        rows = db.execute(
            text(
                """
                WITH active_events AS (
                    SELECT
                        visitor_id,
                        COALESCE(
                            AVG(CASE WHEN event_type IN ('product_view', 'dwell_time', 'scroll')
                                     THEN max_scroll_depth END),
                        0) AS avg_scroll,
                        COALESCE(
                            AVG(CASE WHEN event_type = 'dwell_time'
                                     THEN dwell_seconds END),
                        0) AS avg_dwell,
                        COUNT(CASE WHEN event_type = 'product_view' THEN 1 END) AS visit_count
                    FROM events
                    WHERE shop_domain = :shop
                      AND product_url  = :product_url
                      AND timestamp    >= :since_ms
                    GROUP BY visitor_id
                    HAVING COUNT(CASE WHEN event_type = 'product_view' THEN 1 END) > 0
                )
                SELECT ae.visitor_id, ae.avg_scroll, ae.avg_dwell, ae.visit_count
                FROM active_events ae
                LEFT JOIN visitor_purchase_sessions vps
                    ON vps.visitor_id  = ae.visitor_id
                   AND vps.shop_domain = :shop
                WHERE vps.visitor_id IS NULL
                ORDER BY ae.avg_scroll DESC, ae.avg_dwell DESC
                LIMIT :max_visitors
                """
            ),
            {
                "shop":         shop_domain,
                "product_url":  product_url,
                "since_ms":     since_ms,
                "max_visitors": _MAX_VISITORS,
            },
        ).fetchall()

    except Exception as exc:
        log.error(
            "audience_segments: query failed shop=%s product=%s: %s",
            shop_domain, product_url, exc,
        )
        return _empty_response(product_url, shop_domain, hours, aov, aov_source, calibration)

    total_active = len(rows)

    if total_active == 0:
        log.info(
            "audience_segments: no active visitors for shop=%s product=%s in last %dh",
            shop_domain, product_url, hours,
        )
        return _empty_response(product_url, shop_domain, hours, aov, aov_source, calibration)

    # ------------------------------------------------------------------ #
    # 3. Compute behavioral_index per visitor, classify into segments     #
    # ------------------------------------------------------------------ #
    hot_visitors:  list[dict] = []
    warm_visitors: list[dict] = []
    cold_visitors: list[dict] = []

    hot_thresh, warm_thresh, calibration_state = _resolve_thresholds(calibration)

    log.info(
        "audience_segments: shop=%s product=%s active=%d "
        "calibration=%s hot_thresh=%.3f warm_thresh=%.3f",
        shop_domain, product_url, total_active,
        calibration_state, hot_thresh, warm_thresh,
    )

    for row in rows:
        visitor_id, avg_scroll, avg_dwell, visit_count = row

        bi = _compute_behavioral_index(
            avg_scroll=float(avg_scroll or 0),
            avg_dwell_secs=float(avg_dwell or 0),
            visit_count=float(visit_count or 1),
        )

        entry = {
            "visitor_id":       str(visitor_id),
            "behavioral_index": round(bi, 4),
            "avg_scroll":       round(float(avg_scroll or 0), 1),
            "avg_dwell_secs":   round(float(avg_dwell or 0), 1),
            "visit_count":      int(visit_count or 1),
        }

        if bi >= hot_thresh:
            hot_visitors.append(entry)
        elif bi >= warm_thresh:
            warm_visitors.append(entry)
        else:
            cold_visitors.append(entry)

    log.info(
        "audience_segments: shop=%s product=%s → hot=%d warm=%d cold=%d",
        shop_domain, product_url,
        len(hot_visitors), len(warm_visitors), len(cold_visitors),
    )

    # ------------------------------------------------------------------ #
    # 4. Compute CVR estimates and revenue windows per segment            #
    # ------------------------------------------------------------------ #
    hot_segment  = _build_segment("hot",  hot_visitors,  calibration, aov, warm_thresh, hot_thresh)
    warm_segment = _build_segment("warm", warm_visitors, calibration, aov, warm_thresh, hot_thresh)
    cold_segment = _build_segment("cold", cold_visitors, calibration, aov, warm_thresh, hot_thresh)

    # ------------------------------------------------------------------ #
    # 5. Build response                                                   #
    # ------------------------------------------------------------------ #
    return {
        "product_url":           product_url,
        "shop_domain":           shop_domain,
        "active_window_hours":   hours,
        "total_active_visitors": total_active,
        "hot":                   hot_segment,
        "warm":                  warm_segment,
        "cold":                  cold_segment,
        "meta": {
            "calibration_state":              calibration_state,
            "calibration_base_cvr":           round(float(calibration.base_cvr or 0), 6),
            "converter_behavioral_mean":      round(float(calibration.converter_behavioral_mean or 0), 4),
            "non_converter_behavioral_mean":  round(float(calibration.non_converter_behavioral_mean or 0), 4),
            "discriminability":               round(float(calibration.discriminability or 0), 4),
            "calibration_sample_size":        int(calibration.sample_size or 0),
            "calibration_converter_count":    int(calibration.converter_count or 0),
            "hot_threshold":                  round(hot_thresh, 4),
            "warm_threshold":                 round(warm_thresh, 4),
            "aov_used":                       round(aov, 2),
            "aov_source":                     aov_source,
            "generated_at":                   datetime.utcnow().isoformat() + "Z",
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_thresholds(
    calibration,
) -> tuple[float, float, str]:
    """
    Return (hot_threshold, warm_threshold, calibration_state).

    Empirical mode:  thresholds derived from this shop's real buyer data.
    Fallback mode:   fixed conservative thresholds.
    """
    if calibration.is_empirical:
        return (
            float(calibration.converter_behavioral_mean),
            float(calibration.non_converter_behavioral_mean),
            "empirical",
        )
    return _HOT_THRESHOLD_FALLBACK, _WARM_THRESHOLD_FALLBACK, "fallback"


def _build_segment(
    name: str,
    visitors: list[dict],
    calibration,
    aov: float,
    warm_thresh: float,
    hot_thresh: float,
) -> dict[str, Any]:
    """
    Build the segment dict for a single tier (hot / warm / cold).

    CVR estimation:
      - Empirical mode: compute_empirical_probability_direct() at the segment's
        average behavioral_index.  This is a real empirical estimate based on
        this shop's buyer behavior.
      - Fallback mode: base_cvr × multiplier, where the multiplier is a
        fixed prior for each segment tier.

    Revenue window:
      visitor_count × cvr_estimate × aov
      This is a probabilistic revenue opportunity, NOT a guarantee.
    """
    count = len(visitors)

    if count == 0:
        return {
            "visitor_count":            0,
            "visitor_ids":              [],
            "avg_behavioral_index":     None,
            "cvr_estimate":             None,
            "estimated_revenue_window": 0.0,
            "cvr_source":               "none",
        }

    # Segment average behavioral_index
    avg_bi = sum(v["behavioral_index"] for v in visitors) / count

    # CVR estimate
    if calibration.is_empirical:
        # Pure empirical estimate at the segment's average behavioral quality.
        # What does our real buyer data say the conversion rate is for a visitor
        # with this behavioral fingerprint?
        cvr, cvr_source = compute_empirical_probability_direct(avg_bi, calibration)
    else:
        # Fallback: base_cvr × segment multiplier.
        # base_cvr from calibration training run (may be partial), or generic prior.
        base = float(calibration.base_cvr) if calibration.base_cvr and calibration.base_cvr > 0 \
               else _DEFAULT_SHOP_CVR
        mult = _FALLBACK_CVR_MULTIPLIERS.get(name, 1.0)
        cvr = max(0.001, min(0.999, base * mult))
        cvr_source = "fallback"

    # Revenue window: probabilistic expected revenue from this segment today.
    # Interpretation: if each visitor in this segment follows through at the
    # estimated CVR, this is the total revenue captured.
    estimated_revenue_window = round(count * cvr * aov, 2)

    return {
        "visitor_count":            count,
        # visitor_ids are pseudonymous localStorage UUIDs — not PII in isolation.
        # Returned for Pro use: future agent actions targeting specific visitors.
        "visitor_ids":              [v["visitor_id"] for v in visitors],
        "avg_behavioral_index":     round(avg_bi, 4),
        "cvr_estimate":             round(cvr, 6),
        "estimated_revenue_window": estimated_revenue_window,
        "cvr_source":               cvr_source,
        # Detailed visitor profiles for agent use — ordered by behavioral_index DESC
        "visitors": sorted(
            [
                {
                    "visitor_id":       v["visitor_id"],
                    "behavioral_index": v["behavioral_index"],
                    "avg_scroll":       v["avg_scroll"],
                    "avg_dwell_secs":   v["avg_dwell_secs"],
                    "visit_count":      v["visit_count"],
                }
                for v in visitors
            ],
            key=lambda x: x["behavioral_index"],
            reverse=True,
        ),
    }


def _resolve_aov(db: Session, shop_domain: str) -> tuple[float, str]:
    """Return (aov, source_label). Always returns a positive float."""
    try:
        aov = get_shop_aov(db, shop_domain)
        # get_shop_aov returns FALLBACK_AOV=50.0 when no orders exist.
        # We expose the source so the caller knows whether this is real.
        from app.services.revenue_metrics import FALLBACK_AOV
        source = "real" if aov != FALLBACK_AOV else "fallback"
        return aov, source
    except Exception:
        return 50.0, "fallback"


def _empty_response(
    product_url: str,
    shop_domain: str,
    hours: int,
    aov: float,
    aov_source: str,
    calibration,
) -> dict[str, Any]:
    """Return a structurally valid empty response for products with no active visitors."""
    empty_segment: dict[str, Any] = {
        "visitor_count":            0,
        "visitor_ids":              [],
        "visitors":                 [],
        "avg_behavioral_index":     None,
        "cvr_estimate":             None,
        "estimated_revenue_window": 0.0,
        "cvr_source":               "none",
    }
    return {
        "product_url":           product_url,
        "shop_domain":           shop_domain,
        "active_window_hours":   hours,
        "total_active_visitors": 0,
        "hot":                   empty_segment,
        "warm":                  empty_segment,
        "cold":                  empty_segment,
        "meta": {
            "calibration_state":              "empirical" if calibration.is_empirical else "fallback",
            "calibration_base_cvr":           round(float(calibration.base_cvr or 0), 6),
            "converter_behavioral_mean":      round(float(calibration.converter_behavioral_mean or 0), 4),
            "non_converter_behavioral_mean":  round(float(calibration.non_converter_behavioral_mean or 0), 4),
            "discriminability":               round(float(calibration.discriminability or 0), 4),
            "calibration_sample_size":        int(calibration.sample_size or 0),
            "calibration_converter_count":    int(calibration.converter_count or 0),
            "hot_threshold":                  None,
            "warm_threshold":                 None,
            "aov_used":                       round(aov, 2),
            "aov_source":                     aov_source,
            "generated_at":                   datetime.utcnow().isoformat() + "Z",
        },
    }
