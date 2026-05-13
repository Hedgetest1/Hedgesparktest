"""sentry_noise_filter.py — central detection of expected operational
noise that should be dropped from Sentry rather than counted as bugs.

Two noise classes filtered here:

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


def is_noise(message: str | None) -> bool:
    """Return True iff the message looks like expected operational noise
    (suitable for filtering before Sentry capture).

    Two noise classes covered:
      1. Dev-misconfig secret-missing 500s (regex-matched).
      2. Worker graceful-shutdown signal exceptions (matched against
         `_SIGNAL_SHUTDOWN_NOISE` via `_matches_shutdown_signal` — both
         bare-class and `Class: <msg>` forms).

    Conservative by design: returns False on None/empty input or any
    string that doesn't match. Matches are case-sensitive."""
    if not message or not isinstance(message, str):
        return False
    # Fast-path: signal-class shutdown exceptions (bare OR Class:msg form)
    if _matches_shutdown_signal(message):
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
