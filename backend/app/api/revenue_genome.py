"""
revenue_genome.py — GET /pro/revenue-genome API endpoint.

Returns the full Revenue Genome: the DNA of the merchant's revenue.
The unreachable feature. Pro-gated. Cached 6h.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["revenue_genome"])


@router.get("/pro/revenue-genome")
def get_revenue_genome(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    The Revenue Genome: complete DNA profiling of the merchant's revenue.
    6 gene clusters, 17 genes, overall health score, prescriptive actions.
    """
    from app.services.revenue_genome import compute_revenue_genome
    return compute_revenue_genome(db, shop)
