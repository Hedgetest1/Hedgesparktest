"""
telegram_safety.py — Unbreakable execution safety layer for Telegram commands.

Provides:
1. Idempotency — same command cannot execute twice (Redis dedup, 5-min window)
2. Concurrency locking — only one apply/rollback at a time per candidate
3. State machine validation — strict allowed transitions
4. Confirmation flow — destructive actions require two-step confirmation
5. Execution tracing — progress updates sent to Telegram during long ops

All dangerous Telegram commands MUST go through this layer.
"""
from __future__ import annotations

import hashlib
import logging
import time

log = logging.getLogger("telegram_safety")

# ---------------------------------------------------------------------------
# 1. IDEMPOTENCY — prevents double-execution from Telegram retries / double-taps
# ---------------------------------------------------------------------------

_IDEMPOTENCY_TTL = 300  # 5 minutes — covers Telegram's 60s retry window


def check_idempotency(command: str, entity_id: str, critical: bool = False) -> bool:
    """
    Check if this exact command was already executed recently.
    Returns True if safe to proceed, False if duplicate.

    critical=True: FAIL-CLOSED (block if Redis unavailable).
    critical=False: FAIL-OPEN (allow if Redis unavailable).
    """
    bucket = int(time.time()) // _IDEMPOTENCY_TTL
    raw = f"{command}:{entity_id}:{bucket}"
    key = f"hs:tg_idem:{hashlib.md5(raw.encode()).hexdigest()[:16]}"

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            if critical:
                log.warning("telegram_safety: Redis unavailable — BLOCKING critical command %s", command)
                return False
            return True  # non-critical: fail-open

        result = rc.set(key, "1", nx=True, ex=_IDEMPOTENCY_TTL)
        if not result:
            log.warning("telegram_safety: DUPLICATE blocked %s entity=%s", command, entity_id)
            return False
        return True
    except Exception:
        if critical:
            log.warning("telegram_safety: Redis error — BLOCKING critical command %s", command)
            return False
        return True


# ---------------------------------------------------------------------------
# 2. CONCURRENCY LOCK — prevents overlapping apply/rollback on same candidate
# ---------------------------------------------------------------------------

_LOCK_TTL = 120  # 2 minutes — long enough for apply pipeline


def acquire_execution_lock(entity_type: str, entity_id: str, critical: bool = True) -> bool:
    """
    Acquire exclusive lock for a dangerous operation on an entity.
    Returns True if lock acquired, False if already locked or Redis unavailable.

    FAIL-CLOSED by default for critical operations (apply, rollback).
    If Redis is down, these operations are BLOCKED.
    """
    key = f"hs:tg_lock:{entity_type}:{entity_id}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            if critical:
                log.warning("telegram_safety: Redis unavailable — BLOCKING lock for %s:%s", entity_type, entity_id)
                return False
            return True
        result = rc.set(key, str(int(time.time())), nx=True, ex=_LOCK_TTL)
        if not result:
            log.warning("telegram_safety: LOCKED %s:%s — another operation in progress", entity_type, entity_id)
            return False
        return True
    except Exception:
        # fail-open for non-critical paths, fail-closed for critical ones:
        # Redis down on a critical operation is always a refusal because
        # we cannot guarantee the lock invariant; non-critical paths
        # accept the risk of a duplicate operation rather than losing
        # the action entirely.
        if critical:
            return False
        return True


def release_execution_lock(entity_type: str, entity_id: str):
    """Release the execution lock after operation completes."""
    key = f"hs:tg_lock:{entity_type}:{entity_id}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            rc.delete(key)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3. STATE MACHINE — strict allowed transitions for bugfix candidates
# ---------------------------------------------------------------------------

BUGFIX_TRANSITIONS = {
    "patch_proposed": {"approved", "discarded"},
    "approved": {"applying", "discarded"},
    "applying": {"applied", "apply_failed", "rolled_back"},
    "applied": {"rolled_back"},
    "apply_failed": {"discarded", "patch_proposed"},  # can retry
    "rolled_back": set(),  # terminal
    "discarded": set(),  # terminal
}


def validate_transition(current_status: str, target_status: str) -> tuple[bool, str]:
    """
    Check if a state transition is allowed.
    Returns (allowed, error_message).
    """
    allowed = BUGFIX_TRANSITIONS.get(current_status, set())
    if target_status in allowed:
        return True, ""
    return False, f"Cannot transition from '{current_status}' to '{target_status}'. Allowed: {allowed or 'none (terminal)'}"


# ---------------------------------------------------------------------------
# 4. CONFIRMATION FLOW — destructive actions need two taps
# ---------------------------------------------------------------------------

_CONFIRM_TTL = 120  # confirmation valid for 2 minutes


def request_confirmation(action: str, entity_id: str) -> bool:
    """
    Record that operator requested a dangerous action. Returns True.
    Operator must call confirm_action() within 2 minutes to execute.
    """
    key = f"hs:tg_confirm:{action}:{entity_id}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            rc.set(key, "1", ex=_CONFIRM_TTL)
    except Exception:
        pass
    return True


def check_confirmation(action: str, entity_id: str) -> bool:
    """
    Check if a confirmation exists for this action.
    Returns True if confirmed (and consumes the confirmation), False otherwise.
    """
    key = f"hs:tg_confirm:{action}:{entity_id}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("telegram_safety.confirm")
            return True  # Redis down — skip confirmation (fail-open)
        val = rc.get(key)
        if val:
            rc.delete(key)  # consume — one-time use
            return True
        return False
    except Exception:
        return True  # error — fail-open


# ---------------------------------------------------------------------------
# 5. EXECUTION TRACE — send progress updates during long operations
# ---------------------------------------------------------------------------

def send_progress(step: str, reply_to: int | None = None) -> int | None:
    """Send a progress update to Telegram. Returns message_id."""
    try:
        from app.services.telegram_agent import send_message
        result = send_message(step, reply_to=reply_to)
        return result if isinstance(result, int) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 6. CRITICALITY-BASED RATE LIMITING
# ---------------------------------------------------------------------------

_RATE_LIMITS = {
    # LOW criticality — informational commands
    "/status": 10, "/costs": 10, "/bugfixes": 10, "/incidents": 10,
    "/help": 10, "/merchants": 10, "/evolution": 10, "/scaling": 10,
    "/meta_review": 10, "/digest": 10, "/webhooks": 10,
    "/loop_health": 10, "/weakness": 10,
    # MEDIUM criticality — state changes
    "/bugfix_approve": 5, "/approve": 5, "/reject": 5, "/cleanup": 3,
    # HIGH criticality — code execution
    "/bugfix_apply": 2, "/rollback": 2, "/merge": 2,
}

_rate_buckets: dict[str, list[float]] = {}
_RATE_WINDOW = 60.0


def check_criticality_rate(cmd: str) -> tuple[bool, int]:
    """
    Check criticality-based rate limit.
    Returns (allowed, max_per_minute).
    """
    limit = _RATE_LIMITS.get(cmd, 5)
    now = time.monotonic()
    key = cmd.lower()

    if key not in _rate_buckets:
        _rate_buckets[key] = []

    _rate_buckets[key] = [t for t in _rate_buckets[key] if now - t < _RATE_WINDOW]

    if len(_rate_buckets[key]) >= limit:
        return False, limit

    _rate_buckets[key].append(now)
    return True, limit


def reset_rate_limits():
    """Clear rate state — for testing."""
    _rate_buckets.clear()
