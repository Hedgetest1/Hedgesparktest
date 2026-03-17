from fastapi import APIRouter
from typing import Dict, Any

router = APIRouter(prefix="/decision", tags=["decision"])


def compute_decision(
    intent_score: float,
    uniqueness_hint: str,
    price_opportunity: str | None = None,
) -> Dict[str, Any]:

    confidence = 50
    recommended_action = "OBSERVE"
    reason = "Not enough signals yet"

    if intent_score >= 85:

        if uniqueness_hint == "LIKELY_UNIQUE":
            recommended_action = "HIGHLIGHT_UNIQUENESS_AND_SCARCITY"
            reason = "High intent product likely unique"
            confidence = 90

        elif price_opportunity == "HIGH_INTENT_PRICE_OPPORTUNITY":
            recommended_action = "TEST_PRICE_DROP"
            reason = "High intent with possible price friction"
            confidence = 85

        else:
            recommended_action = "PUSH_SOCIAL_PROOF"
            reason = "High intent but unclear uniqueness"
            confidence = 75

    elif intent_score >= 60:
        recommended_action = "NUDGE_WISHLIST_OR_REMINDER"
        reason = "Medium intent detected"
        confidence = 65

    else:
        recommended_action = "NO_ACTION"
        reason = "Low intent traffic"
        confidence = 40

    return {
        "recommended_action": recommended_action,
        "reason": reason,
        "confidence": confidence,
    }


@router.post("/infer")
def infer_decision(payload: Dict[str, Any]):

    intent_score = payload.get("intent_score", 0)
    uniqueness_hint = payload.get("uniqueness_hint", "UNCLEAR")
    price_opportunity = payload.get("price_opportunity")

    return compute_decision(
        intent_score=intent_score,
        uniqueness_hint=uniqueness_hint,
        price_opportunity=price_opportunity,
    )
