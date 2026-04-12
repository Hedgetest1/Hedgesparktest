"""
annotations.py — Merchant-authored timeline annotations for charts.

"I started a Facebook campaign on March 15" → the merchant marks the date,
every chart renders a vertical line + tooltip on that point, and the system
computes the post-annotation metric delta ("revenue +12% after this change").

Triple Whale ships this as a flagship UX feature. Small effort, big trust
uplift — the merchant connects their actions to the dashboard's metrics.

Storage: Redis JSON list per shop, long TTL, zero schema change.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("annotations")

_REDIS_KEY_PREFIX = "hs:annotations:v1"
_TTL_SECONDS = 3 * 365 * 24 * 3600  # 3 years, refreshed on write
_MAX_PER_SHOP = 200


@dataclass
class Annotation:
    id: str
    date: str            # ISO date yyyy-mm-dd
    label: str           # short user-facing title
    description: str     # longer note (optional)
    category: str        # 'campaign' | 'product' | 'pricing' | 'site_change' | 'other'
    created_at: str
    author: str          # operator/user identifier

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "label": self.label,
            "description": self.description,
            "category": self.category,
            "created_at": self.created_at,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Annotation":
        return cls(
            id=d["id"],
            date=d["date"],
            label=d["label"],
            description=d.get("description", ""),
            category=d.get("category", "other"),
            created_at=d.get("created_at", ""),
            author=d.get("author", "merchant"),
        )


_ALLOWED_CATEGORIES = frozenset({
    "campaign", "product", "pricing", "site_change", "inventory", "other",
})


def _key(shop: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def list_annotations(shop_domain: str, limit: int = 100) -> list[Annotation]:
    """Return the most recent `limit` annotations for a shop, newest first."""
    rc = _redis()
    if rc is None:
        return []
    try:
        raw = rc.get(_key(shop_domain))
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        # Refresh TTL so active shops keep their annotations
        rc.expire(_key(shop_domain), _TTL_SECONDS)
        items = [Annotation.from_dict(d) for d in data if isinstance(d, dict)]
        items.sort(key=lambda a: a.date, reverse=True)
        return items[:limit]
    except Exception as exc:
        log.debug("annotations: list failed: %s", exc)
        return []


def create_annotation(
    shop_domain: str, *, date: str, label: str,
    description: str = "", category: str = "other", author: str = "merchant",
) -> Annotation | None:
    """Add a new annotation. Returns the saved Annotation or None on error."""
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError(
            f"category {category!r} not allowed; must be one of {sorted(_ALLOWED_CATEGORIES)}"
        )
    if not label.strip():
        raise ValueError("label cannot be empty")
    # Validate date
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"date {date!r} must be ISO format YYYY-MM-DD")

    rc = _redis()
    if rc is None:
        return None

    ann = Annotation(
        id=str(uuid.uuid4())[:12],
        date=date,
        label=label.strip()[:120],
        description=description.strip()[:500],
        category=category,
        created_at=_now_iso(),
        author=author[:64],
    )

    try:
        existing = list_annotations(shop_domain, limit=_MAX_PER_SHOP)
        existing.append(ann)
        # Cap at _MAX_PER_SHOP newest — drop oldest
        existing.sort(key=lambda a: a.date, reverse=True)
        trimmed = existing[:_MAX_PER_SHOP]
        rc.setex(
            _key(shop_domain),
            _TTL_SECONDS,
            json.dumps([a.to_dict() for a in trimmed]),
        )
        return ann
    except Exception as exc:
        log.warning("annotations: create failed: %s", exc)
        return None


def delete_annotation(shop_domain: str, annotation_id: str) -> bool:
    """Delete by id. Returns True if removed."""
    rc = _redis()
    if rc is None:
        return False
    try:
        existing = list_annotations(shop_domain, limit=_MAX_PER_SHOP)
        kept = [a for a in existing if a.id != annotation_id]
        if len(kept) == len(existing):
            return False
        if kept:
            rc.setex(
                _key(shop_domain),
                _TTL_SECONDS,
                json.dumps([a.to_dict() for a in kept]),
            )
        else:
            rc.delete(_key(shop_domain))
        return True
    except Exception:
        return False


def get_annotations_in_range(
    shop_domain: str, start_date: str, end_date: str,
) -> list[Annotation]:
    """Return annotations whose date falls in [start_date, end_date] inclusive."""
    all_ann = list_annotations(shop_domain, limit=_MAX_PER_SHOP)
    return [a for a in all_ann if start_date <= a.date <= end_date]
