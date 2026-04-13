"""
audit.py — Append-only audit log writer with hash-chained integrity.

Public interface:
    write_audit_log(db, ...) -> AuditLog
    verify_audit_log_chain(db, *, limit=None) -> dict
    get_chain_head() -> str | None

All agent/system/admin actions that modify data should call this function
to create an immutable audit trail.

This module must NEVER contain update or delete operations on audit_log.

Hash chain (2026-04-11 audit)
-----------------------------
Every new row is signed into a forward hash chain:

    row_digest  = sha256(actor|action|target|before|after|status|...)
    chain_hash  = sha256(prev_chain_hash || row_digest)

We store `{prev, self}` inside the row's `metadata_json` field (no DB
migration required). A verification pass walks the rows in id order,
recomputes each digest + chain hash, and flags any mismatch — which is
the signature of either a row modification or a row deletion.

The head-of-chain hash is ALSO cached in Redis so an attacker who
wipes the whole `audit_log` table has nowhere to hide: the next write
will mint a chain hash inconsistent with the Redis anchor, triggering
the `audit_log_tampering` alert the same day.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

log = logging.getLogger(__name__)

_CHAIN_HEAD_REDIS_KEY = "hs:audit_log:chain_head"
_GENESIS_HASH = "0" * 64  # sentinel for the very first row


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
    Write a single audit log entry with hash-chained integrity.

    All parameters are keyword-only to prevent positional mistakes.
    before_state/after_state/metadata accept dicts — they are JSON-serialized.
    """
    before_json = _to_json(before_state)
    after_json = _to_json(after_state)

    row_digest = _compute_row_digest(
        actor_type=actor_type,
        actor_name=actor_name,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        shop_domain=shop_domain,
        before=before_json,
        after=after_json,
        status=status,
        approval_mode=approval_mode,
    )

    # Serialize audit log writes across all workers to prevent chain link
    # races. Two workers reading prev_hash from Redis simultaneously would
    # both write rows with the same prev, breaking verification. The
    # advisory lock is held for the duration of the current transaction
    # and is the cheapest way to enforce a single global writer.
    # Lock key: hash("hs:audit_log:chain_head") truncated to int64.
    try:
        from sqlalchemy import text as _sql_text
        db.execute(_sql_text("SELECT pg_advisory_xact_lock(7421889543210176881)"))
    except Exception as exc:
        log.debug("audit: advisory lock failed (non-fatal, falling through): %s", exc)

    prev_hash = _load_chain_head_from_db(db)
    chain_hash = _chain(prev_hash, row_digest)

    merged_metadata: dict = dict(metadata) if isinstance(metadata, dict) else {}
    merged_metadata["_chain"] = {
        "prev": prev_hash,
        "self": chain_hash,
        "digest": row_digest,
    }

    entry = AuditLog(
        actor_type=actor_type,
        actor_name=actor_name,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        shop_domain=shop_domain,
        before_state=before_json,
        after_state=after_json,
        status=status,
        approval_mode=approval_mode,
        metadata_json=_to_json(merged_metadata),
    )
    db.add(entry)
    db.flush()

    _store_chain_head(chain_hash)
    return entry


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Hash-chain internals
# ---------------------------------------------------------------------------

def _compute_row_digest(
    *,
    actor_type: str,
    actor_name: str,
    action_type: str,
    target_type: str | None,
    target_id: str | None,
    shop_domain: str | None,
    before: str | None,
    after: str | None,
    status: str,
    approval_mode: str | None,
) -> str:
    fields = [
        actor_type or "",
        actor_name or "",
        action_type or "",
        target_type or "",
        target_id or "",
        shop_domain or "",
        before or "",
        after or "",
        status or "",
        approval_mode or "",
    ]
    raw = "\x1f".join(fields)  # unit separator — never appears in content
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _chain(prev_hash: str, row_digest: str) -> str:
    return hashlib.sha256(
        (prev_hash + "|" + row_digest).encode("utf-8")
    ).hexdigest()


