"""sentry_noise_filter.py — central detection of expected dev-misconfig
errors that should be dropped from Sentry rather than counted as bugs.

The original 2026-05-05 fix string-matched a single message
("OPS_API_KEY not configured"). Founder direttiva is 0 errors —
that is "zero for ALL classes of dev-misconfig 500", not "zero for
this one message". A new optional secret missing on a fresh dev
host (DASHBOARD_API_KEY, RESEND_API_KEY, SLACK_WEBHOOK_URL,
TELEGRAM_WEBHOOK_SECRET, ...) re-creates the same Sentry-noise
problem at the next env onboarding.

This module provides a single regex-based predicate `is_noise(message)`
that classifies messages of the form:

    <ENV_VAR>_API_KEY not configured
    <ENV_VAR>_SECRET not set
    <ENV_VAR>_TOKEN missing
    <ENV_VAR>_WEBHOOK_URL is not configured
    <ENV_VAR>_WEBHOOK_SECRET not present

The regex deliberately requires a *secret-class* suffix (API_KEY,
SECRET, TOKEN, WEBHOOK_URL, WEBHOOK_SECRET) so generic
infrastructure URLs (DATABASE_URL, REDIS_URL) DON'T match — those
missing in prod IS a real bug worth surfacing.

Two consumers:
- `app/core/sentry_init.py::_before_send` (outbound — drop at
  Sentry SDK boundary so the event never reaches Sentry).
- `app/services/sentry_triage.py::ingest_email` (inbound — drop
  at intake so sentry_incidents/regressions don't accumulate).

The function is small, deterministic, and pure — easy to test.
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


def is_noise(message: str | None) -> bool:
    """Return True iff the message looks like an expected dev-misconfig
    secret-missing 500 (suitable for filtering before Sentry capture).

    Conservative by design: returns False on None/empty input or any
    string that doesn't match the suffix-anchored regex. Matches are
    case-sensitive and require a secret-class env var name."""
    if not message or not isinstance(message, str):
        return False
    return bool(_NOISE_RE.search(message))


def any_noise(messages: Iterable[str | None]) -> bool:
    """Convenience: return True iff ANY of the messages is noise."""
    return any(is_noise(m) for m in messages)
