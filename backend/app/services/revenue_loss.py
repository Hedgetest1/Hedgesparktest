"""
revenue_loss.py — Expected revenue loss estimation for a product in a 24-hour window.

Public interface
----------------
    calculate_expected_loss(
        product_metrics_row: dict,
        conversion_probability: float,
        aov: float | None = None,
    ) -> dict

Formula
-------
    expected_loss = views_24h × conversion_probability × aov

Interpretation: the expected revenue NOT captured today because visitors who
had a measurable conversion probability left without buying.  It is NOT a
guarantee — it is a prioritisation signal: higher expected loss means the
product deserves more immediate attention.

Parameters
----------
product_metrics_row
    Dict containing at minimum a ``views_24h`` key.  Compatible with both
    product_metrics ORM rows (when called with __dict__ or via row._mapping)
    and plain dicts assembled by the revenue_radar endpoint.

conversion_probability
    Float in [0, 1].  Typically the output of
    conversion_service.compute_conversion_probability()["conversion_probability"].
    Values outside [0, 1] are clamped before use.

aov
    Average Order Value in the merchant's store currency.  If None or <= 0
    the DEFAULT_AOV fallback (50.0) is used.  The caller should pass the
    real AOV when available.

Returns
-------
dict with:
    expected_loss   float — clamped to [0, MAX_EXPECTED_LOSS]
    loss_band       str   — "LOW", "MEDIUM", or "HIGH"
    urgency_score   float — 0–100, composite of loss magnitude and conversion
                            proximity; higher = act sooner

Loss band thresholds
--------------------
    HIGH    expected_loss >= 500
    MEDIUM  expected_loss >= 50
    LOW     expected_loss <  50

Urgency score formula
---------------------
    loss_factor      = min(1.0, expected_loss / MAX_URGENCY_LOSS)   [0-1]
    proximity_factor = clamped conversion_probability                [0-1]
    urgency_score    = loss_factor × 60  +  proximity_factor × 40

Weighting rationale:
    60 % loss magnitude  — a large loss is urgent even if conversion is uncertain.
    40 % proximity       — high conversion probability means the missed revenue
                           is more concrete (visitor was close to buying).

Score interpretation:
    0–25    monitor
    25–50   low priority action
    50–75   act this week
    75–100  act today
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Deep defensive fallback. All three production callers
# (action_candidates_engine, revenue_radar, weekly_digest) now resolve
# the real per-shop AOV via `revenue_metrics.get_shop_aov()` and pass
# it explicitly to calculate_expected_loss(). This constant is only
# reached when both the caller-resolved AOV is None/0 AND the shop has
# zero ingested orders — the degenerate "brand-new merchant" case.
# Kept at 50.0 to match `revenue_metrics.FALLBACK_AOV` for consistency.
DEFAULT_AOV: float = 50.0

# Hard cap on reported expected_loss.  Prevents a single outlier product with
# thousands of daily views from distorting the loss scale for the whole shop.
_MAX_EXPECTED_LOSS: float = 100_000.0

# Denominator used to normalise expected_loss into the urgency loss_factor.
# $2 000 of expected daily loss → loss_factor = 1.0 (maximum urgency from loss).
_MAX_URGENCY_LOSS: float = 2_000.0

# Loss band boundaries (currency units)
_BAND_HIGH: float = 500.0
_BAND_MEDIUM: float = 50.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _resolve_aov(aov: float | None) -> float:
    """Return aov if valid, otherwise DEFAULT_AOV."""
    if aov is None:
        return DEFAULT_AOV
    try:
        v = float(aov)
        return v if v > 0 else DEFAULT_AOV
    except (TypeError, ValueError):
        return DEFAULT_AOV


def _resolve_views(product_metrics_row: dict) -> int:
    """
    Extract views_24h from the metrics row.

    Accepts:
      - dict with key "views_24h"
      - 0 on any missing / unparseable value (safe fallback — no loss is better
        than a crash on a bad row)
    """
    try:
        v = product_metrics_row.get("views_24h", 0)
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def calculate_expected_loss(
    product_metrics_row: dict,
    conversion_probability: float,
    aov: float | None = None,
) -> dict:
    """
    Estimate the revenue left on the table for a product over the last 24 hours.

    Parameters
    ----------
    product_metrics_row     dict — must contain ``views_24h``
    conversion_probability  float — [0, 1] probability of purchase per visitor
    aov                     float | None — average order value; defaults to 50.0

    Returns
    -------
    {
        "expected_loss":  float,  # 0 – 100 000
        "loss_band":      str,    # "LOW" | "MEDIUM" | "HIGH"
        "urgency_score":  float,  # 0 – 100
    }
    """
    views_24h = _resolve_views(product_metrics_row)
    resolved_aov = _resolve_aov(aov)
    prob = _clamp(float(conversion_probability or 0), 0.0, 1.0)

    # Core formula
    raw_loss = views_24h * prob * resolved_aov

    # Clamp to [0, MAX_EXPECTED_LOSS]
    expected_loss = round(_clamp(raw_loss, 0.0, _MAX_EXPECTED_LOSS), 2)

    # Loss band
    if expected_loss >= _BAND_HIGH:
        loss_band = "HIGH"
    elif expected_loss >= _BAND_MEDIUM:
        loss_band = "MEDIUM"
    else:
        loss_band = "LOW"

    # Urgency score  (0–100)
    #   60 % from loss magnitude  — large loss = urgent regardless of certainty
    #   40 % from conversion proximity — high probability = missed revenue is concrete
    loss_factor = _clamp(expected_loss / _MAX_URGENCY_LOSS, 0.0, 1.0)
    urgency_score = round(loss_factor * 60.0 + prob * 40.0, 1)

    return {
        "expected_loss": expected_loss,
        "loss_band": loss_band,
        "urgency_score": urgency_score,
    }
