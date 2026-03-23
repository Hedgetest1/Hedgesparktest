"""
nudge_gating.py — Visitor-level behavioral eligibility gate for nudge rendering.

Public interface
----------------
    evaluate_visitor_nudge_eligibility(
        db:          Session,
        shop_domain: str,
        product_url: str,
        visitor_id:  str,
        nudge:       ActiveNudge,
    ) -> dict

    Returns a full eligibility decision dict consumed by GET /nudges/active:
        {
            eligible:                 bool,
            visitor_behavioral_index: float | None,
            threshold_used:           float,
            gating_source:            str,   # see below
            calibration_state:        str,
            reason:                   str,   # machine-readable decision label
            data_points:              int,   # event rows used for the profile
        }

gating_source values
--------------------
  "empirical"            — threshold derived from shop's real buyer behavioral mean
  "fallback"             — calibration insufficient; fixed warm threshold used
  "insufficient_history" — visitor has no product events in lookback window;
                           nudge suppressed regardless of nudge/calibration state

reason values
-------------
  "bi_above_threshold"   — eligible; behavioral_index >= threshold
  "bi_below_threshold"   — not eligible; behavioral_index < threshold
  "no_product_events"    — not eligible; visitor has no events on this product
  "calibration_error"    — not eligible; calibration load failed (safe fallback)

Behavioral profile lookup
-------------------------
Queries events WHERE visitor_id = ? AND shop_domain = ? AND product_url = ?
over the last PROFILE_LOOKBACK_HOURS hours.

Computes:
    avg_scroll    — avg(max_scroll_depth) from scroll/dwell_time/product_view events
    avg_dwell     — avg(dwell_seconds) from dwell_time events
    visit_count   — count of product_view events (unique page-load events)

Then applies _compute_behavioral_index() from empirical_calibration.py.

Data points = total event rows matching the filter (for observability).

Timing contract
---------------
spark-tracker.js sends page_view and product_view immediately on load (in DB
within ~1 second).  dwell_time and scroll are sent only on page leave.

The nudge check fires on DOMContentLoaded — after page_view/product_view are
in the DB but before dwell_time/scroll are sent.

Consequence: the current session's engagement quality (scroll depth, dwell)
is NOT available for this check.  Only HISTORICAL sessions from previous visits
are included.

This is the correct precision decision for v1:
  - First-time visitors: no history → eligible=False → nudge suppressed.
    They have demonstrated no intent on this product yet.
  - Return visitors: prior session data available → behavioral_index computed
    accurately → eligible if above threshold.
    Return visitors with high engagement are exactly the warm/hot segment.

The page_view event from the current session IS in the DB, contributing
visit_count=1 with scroll=0 and dwell=0, producing behavioral_index≈0.
For this visitor to qualify, they must have PRIOR sessions with real engagement.

This means the gate is conservative by design:
  - It never shows a nudge on a visitor's first product view.
  - It shows nudges on return visitors who have demonstrated intent.
  - It is 100% truthful — no synthetic precision, no fallback inferences.

Threshold selection
-------------------
Empirical mode (calibration.is_empirical = True):
    threshold = calibration.non_converter_behavioral_mean
    This is the warm threshold — visitors at or above the non-buyer baseline.
    Both warm AND hot visitors pass this gate.  Only cold visitors are filtered.
    Source: this shop's real behavioral data from attributed buyers vs non-buyers.

Fallback mode (calibration.is_empirical = False):
    threshold = FALLBACK_WARM_THRESHOLD = 0.20
    Same constant used by audience_segments.py in fallback mode.
    Consistent with the product-level segmentation system.

Why warm threshold and not hot threshold?
-----------------------------------------
Using the hot threshold (converter_behavioral_mean) would gate too strictly —
only visitors already behaving like buyers would see the nudge.  But the nudge's
job is to convert warm visitors (above the non-buyer baseline) into buyers.  The
nudge is most valuable at the warm→hot transition, not after the visitor is
already behaving like a buyer.  Hot visitors may convert without any nudge.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.active_nudge import ActiveNudge
from app.services.empirical_calibration import (
    _compute_behavioral_index,
    get_or_train_model,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How far back to look for visitor behavioral history.
# 7 days captures enough repeat-visit behavior without going stale.
PROFILE_LOOKBACK_HOURS: int = 168

# Fallback warm threshold — used when calibration.is_empirical = False.
# Matches WARM_THRESHOLD_FALLBACK in audience_segments.py.
FALLBACK_WARM_THRESHOLD: float = 0.20


# ---------------------------------------------------------------------------
# Visitor behavioral profile lookup
# ---------------------------------------------------------------------------

def _get_visitor_product_profile(
    db:          Session,
    shop_domain: str,
    product_url: str,
    visitor_id:  str,
    hours:       int = PROFILE_LOOKBACK_HOURS,
) -> Optional[dict]:
    """
    Query the events table for this visitor's behavioral history on one product.

    Returns None when the visitor has no qualifying product_view events.
    Returns a dict with avg_scroll, avg_dwell, visit_count, data_points when
    behavioral data is available.

    Query is scoped to:
      - This shop + product + visitor (tight filter, fast)
      - Last PROFILE_LOOKBACK_HOURS hours (bounded, avoids full-table scans)
      - Only events where event_type IN ('product_view', 'dwell_time', 'scroll')
        for scroll/dwell; product_view counted separately for visit_count

    HAVING COUNT(product_view) > 0 ensures we only return a profile when the
    visitor has actually viewed the product page — not just had a scroll event
    on another page that happened to have the same product_url set.
    """
    from datetime import datetime, timedelta
    since_ms = int((datetime.utcnow() - timedelta(hours=hours)).timestamp() * 1000)

    try:
        result = db.execute(
            text("""
                SELECT
                    COALESCE(
                        AVG(CASE
                            WHEN event_type IN ('product_view', 'dwell_time', 'scroll')
                            THEN max_scroll_depth
                        END),
                    0) AS avg_scroll,
                    COALESCE(
                        AVG(CASE
                            WHEN event_type = 'dwell_time'
                            THEN dwell_seconds
                        END),
                    0) AS avg_dwell,
                    COUNT(CASE WHEN event_type = 'product_view' THEN 1 END) AS visit_count,
                    COUNT(*) AS data_points
                FROM events
                WHERE shop_domain = :shop
                  AND product_url  = :product_url
                  AND visitor_id   = :visitor_id
                  AND timestamp   >= :since_ms
                HAVING COUNT(CASE WHEN event_type = 'product_view' THEN 1 END) > 0
            """),
            {
                "shop":        shop_domain,
                "product_url": product_url,
                "visitor_id":  visitor_id,
                "since_ms":    since_ms,
            },
        ).fetchone()

    except Exception as exc:
        log.error(
            "nudge_gating: profile query failed shop=%s product=%s visitor=%s: %s",
            shop_domain, product_url, visitor_id[:8] + "…", exc,
        )
        return None

    if result is None:
        return None

    row = dict(result._mapping)
    return {
        "avg_scroll":  float(row.get("avg_scroll") or 0),
        "avg_dwell":   float(row.get("avg_dwell") or 0),
        "visit_count": int(row.get("visit_count") or 0),
        "data_points": int(row.get("data_points") or 0),
    }


# ---------------------------------------------------------------------------
# Threshold selection
# ---------------------------------------------------------------------------

def _select_threshold(calibration) -> tuple[float, str]:
    """
    Return (threshold, gating_source) based on calibration state.

    Empirical:  threshold = non_converter_behavioral_mean (warm boundary)
    Fallback:   threshold = FALLBACK_WARM_THRESHOLD (0.20)
    """
    if calibration.is_empirical:
        return float(calibration.non_converter_behavioral_mean), "empirical"
    return FALLBACK_WARM_THRESHOLD, "fallback"


# ---------------------------------------------------------------------------
# Main eligibility evaluation
# ---------------------------------------------------------------------------

def evaluate_visitor_nudge_eligibility(
    db:          Session,
    shop_domain: str,
    product_url: str,
    visitor_id:  str,
    nudge:       ActiveNudge,
) -> dict:
    """
    Evaluate whether a specific visitor qualifies to see an active nudge.

    Steps:
    1. Load calibration (cached in DB, max 6h old).
    2. Query visitor behavioral profile on this product.
    3. If no profile: eligible=False, reason="no_product_events".
    4. Compute behavioral_index from profile.
    5. Compare against threshold (empirical or fallback).
    6. Return full decision dict.

    Never raises.  All errors degrade to eligible=False with reason logged.
    The storefront receives a safe, conservative answer on failure.

    Parameters
    ----------
    db           SQLAlchemy session.
    shop_domain  Merchant shop domain.
    product_url  Canonical product path — must match events.product_url format.
    visitor_id   Pseudonymous localStorage UUID from hedgespark_visitor_id.
    nudge        The active nudge being considered for delivery.

    Returns
    -------
    dict with keys:
        eligible                 bool
        visitor_behavioral_index float | None  (None when no history)
        threshold_used           float
        gating_source            str
        calibration_state        str
        reason                   str
        data_points              int
    """
    # Default safe response — used as fallback on any error
    safe_deny = {
        "eligible":                 False,
        "visitor_behavioral_index": None,
        "threshold_used":           FALLBACK_WARM_THRESHOLD,
        "gating_source":            "fallback",
        "calibration_state":        nudge.calibration_state or "unknown",
        "reason":                   "calibration_error",
        "data_points":              0,
    }

    # 1. Load calibration
    try:
        calibration = get_or_train_model(db, shop_domain)
    except Exception as exc:
        log.error(
            "nudge_gating: calibration load failed shop=%s: %s — denying",
            shop_domain, exc,
        )
        return safe_deny

    threshold, gating_source   = _select_threshold(calibration)
    calibration_state          = "empirical" if calibration.is_empirical else "fallback"

    # 2. Query visitor behavioral profile
    profile = _get_visitor_product_profile(
        db=db,
        shop_domain=shop_domain,
        product_url=product_url,
        visitor_id=visitor_id,
    )

    # 3. No product events — visitor has no demonstrated intent on this product
    if profile is None:
        log.debug(
            "nudge_gating: SUPPRESS visitor=%s shop=%s product=%s "
            "reason=no_product_events threshold=%.4f source=%s",
            visitor_id[:8] + "…", shop_domain, product_url,
            threshold, gating_source,
        )
        return {
            "eligible":                 False,
            "visitor_behavioral_index": None,
            "threshold_used":           round(threshold, 4),
            "gating_source":            "insufficient_history",
            "calibration_state":        calibration_state,
            "reason":                   "no_product_events",
            "data_points":              0,
        }

    # 4. Compute behavioral_index
    bi = _compute_behavioral_index(
        avg_scroll=profile["avg_scroll"],
        avg_dwell_secs=profile["avg_dwell"],
        visit_count=float(profile["visit_count"]),
    )

    # 5. Gate decision
    eligible = bi >= threshold
    reason   = "bi_above_threshold" if eligible else "bi_below_threshold"

    log.info(
        "nudge_gating: %s visitor=%s shop=%s product=%s "
        "bi=%.4f threshold=%.4f source=%s calibration=%s "
        "visit_count=%d data_points=%d",
        "ELIGIBLE" if eligible else "SUPPRESS",
        visitor_id[:8] + "…", shop_domain, product_url,
        bi, threshold, gating_source, calibration_state,
        profile["visit_count"], profile["data_points"],
    )

    return {
        "eligible":                 eligible,
        "visitor_behavioral_index": round(bi, 4),
        "threshold_used":           round(threshold, 4),
        "gating_source":            gating_source,
        "calibration_state":        calibration_state,
        "reason":                   reason,
        "data_points":              profile["data_points"],
    }
