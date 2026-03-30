from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_product_intelligence(goal: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}

    product_name = payload.get("product_name", "unknown product")
    product_url = payload.get("product_url", payload.get("product_id", "unknown"))
    intent_score = float(payload.get("intent_score", payload.get("avg_intent_score", 0) or 0))
    confidence = float(payload.get("confidence", payload.get("confidence_score", 0) or 0))

    uniqueness_hint = payload.get("uniqueness_hint", "UNKNOWN")
    price_opportunity = payload.get("price_opportunity", "UNKNOWN")
    recommended_action = payload.get("recommended_action", payload.get("recommended_price_action", "NONE"))

    commercial_priority = "LOW"
    if intent_score >= 80 or confidence >= 80:
        commercial_priority = "HIGH"
    elif intent_score >= 60 or confidence >= 60:
        commercial_priority = "MEDIUM"

    summary = (
        f"WishSpark detected a {commercial_priority.lower()} priority commercial signal "
        f"for {product_name}."
    )

    recommendation_lines = [
        f"Goal: {goal}",
        f"Product: {product_name}",
        f"Product URL: {product_url}",
        f"Intent score: {intent_score}",
        f"Confidence: {confidence}",
        f"Uniqueness hint: {uniqueness_hint}",
        f"Price opportunity: {price_opportunity}",
        f"Recommended action: {recommended_action}",
    ]

    return {
        "generated_at_utc": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "goal": goal,
        "product_name": product_name,
        "product_url": product_url,
        "intent_score": intent_score,
        "confidence": confidence,
        "commercial_priority": commercial_priority,
        "uniqueness_hint": uniqueness_hint,
        "price_opportunity": price_opportunity,
        "recommended_action": recommended_action,
        "summary": summary,
        "recommendation_lines": recommendation_lines,
    }
