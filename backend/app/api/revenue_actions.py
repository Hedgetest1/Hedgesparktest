from fastapi import APIRouter
from app.services.revenue_recovery_engine import generate_revenue_actions

router = APIRouter(prefix="/ai")

@router.get("/revenue-actions")
def revenue_actions():

    # placeholder until DB integration
    visitor_scores = []
    opportunities = []
    price_intel = []

    actions = generate_revenue_actions(
        visitor_scores,
        opportunities,
        price_intel
    )

    return {"actions": actions}
