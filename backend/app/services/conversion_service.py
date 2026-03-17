# Consolidated from app/conversion_probability_engine.py
from __future__ import annotations

from typing import Any


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(value, high))


def _norm(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return _clamp(value / max_value, 0.0, 1.0)


def compute_behavioral_score(features: dict[str, Any]) -> float:
    intent = _norm(float(features.get("intent_score", 0)), 100)
    wishlist = _norm(float(features.get("wishlist_adds", 0)), 5)
    repeat = _norm(float(features.get("repeat_views", 0)), 10)
    dwell = _norm(float(features.get("avg_dwell_seconds", 0)), 60)
    scroll = _norm(float(features.get("avg_scroll_depth", 0)), 100)

    score = (
        0.40 * intent
        + 0.20 * wishlist
        + 0.20 * repeat
        + 0.10 * dwell
        + 0.10 * scroll
    )
    return _clamp(score)


def compute_friction_score(features: dict[str, Any]) -> float:
    views = float(features.get("views", 0))
    wishlist = float(features.get("wishlist_adds", 0))
    price_pressure = _norm(float(features.get("price_pressure_score", 0)), 100)
    comparability = _norm(float(features.get("comparability_score", 0)), 100)

    gap_penalty = 0.0
    if views >= 8 and wishlist == 0:
        gap_penalty += 0.25
    elif views >= 15 and wishlist <= 1:
        gap_penalty += 0.35

    friction = gap_penalty + (0.35 * price_pressure) + (0.25 * comparability)
    return _clamp(friction)


def compute_momentum_score(features: dict[str, Any]) -> float:
    recent_views = float(features.get("recent_views", 0))
    past_views = float(features.get("past_views", 1))

    if past_views <= 0:
        past_views = 1

    ratio = recent_views / past_views

    if ratio >= 2:
        return 1.0
    if ratio >= 1.3:
        return 0.75
    if ratio >= 1.0:
        return 0.55
    if ratio >= 0.6:
        return 0.35
    return 0.15


def compute_conversion_probability(features: dict[str, Any]) -> dict[str, Any]:
    behavioral = compute_behavioral_score(features)
    friction = compute_friction_score(features)
    momentum = compute_momentum_score(features)
    uniqueness = _norm(float(features.get("uniqueness_score", 0)), 100)

    probability = (
        0.45 * behavioral
        + 0.20 * momentum
        + 0.15 * uniqueness
        - 0.20 * friction
    )
    probability = _clamp(probability, 0.01, 0.99)

    confidence = _clamp(
        0.35 * _norm(float(features.get("intent_score", 0)), 100)
        + 0.20 * _norm(float(features.get("views", 0)), 20)
        + 0.15 * _norm(float(features.get("repeat_views", 0)), 10)
        + 0.15 * _norm(float(features.get("wishlist_adds", 0)), 5)
        + 0.15 * _norm(float(features.get("market_confidence", 0)), 100),
        0.05,
        0.99,
    )

    if probability >= 0.80:
        time_to_conversion = "IMMINENT_24H"
    elif probability >= 0.65:
        time_to_conversion = "LIKELY_3D"
    elif probability >= 0.45:
        time_to_conversion = "LIKELY_7D"
    elif probability >= 0.25:
        time_to_conversion = "LONGER_HORIZON"
    else:
        time_to_conversion = "LOW_PROBABILITY"

    return {
        "conversion_probability": round(probability, 4),
        "confidence": round(confidence, 4),
        "behavioral_score": round(behavioral * 100, 2),
        "friction_score": round(friction * 100, 2),
        "momentum_score": round(momentum * 100, 2),
        "time_to_conversion": time_to_conversion,
    }


def simulate_action_uplift(
    conversion_probability: float,
    features: dict[str, Any],
) -> dict[str, Any]:
    uplifts: dict[str, float] = {}

    uniqueness_score = float(features.get("uniqueness_score", 0))
    price_pressure = float(features.get("price_pressure_score", 0))
    wishlist_adds = float(features.get("wishlist_adds", 0))
    comparability = float(features.get("comparability_score", 0))

    if uniqueness_score >= 60:
        uplifts["HIGHLIGHT_UNIQUENESS_AND_SCARCITY"] = conversion_probability + 0.08

    if price_pressure >= 45 or comparability >= 65:
        uplifts["TEST_PRICE_NUDGE"] = conversion_probability + 0.07

    if wishlist_adds >= 1:
        uplifts["SEND_REMINDER"] = conversion_probability + 0.06

    uplifts["ADD_SOCIAL_PROOF"] = conversion_probability + 0.03

    best_action = max(uplifts, key=uplifts.get)
    best_probability = _clamp(uplifts[best_action], 0.01, 0.99)
    expected_uplift = max(0.0, best_probability - conversion_probability)

    return {
        "recommended_action": best_action,
        "expected_probability_after_action": round(best_probability, 4),
        "expected_uplift": round(expected_uplift, 4),
        "all_action_uplifts": {
            key: round(value, 4) for key, value in uplifts.items()
        },
    }


def compute_revenue_opportunity_score(
    features: dict[str, Any],
    probability_result: dict[str, Any],
    uplift_result: dict[str, Any],
) -> dict[str, Any]:
    probability = float(probability_result.get("conversion_probability", 0))
    uplift = float(uplift_result.get("expected_uplift", 0))

    commercial_priority = _clamp(
        0.50 * _norm(float(features.get("views", 0)), 20)
        + 0.30 * _norm(float(features.get("wishlist_adds", 0)), 5)
        + 0.20 * _norm(float(features.get("repeat_views", 0)), 10)
    )

    urgency_multiplier = 1.0
    if float(features.get("price_pressure_score", 0)) >= 60:
        urgency_multiplier += 0.20
    if float(features.get("recent_views", 0)) > float(features.get("past_views", 1)):
        urgency_multiplier += 0.10
    if probability >= 0.65:
        urgency_multiplier += 0.10

    score = probability * uplift * commercial_priority * urgency_multiplier * 1000
    score = round(score, 2)

    if score >= 60:
        band = "CRITICAL"
    elif score >= 35:
        band = "HIGH"
    elif score >= 15:
        band = "MEDIUM"
    else:
        band = "LOW"

    return {
        "revenue_opportunity_score": score,
        "revenue_opportunity_band": band,
        "commercial_priority_score": round(commercial_priority * 100, 2),
        "urgency_multiplier": round(urgency_multiplier, 2),
    }


def infer_conversion_outcome(product: dict[str, Any]) -> dict[str, Any]:
    features = {
        "views": float(product.get("total_views", 0)),
        "repeat_views": float(product.get("repeat_views", max(0, int(float(product.get("total_views", 0)) / 2)))),
        "wishlist_adds": float(product.get("wishlist_adds", 0)),
        "avg_dwell_seconds": float(product.get("avg_dwell_seconds", 45)),
        "avg_scroll_depth": float(product.get("avg_scroll_depth", 70)),
        "intent_score": float(product.get("avg_intent_score", 0)),
        "uniqueness_score": float(product.get("uniqueness_score", 50)),
        "comparability_score": float(product.get("comparability_score", 50)),
        "price_pressure_score": float(product.get("price_pressure_score", 30)),
        "market_confidence": float(product.get("market_confidence", 70)),
        "recent_views": float(product.get("recent_views", product.get("total_views", 0))),
        "past_views": float(product.get("past_views", max(1, int(float(product.get("total_views", 0)) / 2)))),
    }

    probability_result = compute_conversion_probability(features)
    uplift_result = simulate_action_uplift(
        conversion_probability=float(probability_result["conversion_probability"]),
        features=features,
    )
    revenue_result = compute_revenue_opportunity_score(
        features=features,
        probability_result=probability_result,
        uplift_result=uplift_result,
    )

    primary_barrier = "PRICE_OR_COMPARABILITY"
    if features["price_pressure_score"] < 40 and features["comparability_score"] < 50:
        primary_barrier = "LOW_COMMITMENT"

    primary_driver = "HIGH_REPEAT_INTENT"
    if features["uniqueness_score"] >= 70:
        primary_driver = "UNIQUENESS_ADVANTAGE"

    auto_action_candidate = (
        float(probability_result["confidence"]) >= 0.70
        and float(uplift_result["expected_uplift"]) >= 0.05
    )

    return {
        "product_id": product.get("product_id"),
        "product_name": product.get("product_name") or product.get("product_id"),
        **probability_result,
        "uniqueness_score": round(features["uniqueness_score"], 2),
        "comparability_score": round(features["comparability_score"], 2),
        "price_pressure_score": round(features["price_pressure_score"], 2),
        "primary_driver": primary_driver,
        "primary_barrier": primary_barrier,
        **uplift_result,
        **revenue_result,
        "auto_action_candidate": auto_action_candidate,
    }
