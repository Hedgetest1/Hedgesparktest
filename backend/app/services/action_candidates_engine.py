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

import logging
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

# F821 class fix (2026-05-19i): `log` was used in 3 except handlers
# (lines ~115/126/603) but NEVER bound → every error path raised
# NameError instead of logging (the exact recurring Sentry class).
# Canonical project pattern (revenue_metrics.py:72).
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal refresh concurrency guard — 10k merchant scale
#
# v2 approach (Apr 2026): a per-process thread-safe dict kept throwing a
# global meta-lock at every request regardless of whether a refresh was
# actually needed. At 10k merchants × N concurrent requests each, that
# global lock serializes the entire fleet through one mutex.
#
# Fix: lock-free fast path via a per-shop epoch read. If the last refresh
# is within cooldown, we return immediately without touching ANY lock
# (dict reads are atomic under the GIL, so reading `_refresh_last_run`
# without synchronization is safe — the worst case is a stale read that
# triggers one extra refresh, which is still gated by the Redis SETNX
# claim below).
#
# The actual inter-process dedup is done by a Redis SETNX claim, which
# works across uvicorn workers too — unlike threading.Lock which is
# process-local and useless once you fork.
# ---------------------------------------------------------------------------
_refresh_last_run: dict[str, float] = {}   # multi-worker: redis-backed — fast-path cache; real dedup via Redis SETNX

# Minimum seconds between signal refreshes per shop.
# The opportunity engine internally checks signal TTL (24h), but without this
# guard every concurrent request triggers the check query.
_REFRESH_COOLDOWN_SECS: float = 30.0


def _maybe_refresh_signals(shop_domain: str) -> None:
    """
    Trigger get_or_refresh_signals() for *shop_domain* at most once per
    _REFRESH_COOLDOWN_SECS across concurrent requests AND processes.

    Fast path: lock-free cooldown check. Only if the cooldown has elapsed
    do we attempt a Redis SETNX claim (cross-process atomic). Fallback to
    no claim if Redis is unavailable — degrades to per-process dedup only.
    """
    # Lock-free cooldown gate — atomic dict read under GIL.
    last = _refresh_last_run.get(shop_domain, 0.0)
    if time.monotonic() - last < _REFRESH_COOLDOWN_SECS:
        return

    # Cross-process claim via Redis SETNX. TTL slightly longer than cooldown
    # so a crashed claimant frees the lock automatically.
    claimed = True
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            key = f"hs:refresh_claim:{shop_domain}"
            claimed = bool(rc.set(key, "1", nx=True, ex=int(_REFRESH_COOLDOWN_SECS) + 10))
    except Exception as exc:
        log.warning("action_candidates_engine: redis claim failed: %s", exc)

    if not claimed:
        # Another process already doing the refresh — bump our local epoch
        # so we skip for the cooldown window.
        _refresh_last_run[shop_domain] = time.monotonic()
        return

    try:
        get_or_refresh_signals(shop_domain)
    except Exception as exc:
        log.warning("action_candidates_engine: signal refresh failed: %s", exc)
    finally:
        _refresh_last_run[shop_domain] = time.monotonic()

