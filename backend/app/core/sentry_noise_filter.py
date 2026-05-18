"""sentry_noise_filter.py — central detection of expected operational
noise that should be dropped from Sentry rather than counted as bugs.

Three noise classes filtered here:

1. **Dev-misconfig secret-missing 500s** — pre-2026-05-05 fix
   string-matched a single message ("OPS_API_KEY not configured").
   Generalized via regex so ANY `<ENV_VAR>_API_KEY/_SECRET/_TOKEN/_WEBHOOK_URL/
   _WEBHOOK_SECRET not configured/set/present/missing` matches. New
   optional secrets missing on a fresh dev host don't re-create the
   noise problem.

2. **Worker graceful-shutdown signal exceptions** (born 2026-05-13) —
   `KeyboardInterrupt` / `SystemExit` / `asyncio.CancelledError`
   raised at top of worker main loops when PM2 reload sends SIGINT
   for an auto-deploy. Each session with N commits triggers ~N×8
   worker reloads → ~N×8 KeyboardInterrupt events captured by the
   SDK and stored as `sentry_incidents` rows. Pure operational
   noise, not bugs — a graceful shutdown is the documented
   behavior. The `recurrence_count` on these incidents previously
   crossed the capillary scope probe threshold (10 in 24h) without
   any underlying regression.

3. **Backend/DB restart connection-drop OperationalError** (born
   2026-05-18) — when PM2 restarts `wishspark-backend` (every
   auto-deploy, N times per multi-commit session) or PgBouncer is
   bounced, in-flight pooled PG connections are killed mid-query and
   psycopg2/SQLAlchemy raise the canonical libpq message
   `OperationalError: (psycopg2.OperationalError) server closed the
   connection unexpectedly` (also `terminating connection due to
   administrator command`, `SSL connection has been closed
   unexpectedly`, `connection already closed`). Ground-truthed: ~20
   such incidents accumulated over 3 days (recurrence_count up to
   17), the dominant driver tripping the capillary `sentry_incidents`
   probe RED every session. This is the exact analogue of class 2 —
   a documented, expected consequence of our own deploy restarts,
   not a code bug. The connection is transparently re-established by
   the pool (`pool_recycle`/`pool_pre_ping`); the request that hit
   the drop failed transiently and is retried by the caller.

   **Honest tradeoff (no theater):** the same libpq string is also
   emitted by a *real* sustained DB outage. We accept filtering it
   from the `sentry_incidents` bug counter because a true outage's
   PRIMARY, purpose-built signal is the dedicated `/system/health`
   DB-subsystem probe + the capillary `system_health` dimension
   (loud, immediate, not a 24h-accumulation counter) — counting
   deploy-restart drops as "bugs" only masks that real signal under
   self-inflicted noise. Identical rationale to "a graceful shutdown
   is documented behavior" for class 2. Matched by MESSAGE SHAPE
   only (not exception type) — a generic `OperationalError` from a
   SQL/schema bug (`column ... does not exist`, `deadlock detected`,
   `duplicate key value`) does NOT match and still surfaces.

Two consumers:
- `app/core/sentry_init.py::_before_send` (outbound — drop at
  Sentry SDK boundary so the event never reaches Sentry). Also
  `ignore_errors=[KeyboardInterrupt, SystemExit]` on init for
  defense-in-depth at the SDK layer.
- `app/services/sentry_triage.py::ingest_email` (inbound — drop
  at intake so sentry_incidents/regressions don't accumulate).

The functions are small, deterministic, and pure — easy to test.
"""
from __future__ import annotations

import re
from typing import Iterable

# Single source-of-truth regex. Anchored, case-sensitive, requires
# secret-class suffix. Tolerant on the verb ("not configured" /
# "not set" / "not present" / "missing" / "is not <X>").
_NOISE_RE = re.compile(
    r"\b"
    r"[A-Z][A-Z0-9_]*"
    r"(?:_API_KEY|_SECRET|_TOKEN|_WEBHOOK_URL|_WEBHOOK_SECRET)"
    r"\b"
    r"\s+"
    r"(?:"
    r"not\s+(?:configured|set|present)"
    r"|"
    r"is\s+not\s+(?:configured|set|present)"
    r"|"
    r"missing"
    r")"
)

