"""annotations.py — CRUD API for merchant chart annotations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["annotations"])


class AnnotationPayload(BaseModel):
    date: str = Field(..., description="ISO date YYYY-MM-DD")
    label: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    category: str = Field("other")


class AnnotationRow(BaseModel):
    id: str
    date: str
    label: str
    description: str
    category: str
    created_at: str
    author: str


class AnnotationsListResponse(BaseModel):
    shop_domain: str
    annotations: list[AnnotationRow] = Field(default_factory=list)


@router.get(
    "/pro/annotations",
    response_model=AnnotationsListResponse,
    response_model_exclude_none=False,
)
def list_annotations_endpoint(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.annotations import list_annotations, get_annotations_in_range
    if start_date and end_date:
        anns = get_annotations_in_range(shop, start_date, end_date)
    else:
        anns = list_annotations(shop)
    return AnnotationsListResponse(
        shop_domain=shop,
        annotations=[AnnotationRow(**a.to_dict()) for a in anns],
    )


@router.post(
    "/pro/annotations",
    response_model=AnnotationRow,
    response_model_exclude_none=False,
)
def create_annotation_endpoint(
    payload: AnnotationPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.annotations import create_annotation
    try:
        ann = create_annotation(
            shop,
            date=payload.date,
            label=payload.label,
            description=payload.description,
            category=payload.category,
            author="merchant",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if ann is None:
        raise HTTPException(status_code=503, detail="annotation storage unavailable")
    return AnnotationRow(**ann.to_dict())


@router.delete("/pro/annotations/{annotation_id}")
def delete_annotation_endpoint(
    annotation_id: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.annotations import delete_annotation
    removed = delete_annotation(shop, annotation_id)
    if not removed:
        raise HTTPException(status_code=404, detail="annotation not found")
    return {"deleted": True, "id": annotation_id}
