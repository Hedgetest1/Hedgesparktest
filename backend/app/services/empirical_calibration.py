"""
empirical_calibration.py — Shop-specific empirical conversion probability calibration.

Replaces the uniform hand-crafted weights in conversion_service.py with
shop-specific calibration learned from real buyer vs non-buyer behavioral data.

Model: Behavioral Index Calibration with Log-Odds Lift (v1)
-----------------------------------------------------------
1. BEHAVIORAL INDEX
   A single scalar (0–1) summarising a visitor's engagement quality on product
   pages.  Computed from three features available in the events table:

     avg_scroll_depth  — how deep the visitor scrolled (0–100)
     avg_dwell_secs    — how long the visitor spent on product pages (capped 120s)
     visit_count       — how many product page visits (each unique event row)

   Formula:
     behavioral_index = 0.40 × norm(scroll, 100)
                      + 0.40 × norm(dwell, 120)
                      + 0.20 × norm(max(visits-1, 0), 4)

   The feature weights (0.40 / 0.40 / 0.20) are provisional starters, not
   empirically derived — that calibration happens at the shop statistics level.

2. TRAINING
   From the last `lookback_days` (default 30) days:
     - Converters: visitors in visitor_purchase_sessions (real attributed buyers)
     - Non-converters: all other visitors with product page events
   Compute per-cohort means of behavioral_index.
   Compute the shop's empirical base_cvr.
   Compute discriminability = converter_mean - non_converter_mean.

3. APPLICATION (apply_calibration)
   Given an inferred probability from the handcrafted model and the current
   product's behavioral features:

   a. Compute behavioral_index for this product.
   b. Compute log-odds lift:
        lift = clamp((behavioral_index - non_converter_mean) / discriminability, -2, 2)
        log_odds = log(base_cvr / (1-base_cvr)) + lift × log(10)
        empirical_prob = sigmoid(log_odds)
   c. Blend with inferred based on data confidence:
        blend_weight = min(1, sample_size/200) × min(1, converter_count/20)
        calibrated = (1 - w) × inferred + w × empirical_prob

4. FALLBACK
   When data is insufficient (converter_count < 10, sample_size < 50,
   or discriminability < 0.02), is_empirical = False and apply_calibration()
   returns the inferred probability unchanged with source = "inferred".

Conversion source labels
------------------------
   "real"      — real product-level CVR from shop_orders (highest priority)
   "empirical" — shop-level behavioral calibration applied (this module)
   "inferred"  — pure handcrafted inference (fallback)

No external ML dependencies.  Pure Python + SQLAlchemy.  Safe to call on
every request — the model is cached in DB and reloaded only when stale.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.shop_conversion_calibration import ShopConversionCalibration

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum attributed purchases to activate empirical mode
_MIN_CONVERTERS: int = 10

# Minimum product-viewing visitors in training window
_MIN_SAMPLE_SIZE: int = 50

# Minimum discriminability (converter_mean - non_converter_mean) to use
# the behavioral index as a lift predictor.  Below this, features are
# not informative enough to trust the log-odds adjustment.
_MIN_DISCRIMINABILITY: float = 0.02

# How long a calibration record stays fresh before a lazy retrain is triggered
_MAX_AGE_HOURS: float = 6.0

# Training window in days
_LOOKBACK_DAYS: int = 30

# Maximum training rows fetched — keeps the query bounded for large shops
_MAX_TRAINING_ROWS: int = 10_000

# Log-odds lift at behavioral_index == converter_mean (10× base CVR max lift)
_MAX_LOG_LIFT: float = math.log(10)


# ---------------------------------------------------------------------------
# Behavioral index computation
# ---------------------------------------------------------------------------

def _compute_behavioral_index(
    avg_scroll: float,
    avg_dwell_secs: float,
    visit_count: float,
) -> float:
    """
    Compute a single 0–1 engagement quality index from visitor behavioral features.

    Normalization anchors:
      scroll   → 100% scroll = 1.0  (full page read)
      dwell    → 120 seconds = 1.0  (2 minutes is deeply engaged)
      visits   → 5 visits = 1.0     (4+ repeat views = very high interest)

    These anchors are conservative: they reflect "clearly engaged" rather than
    "average", which biases the index toward discriminating high-intent visitors.
    """
    scroll_norm = min(float(avg_scroll or 0) / 100.0, 1.0)
    dwell_norm  = min(float(avg_dwell_secs or 0) / 120.0, 1.0)
    visit_norm  = min(max(float(visit_count or 1) - 1.0, 0.0) / 4.0, 1.0)
    return 0.40 * scroll_norm + 0.40 * dwell_norm + 0.20 * visit_norm


def compute_behavioral_index_from_features(features: dict[str, Any]) -> float:
    """
    Extract behavioral index from a feature dict produced by action_candidates_engine
    or revenue_radar enrichment steps.

    Tries multiple key aliases to be robust against different dict shapes.
    Falls back to 0 when no behavioral data is present.
    """
    avg_scroll = float(
        features.get("avg_scroll_depth") or
        features.get("avg_scroll_24h") or
        features.get("scroll_depth") or
        0
    )
    avg_dwell = float(
        features.get("avg_dwell_seconds") or
        features.get("avg_dwell_24h") or
        features.get("dwell_seconds") or
        0
    )
    visit_count = float(
        features.get("total_views") or
        features.get("views_24h") or
        1
    )
    return _compute_behavioral_index(avg_scroll, avg_dwell, visit_count)


# Generic e-commerce conversion rate prior — used when no shop-specific data exists
_DEFAULT_SHOP_CVR: float = 0.02


def compute_empirical_probability_direct(
    behavioral_index: float,
    model: ShopConversionCalibration,
) -> tuple[float, str]:
    """
    Compute a conversion probability estimate directly from the empirical calibration,
    WITHOUT blending with the handcrafted inference model.

    Used by the audience segments API where we want pure empirical estimates per
    behavioral_index value — not a weighted blend with inference.  Each segment has
    a single representative behavioral_index (the segment average) and we want to
    know what the calibration says about it directly.

    Returns
    -------
    (probability, source)
        source = "empirical"  when calibration.is_empirical = True
        source = "fallback"   when data insufficient — uses base_cvr or DEFAULT_CVR

    The fallback is still better than nothing: it uses whatever base_cvr the training
    run computed (even with < 10 converters, the direction is meaningful as a prior).
    """
    if not model.is_empirical:
        # Not enough data for full empirical mode.
        # Use base_cvr if it's non-zero (partial data is still informative),
        # otherwise use the generic ecommerce prior.
        base = float(model.base_cvr) if model.base_cvr and model.base_cvr > 0 else _DEFAULT_SHOP_CVR
        return base, "fallback"

    base_cvr         = max(0.001, min(0.999, float(model.base_cvr)))
    discriminability = float(model.discriminability)
    non_conv_mean    = float(model.non_converter_behavioral_mean)

    log_odds_base = math.log(base_cvr / (1.0 - base_cvr))

    if discriminability >= _MIN_DISCRIMINABILITY:
        lift = (behavioral_index - non_conv_mean) / discriminability
        lift = max(-2.0, min(2.0, lift))
        log_odds = log_odds_base + lift * _MAX_LOG_LIFT
        prob = 1.0 / (1.0 + math.exp(-log_odds))
    else:
        # Features aren't discriminating — everyone gets base CVR
        prob = base_cvr

    return max(0.001, min(0.999, prob)), "empirical"


# ---------------------------------------------------------------------------
# Blend weight
# ---------------------------------------------------------------------------

def _blend_weight(converter_count: int, sample_size: int) -> float:
    """
    Compute how much to trust the empirical model vs the handcrafted inference.

    Both dimensions must be satisfied:
      - sample_size saturates at 200 visitors (50% confidence at 100 visitors)
      - converter_count saturates at 20 converters (50% confidence at 10)

    At 20 converters + 200 visitors → blend_weight = 1.0 (full empirical trust).
    At 10 converters + 50 visitors  → blend_weight = 0.25 (mostly inference).

    This is intentionally conservative.  The empirical model is a strong
    signal but needs sufficient data to be trustworthy.
    """
    sample_conf    = min(1.0, sample_size    / 200.0)
    converter_conf = min(1.0, converter_count / 20.0)
    return sample_conf * converter_conf


# ---------------------------------------------------------------------------
# Apply calibration
# ---------------------------------------------------------------------------

def apply_calibration(
    inferred_prob: float,
    behavioral_index: float,
    model: ShopConversionCalibration,
) -> tuple[float, str]:
    """
    Apply shop-specific empirical calibration to a handcrafted inferred probability.

    Parameters
    ----------
    inferred_prob      float — output of infer_conversion_outcome() → conversion_probability
    behavioral_index   float — output of compute_behavioral_index_from_features()
    model              ShopConversionCalibration — loaded from DB

    Returns
    -------
    (calibrated_probability, conversion_source)
        source is "empirical" when empirical signal dominates (blend_weight > 0.1)
               or "inferred" when falling back (not enough data or model.is_empirical=False)

    Never raises — falls back to (inferred_prob, "inferred") on any error.
    """
    if not model.is_empirical:
        return float(inferred_prob), "inferred"

    try:
        base_cvr          = float(model.base_cvr)
        discriminability  = float(model.discriminability)
        non_conv_mean     = float(model.non_converter_behavioral_mean)
        converter_count   = int(model.converter_count)
        sample_size       = int(model.sample_size)

        # ------------------------------------------------------------------ #
        # Step 1: compute empirical probability via log-odds lift             #
        # ------------------------------------------------------------------ #
        # Guard against edge cases in log computation
        p = max(0.001, min(0.999, base_cvr))
        log_odds_base = math.log(p / (1.0 - p))

        if discriminability >= _MIN_DISCRIMINABILITY:
            # How many "discriminability units" above the non-converter mean is
            # this visitor?  Positive = more like a buyer, negative = less like one.
            lift = (behavioral_index - non_conv_mean) / discriminability
            lift = max(-2.0, min(2.0, lift))   # clamp to ±2σ

            # Lift = +1 (at converter mean) → log-odds increases by log(10) → ~10× probability
            # Lift = -1 (below non-conv mean) → log-odds decreases by log(10) → ~0.1× probability
            # Lift = 0 (at non-converter mean) → no adjustment → probability = base_cvr
            log_odds_calibrated = log_odds_base + lift * _MAX_LOG_LIFT
            empirical_prob = 1.0 / (1.0 + math.exp(-log_odds_calibrated))
        else:
            # Behavioral features are not discriminating for this shop.
            # Empirical signal = base CVR (same for everyone on this shop).
            empirical_prob = base_cvr

        # ------------------------------------------------------------------ #
        # Step 2: blend with inferred based on data confidence               #
        # ------------------------------------------------------------------ #
        w = _blend_weight(converter_count, sample_size)
        inferred  = float(inferred_prob)
        calibrated = (1.0 - w) * inferred + w * empirical_prob
        calibrated = max(0.001, min(0.999, calibrated))

        source = "empirical" if w > 0.1 else "inferred"
        return calibrated, source

    except Exception as exc:
        log.error(
            "empirical_calibration.apply_calibration: error for shop=%s: %s — returning inferred",
            model.shop_domain, exc,
        )
        return float(inferred_prob), "inferred"


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_shop_model(
    db: Session,
    shop_domain: str,
    lookback_days: int = _LOOKBACK_DAYS,
) -> ShopConversionCalibration:
    """
    Compute and persist an empirical conversion calibration for a shop.

    Queries events and visitor_purchase_sessions to build a training dataset
    of converter vs non-converter behavioral profiles, then derives calibration
    parameters and upserts a ShopConversionCalibration row.

    Always returns a ShopConversionCalibration — if data is insufficient,
    is_empirical=False and apply_calibration() will return inferred unchanged.

    Never raises — catches all errors and returns a safe fallback calibration.
    """
    since_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
    since_ms = int(since_dt.timestamp() * 1000)

    log.info(
        "empirical_calibration: training shop=%s lookback=%dd since=%s",
        shop_domain, lookback_days, since_dt.date(),
    )

    try:
        # ------------------------------------------------------------------ #
        # 1. Pull training dataset: one row per visitor with behavioral feats #
        # ------------------------------------------------------------------ #
        rows = db.execute(
            text(
                """
                WITH behavioral_data AS (
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
                    WHERE shop_domain   = :shop
                      AND product_url   IS NOT NULL
                      AND timestamp     >= :since_ms
                    GROUP BY visitor_id
                    HAVING COUNT(CASE WHEN event_type = 'product_view' THEN 1 END) > 0
                    LIMIT :max_rows
                ),
                converters AS (
                    SELECT DISTINCT visitor_id
                    FROM visitor_purchase_sessions
                    WHERE shop_domain  = :shop
                      AND confirmed_at >= :since_dt
                )
                SELECT
                    bd.avg_scroll,
                    bd.avg_dwell,
                    bd.visit_count,
                    CASE WHEN c.visitor_id IS NOT NULL THEN 1 ELSE 0 END AS converted
                FROM behavioral_data bd
                LEFT JOIN converters c ON c.visitor_id = bd.visitor_id
                """
            ),
            {
                "shop":     shop_domain,
                "since_ms": since_ms,
                "since_dt": since_dt,
                "max_rows": _MAX_TRAINING_ROWS,
            },
        ).fetchall()

    except Exception as exc:
        log.error(
            "empirical_calibration: training query failed for shop=%s: %s — using fallback",
            shop_domain, exc,
        )
        return _upsert_fallback(db, shop_domain, lookback_days, sample_size=0, converter_count=0)

    # ------------------------------------------------------------------ #
    # 2. Compute behavioral_index per visitor, split by label             #
    # ------------------------------------------------------------------ #
    converter_indices:     list[float] = []
    non_converter_indices: list[float] = []

    for row in rows:
        avg_scroll, avg_dwell, visit_count, converted = row
        bi = _compute_behavioral_index(
            avg_scroll=float(avg_scroll or 0),
            avg_dwell_secs=float(avg_dwell or 0),
            visit_count=float(visit_count or 1),
        )
        if converted:
            converter_indices.append(bi)
        else:
            non_converter_indices.append(bi)

    sample_size     = len(rows)
    converter_count = len(converter_indices)

    log.info(
        "empirical_calibration: shop=%s training_rows=%d converters=%d non_converters=%d",
        shop_domain, sample_size, converter_count, len(non_converter_indices),
    )

    # ------------------------------------------------------------------ #
    # 3. Check minimum data thresholds                                    #
    # ------------------------------------------------------------------ #
    if converter_count < _MIN_CONVERTERS or sample_size < _MIN_SAMPLE_SIZE:
        log.info(
            "empirical_calibration: shop=%s INSUFFICIENT DATA "
            "(converters=%d < %d OR sample_size=%d < %d) — is_empirical=False",
            shop_domain, converter_count, _MIN_CONVERTERS,
            sample_size, _MIN_SAMPLE_SIZE,
        )
        return _upsert_fallback(
            db, shop_domain, lookback_days,
            sample_size=sample_size, converter_count=converter_count,
        )

    # ------------------------------------------------------------------ #
    # 4. Compute calibration parameters                                   #
    # ------------------------------------------------------------------ #
    # sample_size is guaranteed >= _MIN_SAMPLE_SIZE by the threshold
    # check above; defensive `or 1` in case upstream invariant changes.
    base_cvr                  = converter_count / sample_size if sample_size else 0.0
    converter_mean            = sum(converter_indices) / len(converter_indices)
    non_converter_mean        = (
        sum(non_converter_indices) / len(non_converter_indices)
        if non_converter_indices else 0.0
    )
    discriminability          = converter_mean - non_converter_mean

    is_empirical = discriminability >= _MIN_DISCRIMINABILITY

    if not is_empirical:
        log.info(
            "empirical_calibration: shop=%s LOW DISCRIMINABILITY (%.4f < %.4f) — "
            "behavioral features do not predict conversion for this shop — is_empirical=False",
            shop_domain, discriminability, _MIN_DISCRIMINABILITY,
        )
    else:
        log.info(
            "empirical_calibration: shop=%s EMPIRICAL MODEL READY "
            "base_cvr=%.4f discriminability=%.4f converter_mean=%.3f non_converter_mean=%.3f",
            shop_domain, base_cvr, discriminability, converter_mean, non_converter_mean,
        )

    # ------------------------------------------------------------------ #
    # 5. Upsert calibration record                                        #
    # ------------------------------------------------------------------ #
    return _upsert_calibration(
        db=db,
        shop_domain=shop_domain,
        lookback_days=lookback_days,
        sample_size=sample_size,
        converter_count=converter_count,
        base_cvr=base_cvr,
        converter_mean=converter_mean,
        non_converter_mean=non_converter_mean,
        discriminability=discriminability,
        is_empirical=is_empirical,
    )


# ---------------------------------------------------------------------------
# Lazy load with staleness check
# ---------------------------------------------------------------------------

def get_or_train_model(
    db: Session,
    shop_domain: str,
    max_age_hours: float = _MAX_AGE_HOURS,
) -> ShopConversionCalibration:
    """
    Return the current calibration for a shop, retraining if stale or absent.

    Called once per request at the top of generate_action_candidates() and
    revenue_radar_top() — the same pattern as get_shop_aov() and
    get_real_product_conversion_map().

    Staleness: if trained_at < now - max_age_hours, retrain.
    First call: always trains (no existing record).

    Never raises.  Returns a calibration with is_empirical=False when any
    error occurs — apply_calibration() then returns inferred unchanged.
    """
    try:
        existing: ShopConversionCalibration | None = (
            db.query(ShopConversionCalibration)
            .filter(ShopConversionCalibration.shop_domain == shop_domain)
            .first()
        )

        if existing is not None:
            age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - existing.trained_at).total_seconds() / 3600.0
            if age_hours < max_age_hours:
                log.debug(
                    "empirical_calibration: using cached model for shop=%s "
                    "(age=%.1fh is_empirical=%s blend_weight=%.2f)",
                    shop_domain, age_hours, existing.is_empirical,
                    _blend_weight(existing.converter_count, existing.sample_size),
                )
                return existing

            log.info(
                "empirical_calibration: cached model stale for shop=%s (age=%.1fh) — retraining",
                shop_domain, age_hours,
            )

        return train_shop_model(db, shop_domain)

    except Exception as exc:
        log.error(
            "empirical_calibration.get_or_train_model: error for shop=%s: %s — using in-memory fallback",
            shop_domain, exc,
        )
        # Return an in-memory fallback that signals no empirical data — never persisted
        return _in_memory_fallback(shop_domain)


# ---------------------------------------------------------------------------
# Internal upsert helpers
# ---------------------------------------------------------------------------

def _upsert_calibration(
    db: Session,
    shop_domain: str,
    lookback_days: int,
    sample_size: int,
    converter_count: int,
    base_cvr: float,
    converter_mean: float,
    non_converter_mean: float,
    discriminability: float,
    is_empirical: bool,
) -> ShopConversionCalibration:
    """INSERT or UPDATE the calibration row for a shop."""
    existing = (
        db.query(ShopConversionCalibration)
        .filter(ShopConversionCalibration.shop_domain == shop_domain)
        .first()
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if existing:
        existing.model_version                 = (existing.model_version or 0) + 1
        existing.lookback_days                 = lookback_days
        existing.sample_size                   = sample_size
        existing.converter_count               = converter_count
        existing.base_cvr                      = base_cvr
        existing.converter_behavioral_mean     = converter_mean
        existing.non_converter_behavioral_mean = non_converter_mean
        existing.discriminability              = discriminability
        existing.is_empirical                  = is_empirical
        existing.trained_at                    = now
        try:
            db.commit()
            db.refresh(existing)
        except Exception as exc:
            db.rollback()
            log.error("empirical_calibration: failed to update calibration for shop=%s: %s", shop_domain, exc)
        return existing

    row = ShopConversionCalibration(
        shop_domain                   = shop_domain,
        model_version                 = 1,
        lookback_days                 = lookback_days,
        sample_size                   = sample_size,
        converter_count               = converter_count,
        base_cvr                      = base_cvr,
        converter_behavioral_mean     = converter_mean,
        non_converter_behavioral_mean = non_converter_mean,
        discriminability              = discriminability,
        is_empirical                  = is_empirical,
        trained_at                    = now,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        # Race between two concurrent first-time training calls — re-fetch
        row = (
            db.query(ShopConversionCalibration)
            .filter(ShopConversionCalibration.shop_domain == shop_domain)
            .first()
        )
    except Exception as exc:
        db.rollback()
        log.error("empirical_calibration: failed to insert calibration for shop=%s: %s", shop_domain, exc)

    return row or _in_memory_fallback(shop_domain)


def _upsert_fallback(
    db: Session,
    shop_domain: str,
    lookback_days: int,
    sample_size: int,
    converter_count: int,
) -> ShopConversionCalibration:
    """Upsert a calibration row with is_empirical=False (data insufficient)."""
    return _upsert_calibration(
        db=db,
        shop_domain=shop_domain,
        lookback_days=lookback_days,
        sample_size=sample_size,
        converter_count=converter_count,
        base_cvr=0.0,
        converter_mean=0.5,
        non_converter_mean=0.25,
        discriminability=0.0,
        is_empirical=False,
    )


def _in_memory_fallback(shop_domain: str) -> ShopConversionCalibration:
    """
    Return a non-persisted fallback calibration for error recovery.

    is_empirical=False ensures apply_calibration() returns inferred unchanged.
    Used only when get_or_train_model() itself fails — should never happen
    in normal operation.
    """
    row = ShopConversionCalibration()
    row.shop_domain                   = shop_domain
    row.model_version                 = 0
    row.lookback_days                 = _LOOKBACK_DAYS
    row.sample_size                   = 0
    row.converter_count               = 0
    row.base_cvr                      = 0.0
    row.converter_behavioral_mean     = 0.5
    row.non_converter_behavioral_mean = 0.25
    row.discriminability              = 0.0
    row.is_empirical                  = False
    row.trained_at                    = datetime.now(timezone.utc).replace(tzinfo=None)
    return row
