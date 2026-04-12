"""team.py — Team collaboration API (members + comments)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["team"])


# --- Members ---

class MemberPayload(BaseModel):
    email: str = Field(..., max_length=256)
    display_name: str = Field("", max_length=120)
    role: str = Field("viewer", description="viewer | editor | admin")


class MemberRow(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    added_at: str
    added_by: str


class MembersListResponse(BaseModel):
    shop_domain: str
    members: list[MemberRow] = Field(default_factory=list)


@router.get(
    "/pro/team/members",
    response_model=MembersListResponse,
    response_model_exclude_none=False,
)
def list_members_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.team import list_members
    return MembersListResponse(
        shop_domain=shop,
        members=[MemberRow(**m.to_dict()) for m in list_members(shop)],
    )


@router.post(
    "/pro/team/members",
    response_model=MemberRow,
    response_model_exclude_none=False,
)
def add_member_endpoint(
    payload: MemberPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.team import add_member
    try:
        m = add_member(
            shop,
            email=payload.email,
            display_name=payload.display_name,
            role=payload.role,
            added_by="owner",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if m is None:
        raise HTTPException(status_code=503, detail="team storage unavailable")
    return MemberRow(**m.to_dict())


@router.delete("/pro/team/members/{member_id}")
def remove_member_endpoint(
    member_id: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.team import remove_member
    removed = remove_member(shop, member_id)
    if not removed:
        raise HTTPException(status_code=404, detail="member not found")
    return {"removed": True, "id": member_id}


# --- Comments ---

class CommentPayload(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class CommentRow(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    author_id: str
    author_name: str
    body: str
    mentions: list[str]
    created_at: str


class CommentsListResponse(BaseModel):
    shop_domain: str
    entity_type: str
    entity_id: str
    comments: list[CommentRow] = Field(default_factory=list)


@router.get(
    "/pro/team/comments/{entity_type}/{entity_id}",
    response_model=CommentsListResponse,
    response_model_exclude_none=False,
)
def list_comments_endpoint(
    entity_type: str,
    entity_id: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.team import list_comments
    comments = list_comments(shop, entity_type, entity_id)
    return CommentsListResponse(
        shop_domain=shop,
        entity_type=entity_type,
        entity_id=entity_id,
        comments=[CommentRow(**c.to_dict()) for c in comments],
    )


@router.post(
    "/pro/team/comments/{entity_type}/{entity_id}",
    response_model=CommentRow,
    response_model_exclude_none=False,
)
def add_comment_endpoint(
    entity_type: str,
    entity_id: str,
    payload: CommentPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.team import add_comment
    try:
        c = add_comment(
            shop,
            entity_type=entity_type,
            entity_id=entity_id,
            author_id="owner",
            author_name="Shop Owner",
            body=payload.body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if c is None:
        raise HTTPException(status_code=503, detail="comment storage unavailable")
    return CommentRow(**c.to_dict())


@router.delete("/pro/team/comments/{entity_type}/{entity_id}/{comment_id}")
def delete_comment_endpoint(
    entity_type: str,
    entity_id: str,
    comment_id: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.team import delete_comment
    removed = delete_comment(shop, entity_type, entity_id, comment_id)
    if not removed:
        raise HTTPException(status_code=404, detail="comment not found")
    return {"deleted": True, "id": comment_id}
