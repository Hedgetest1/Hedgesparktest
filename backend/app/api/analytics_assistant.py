"""
analytics_assistant.py — endpoint for the AI analytics chat.

Strada 4 dominance move (2026-04-20). Single POST endpoint:
  POST /chat/analytics
  body: { "question": "..." }
  response: { "answer", "data_sources", "suggested_followups", "degraded" }

Auth: require_merchant_session (Lite + Pro).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.services import analytics_assistant as svc

router = APIRouter(tags=["analytics_assistant"])


class AnalyticsAskRequest(BaseModel):
    question: str = Field(..., max_length=500)
    # Short-memory: last question + excerpt of last answer + last
    # followups, so the LLM avoids repeating itself on consecutive
    # turns. All optional — first question in a session has no prior.
    prior_question: str | None = Field(default=None, max_length=500)
    prior_answer_excerpt: str | None = Field(default=None, max_length=400)
    prior_followups: list[str] = Field(default_factory=list, max_length=5)


class AnalyticsAskResponse(BaseModel):
    answer: str
    data_sources: list[str] = Field(default_factory=list)
    suggested_followups: list[str] = Field(default_factory=list)
    degraded: bool = False


@router.post("/chat/analytics", response_model=AnalyticsAskResponse)
def ask(
    body: AnalyticsAskRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Answer an analytics question using real data from the shop's
    services (RARS, Brief, Benchmarks, Cohorts, Attribution, P&L).
    LLM composes prose around deterministic numbers; no invented
    metrics. Degrades gracefully to a data-only summary if LLM is
    unavailable (budget, backoff, or PII guard).

    Optional prior_question/prior_answer_excerpt/prior_followups let
    the dashboard pass short-memory so consecutive turns in a session
    don't echo each other.
    """
    prior = None
    if body.prior_question or body.prior_answer_excerpt:
        prior = svc.PriorExchange(
            question=body.prior_question or "",
            answer_excerpt=body.prior_answer_excerpt or "",
            previous_followups=list(body.prior_followups or []),
        )
    result = svc.answer(db, shop, body.question, prior=prior)
    return AnalyticsAskResponse(
        answer=result.answer,
        data_sources=result.data_sources,
        suggested_followups=result.suggested_followups,
        degraded=result.degraded,
    )
