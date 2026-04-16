"""
file_lock.py — Distributed file-level lock to prevent concurrent agent edits.

Uses Redis SET NX with TTL (same pattern as repair_claim.py).
Falls back to in-process dict when Redis is unavailable.

Design:
    - Lock key: hs:filelock:{normalized_path}
    - TTL: 300 seconds (5 minutes) — no single patch operation takes longer
    - Owner: agent identifier (e.g., "bugfix_pipeline", "evolution_converter")
    - Prevents two agents from editing the same file simultaneously

Public interface:
    try_lock_files(files, owner) -> FileLockResult
    release_file_locks(files, owner) -> None
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("file_lock")

_LOCK_TTL_SECONDS = 300  # 5 minutes

# In-process fallback when Redis is unavailable
_fallback_locks: dict[str, tuple[str, float]] = {}  # key → (owner, timestamp)


def _normalize_path(path: str) -> str:
    """Normalize a file path for consistent lock keys."""
    normalized = path.lstrip("/")
    for prefix in ("opt/wishspark/backend/", "opt/wishspark/", "backend/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized


def _lock_key(path: str) -> str:
    """Generate Redis key for a file lock."""
    return f"hs:filelock:{_normalize_path(path)}"


@dataclass
class FileLockResult:
    """Result of attempting to lock a set of files."""
    acquired: bool
    locked_files: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)  # [{file, held_by}]


def try_lock_files(files: list[str], owner: str) -> FileLockResult:
    """
    Attempt to acquire locks on all files atomically.

    If ANY file is already locked by a different owner, no locks are acquired
    (all-or-nothing semantics to prevent partial lock deadlocks).

    Args:
        files: List of file paths to lock
        owner: Identifier of the agent requesting locks (e.g., "bugfix_pipeline")

    Returns:
        FileLockResult with acquisition status and any conflicts
    """
    if not files:
        return FileLockResult(acquired=True)

    # Deduplicate using normalized paths
    seen = set()
    unique_files = []
    for f in files:
        norm = _normalize_path(f)
        if norm not in seen:
            seen.add(norm)
            unique_files.append(f)

    # Try atomic multi-file lock via Redis Lua (eliminates TOCTTOU race)
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            result = _atomic_acquire_all(rc, unique_files, owner)
            if result is not None:
                return result
    except Exception as exc:
        log.warning("file_lock: Redis atomic lock failed, falling back: %s", exc)

    # Fallback: two-phase check-then-acquire (in-process only)
    conflicts = []
    for f in unique_files:
        held_by = _check_lock(f)
        if held_by and held_by != owner:
            conflicts.append({"file": f, "held_by": held_by})

    if conflicts:
        log.info(
            "file_lock: DENIED owner=%s — %d conflicts: %s",
            owner,
            len(conflicts),
            ", ".join(c["file"] for c in conflicts[:3]),
        )
        return FileLockResult(acquired=False, conflicts=conflicts)

    acquired = []
    for f in unique_files:
        if _acquire_lock(f, owner):
            acquired.append(f)
        else:
            for af in acquired:
                _release_lock(af, owner)
            log.warning("file_lock: race condition — releasing %d locks for owner=%s", len(acquired), owner)
            return FileLockResult(
                acquired=False,
                conflicts=[{"file": f, "held_by": "unknown (race)"}],
            )

    log.info("file_lock: ACQUIRED owner=%s files=%d", owner, len(acquired))
    return FileLockResult(acquired=True, locked_files=acquired)


def release_file_locks(files: list[str], owner: str) -> None:
    """
    Release locks on a set of files.
    Only releases if the lock is held by the specified owner (no stealing).
    """
    released = 0
    for f in files:
        if _release_lock(f, owner):
            released += 1
    if released:
        log.info("file_lock: RELEASED owner=%s files=%d", owner, released)


def _atomic_acquire_all(rc, files: list[str], owner: str) -> FileLockResult | None:
    """
    Atomically check and acquire all file locks using a Redis Lua script.
    Returns FileLockResult on success/conflict, or None if Lua execution fails.
    """
    lua = """
    local owner = ARGV[1]
    local ttl = tonumber(ARGV[2])
    local n = #KEYS

    -- Phase 1: check all keys for conflicts
    local conflicts = {}
    for i = 1, n do
        local held = redis.call("GET", KEYS[i])
        if held and held ~= owner then
            conflicts[#conflicts + 1] = KEYS[i] .. "|" .. held
        end
    end

    if #conflicts > 0 then
        return conflicts
    end

    -- Phase 2: acquire all (atomic — no interleaving possible)
    for i = 1, n do
        local ok = redis.call("SET", KEYS[i], owner, "NX", "EX", ttl)
        if not ok then
            -- Re-entrant: check if we already own it
            local held = redis.call("GET", KEYS[i])
            if held == owner then
                redis.call("EXPIRE", KEYS[i], ttl)
            else
                -- Unexpected conflict inside atomic block — release all acquired
                for j = 1, i - 1 do
                    local jval = redis.call("GET", KEYS[j])
                    if jval == owner then
                        redis.call("DEL", KEYS[j])
                    end
                end
                return {KEYS[i] .. "|" .. (held or "unknown")}
            end
        end
    end

    return {}
    """
    keys = [_lock_key(f) for f in files]
    try:
        result = rc.eval(lua, len(keys), *keys, owner, str(_LOCK_TTL_SECONDS))
    except Exception as exc:
        log.warning("file_lock: _atomic_acquire_all failed: %s", exc)
        return None

    if not result:
        # Empty list = success
        log.info("file_lock: ACQUIRED (atomic) owner=%s files=%d", owner, len(files))
        return FileLockResult(acquired=True, locked_files=list(files))

    # Non-empty = conflicts
    conflicts = []
    for item in result:
        item_str = item.decode() if isinstance(item, bytes) else str(item)
        parts = item_str.split("|", 1)
        file_path = parts[0].replace("hs:filelock:", "", 1)
        held_by = parts[1] if len(parts) > 1 else "unknown"
        conflicts.append({"file": file_path, "held_by": held_by})

    log.info(
        "file_lock: DENIED (atomic) owner=%s — %d conflicts: %s",
        owner, len(conflicts),
        ", ".join(c["file"] for c in conflicts[:3]),
    )
    return FileLockResult(acquired=False, conflicts=conflicts)


def _check_lock(path: str) -> str | None:
    """Check if a file is locked. Returns owner name or None."""
    key = _lock_key(path)

    # Try Redis
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            val = rc.get(key)
            if val:
                return val.decode() if isinstance(val, bytes) else str(val)
            return None
    except Exception as exc:
        log.warning("file_lock: Redis read failed for %s, falling back to in-process: %s", path, exc)

    # Fallback
    entry = _fallback_locks.get(key)
    if entry is None:
        return None
    owner, ts = entry
    if (time.monotonic() - ts) >= _LOCK_TTL_SECONDS:
        del _fallback_locks[key]
        return None
    return owner


def _acquire_lock(path: str, owner: str) -> bool:
    """Acquire a single file lock. Returns True if acquired (or already held by same owner)."""
    key = _lock_key(path)

    # Try Redis
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            acquired = rc.set(key, owner, nx=True, ex=_LOCK_TTL_SECONDS)
            if acquired:
                return True
            # NX failed — check if we already hold it (re-entrant)
            existing = rc.get(key)
            if existing:
                existing_str = existing.decode() if isinstance(existing, bytes) else str(existing)
                if existing_str == owner:
                    # Refresh TTL on our existing lock
                    rc.expire(key, _LOCK_TTL_SECONDS)
                    return True
            return False
    except Exception as exc:
        log.warning("file_lock: redis error for %s: %s", key, exc)

    # Fallback
    now = time.monotonic()
    entry = _fallback_locks.get(key)
    if entry is not None:
        existing_owner, ts = entry
        if (now - ts) < _LOCK_TTL_SECONDS and existing_owner != owner:
            return False
    _fallback_locks[key] = (owner, now)
    return True


def _release_lock(path: str, owner: str) -> bool:
    """Release a single file lock. Only releases if owned by the caller."""
    key = _lock_key(path)

    # Try Redis — use Lua script for atomic check-and-delete
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            # Atomic: only delete if value matches owner
            lua = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            result = rc.eval(lua, 1, key, owner)
            return bool(result)
    except Exception as exc:
        log.warning("file_lock: _release_lock failed: %s", exc)

    # Fallback
    entry = _fallback_locks.get(key)
    if entry and entry[0] == owner:
        del _fallback_locks[key]
        return True
    return False


def list_active_locks() -> list[dict]:
    """
    List all currently held file locks. Operator visibility only.
    Returns list of {"file": str, "owner": str, "ttl_remaining": int}.
    """
    locks = []

    # Try Redis first
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            for key in rc.scan_iter("hs:filelock:*"):
                key_str = key.decode() if isinstance(key, bytes) else str(key)
                file_path = key_str.replace("hs:filelock:", "", 1)
                owner_val = rc.get(key)
                ttl = rc.ttl(key)
                if owner_val:
                    locks.append({
                        "file": file_path,
                        "owner": owner_val.decode() if isinstance(owner_val, bytes) else str(owner_val),
                        "ttl_remaining": max(ttl, 0) if ttl and ttl > 0 else 0,
                        "source": "redis",
                    })
            return locks
    except Exception as exc:
        log.warning("file_lock: list_active_locks failed: %s", exc)

    # Fallback: in-process locks
    now = time.monotonic()
    for key, (owner, ts) in list(_fallback_locks.items()):
        elapsed = now - ts
        if elapsed >= _LOCK_TTL_SECONDS:
            continue  # expired
        file_path = key.replace("hs:filelock:", "", 1)
        locks.append({
            "file": file_path,
            "owner": owner,
            "ttl_remaining": int(_LOCK_TTL_SECONDS - elapsed),
            "source": "fallback",
        })
    return locks


def _clear_all_locks() -> None:
    """For testing only. Clears both fallback and Redis locks."""
    _fallback_locks.clear()
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            for key in rc.scan_iter("hs:filelock:*"):
                rc.delete(key)
    except Exception as exc:
        log.warning("file_lock: _clear_all_locks failed: %s", exc)
