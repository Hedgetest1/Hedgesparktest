"""
team.py — Multi-user team collaboration per shop.

Pro tier feature. Lets the shop owner invite team members with
role-based access (viewer, editor, admin) and a comment system on
findings. Redis-backed storage, no schema change.

v1 scope (this session):
  * Team member CRUD (add/remove/list)
  * Role model: viewer | editor | admin
  * Comments on any entity by (entity_type, entity_id)
  * @mention extraction from comment bodies

v2 (memory):
  * Email notification on @mention
  * SSO / SAML integration
  * Audit log of comment activity
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger("team")

_REDIS_KEY_MEMBERS = "hs:team_members:v1"
_REDIS_KEY_COMMENTS = "hs:team_comments:v1"
_TTL_SECONDS = 3 * 365 * 24 * 3600
_MAX_MEMBERS_PER_SHOP = 25
_MAX_COMMENTS_PER_ENTITY = 100

Role = Literal["viewer", "editor", "admin"]
_VALID_ROLES: frozenset[str] = frozenset({"viewer", "editor", "admin"})

_MENTION_RE = re.compile(r"@([a-zA-Z0-9_.-]+)")


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


# ---------------------------------------------------------------------------
# Comments on entities
# ---------------------------------------------------------------------------


@dataclass
class Comment:
    id: str
    entity_type: str  # bugfix_candidate | finding | goal | nudge | ...
    entity_id: str
    author_id: str
    author_name: str
    body: str
    mentions: list[str]
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "body": self.body,
            "mentions": self.mentions,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Comment":
        return cls(
            id=d["id"],
            entity_type=d["entity_type"],
            entity_id=str(d["entity_id"]),
            author_id=d.get("author_id", ""),
            author_name=d.get("author_name", ""),
            body=d.get("body", ""),
            mentions=list(d.get("mentions") or []),
            created_at=d.get("created_at", ""),
        )


def _key_comments(shop: str, entity_type: str, entity_id: str) -> str:
    return f"{_REDIS_KEY_COMMENTS}:{shop}:{entity_type}:{entity_id}"


def extract_mentions(body: str) -> list[str]:
    """Return the lowercased list of @mention handles found in body."""
    return sorted(set(m.lower() for m in _MENTION_RE.findall(body)))


def list_comments(shop_domain: str, entity_type: str, entity_id: str) -> list[Comment]:
    rc = _redis()
    if rc is None:
        return []
    try:
        raw = rc.get(_key_comments(shop_domain, entity_type, entity_id))
        if not raw:
            return []
        data = json.loads(raw)
        return [Comment.from_dict(d) for d in data if isinstance(d, dict)]
    except Exception:
        return []


def _resolve_mentions_to_members(
    shop_domain: str, mentions: list[str],
) -> list[TeamMember]:
    """
    Match @handle tokens to actual team members. A handle matches if it
    equals the member's display_name (lowercased), the portion before @
    in their email, or a prefix of either.
    """
    if not mentions:
        return []
    members = list_members(shop_domain)
    matched: list[TeamMember] = []
    for mention in mentions:
        ml = mention.lower()
        for m in members:
            display_lower = (m.display_name or "").lower()
            email_local = m.email.split("@")[0].lower() if m.email else ""
            if display_lower == ml or email_local == ml:
                if m not in matched:
                    matched.append(m)
                break
            # soft prefix match for partial handles
            if (display_lower and display_lower.startswith(ml)) or (email_local and email_local.startswith(ml)):
                if m not in matched:
                    matched.append(m)
                break
    return matched


def _send_mention_notifications(
    shop_domain: str, comment: Comment, mentioned_members: list[TeamMember],
) -> None:
    """
    Fire one email per mentioned member via the email_orchestrator.
    Idempotent at the orchestrator level (send_immediate dedups recent
    same-recipient intents for this email_type).
    """
    if not mentioned_members:
        return
    try:
        from app.core.database import SessionLocal
        from app.services.email_orchestrator import EmailIntent, send_immediate
    except Exception as exc:
        log.debug("team: email orchestrator unavailable: %s", exc)
        return

    subject = f"💬 {comment.author_name} mentioned you on HedgeSpark"
    entity_label = comment.entity_type.replace("_", " ").title()
    html_body = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;padding:24px;">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 2px 8px rgba(0,0,0,0.04);">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.14em;color:#64748b;margin-bottom:6px;">
    HedgeSpark · Team mention
  </div>
  <h1 style="font-size:18px;margin:0 0 12px;color:#0f172a;line-height:1.4;">
    {comment.author_name} mentioned you on a {entity_label}.
  </h1>
  <div style="background:#f1f5f9;border-left:3px solid #d4893a;border-radius:4px;padding:14px 16px;margin:16px 0;font-size:14px;color:#334155;line-height:1.6;">
    {comment.body[:1000]}
  </div>
  <div style="font-size:12px;color:#64748b;">
    Open HedgeSpark to reply or view the full thread.
  </div>
  <div style="font-size:10px;color:#94a3b8;margin-top:20px;border-top:1px solid #e2e8f0;padding-top:14px;">
    Sent {comment.created_at[:16].replace('T', ' ')} · HedgeSpark
  </div>
</div>
</body></html>"""

    plain_body = (
        f"{comment.author_name} mentioned you on a {entity_label}.\n\n"
        f"  {comment.body[:1000]}\n\n"
        f"Open HedgeSpark to reply or view the full thread.\n"
    )

    db = SessionLocal()
    try:
        for member in mentioned_members:
            if not member.email:
                continue
            try:
                intent = EmailIntent(
                    shop_domain=shop_domain,
                    email_type="team_mention",
                    to_email=member.email,
                    subject=subject,
                    html=html_body,
                    plain_text=plain_body,
                    producer="team_comments",
                    context={
                        "comment_id": comment.id,
                        "entity_type": comment.entity_type,
                        "entity_id": comment.entity_id,
                        "mentioned_member_id": member.id,
                    },
                )
                send_immediate(db, intent)
            except Exception as exc:
                log.debug(
                    "team: mention email failed for %s: %s",
                    member.email, exc,
                )
    finally:
        db.close()


