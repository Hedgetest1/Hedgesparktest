"""sentry_noise_filter.py — central detection of expected operational
noise that should be dropped from Sentry rather than counted as bugs.

Five noise classes filtered here:

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

4. **Synthetic load-harness shop TLS noise** (born 2026-05-19) —
   `scripts/load_test_harness.py` INSERTs up to 10 000 synthetic
   `_loadtest_NNNNN.myshopify.com` merchants with `install_status=
   'active'` for the duration of a 10k load run. While the run is in
   flight (before the `finally` teardown deletes them) EVERY active-
   merchant-iterating production worker (`webhook_health_task`,
   `shopify_client` product fetches, etc.) calls Shopify for each fake
   shop → the TLS handshake fails with `[SSL: CERTIFICATE_VERIFY_FAILED]
   ... Hostname mismatch` (the host has no valid cert for a
   `_loadtest_*` subdomain). Ground-truthed 2026-05-19: 26 such rows,
   recurrence_count up to 72, were the dominant driver tripping the
   capillary `sentry_incidents` probe YELLOW. Doubly-anchored: requires
   BOTH a synthetic `_loadtest_\\d+\\.myshopify\\.com` host AND a
   TLS-cert-verify-failure phrase. A real merchant's shop_domain never
   contains `_loadtest_` (Shopify never issues such subdomains; the
   harness itself refuses to run if real `_loadtest_` merchants exist;
   DB-proven 0/4 real merchants match) so a genuine merchant TLS error
   to a real `*.myshopify.com` host STILL surfaces. Makes the load rig
   self-cleaning at the Sentry layer — no per-run manual hygiene.

5. **By-design dashboard warming-503** (born 2026-05-19) — the
   `b28dc07` Redis-down defence-in-depth raises
   `HTTPException(503, "dashboard warming — retry shortly")` as a
   deterministic graceful-degradation response when a cold-build is
   shed under load / Redis blip. The client retries; the merchant
   never sees an error. Same honest tradeoff as classes 2-3: counting
   a purpose-built degradation response as a "bug" only buries the
   real signal under self-inflicted noise. A genuine warm-path
   regression's PRIMARY signal is the dedicated `/system/health`
   probe + the capillary `system_health` / pool-timeout dimensions
   (loud, immediate), NOT a 24h incident counter. Matched on the
   exact purpose-built phrase only — a generic `HTTPException: 500`
   or any other 503 does NOT match and still surfaces.

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


# Class 4: synthetic load-harness shop TLS noise (born 2026-05-19).
# Doubly-anchored — BOTH a synthetic `_loadtest_<digits>.myshopify.com`
# host AND a TLS-cert-verify-failure phrase must be present. `_loadtest_`
# is the `load_test_harness.py` default `_SHOP_PREFIX`; a real Shopify
# merchant domain never contains it (DB-proven 0/4) so a genuine
# merchant TLS error to a real `*.myshopify.com` host does NOT match.
# Suffix is `[a-z0-9_]+` not `\d+`: the harness default is
# `_loadtest_00019` but rigs use named cold-shops (`_loadtest_cold01`,
# the J1 worker-cycle rig). `_loadtest_` itself is the discriminator
# (DB-proven 0/4 real merchants); the alnum suffix just bounds it to a
# shop token. Still doubly-anchored with the TLS phrase below.
_LOADTEST_SHOP_RE = re.compile(r"_loadtest_[a-z0-9_]+\.myshopify\.com")
_TLS_CERT_FAIL_RE = re.compile(
    r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|Hostname mismatch",
    re.IGNORECASE,
)

# Class 5: by-design dashboard warming-503 (born 2026-05-19). The exact
# purpose-built `b28dc07` graceful-degradation phrase. Tolerant of
# em-dash / en-dash / hyphen because capture paths vary in how they
# normalize the `—`; case-insensitive. A generic HTTPException 500/503
# does NOT contain this phrase so it still surfaces.
_WARMING_503_RE = re.compile(
    r"dashboard warming\s*[—–-]\s*retry shortly",
    re.IGNORECASE,
)


def is_noise(message: str | None) -> bool:
    """Return True iff the message looks like expected operational noise
    (suitable for filtering before Sentry capture).

    Five noise classes covered:
      1. Dev-misconfig secret-missing 500s (regex-matched).
      2. Worker graceful-shutdown signal exceptions (matched against
         `_SIGNAL_SHUTDOWN_NOISE` via `_matches_shutdown_signal` — both
         bare-class and `Class: <msg>` forms).
      3. Backend/DB restart connection-drop OperationalError (matched
         by message SHAPE via `_DB_CONN_DROP_NOISE_RE` — NOT exception
         type, so a real SQL/schema-bug OperationalError still surfaces).
      4. Synthetic load-harness `_loadtest_*` shop TLS failures
         (doubly-anchored: synthetic-shop host AND cert-fail phrase —
         a real merchant TLS error still surfaces).
      5. By-design dashboard warming-503 (exact purpose-built phrase —
         a generic 500/503 still surfaces).

    Conservative by design: returns False on None/empty input or any
    string that doesn't match. Classes 1-2 are case-sensitive; classes
    3-5 are case-insensitive on fixed phrasings."""
    if not message or not isinstance(message, str):
        return False
    # Fast-path: signal-class shutdown exceptions (bare OR Class:msg form)
    if _matches_shutdown_signal(message):
        return True
    if _DB_CONN_DROP_NOISE_RE.search(message):
        return True
    # Class 4: BOTH anchors required (synthetic host + TLS cert failure).
    if _LOADTEST_SHOP_RE.search(message) and _TLS_CERT_FAIL_RE.search(message):
        return True
    if _WARMING_503_RE.search(message):
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
