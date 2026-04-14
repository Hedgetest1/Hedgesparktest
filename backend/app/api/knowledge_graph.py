"""
knowledge_graph.py — Phase Ω NL query API.

  POST /pro/kg/query      — natural language question → structured answer
  GET  /pro/kg/stats      — graph composition for the merchant
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge_graph"])


class KGQueryIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class KGQueryResponse(BaseModel):
    question: str | None = None
    answer: str | None = None
    query_type: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    detail: str | None = None
    narrative: str | None = None
    error: str | None = None


class KGStatsResponse(BaseModel):
    shop_domain: str
    nodes: int
    edges: int
    node_types: dict[str, int] = Field(default_factory=dict)
    edge_types: dict[str, int] = Field(default_factory=dict)
    built_at: str | None = None


@router.post("/pro/kg/query", response_model=KGQueryResponse)
def post_kg_query(
    payload: KGQueryIn,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Natural-language query against the merchant's knowledge graph."""
    from app.services.knowledge_graph import query
    return query(db, shop, payload.question)


@router.get("/pro/kg/stats", response_model=KGStatsResponse)
def get_kg_stats(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Return composition of the merchant's knowledge graph (debug + UI)."""
    from app.services.knowledge_graph import build_graph
    return build_graph(db, shop).stats()
