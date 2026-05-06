"""Locks G7 — Sentry noise filter generalization (2026-05-06).

Before this fix the Sentry noise denylist was a string-match for
"OPS_API_KEY not configured". A new optional secret missing in dev
(DASHBOARD_API_KEY, RESEND_API_KEY, SLACK_WEBHOOK_URL,
TELEGRAM_WEBHOOK_SECRET, ANTHROPIC_API_KEY, ...) re-created the same
Sentry-noise problem under a different message string.

After: app.core.sentry_noise_filter.is_noise() regex-matches the
generic dev-misconfig pattern. Two consumers (sentry_init outbound +
sentry_triage inbound) share the same predicate so drift is impossible.

These tests pin:
    1. Each representative dev-misconfig variant IS classified noise
    2. Real exception messages are NOT classified noise (no false drop)
    3. Generic infra URL misconfig (DATABASE_URL) is NOT noise — those
       missing in prod IS a real bug worth surfacing
"""
from __future__ import annotations

from app.core.sentry_noise_filter import any_noise, is_noise


def test_ops_api_key_variants_are_noise():
    """Original case + verb variants the regex must cover."""
    assert is_noise("OPS_API_KEY not configured") is True
    assert is_noise("OPS_API_KEY not configured on server") is True
    assert is_noise("OPS_API_KEY not set") is True
    assert is_noise("OPS_API_KEY is not configured") is True
    assert is_noise("OPS_API_KEY missing") is True


def test_other_secret_class_env_vars_are_noise():
    """Generalization: any secret-class env var matching the suffix
    pattern must be classified as noise."""
    assert is_noise("DASHBOARD_API_KEY not set") is True
    assert is_noise("RESEND_API_KEY not configured") is True
    assert is_noise("SLACK_WEBHOOK_URL is not configured") is True
    assert is_noise("TELEGRAM_WEBHOOK_SECRET missing") is True
    assert is_noise("ANTHROPIC_API_KEY not present") is True


def test_real_exception_messages_are_not_noise():
    """Conservative by design — real bugs must NOT be filtered."""
    # Random Python exception
    assert is_noise("KeyError: 'shop_domain'") is False
    # Stack trace fragment
    assert is_noise(
        "AttributeError: 'NoneType' object has no attribute 'execute'"
    ) is False
    # SQL error
    assert is_noise(
        "psycopg2.errors.UniqueViolation: duplicate key value"
    ) is False


def test_infra_url_misconfig_is_not_noise():
    """DATABASE_URL/REDIS_URL missing IS a real bug. The regex
    deliberately requires a *secret-class* suffix
    (API_KEY/SECRET/TOKEN/WEBHOOK_URL/WEBHOOK_SECRET) so generic
    infrastructure URLs DON'T match."""
    assert is_noise("DATABASE_URL not configured") is False
    assert is_noise("REDIS_URL not set") is False
    # APP_URL is also infra, not a secret
    assert is_noise("APP_URL is not configured") is False


def test_empty_or_none_input_is_not_noise():
    assert is_noise(None) is False
    assert is_noise("") is False
    # Non-string defensive
    assert is_noise(42) is False  # type: ignore[arg-type]


def test_secret_in_middle_of_log_message_is_noise():
    """Real-world: messages often have a prefix/suffix around the
    canonical phrase (e.g. log timestamps, scope tags). The regex
    matches anywhere in the string, not anchored to start."""
    assert is_noise(
        "[INFO] 2026-05-06 ops_endpoint_handler: OPS_API_KEY not configured"
    ) is True
    assert is_noise(
        "Error: RESEND_API_KEY missing — email flows disabled"
    ) is True


def test_any_noise_helper_works_over_iterable():
    msgs = [
        "KeyError: 'foo'",
        None,
        "OPS_API_KEY not configured",
    ]
    assert any_noise(msgs) is True
    assert any_noise(["bug", "error", None]) is False


def test_lowercase_does_not_match():
    """Env var names are uppercase by convention; the regex is
    case-sensitive to avoid matching prose that mentions an api_key
    in lowercase (e.g. user-typed bug reports)."""
    assert is_noise("api_key not configured") is False
    assert is_noise("ops_api_key not configured") is False