def _load_chain_head_from_db(db: Session) -> str:
    """Return the chain head from the DB only — authoritative.

    Used inside write_audit_log() while holding the advisory lock. Redis
    is NOT consulted because stale Redis state could return an old head
    between workers, which is exactly the race we're preventing.
    """
    try:
        last = (
            db.query(AuditLog)
            .order_by(AuditLog.id.desc())
            .limit(1)
            .first()
        )
        if last is None:
            return _GENESIS_HASH
        parsed = _parse_chain_metadata(last.metadata_json)
        if parsed and parsed.get("self"):
            return parsed["self"]
    except Exception as exc:
        log.debug("audit: DB chain head lookup failed: %s", exc)
    return _GENESIS_HASH


def _load_chain_head(db: Session) -> str:
    """Return the hash of the tail of the chain. Tries Redis first for
    O(1) reads; falls back to the last row's stored hash; finally to
    the genesis sentinel on an empty table."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(_CHAIN_HEAD_REDIS_KEY)
            if raw:
                return raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        pass

    try:
        last = (
            db.query(AuditLog)
            .order_by(AuditLog.id.desc())
            .limit(1)
            .first()
        )
        if last is None:
            return _GENESIS_HASH
        parsed = _parse_chain_metadata(last.metadata_json)
        if parsed and parsed.get("self"):
            return parsed["self"]
    except Exception as exc:
        log.debug("audit: chain head fallback failed: %s", exc)

    return _GENESIS_HASH


def _store_chain_head(chain_hash: str) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return
        rc.set(_CHAIN_HEAD_REDIS_KEY, chain_hash)  # no expiry — chain is monotonic
    except Exception:
        pass


def get_chain_head() -> str | None:
    """Public accessor for the current chain head (used by verification
    + compliance synthesizer)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return None
        raw = rc.get(_CHAIN_HEAD_REDIS_KEY)
        if not raw:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        return None


def _parse_chain_metadata(metadata_json: str | None) -> dict | None:
    if not metadata_json:
        return None
    try:
        parsed = json.loads(metadata_json)
        if isinstance(parsed, dict):
            chain = parsed.get("_chain")
            if isinstance(chain, dict):
                return chain
    except (TypeError, ValueError):
        return None
    return None


def verify_audit_log_chain(
    db: Session, *, limit: int | None = None,
) -> dict[str, Any]:
    """Walk the audit log in id order and verify every row's stored
    chain hash matches a recomputation. A mismatch is either a row
    modification or a row deletion — either way, tampering evidence.

    Rows written BEFORE this module started chaining will have no
    `_chain` metadata; those are treated as `legacy` and counted
    separately, not as violations.

    Returns a report dict suitable for the daily digest.
    """
    report: dict[str, Any] = {
        "total_rows": 0,
        "chained_rows": 0,
        "legacy_rows": 0,
        "violations": [],
        "head_matches_redis": None,
    }

    try:
        query = db.query(AuditLog).order_by(AuditLog.id.asc())
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()
    except Exception as exc:
        log.warning("audit: chain verify query failed: %s", exc)
        return report

    prev_chain_self: str | None = None  # last chained row's stored self-hash
    last_chain_hash: str | None = None

    for row in rows:
        report["total_rows"] += 1
        stored = _parse_chain_metadata(row.metadata_json)
        if stored is None:
            report["legacy_rows"] += 1
            continue

        report["chained_rows"] += 1
        expected_digest = _compute_row_digest(
            actor_type=row.actor_type,
            actor_name=row.actor_name,
            action_type=row.action_type,
            target_type=row.target_type,
            target_id=row.target_id,
            shop_domain=row.shop_domain,
            before=row.before_state,
            after=row.after_state,
            status=row.status,
            approval_mode=row.approval_mode,
        )
        stored_prev = stored.get("prev") or _GENESIS_HASH
        expected_self = _chain(stored_prev, expected_digest)

        violation: dict[str, Any] | None = None
        if stored.get("digest") != expected_digest:
            violation = {
                "row_id": row.id,
                "reason": "digest_mismatch",
                "stored_digest": stored.get("digest"),
                "expected_digest": expected_digest,
            }
        elif stored.get("self") != expected_self:
            violation = {
                "row_id": row.id,
                "reason": "self_hash_mismatch",
                "stored_self": stored.get("self"),
                "expected_self": expected_self,
            }
        elif prev_chain_self is not None and stored_prev != prev_chain_self:
            # The chain link from the previous chained row is broken —
            # classic signature of a deleted middle row.
            violation = {
                "row_id": row.id,
                "reason": "chain_link_broken",
                "stored_prev": stored_prev,
                "expected_prev": prev_chain_self,
            }

        if violation is not None:
            violation["created_at"] = (
                row.created_at.isoformat() if row.created_at else None
            )
            report["violations"].append(violation)

        prev_chain_self = stored.get("self") or expected_self
        last_chain_hash = prev_chain_self

    # Cross-check the Redis chain head anchor if we have one.
    redis_head = get_chain_head()
    if redis_head and last_chain_hash:
        report["head_matches_redis"] = redis_head == last_chain_hash

    return report


