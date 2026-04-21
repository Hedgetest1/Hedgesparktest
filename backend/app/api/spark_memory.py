"""
spark_memory.py — GET /merchant/spark-memory endpoint.

Returns the Spark Memory timeline payload for Lite v5 Zone 5:
up to 5 most-recent notable events from the last 7 days, each
formatted as a first-person Spark sentence.

Spec: /docs/LITE_VISUAL_SPEC_v5.md §2 Zone 5 + §9 endpoint contract.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.services.spark_memory import build_spark_memory

router = APIRouter(tags=["spark_memory"])


class SparkMemoryEventResponse(BaseModel):
    timestamp: str  # ISO
    relative_label: str
    event_type: str
    sentence: str
    dot_color: str


class SparkMemoryResponse(BaseModel):
    shop_domain: str
    events: list[SparkMemoryEventResponse] = Field(default_factory=list)
    count: int = 0
    generated_at: str | None = None


@router.get(
    "/merchant/spark-memory",
    response_model=SparkMemoryResponse,
    response_model_exclude_none=False,
)
def get_spark_memory(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Up to 5 most-recent events for Spark's Memory timeline (Zone 5).

    Sources (v1): daily_brief + ops_alerts (scoped to the shop,
    last 7 days, severity in {info, warning}, alert_type mapped to
    canonical Spark event_type). Unmapped alerts are skipped —
    never fabricate a narrative to fit an unknown event.

    Cold-start: empty events list + count=0. UI renders
    "Watching your store…" placeholder.
    """
    return build_spark_memory(db, shop)
