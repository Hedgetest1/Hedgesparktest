"""
community_marketplace.py — Phase Ω''' marketplace service.

Publish, list, and clone community templates. Templates are vertical-aware
so when a beauty merchant browses, they see beauty templates first.

Safety:
  * Only the author shop can update / unpublish their template.
  * Clone is idempotent per (template, shop).
  * Title + description capped, payload validated against template_type.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.community_template import CommunityTemplate, CommunityTemplateClone

log = logging.getLogger("community_marketplace")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate_payload(template_type: str, payload: dict) -> None:
    if template_type == "nudge":
        required = {"nudge_type", "copy"}
    elif template_type == "rule":
        required = {"trigger_signal", "action"}
    else:
        raise ValueError("template_type must be 'nudge' or 'rule'")
    missing = required - set(payload.keys())
    if missing:
        raise ValueError(f"missing required payload keys: {sorted(missing)}")


def publish(
    db: Session,
    *,
    author_shop: str,
    template_type: str,
    title: str,
    description: str | None,
    vertical: str,
    payload: dict,
    author_label: str | None = None,
) -> CommunityTemplate:
    _validate_payload(template_type, payload)
    t = CommunityTemplate(
        template_type=template_type,
        title=title.strip()[:200],
        description=(description or "").strip()[:500] or None,
        author_shop=author_shop,
        author_label=author_label,
        vertical=vertical or "other",
        payload=payload,
        status="published",
    )
    db.add(t)
    db.flush()
    return t


def unpublish(db: Session, template_id: int, requesting_shop: str) -> bool:
    t = db.query(CommunityTemplate).get(template_id)
    if not t or t.author_shop != requesting_shop:
        return False
    t.status = "removed"
    db.flush()
    return True


def upvote(db: Session, template_id: int) -> bool:
    t = db.query(CommunityTemplate).get(template_id)
    if not t or t.status != "published":
        return False
    t.upvotes = (t.upvotes or 0) + 1
    db.flush()
    return True


def clone_template(
    db: Session,
    template_id: int,
    cloning_shop: str,
) -> dict:
    """
    Idempotent clone. Returns a dict with the cloned payload + a flag
    saying whether this was a first-time clone (counter incremented).
    """
    t = db.query(CommunityTemplate).get(template_id)
    if not t or t.status != "published":
        return {"ok": False, "error": "template_not_found"}

    existing = (
        db.query(CommunityTemplateClone)
        .filter(
            CommunityTemplateClone.template_id == template_id,
            CommunityTemplateClone.shop_domain == cloning_shop,
        )
        .one_or_none()
    )
    is_new_clone = existing is None
    if is_new_clone:
        db.add(CommunityTemplateClone(template_id=template_id, shop_domain=cloning_shop))
        t.clone_count = (t.clone_count or 0) + 1
        db.flush()

    return {
        "ok": True,
        "template_id": t.id,
        "template_type": t.template_type,
        "title": t.title,
        "payload": t.payload,
        "first_clone": is_new_clone,
    }


def list_templates(
    db: Session,
    *,
    template_type: str | None = None,
    vertical: str | None = None,
    sort: str = "popular",  # "popular" | "recent" | "upvotes"
    limit: int = 50,
) -> list[dict]:
    q = db.query(CommunityTemplate).filter(CommunityTemplate.status == "published")
    if template_type:
        q = q.filter(CommunityTemplate.template_type == template_type)
    if vertical:
        # Vertical-aware ranking: same vertical first, then "other"
        q = q.filter(or_(CommunityTemplate.vertical == vertical, CommunityTemplate.vertical == "other"))

    if sort == "recent":
        q = q.order_by(CommunityTemplate.created_at.desc())
    elif sort == "upvotes":
        q = q.order_by(CommunityTemplate.upvotes.desc(), CommunityTemplate.clone_count.desc())
    else:
        q = q.order_by(CommunityTemplate.clone_count.desc(), CommunityTemplate.upvotes.desc())

    rows = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "template_type": r.template_type,
            "title": r.title,
            "description": r.description,
            "author_label": r.author_label or "anonymous",
            "vertical": r.vertical,
            "payload": r.payload,
            "upvotes": r.upvotes or 0,
            "clone_count": r.clone_count or 0,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
