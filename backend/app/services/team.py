"""
team.py — Multi-user team collaboration per shop.

Pro tier feature. Lets the shop owner invite team members with
role-based access (viewer, editor, admin). Redis-backed storage, no
schema change.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("team")

_REDIS_KEY_MEMBERS = "hs:team_members:v1"
_TTL_SECONDS = 3 * 365 * 24 * 3600
_MAX_MEMBERS_PER_SHOP = 25

Role = Literal["viewer", "editor", "admin"]
_VALID_ROLES: frozenset[str] = frozenset({"viewer", "editor", "admin"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Team members
# ---------------------------------------------------------------------------


@dataclass
class TeamMember:
    id: str
    email: str
    display_name: str
    role: Role
    added_at: str
    added_by: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "added_at": self.added_at,
            "added_by": self.added_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TeamMember":
        return cls(
            id=d["id"],
            email=d["email"],
            display_name=d.get("display_name", ""),
            role=d.get("role", "viewer"),
            added_at=d.get("added_at", ""),
            added_by=d.get("added_by", ""),
        )


def _key_members(shop: str) -> str:
    return f"{_REDIS_KEY_MEMBERS}:{shop}"


def list_members(shop_domain: str) -> list[TeamMember]:
    rc = _redis()
    if rc is None:
        record_silent_return("team.list")
        return []
    try:
        raw = rc.get(_key_members(shop_domain))
        if not raw:
            return []
        data = json.loads(raw)
        rc.expire(_key_members(shop_domain), _TTL_SECONDS)
        return [TeamMember.from_dict(d) for d in data if isinstance(d, dict)]
    except Exception:
        return []


def add_member(
    shop_domain: str, *, email: str, display_name: str = "",
    role: Role = "viewer", added_by: str = "owner",
) -> TeamMember | None:
    if role not in _VALID_ROLES:
        raise ValueError(f"invalid role {role!r}; must be in {sorted(_VALID_ROLES)}")
    if "@" not in email or len(email) > 256:
        raise ValueError("invalid email")

    existing = list_members(shop_domain)
    if any(m.email == email for m in existing):
        raise ValueError(f"member with email {email!r} already exists")
    if len(existing) >= _MAX_MEMBERS_PER_SHOP:
        raise ValueError(f"max {_MAX_MEMBERS_PER_SHOP} members per shop")

    rc = _redis()
    if rc is None:
        record_silent_return("team.add")
        return None

    member = TeamMember(
        id=str(uuid.uuid4())[:12],
        email=email,
        display_name=display_name or email.split("@")[0],
        role=role,
        added_at=_now_iso(),
        added_by=added_by,
    )
    existing.append(member)
    try:
        rc.setex(
            _key_members(shop_domain),
            _TTL_SECONDS,
            json.dumps([m.to_dict() for m in existing]),
        )
        return member
    except Exception as exc:
        log.warning("team: add_member failed: %s", exc)
        return None


def remove_member(shop_domain: str, member_id: str) -> bool:
    rc = _redis()
    if rc is None:
        record_silent_return("team.remove")
        return False
    existing = list_members(shop_domain)
    kept = [m for m in existing if m.id != member_id]
    if len(kept) == len(existing):
        return False
    try:
        if kept:
            rc.setex(
                _key_members(shop_domain),
                _TTL_SECONDS,
                json.dumps([m.to_dict() for m in kept]),
            )
        else:
            rc.delete(_key_members(shop_domain))
        return True
    except Exception:
        return False


def update_member_role(shop_domain: str, member_id: str, new_role: Role) -> bool:
    if new_role not in _VALID_ROLES:
        raise ValueError(f"invalid role {new_role!r}")
    rc = _redis()
    if rc is None:
        record_silent_return("team.update_role")
        return False
    existing = list_members(shop_domain)
    updated = False
    for m in existing:
        if m.id == member_id:
            m.role = new_role
            updated = True
            break
    if not updated:
        return False
    try:
        rc.setex(
            _key_members(shop_domain),
            _TTL_SECONDS,
            json.dumps([m.to_dict() for m in existing]),
        )
        return True
    except Exception:
        return False