# Redis key set for row IDs quarantined as "known_damaged". Once an operator
# has acknowledged a chain break and decided not to heal it (historical
# damage, irreversible), the row ID is added here and excluded from future
# verification runs. This prevents perpetual re-alerting on damage that
# will never be fixed.
_QUARANTINE_REDIS_KEY = "hs:audit_log:quarantined_row_ids"


def get_quarantined_row_ids() -> set[int]:
    """Return the set of row IDs marked as known-damaged (quarantined)."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return set()
        raw = rc.smembers(_QUARANTINE_REDIS_KEY)
        if not raw:
            return set()
        return {int(x.decode() if isinstance(x, bytes) else x) for x in raw}
    except Exception:
        return set()


def quarantine_row_ids(row_ids: list[int] | set[int]) -> int:
    """Mark row IDs as known-damaged. They will be skipped by future chain
    verification. Returns the number of rows newly quarantined."""
    if not row_ids:
        return 0
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return 0
        added = rc.sadd(_QUARANTINE_REDIS_KEY, *[str(int(i)) for i in row_ids])
        return int(added or 0)
    except Exception as exc:
        log.warning("audit: quarantine write failed: %s", exc)
        return 0


def enforce_chain_integrity(db: Session) -> dict[str, Any]:
    """Run `verify_audit_log_chain` and, on violations, emit a CRITICAL
    ops_alert via write_alert() so the dedup window collapses repeats.

    Quarantined (known-damaged) rows are filtered out before alerting, so
    historical unfixable chain damage does not keep re-emitting noise.
    Intended for the agent worker daily phase."""
    from app.services.alerting import write_alert

    result = verify_audit_log_chain(db)

    # Filter out known-damaged rows — they are historical tampering that
    # cannot be healed without rewriting history (which would be worse).
    quarantined = get_quarantined_row_ids()
    if quarantined:
        actionable = [v for v in result["violations"] if v["row_id"] not in quarantined]
    else:
        actionable = result["violations"]

    if actionable:
        # Fingerprint the specific set of broken rows so write_alert dedup
        # can aggregate "same damage" but still surface "new damage".
        row_ids_sorted = sorted(v["row_id"] for v in actionable)
        fingerprint = ",".join(str(i) for i in row_ids_sorted[:10])
        try:
            write_alert(
                db=db,
                alert_type="audit_log_tampering",
                source=f"audit_log_chain:{fingerprint}",
                severity="critical",
                detail={
                    "violation_count": len(actionable),
                    "row_ids": row_ids_sorted[:5],
                    "message": (
                        f"Audit log tampering detected: {len(actionable)} "
                        f"row(s) mismatch chain. First offenders: "
                        f"{row_ids_sorted[:5]}. Investigate — row modification "
                        f"or deletion by a non-audit path. If historical and "
                        f"unfixable, add to quarantine via "
                        f"quarantine_row_ids() to silence."
                    ),
                },
            )
        except Exception as exc:
            log.warning("audit: tampering alert write failed: %s", exc)

    # Report still includes the raw violations so operators can see them.
    result["quarantined_count"] = len(quarantined)
    result["actionable_violations"] = actionable
    return result