# Signal-class exception types that mean "graceful shutdown", NEVER a bug.
# Matched exactly (full string equality) on the exception type/title so
# they never collide with merchant-class strings. Born 2026-05-13 after
# 11 PM2-reload KeyboardInterrupts pushed sentry_incidents probe to RED
# during a 35-commit deploy storm.
_SIGNAL_SHUTDOWN_NOISE = frozenset({
    "KeyboardInterrupt",
    "SystemExit",
    "asyncio.CancelledError",
    "CancelledError",  # asyncio.CancelledError can stringify either way
})


def _matches_shutdown_signal(text: str) -> bool:
    """Match a Sentry-payload string against the signal-class noise set.

    Two formats covered (Agent-review finding 2026-05-13):
      - Bare class name: `"KeyboardInterrupt"` (how locally-captured
        SDK events stringify the exception when the exception has no
        message attached — e.g. `raise KeyboardInterrupt()`).
      - Class-name-colon-message form: `"KeyboardInterrupt: ..."` (how
        Sentry's webhook payload formats `issue.title` when the
        exception carries a message). Prefix-match on the class name
        followed by `:` (with optional whitespace) is the canonical
        Sentry idiom.

    The bare-class-name match is exact (no substring) to avoid false
    positives like `"RuntimeError: caught KeyboardInterrupt"`.
    """
    stripped = text.strip()
    if stripped in _SIGNAL_SHUTDOWN_NOISE:
        return True
    for cls in _SIGNAL_SHUTDOWN_NOISE:
        if stripped.startswith(cls + ":") or stripped.startswith(cls + " :"):
            return True
    return False


# Class 3: backend/DB restart connection-drop signatures (born
# 2026-05-18). High-precision libpq/psycopg2 phrasings that mean
# "the connection was alive and got dropped" — emitted when PM2
# restarts the backend or PgBouncer/PG is bounced (every auto-deploy).
# Each phrase is unambiguously a transient connection loss, NEVER a
# code-logic bug. Case-insensitive: libpq emits stable lowercase but
# capture paths vary; the phrases are specific enough that they do not
# occur in legitimate SQL/schema error messages (pinned by
# test_db_restart_*: a real OperationalError from a bad column /
# deadlock / duplicate-key does NOT match).
_DB_CONN_DROP_NOISE_RE = re.compile(
    r"server closed the connection unexpectedly"
    r"|terminating connection due to administrator command"
    r"|SSL connection has been closed unexpectedly"
    r"|connection already closed"
    r"|the connection is closed",
    re.IGNORECASE,
)


def is_noise(message: str | None) -> bool:
    """Return True iff the message looks like expected operational noise
    (suitable for filtering before Sentry capture).

    Three noise classes covered:
      1. Dev-misconfig secret-missing 500s (regex-matched).
      2. Worker graceful-shutdown signal exceptions (matched against
         `_SIGNAL_SHUTDOWN_NOISE` via `_matches_shutdown_signal` — both
         bare-class and `Class: <msg>` forms).
      3. Backend/DB restart connection-drop OperationalError (matched
         by message SHAPE via `_DB_CONN_DROP_NOISE_RE` — NOT exception
         type, so a real SQL/schema-bug OperationalError still surfaces).

    Conservative by design: returns False on None/empty input or any
    string that doesn't match. Classes 1-2 are case-sensitive; class 3
    is case-insensitive on fixed libpq phrasings."""
    if not message or not isinstance(message, str):
        return False
    # Fast-path: signal-class shutdown exceptions (bare OR Class:msg form)
    if _matches_shutdown_signal(message):
        return True
    if _DB_CONN_DROP_NOISE_RE.search(message):
        return True
    return bool(_NOISE_RE.search(message))


def any_noise(messages: Iterable[str | None]) -> bool:
    """Convenience: return True iff ANY of the messages is noise."""
    return any(is_noise(m) for m in messages)


def is_shutdown_signal_type(error_type: str | None) -> bool:
    """Inbound triage helper — checks ONLY the signal-class noise.
    Used by `sentry_triage.ingest_webhook` and `ingest_email` on the
    parsed type/title field.

    Accepts both formats (bare class name OR `Class: <msg>` colon-form)
    because Sentry's `issue.title` payload carries the latter when the
    exception has an attached message, and the bare form when it
    doesn't. Pre-2026-05-13 exact-match-only form silently let
    colon-formatted titles through."""
    if not error_type or not isinstance(error_type, str):
        return False
    return _matches_shutdown_signal(error_type)
