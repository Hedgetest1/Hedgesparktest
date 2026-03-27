"""
audit.py — Append-only audit log writer.

Public interface:
    write_audit_log(db, ...) -> AuditLog

All agent/system/admin actions that modify data should call this function
to create an immutable audit trail.

This module must NEVER contain update or delete operations on audit_log.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

log = logging.getLogger(__name__)


def write_audit_log(
    db: Session,
    *,
    actor_type: str,
    actor_name: str,
    action_type: str,
    target_type: str | None = None,
    target_id: str | None = None,
    shop_domain: str | None = None,
    before_state: Any = None,
    after_state: Any = None,
    status: str = "completed",
    approval_mode: str | None = None,
    metadata: dict | None = None,
) -> AuditLog:
    """
    Write a single audit log entry.  Returns the created row.

    All parameters are keyword-only to prevent positional mistakes.
    before_state/after_state/metadata accept dicts — they are JSON-serialized.
    """
    entry = AuditLog(
        actor_type=actor_type,
        actor_name=actor_name,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        shop_domain=shop_domain,
        before_state=_to_json(before_state),
        after_state=_to_json(after_state),
        status=status,
        approval_mode=approval_mode,
        metadata_json=_to_json(metadata),
    )
    db.add(entry)
    db.flush()
    return entry


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