# ---------------------------------------------------------------------------
# Signal → action type mapping
# Add new entries here when extending to new action types in v2+.
# Signal types not listed are ignored in v1 (no candidate produced).
# ---------------------------------------------------------------------------
_SIGNAL_TO_ACTION: dict[str, str] = {
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

_ACTION_BLOCKLIST = frozenset({
    "legacy.myshopify.com",
})


# ---------------------------------------------------------------------------
# Pipeline stages — each pure function consumes the prior stage's output.
# generate_action_candidates is the composer; stages are independently
# testable with no per-stage state mutation across the pipeline.
# ---------------------------------------------------------------------------


def _fetch_active_signals(db: Session, shop_domain: str, now: datetime) -> list[dict]:
    """Stage 1: active opportunity_signals for this shop."""
    return _rows(
        db,
        """
        SELECT product_url, signal_type, signal_strength, explanation
        FROM opportunity_signals
        WHERE shop_domain = :shop
          AND expires_at > :now
        ORDER BY signal_strength DESC
        """,
        {"shop": shop_domain, "now": now},
    )


def _fetch_supporting_tables(db: Session, shop_domain: str) -> dict[str, dict]:
    """Stage 2: bulk fetch supporting tables — one query per table,
    no dynamic IN clauses. Returns indexed maps keyed by product_url."""
    params = {"shop": shop_domain}
    metrics_rows = _rows(db, """
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
    """, params)

    pi_rows = _rows(db, """
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
    """, params)

    upd_rows = _rows(db, """
        SELECT product_url, uniqueness_status, uniqueness_score
        FROM unique_product_detection
        WHERE shop_domain = :shop
    """, params)

    return {
        "metrics_map": {r["product_url"]: r for r in metrics_rows},
        "pi_map":      {r["product_url"]: r for r in pi_rows},
        "upd_map":     {r["product_url"]: r for r in upd_rows},
    }


def _build_signal_buckets(
    signal_rows: list[dict],
    metrics_map: dict[str, dict],
    pi_map: dict[str, dict],
) -> dict[tuple, dict]:
    """Stage 3: map signals → buckets keyed by (url, action_type).
    Multiple signals mapping to the same bucket are merged (max
    signal_strength, ordered union of signal names). PRICE_TEST is
    sourced from price_intelligence directly — injected here."""
    buckets: dict[tuple, dict] = defaultdict(lambda: {
        "signal_strength": 0.0,
        "supporting_signals": [],
    })

    for sig in signal_rows:
        action_type = _SIGNAL_TO_ACTION.get(sig["signal_type"])
        if action_type is None:
            continue
        b = buckets[(sig["product_url"], action_type)]
        b["signal_strength"] = max(b["signal_strength"], float(sig["signal_strength"] or 0))
        b["supporting_signals"].append(sig["signal_type"])

    # PRICE_TEST injection from price_intelligence (not from signals).
    for url, pi in pi_map.items():
        price_pos = pi.get("price_position", "")
        conf_score = float(pi.get("confidence_score") or 0)
        views_24h = int((metrics_map.get(url) or {}).get("views_24h") or 0)
        if (
            price_pos in ("POSSIBLY_TOO_HIGH", "REVIEW_NEEDED")
            and conf_score >= 65
            and views_24h >= 15
        ):
            b = buckets[(url, "PRICE_TEST")]
            b["signal_strength"] = max(b["signal_strength"], conf_score / 100.0)
            if "PRICE_FRICTION" not in b["supporting_signals"]:
                b["supporting_signals"].append("PRICE_FRICTION")
    return buckets


def _gate_scarcity(b: dict, metrics: dict, pi: dict, upd: dict) -> tuple[float, float] | None:
    """SCARCITY_NUDGE gate: uniqueness must be confirmed."""
    if upd.get("uniqueness_status", "") != "UNIQUE_LIKELY":
        return None
    uniqueness_score = float(upd.get("uniqueness_score") or 0)
    if uniqueness_score < 70:
        return None
    confidence = _clamp((b["signal_strength"] + uniqueness_score / 100.0) / 2.0)
    urgency = _clamp(b["signal_strength"] * 80.0, 0.0, 100.0)
    return confidence, urgency


def _gate_price_test(b: dict, metrics: dict, pi: dict, upd: dict) -> tuple[float, float] | None:
    """PRICE_TEST: already pre-gated at injection."""
    conf_score = float(pi.get("confidence_score") or 0)
    confidence = _clamp(conf_score / 100.0)
    # Urgency scales linearly from the 65% floor to max 60.
    urgency = _clamp((conf_score - 65.0) / 35.0 * 60.0, 0.0, 100.0)
    return confidence, urgency


def _gate_retarget(b: dict, metrics: dict, pi: dict, upd: dict) -> tuple[float, float] | None:
    """RETARGET_HOT_TRAFFIC gate: strong returns + no conversions yet."""
    returns_7d = int(metrics.get("return_visitor_count_7d") or 0)
    cart_24h = int(metrics.get("cart_conversions_24h") or 0)
    if returns_7d < 5 or cart_24h > 0:
        return None
    confidence = _clamp(b["signal_strength"] * 0.9)
    urgency = _clamp(float(min(returns_7d * 5, 80)), 0.0, 100.0)
    return confidence, urgency


def _gate_flash(b: dict, metrics: dict, pi: dict, upd: dict) -> tuple[float, float] | None:
    """FLASH_INCENTIVE pre-gate: strength only; urgency confirmed at enrichment."""
    if b["signal_strength"] < 0.5:
        return None
    confidence = _clamp(b["signal_strength"] * 0.85)
    return confidence, 0.0  # urgency overwritten by enrichment


_ACTION_GATES: dict[str, Any] = {
    "SCARCITY_NUDGE":       _gate_scarcity,
    "PRICE_TEST":           _gate_price_test,
    "RETARGET_HOT_TRAFFIC": _gate_retarget,
    "FLASH_INCENTIVE":      _gate_flash,
}


def _apply_action_gates(
    buckets: dict[tuple, dict],
    supporting: dict[str, dict],
) -> list[dict]:
    """Stage 4: apply per-action-type gates, compute confidence/urgency.
    Filters buckets that fail their gate; emits raw candidate dicts."""
    raw: list[dict] = []
    for (url, action_type), b in buckets.items():
        gate = _ACTION_GATES.get(action_type)
        if gate is None:
            continue
        metrics = supporting["metrics_map"].get(url, {})
        pi      = supporting["pi_map"].get(url, {})
        upd     = supporting["upd_map"].get(url, {})
        result = gate(b, metrics, pi, upd)
        if result is None:
            continue
        confidence, urgency = result
        raw.append({
            "product_url": url,
            "action_type": action_type,
            "supporting_signals": list(dict.fromkeys(b["supporting_signals"])),
            "confidence": round(confidence, 4),
            "urgency": round(urgency, 1),
            "signal_strength": b["signal_strength"],
            # Refs for enrichment — stripped from final output.
            "_metrics": metrics,
            "_pi": pi,
            "_upd": upd,
        })
    return raw


def _fetch_enrichment(db: Session, shop_domain: str) -> dict[str, dict]:
    """Stage 5: revenue enrichment fetches (visitor_product_state + market_lookup).
    Two SQL queries, indexed by product_url."""
    params = {"shop": shop_domain}
    vps_rows = _rows(db, """
        SELECT
            product_url,
            COALESCE(SUM(total_views), 0)                                                   AS total_views,
            COALESCE(SUM(CASE WHEN wishlist_added IS TRUE THEN 1 ELSE 0 END), 0)            AS wishlist_adds,
            COALESCE(AVG(intent_score), 0)                                                  AS avg_intent_score
        FROM visitor_product_state
        WHERE shop_domain = :shop
        GROUP BY product_url
    """, params)

    ml_rows = _rows(db, """
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
    """, params)

    return {
        "vps_map": {r["product_url"]: r for r in vps_rows},
        "ml_map":  {r["product_url"]: r for r in ml_rows},
    }


def _resolve_conversion_probability(
    *,
    url: str,
    metrics: dict,
    enrichment_features: dict,
    real_conv_map: dict,
    calibration: Any,
    inferred_prob: float,
) -> tuple[float, str]:
    """3-tier conversion probability resolution in priority order:
        Tier 1 (real):      product-level CVR from real order data
        Tier 2 (empirical): shop-level behavioral calibration
        Tier 3 (inferred):  handcrafted conversion_service.py model
    Returns (probability, source_label)."""
    real_cvr = compute_real_conversion_probability(
        product_url=url,
        conv_map=real_conv_map,
        views_24h=int(metrics.get("views_24h") or 0),
        views_7d=int(metrics.get("views_7d") or 0),
    )
    if real_cvr is not None:
        return real_cvr, "real"
    behavioral_index = compute_behavioral_index_from_features(enrichment_features)
    prob, source = apply_calibration(
        inferred_prob=inferred_prob,
        behavioral_index=behavioral_index,
        model=calibration,
    )
    return prob, source


def _build_final_candidate(
    raw: dict,
    enrichment: dict[str, dict],
    *,
    real_conv_map: dict,
    calibration: Any,
    aov: float,
) -> dict | None:
    """Stage 6: build one final candidate dict with all enrichment applied.
    Returns None if a FLASH_INCENTIVE post-enrichment gate fails."""
    url = raw["product_url"]
    action_type = raw["action_type"]
    metrics = raw["_metrics"]
    pi = raw["_pi"]
    upd = raw["_upd"]
    vps = enrichment["vps_map"].get(url, {})
    ml = enrichment["ml_map"].get(url, {})

    features: dict[str, Any] = {
        "product_id": url,
        "product_name": url,
        "total_views": float(vps.get("total_views") or 0),
        "wishlist_adds": float(vps.get("wishlist_adds") or 0),
        "avg_intent_score": float(vps.get("avg_intent_score") or 0),
        "avg_dwell_seconds": float(metrics.get("avg_dwell_24h") or 45),
        "avg_scroll_depth": float(metrics.get("avg_scroll_24h") or 70),
        "market_confidence": float(ml.get("market_confidence") or 70),
        "uniqueness_score": float(ml.get("uniqueness_score") or 50),
        "comparability_score": float(ml.get("comparability_score") or 50),
        "price_pressure_score": float(pi.get("price_pressure_score") or 30),
    }
    outcome = infer_conversion_outcome(features)
    inferred_prob = float(outcome.get("conversion_probability") or 0)
    conversion_prob, conversion_source = _resolve_conversion_probability(
        url=url, metrics=metrics, enrichment_features=features,
        real_conv_map=real_conv_map, calibration=calibration,
        inferred_prob=inferred_prob,
    )
    loss_result = calculate_expected_loss(
        product_metrics_row={"views_24h": metrics.get("views_24h") or 0},
        conversion_probability=conversion_prob,
        aov=aov,
    )

    urgency = raw["urgency"]
    if action_type == "FLASH_INCENTIVE":
        urgency = loss_result["urgency_score"]
        # Post-enrichment gate: urgency threshold + auto-action confirmation.
        if urgency < 60 or not outcome.get("auto_action_candidate"):
            return None

    confidence = raw["confidence"]
    return {
        "product_url": url,
        "action_type": action_type,
        "reason": _build_reason(action_type, metrics, pi, upd),
        "supporting_signals": raw["supporting_signals"],
        "confidence": confidence,
        "urgency": round(urgency, 1),
        "expected_loss": loss_result["expected_loss"],
        "loss_band": loss_result["loss_band"],
        # conversion_probability: real → empirical → inferred (priority order).
        "conversion_probability": round(conversion_prob, 4),
        "conversion_source": conversion_source,
        "time_to_conversion": outcome.get("time_to_conversion"),
        "estimated_uplift": outcome.get("expected_uplift"),
        "source_systems": _source_systems(action_type, bool(vps), bool(ml)),
        "ready_now": bool(urgency >= 60 and confidence >= 0.65),
        "action_hint": _ACTION_HINTS[action_type],
    }


def _load_effectiveness_map(db: Session) -> dict[str, float]:
    """Stage 7a: historical action_type effectiveness as ±1.0 scale.
    Returns empty dict if no measurements or query fails."""
    try:
        from app.services.action_proof import get_action_effectiveness
        eff_stats = get_action_effectiveness(db)
    except Exception as exc:
        log.warning("action_candidates_engine: effectiveness history query failed: %s", exc)
        return {}
    out: dict[str, float] = {}
    for at, stats in eff_stats.items():
        if stats["total"] < 3:
            continue  # only trust signal with 3+ measurements
        improved_ratio = stats["effectiveness"]
        declined_ratio = stats["declined"] / stats["total"] if stats["total"] > 0 else 0
        out[at] = improved_ratio - declined_ratio
    return out


def _rank_and_finalize(
    candidates: list[dict],
    effectiveness_map: dict[str, float],
) -> list[dict]:
    """Stage 7b: sort by rank_score, attach rank + historical_effectiveness."""
    candidates.sort(
        key=lambda c: _rank_score(
            urgency=float(c["urgency"]),
            confidence=float(c["confidence"]),
            expected_loss=float(c["expected_loss"] or 0),
            effectiveness_boost=effectiveness_map.get(c["action_type"], 0.0),
        ),
        reverse=True,
    )
    for i, candidate in enumerate(candidates, start=1):
        candidate["rank"] = i
        at = candidate["action_type"]
        if at in effectiveness_map:
            candidate["historical_effectiveness"] = round(effectiveness_map[at], 2)
    return candidates


def generate_action_candidates(shop_domain: str, db: Session) -> list[dict]:
    """Generate a ranked list of Pro action candidates from existing data sources.

    Returns at most 20 candidates sorted by rank_score descending. Each item
    is a plain dict matching the /actions/candidates/pro response schema.

    Pipeline (each stage is a pure helper above):
       0  refresh signals (rate-limited per shop)
       1  fetch active opportunity_signals
       2  fetch supporting tables (metrics + price_intelligence + uniqueness)
       3  build signal buckets keyed by (url, action_type) + PRICE_TEST inject
       4  apply per-action-type gates → raw candidates with confidence/urgency
       5  fetch enrichment (visitor_product_state + market_lookup) for top-20
       6  build final candidates with conversion-tier resolution + loss math
       7  sort by rank_score boosted by historical effectiveness

    No side effects. No DB writes. No caching.
    """
    if shop_domain in _ACTION_BLOCKLIST:
        return []

    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    # Per-invocation shop context — these touch external tables/Redis and
    # are shared across every candidate computation below.
    aov = get_shop_aov(db, shop_domain)
    real_conv_map = get_real_product_conversion_map(db, shop_domain)
    calibration = get_or_train_model(db, shop_domain)

    _maybe_refresh_signals(shop_domain)  # stage 0

    signal_rows = _fetch_active_signals(db, shop_domain, now)
    supporting = _fetch_supporting_tables(db, shop_domain)

    buckets = _build_signal_buckets(
        signal_rows, supporting["metrics_map"], supporting["pi_map"],
    )
    if not buckets:
        return []

    raw_candidates = _apply_action_gates(buckets, supporting)
    if not raw_candidates:
        return []

    raw_candidates.sort(key=lambda c: c["signal_strength"], reverse=True)
    top_candidates = raw_candidates[:20]
    enrichment = _fetch_enrichment(db, shop_domain)

    final: list[dict] = []
    for raw in top_candidates:
        candidate = _build_final_candidate(
            raw, enrichment,
            real_conv_map=real_conv_map,
            calibration=calibration,
            aov=aov,
        )
        if candidate is not None:
            final.append(candidate)

    return _rank_and_finalize(final, _load_effectiveness_map(db))