def add_comment(
    shop_domain: str, *, entity_type: str, entity_id: str,
    author_id: str, author_name: str, body: str,
) -> Comment | None:
    if not body.strip():
        raise ValueError("comment body cannot be empty")
    if len(body) > 2000:
        raise ValueError("comment body too long (max 2000 chars)")

    rc = _redis()
    if rc is None:
        return None

    mentions = extract_mentions(body)
    comment = Comment(
        id=str(uuid.uuid4())[:12],
        entity_type=entity_type,
        entity_id=str(entity_id),
        author_id=author_id,
        author_name=author_name,
        body=body.strip(),
        mentions=mentions,
        created_at=_now_iso(),
    )

    existing = list_comments(shop_domain, entity_type, entity_id)
    existing.append(comment)
    # Keep most recent N
    if len(existing) > _MAX_COMMENTS_PER_ENTITY:
        existing = existing[-_MAX_COMMENTS_PER_ENTITY:]

    try:
        rc.setex(
            _key_comments(shop_domain, entity_type, entity_id),
            _TTL_SECONDS,
            json.dumps([c.to_dict() for c in existing]),
        )
    except Exception as exc:
        log.warning("team: add_comment failed: %s", exc)
        return None

    # Fire mention notifications (best-effort — never fails the comment save).
    # Runs synchronously but the orchestrator is fast; if there's a slowdown
    # we can make this async in a worker later.
    try:
        mentioned = _resolve_mentions_to_members(shop_domain, mentions)
        if mentioned:
            _send_mention_notifications(shop_domain, comment, mentioned)
    except Exception as exc:
        log.debug("team: mention notification failed (non-fatal): %s", exc)

    return comment


def delete_comment(
    shop_domain: str, entity_type: str, entity_id: str, comment_id: str,
) -> bool:
    rc = _redis()
    if rc is None:
        return False
    existing = list_comments(shop_domain, entity_type, entity_id)
    kept = [c for c in existing if c.id != comment_id]
    if len(kept) == len(existing):
        return False
    try:
        if kept:
            rc.setex(
                _key_comments(shop_domain, entity_type, entity_id),
                _TTL_SECONDS,
                json.dumps([c.to_dict() for c in kept]),
            )
        else:
            rc.delete(_key_comments(shop_domain, entity_type, entity_id))
        return True
    except Exception:
        return False
