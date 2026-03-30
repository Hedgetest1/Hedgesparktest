"""
action_candidates_engine.py — Action Engine v1 candidate generator.

Produces a ranked list of action candidates from existing Pro-tier data sources.
No execution logic. No DB writes. No caching. No new tables.

This module is the bridge between signal detection and the future execution layer.
AI agents extending this should add new action types to _SIGNAL_TO_ACTION and
_ACTION_GATES — the rest of the pipeline is data-agnostic.

Data sources consumed
---------------------
  opportunity_signals       — primary behavioral triggers (8 rule-based signals)
  product_metrics           — threshold gating and reason interpolation
  price_intelligence        — PRICE_TEST action type trigger
  unique_product_detection  — SCARCITY_NUDGE gate (uniqueness confirmation)
  visitor_product_state     — revenue enrichment (top 20 candidates only)
  market_lookup             — enrichment inputs to infer_conversion_outcome

V1 Action Types
---------------
  CRO_FIX              page/funnel broken — fix the experience first
  SCARCITY_NUDGE       deep engagement + unique product — inject urgency/scarcity
  PRICE_TEST           price is the friction point — test a reduction
  RETARGET_HOT_TRAFFIC return visitors not converting — close the loop
  FLASH_INCENTIVE      live traffic spike — time-sensitive revenue window

Deduplication rule
------------------
  One candidate per (product_url, action_type). Multiple signals mapping to the
  same action type are merged: highest signal_strength kept, all signal names
  collected into supporting_signals.

Ranking formula
---------------
  rank_score = urgency × 0.5 + confidence × 100 × 0.3 + expected_loss_norm × 0.2
  where expected_loss_norm = clamp(expected_loss / 2000, 0, 1)
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.conversion_service import infer_conversion_outcome
from app.services.conversion_metrics import (
    compute_real_conversion_probability,
    get_real_product_conversion_map,
)
from app.services.empirical_calibration import (
    apply_calibration,
    compute_behavioral_index_from_features,
    get_or_train_model,
)
from app.services.opportunity_engine import get_or_refresh_signals
from app.services.revenue_loss import calculate_expected_loss
from app.services.revenue_metrics import get_shop_aov

# ---------------------------------------------------------------------------
# Signal refresh concurrency guard
#
# Prevents thundering-herd: if N requests arrive simultaneously when signals
# are stale, only ONE triggers the refresh; the others skip it and serve
# whatever signals currently exist (possibly slightly stale — acceptable).
#
# Uses a simple per-shop lock dict.  The lock is acquired only during the
# brief check-and-set on _refresh_in_progress; the actual signal refresh runs
# outside the lock (so it doesn't block other shops or the check itself).
# ---------------------------------------------------------------------------
_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_meta: threading.Lock = threading.Lock()
_refresh_last_run: dict[str, float] = {}   # shop → epoch seconds of last refresh

# Minimum seconds between signal refreshes per shop.
# The opportunity engine internally checks signal TTL (24h), but without this
# guard every concurrent request triggers the check query.
_REFRESH_COOLDOWN_SECS: float = 30.0


def _maybe_refresh_signals(shop_domain: str) -> None:
    """
    Trigger get_or_refresh_signals() for *shop_domain* at most once per
    _REFRESH_COOLDOWN_SECS across concurrent requests.

    If a refresh is already in-flight for this shop, the current caller skips
    it.  Signals from the last completed refresh are used instead — they are
    never more than SIGNAL_TTL_HOURS old by the opportunity engine's own
    staleness check.
    """
    with _refresh_locks_meta:
        if shop_domain not in _refresh_locks:
            _refresh_locks[shop_domain] = threading.Lock()
        lock = _refresh_locks[shop_domain]

    # Non-blocking try — if another thread holds the lock, skip the refresh.
    acquired = lock.acquire(blocking=False)
    if not acquired:
        return

    try:
        last = _refresh_last_run.get(shop_domain, 0.0)
        if time.monotonic() - last < _REFRESH_COOLDOWN_SECS:
            return   # cooldown not elapsed — skip
        get_or_refresh_signals(shop_domain)
        _refresh_last_run[shop_domain] = time.monotonic()
    except Exception:
        pass  # signal refresh failures are non-fatal; stale signals degrade gracefully
    finally:
        lock.release()

# ---------------------------------------------------------------------------
# Signal → action type mapping
# Add new entries here when extending to new action types in v2+.
# Signal types not listed are ignored in v1 (no candidate produced).
# ---------------------------------------------------------------------------
_SIGNAL_TO_ACTION: dict[str, str] = {
    # Traffic quality failures → fix the page experience first
    "HIGH_TRAFFIC_NO_CART":       "CRO_FIX",
    "LOW_CONVERSION_ATTENTION":   "CRO_FIX",
    "DEAD_TRAFFIC":               "CRO_FIX",
    # Deep engagement with no action → scarcity / uniqueness nudge
    "HIGH_ENGAGEMENT_NO_ACTION":  "SCARCITY_NUDGE",
    "SCROLL_HIGH_NO_CLICK":       "SCARCITY_NUDGE",
    # Return visitor interest without conversion → retarget
    "HIGH_RETURN_LOW_CONVERSION": "RETARGET_HOT_TRAFFIC",
    "RETURN_VISITOR_INTEREST":    "RETARGET_HOT_TRAFFIC",
    # Live spike → flash incentive (gated further on urgency in enrichment step)
    "TRAFFIC_SPIKE":              "FLASH_INCENTIVE",
}

# Action hints — one per action type, written for merchant consumption.
# Defined here (not via humanize_action()) because v1 action types do not
# map 1:1 to the signal_text signal types. Imperative, one sentence each.
_ACTION_HINTS: dict[str, str] = {
    "CRO_FIX": (
        "Audit the page experience — check load speed, above-the-fold content, "
        "and the primary CTA. Visitors are arriving but not engaging."
    ),
    "SCARCITY_NUDGE": (
        "Add a scarcity or social proof element — 'Only X left', a recent-purchase "
        "notification, or a limited-time label. Visitors are reading deeply but not committing."
    ),
    "PRICE_TEST": (
        "Run a controlled price reduction (5–10%) or add a price-anchoring element. "
        "Price friction is the primary identified barrier to conversion."
    ),
    "RETARGET_HOT_TRAFFIC": (
        "Target return visitors with a re-engagement offer — a loyalty discount, "
        "'welcome back' popup, or personalized reminder. These visitors already know the product."
    ),
    "FLASH_INCENTIVE": (
        "Launch a time-limited offer now — flash discount, countdown timer, or urgency banner. "
        "A traffic spike is live and not converting."
    ),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _rows(db: Session, query: str, params: dict) -> list[dict]:
    result = db.execute(text(query), params)
    return [dict(row._mapping) for row in result.fetchall()]


def _rank_score(
    urgency: float, confidence: float, expected_loss: float,
    effectiveness_boost: float = 0.0,
) -> float:
    """
    Composite rank score — higher is more urgent / valuable.

    effectiveness_boost: 0.0-1.0 multiplier from historical action outcomes.
    When data exists, effective action types get up to +10 rank points;
    ineffective types get -10. When no data exists, boost is 0 (neutral).
    """
    loss_norm = _clamp(expected_loss / 2_000.0, 0.0, 1.0)
    base = urgency * 0.5 + confidence * 100.0 * 0.3 + loss_norm * 100.0 * 0.2
    # effectiveness_boost ranges from -1.0 (all declined) to +1.0 (all improved)
    # Scale to ±10 rank points (significant but not dominant)
    return base + effectiveness_boost * 10.0


def _source_systems(action_type: str, has_vps: bool, has_ml: bool) -> list[str]:
    """List the data sources actually consulted for this candidate."""
    base = ["opportunity_signals", "product_metrics"]
    if action_type == "SCARCITY_NUDGE":
        base.append("unique_product_detection")
    if action_type == "PRICE_TEST":
        base.append("price_intelligence")
    if has_vps:
        base.append("visitor_product_state")
    if has_ml:
        base.append("market_lookup")
    return base


def _build_reason(
    action_type: str,
    metrics: dict,
    pi: dict,
    upd: dict,
) -> str:
    """Build a metric-interpolated reason string per action type."""
    v24 = int(metrics.get("views_24h") or 0)
    cart = int(metrics.get("cart_conversions_24h") or 0)
    v1h = int(metrics.get("views_1h") or 0)
    dwell = float(metrics.get("avg_dwell_24h") or 0)
    scroll = float(metrics.get("avg_scroll_24h") or 0)
    returns = int(metrics.get("return_visitor_count_7d") or 0)

    if action_type == "CRO_FIX":
        dwell_str = f"{dwell:.1f}s avg dwell" if dwell else "very low dwell time"
        return (
            f"{v24} views in 24h, {cart} added to cart. "
            f"{dwell_str} — the page is not converting attention."
        )

    if action_type == "SCARCITY_NUDGE":
        scroll_str = f"avg scroll {scroll:.0f}%" if scroll else "deep scroll engagement"
        u_score = int(upd.get("uniqueness_score") or 0)
        return (
            f"Visitors engage deeply ({scroll_str}) but don't act. "
            f"Product classified as likely unique (score {u_score}) — scarcity trigger is missing."
        )

    if action_type == "PRICE_TEST":
        pos = pi.get("price_position", "unclear")
        conf = int(pi.get("confidence_score") or 0)
        expl = (pi.get("intelligence_explanation") or "").strip()
        base = f"Price friction detected ({pos}, {conf}% confidence)."
        return f"{base} {expl}" if expl else base

    if action_type == "RETARGET_HOT_TRAFFIC":
        return (
            f"{returns} returning visitors this week, {cart} cart additions. "
            "These visitors know the product but haven't converted."
        )

    if action_type == "FLASH_INCENTIVE":
        return (
            f"Traffic spike active — {v1h} views in the last hour. "
            f"{v24} total in 24h with {cart} conversions. Revenue window is open now."
        )

    return "Multiple converging signals indicate an immediate action opportunity."


# ---------------------------------------------------------------------------
# Main engine function
# ---------------------------------------------------------------------------

def generate_action_candidates(shop_domain: str, db: Session) -> list[dict]:
    """
    Generate a ranked list of Pro action candidates from existing data sources.

    Returns at most 20 candidates sorted by rank_score descending.
    Each item is a plain dict matching the /actions/candidates/pro response schema.

    No side effects. No DB writes. No caching.
    """
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    params: dict[str, Any] = {"shop": shop_domain}

    # Resolve real AOV once per invocation — shared by all calculate_expected_loss()
    # calls below.  Falls back to 50.0 with a WARNING log if no orders exist yet.
    aov = get_shop_aov(db, shop_domain)

    # Resolve real product-level conversion data from ingested orders.
    # Returns empty dict until tracker or Product API enrichment populates
    # product_url in line_items — fallback to inferred conversion activates
    # automatically per product when real data is absent.
    real_conv_map = get_real_product_conversion_map(db, shop_domain)

    # Load or retrain the shop-specific empirical conversion calibration.
    # Returns a calibration with is_empirical=False when data is insufficient
    # (< 10 converters or < 50 product-viewing visitors) — apply_calibration()
    # then returns the inferred probability unchanged.
    calibration = get_or_train_model(db, shop_domain)

    # ------------------------------------------------------------------ #
    # 0. Ensure opportunity_signals are reasonably fresh.                 #
    # _maybe_refresh_signals() is rate-limited per shop to at most once   #
    # per _REFRESH_COOLDOWN_SECS — prevents thundering-herd when N        #
    # dashboard requests arrive simultaneously with stale signals.        #
    # The opportunity engine's own TTL check (24h) still runs inside      #
    # get_or_refresh_signals() — this guard only controls HOW OFTEN we    #
    # call into the engine per shop per process.                          #
    # ------------------------------------------------------------------ #
    _maybe_refresh_signals(shop_domain)

    # ------------------------------------------------------------------ #
    # 1. Active opportunity signals — primary triggers                     #
    # ------------------------------------------------------------------ #
    signal_rows = _rows(
        db,
        """
        SELECT product_url, signal_type, signal_strength, explanation
        FROM opportunity_signals
        WHERE shop_domain = :shop
          AND expires_at > :now
        ORDER BY signal_strength DESC
        """,
        {**params, "now": now},
    )

    if not signal_rows:
        # No active signals → no candidates. PRICE_TEST may still apply (see below).
        signal_rows = []

    # ------------------------------------------------------------------ #
    # 2. Bulk fetch supporting tables — shop-scoped, filtered in Python   #
    # One query per table, no dynamic IN clauses needed.                  #
    # ------------------------------------------------------------------ #
    metrics_rows = _rows(
        db,
        """
        SELECT
            product_url,
            COALESCE(views_1h, 0)                AS views_1h,
            COALESCE(views_24h, 0)               AS views_24h,
            COALESCE(cart_conversions_24h, 0)    AS cart_conversions_24h,
            COALESCE(return_visitor_count_7d, 0) AS return_visitor_count_7d,
            avg_dwell_24h,
            avg_scroll_24h
        FROM product_metrics
        WHERE shop_domain = :shop
        """,
        params,
    )

    pi_rows = _rows(
        db,
        """
        SELECT
            product_url,
            price_position,
            confidence_score,
            intelligence_explanation,
            CASE
                WHEN UPPER(COALESCE(price_opportunity, '')) = 'HIGH_INTENT_PRICE_OPPORTUNITY'
                    THEN 75
                ELSE 35
            END AS price_pressure_score
        FROM price_intelligence
        WHERE shop_domain = :shop
        """,
        params,
    )

    upd_rows = _rows(
        db,
        """
        SELECT product_url, uniqueness_status, uniqueness_score
        FROM unique_product_detection
        WHERE shop_domain = :shop
        """,
        params,
    )

    metrics_map: dict[str, dict] = {r["product_url"]: r for r in metrics_rows}
    pi_map:      dict[str, dict] = {r["product_url"]: r for r in pi_rows}
    upd_map:     dict[str, dict] = {r["product_url"]: r for r in upd_rows}

    # ------------------------------------------------------------------ #
    # 3. Map signals → raw candidates, deduplicate by (url, action_type) #
    # ------------------------------------------------------------------ #
    # One bucket per (product_url, action_type).
    # Multiple signals mapping to the same bucket are merged.
    buckets: dict[tuple, dict] = defaultdict(lambda: {
        "signal_strength": 0.0,
        "supporting_signals": [],
    })

    for sig in signal_rows:
        url = sig["product_url"]
        stype = sig["signal_type"]
        action_type = _SIGNAL_TO_ACTION.get(stype)
        if action_type is None:
            continue

        key = (url, action_type)
        b = buckets[key]
        b["signal_strength"] = max(b["signal_strength"], float(sig["signal_strength"] or 0))
        b["supporting_signals"].append(stype)

    # PRICE_TEST is sourced from price_intelligence directly — not from opportunity_signals.
    # Inject as candidates here, before dedup/gating.
    for url, pi in pi_map.items():
        price_pos = pi.get("price_position", "")
        conf_score = float(pi.get("confidence_score") or 0)
        views_24h = int((metrics_map.get(url) or {}).get("views_24h") or 0)

        if (
            price_pos in ("POSSIBLY_TOO_HIGH", "REVIEW_NEEDED")
            and conf_score >= 65
            and views_24h >= 15
        ):
            key = (url, "PRICE_TEST")
            b = buckets[key]
            strength = conf_score / 100.0
            b["signal_strength"] = max(b["signal_strength"], strength)
            # Sentinel signal name — marks this as a price-intelligence-derived candidate
            if "PRICE_FRICTION" not in b["supporting_signals"]:
                b["supporting_signals"].append("PRICE_FRICTION")

    if not buckets:
        return []

    # ------------------------------------------------------------------ #
    # 4. Apply per-action-type gates, compute confidence and urgency      #
    # ------------------------------------------------------------------ #
    raw_candidates: list[dict] = []

    for (url, action_type), b in buckets.items():
        metrics = metrics_map.get(url, {})
        pi      = pi_map.get(url, {})
        upd     = upd_map.get(url, {})

        views_24h       = int(metrics.get("views_24h") or 0)
        cart_24h        = int(metrics.get("cart_conversions_24h") or 0)
        returns_7d      = int(metrics.get("return_visitor_count_7d") or 0)
        avg_dwell       = float(metrics.get("avg_dwell_24h") or 0)
        base_strength   = b["signal_strength"]
        uniqueness_status = upd.get("uniqueness_status", "")
        uniqueness_score  = float(upd.get("uniqueness_score") or 0)
        conf_score_pi     = float(pi.get("confidence_score") or 0)

        if action_type == "CRO_FIX":
            # Gate: meaningful traffic AND a clear attention failure
            if views_24h < 20:
                continue
            if avg_dwell >= 10 and cart_24h > 0:
                continue  # dwell is acceptable AND some conversions exist — not a CRO failure
            confidence = _clamp(base_strength)
            urgency    = _clamp(min(views_24h / 2.0, 100.0), 0.0, 100.0)

        elif action_type == "SCARCITY_NUDGE":
            # Gate: uniqueness must be confirmed — don't inject scarcity for comparable products
            if uniqueness_status != "UNIQUE_LIKELY" or uniqueness_score < 70:
                continue
            confidence = _clamp((base_strength + uniqueness_score / 100.0) / 2.0)
            urgency    = _clamp(base_strength * 80.0, 0.0, 100.0)

        elif action_type == "PRICE_TEST":
            # Already gated at injection (conf_score >= 65, views_24h >= 15)
            confidence = _clamp(conf_score_pi / 100.0)
            # Urgency scales linearly from the 65% confidence floor to max 60
            urgency    = _clamp((conf_score_pi - 65.0) / 35.0 * 60.0, 0.0, 100.0)

        elif action_type == "RETARGET_HOT_TRAFFIC":
            # Gate: strong return-visitor signal AND no conversions yet
            if returns_7d < 5 or cart_24h > 0:
                continue
            confidence = _clamp(base_strength * 0.9)
            urgency    = _clamp(float(min(returns_7d * 5, 80)), 0.0, 100.0)

        elif action_type == "FLASH_INCENTIVE":
            # Gate: only high-strength spikes qualify pre-enrichment.
            # Urgency and auto_action_candidate are confirmed in the enrichment step.
            if base_strength < 0.5:
                continue
            confidence = _clamp(base_strength * 0.85)
            urgency    = 0.0  # overwritten by revenue enrichment below

        else:
            continue

        raw_candidates.append({
            "product_url":       url,
            "action_type":       action_type,
            # Order-preserving deduplicate of signal names within the bucket
            "supporting_signals": list(dict.fromkeys(b["supporting_signals"])),
            "confidence":        round(confidence, 4),
            "urgency":           round(urgency, 1),
            "signal_strength":   base_strength,
            # Keep refs for enrichment step — stripped from final output
            "_metrics": metrics,
            "_pi":      pi,
            "_upd":     upd,
        })

    if not raw_candidates:
        return []

    # ------------------------------------------------------------------ #
    # 5. Revenue enrichment — top 20 by signal_strength only             #
    # Two additional bulk queries, scoped to the top candidate set.      #
    # ------------------------------------------------------------------ #
    raw_candidates.sort(key=lambda c: c["signal_strength"], reverse=True)
    top_candidates = raw_candidates[:20]

    # Visitor-level behavioral aggregates per product
    vps_rows = _rows(
        db,
        """
        SELECT
            product_url,
            COALESCE(SUM(total_views), 0)                                                   AS total_views,
            COALESCE(SUM(CASE WHEN wishlist_added IS TRUE THEN 1 ELSE 0 END), 0)            AS wishlist_adds,
            COALESCE(AVG(intent_score), 0)                                                  AS avg_intent_score
        FROM visitor_product_state
        WHERE shop_domain = :shop
        GROUP BY product_url
        """,
        params,
    )

    ml_rows = _rows(
        db,
        """
        SELECT
            product_url,
            COALESCE(lookup_confidence, 70) AS market_confidence,
            CASE
                WHEN UPPER(COALESCE(uniqueness_hint, 'UNCLEAR')) = 'LIKELY_UNIQUE' THEN 80
                WHEN UPPER(COALESCE(uniqueness_hint, 'UNCLEAR')) = 'UNCLEAR'       THEN 55
                ELSE 35
            END AS uniqueness_score,
            CASE
                WHEN UPPER(COALESCE(comparable_presence, '')) = 'LIKELY_EXISTS_ELSEWHERE' THEN 80
                WHEN UPPER(COALESCE(comparable_presence, '')) = 'UNCLEAR'                 THEN 55
                ELSE 30
            END AS comparability_score
        FROM market_lookup
        WHERE shop_domain = :shop
        """,
        params,
    )

    vps_map: dict[str, dict] = {r["product_url"]: r for r in vps_rows}
    ml_map:  dict[str, dict] = {r["product_url"]: r for r in ml_rows}

    # ------------------------------------------------------------------ #
    # 6. Build final candidates with enrichment                           #
    # ------------------------------------------------------------------ #
    final: list[dict] = []

    for c in top_candidates:
        url         = c["product_url"]
        action_type = c["action_type"]
        metrics     = c["_metrics"]
        pi          = c["_pi"]
        upd         = c["_upd"]
        vps         = vps_map.get(url, {})
        ml          = ml_map.get(url, {})

        # Build the feature dict that infer_conversion_outcome expects
        enrichment: dict[str, Any] = {
            "product_id":         url,
            "product_name":       url,
            "total_views":        float(vps.get("total_views") or 0),
            "wishlist_adds":      float(vps.get("wishlist_adds") or 0),
            "avg_intent_score":   float(vps.get("avg_intent_score") or 0),
            "avg_dwell_seconds":  float(metrics.get("avg_dwell_24h") or 45),
            "avg_scroll_depth":   float(metrics.get("avg_scroll_24h") or 70),
            "market_confidence":  float(ml.get("market_confidence") or 70),
            "uniqueness_score":   float(ml.get("uniqueness_score") or 50),
            "comparability_score": float(ml.get("comparability_score") or 50),
            "price_pressure_score": float(pi.get("price_pressure_score") or 30),
        }

        outcome = infer_conversion_outcome(enrichment)

        # ------------------------------------------------------------------ #
        # 3-tier conversion probability resolution — in priority order:       #
        #                                                                      #
        #   Tier 1 (real):      product-level CVR from real order data        #
        #                       Activated when product_url is in line_items   #
        #                       (requires tracker product_id capture)         #
        #                                                                      #
        #   Tier 2 (empirical): shop-level behavioral calibration             #
        #                       Activated when ≥10 attributed purchases       #
        #                       and ≥50 product-viewing visitors exist        #
        #                                                                      #
        #   Tier 3 (inferred):  handcrafted conversion_service.py model       #
        #                       Always available; opinion-based weights        #
        # ------------------------------------------------------------------ #
        inferred_prob = float(outcome.get("conversion_probability") or 0)

        real_cvr = compute_real_conversion_probability(
            product_url=url,
            conv_map=real_conv_map,
            views_24h=int(metrics.get("views_24h") or 0),
            views_7d=int(metrics.get("views_7d") or 0),
        )

        if real_cvr is not None:
            # Tier 1: real product-level order data — highest accuracy
            conversion_prob   = real_cvr
            conversion_source = "real"
        else:
            # Tier 2 or 3: apply empirical calibration (may fall back to inferred)
            behavioral_index = compute_behavioral_index_from_features(enrichment)
            conversion_prob, conversion_source = apply_calibration(
                inferred_prob    = inferred_prob,
                behavioral_index = behavioral_index,
                model            = calibration,
            )

        loss_result = calculate_expected_loss(
            product_metrics_row={"views_24h": metrics.get("views_24h") or 0},
            conversion_probability=conversion_prob,
            aov=aov,
        )

        urgency = c["urgency"]

        # FLASH_INCENTIVE: urgency and auto_action_candidate come from enrichment
        if action_type == "FLASH_INCENTIVE":
            urgency = loss_result["urgency_score"]
            # Drop candidates that don't meet the urgency threshold after enrichment
            if urgency < 60:
                continue
            # Drop if the enrichment pipeline doesn't confirm auto-action readiness
            if not outcome.get("auto_action_candidate"):
                continue

        confidence = c["confidence"]

        final.append({
            "product_url":           url,
            "action_type":           action_type,
            "reason":                _build_reason(action_type, metrics, pi, upd),
            "supporting_signals":    c["supporting_signals"],
            "confidence":            confidence,
            "urgency":               round(urgency, 1),
            "expected_loss":         loss_result["expected_loss"],
            "loss_band":             loss_result["loss_band"],
            # conversion_probability: real → empirical → inferred (in priority order)
            "conversion_probability": round(conversion_prob, 4),
            "conversion_source":     conversion_source,
            "time_to_conversion":    outcome.get("time_to_conversion"),
            "estimated_uplift":      outcome.get("expected_uplift"),
            "source_systems":        _source_systems(action_type, bool(vps), bool(ml)),
            # ready_now: candidate clears both urgency and confidence thresholds
            "ready_now":             bool(urgency >= 60 and confidence >= 0.65),
            "action_hint":           _ACTION_HINTS[action_type],
        })

    # ------------------------------------------------------------------ #
    # 7. Sort, rank, return — boosted by historical effectiveness          #
    # ------------------------------------------------------------------ #

    # Load historical action effectiveness (closed-loop learning signal)
    effectiveness_map: dict[str, float] = {}
    try:
        from app.services.action_proof import get_action_effectiveness
        eff_stats = get_action_effectiveness(db)
        for at, stats in eff_stats.items():
            if stats["total"] >= 3:  # only trust signal with 3+ measurements
                # effectiveness is 0.0–1.0 (fraction improved)
                # Convert to -1.0 to +1.0 scale: all improved = +1, all declined = -1
                improved_ratio = stats["effectiveness"]
                declined_ratio = stats["declined"] / stats["total"] if stats["total"] > 0 else 0
                effectiveness_map[at] = improved_ratio - declined_ratio
    except Exception:
        pass  # no historical data — all boosts are 0

    final.sort(
        key=lambda c: _rank_score(
            urgency=float(c["urgency"]),
            confidence=float(c["confidence"]),
            expected_loss=float(c["expected_loss"] or 0),
            effectiveness_boost=effectiveness_map.get(c["action_type"], 0.0),
        ),
        reverse=True,
    )

    for i, candidate in enumerate(final, start=1):
        candidate["rank"] = i
        # Include effectiveness data in response for dashboard transparency
        at = candidate["action_type"]
        if at in effectiveness_map:
            candidate["historical_effectiveness"] = round(effectiveness_map[at], 2)

    return final
