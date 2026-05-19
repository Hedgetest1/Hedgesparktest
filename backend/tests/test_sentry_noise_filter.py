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


# ---------------------------------------------------------------------------
# Signal-class shutdown noise (born 2026-05-13)
# ---------------------------------------------------------------------------

from app.core.sentry_noise_filter import is_shutdown_signal_type


class TestShutdownSignalNoise:
    """11 KeyboardInterrupt incidents pushed the capillary scope probe
    to RED during a 35-commit deploy storm 2026-05-13 — every PM2 reload
    sends SIGINT to workers, raising KeyboardInterrupt at the top of
    `while True: time.sleep(...)` main loops. These are graceful
    shutdowns, NEVER bugs."""

    def test_keyboard_interrupt_is_noise_via_is_noise(self):
        # ingest_email path uses composite_text (subject + body)
        assert is_noise("KeyboardInterrupt") is True
        assert is_noise("KeyboardInterrupt\n") is True
        # With trailing body content stripped to bare title
        assert is_noise("  KeyboardInterrupt  ") is True

    def test_system_exit_is_noise(self):
        assert is_noise("SystemExit") is True
        assert is_noise("SystemExit\n") is True

    def test_asyncio_cancelled_error_is_noise(self):
        # asyncio.CancelledError can stringify either way depending
        # on capture path — both variants must match.
        assert is_noise("asyncio.CancelledError") is True
        assert is_noise("CancelledError") is True

    def test_colon_suffix_form_is_noise(self):
        # AGENT-REVIEW FINDING 2026-05-13: Sentry's `issue.title`
        # carries `"KeyboardInterrupt: <message>"` when the exception
        # has an attached message. Pre-fix exact-match-only let these
        # through silently. Now both forms match.
        assert is_noise("KeyboardInterrupt: signal received") is True
        assert is_noise("SystemExit: shutdown requested") is True
        assert is_noise("asyncio.CancelledError: task cancelled") is True
        assert is_noise("CancelledError: ") is True

    def test_signal_noise_only_matches_exact_or_prefix_with_colon(self):
        # Substring "KeyboardInterrupt" INSIDE a real exception's
        # message MUST NOT match — only bare type-name OR `Class:`
        # prefix is noise. A `RuntimeError: caught KeyboardInterrupt`
        # is a real bug (the RuntimeError, not the KI inside).
        assert is_noise(
            "RuntimeError: caught KeyboardInterrupt during cleanup"
        ) is False
        # Real merchant-class message that mentions exit
        assert is_noise("SystemExit code 1 from invalid config") is False
        # Class name appearing mid-line WITHOUT colon-suffix MUST NOT match
        assert is_noise("Worker received KeyboardInterrupt at 0x7f") is False

    def test_shutdown_signal_type_helper(self):
        # Inbound triage helper — checks the bare error_type field
        # parsed by sentry_triage.parse_sentry_webhook.
        assert is_shutdown_signal_type("KeyboardInterrupt") is True
        assert is_shutdown_signal_type("SystemExit") is True
        assert is_shutdown_signal_type("asyncio.CancelledError") is True
        assert is_shutdown_signal_type("CancelledError") is True
        # Also matches colon-suffix form (Sentry issue.title format)
        assert is_shutdown_signal_type("KeyboardInterrupt: shutdown") is True
        # Real exception types MUST NOT match
        assert is_shutdown_signal_type("KeyError") is False
        assert is_shutdown_signal_type("RuntimeError") is False
        assert is_shutdown_signal_type("IntegrityError") is False
        # None/empty defensive
        assert is_shutdown_signal_type(None) is False
        assert is_shutdown_signal_type("") is False
        # Whitespace tolerance
        assert is_shutdown_signal_type("  KeyboardInterrupt  ") is True


class TestSentryInitIgnoreErrors:
    """Locks the SDK-init `ignore_errors=[KeyboardInterrupt, SystemExit]`
    config — defense-in-depth at the SDK boundary so signal-class
    exceptions never even reach the network. Born 2026-05-13."""

    def test_sentry_init_passes_ignore_errors(self, monkeypatch):
        captured = {}

        def _fake_init(**kwargs):
            captured.update(kwargs)

        import sentry_sdk
        monkeypatch.setattr(sentry_sdk, "init", _fake_init)
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **kw: None)
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.example/1")
        # init_sentry returns False when APP_ENV=test (test-env gate to
        # prevent test runs from spamming the production Sentry project).
        # Override to "production" so the real init call path runs.
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
        # Force low sample rates to keep the test cheap
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")
        monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0")

        # Reset module init state so init_sentry runs the call path
        import app.core.sentry_init as si
        si._enabled = False
        si._initialized_for = None
        try:
            si.init_sentry(component="backend")
        finally:
            # Reset state so other tests aren't affected
            si._enabled = False
            si._initialized_for = None

        assert "ignore_errors" in captured, (
            "sentry_sdk.init must pass ignore_errors= to drop signal-class "
            "shutdown exceptions at the SDK boundary"
        )
        assert KeyboardInterrupt in captured["ignore_errors"]
        assert SystemExit in captured["ignore_errors"]


# ---------------------------------------------------------------------------
# Class 3 — backend/DB restart connection-drop noise (born 2026-05-18)
# ---------------------------------------------------------------------------


class TestDbRestartConnectionDropNoise:
    """Ground-truthed 2026-05-18: ~20 `OperationalError: (psycopg2.
    OperationalError) server closed the connection unexpectedly`
    incidents accumulated over 3 days (recurrence_count up to 17),
    the dominant driver tripping the capillary `sentry_incidents`
    probe RED every session. Caused by PM2 restarting the backend on
    every auto-deploy (N times per multi-commit session) — in-flight
    pooled conns killed mid-query. The exact analogue of the
    KeyboardInterrupt class: a documented consequence of our own
    deploy restarts, not a code bug."""

    def test_exact_ground_truthed_string_is_noise(self):
        # The literal string read from sentry_incidents.raw_subject.
        assert is_noise(
            "OperationalError: (psycopg2.OperationalError) "
            "server closed the connection unexpectedly"
        ) is True

    def test_all_restart_signatures_are_noise(self):
        for msg in (
            "server closed the connection unexpectedly",
            "psycopg2.OperationalError: terminating connection due to "
            "administrator command",
            "OperationalError: SSL connection has been closed unexpectedly",
            "psycopg2.InterfaceError: connection already closed",
            "sqlalchemy.exc.OperationalError: the connection is closed",
        ):
            assert is_noise(msg) is True, f"should be noise: {msg!r}"

    def test_inbound_composite_subject_body_form_is_noise(self):
        # sentry_triage builds composite_text = f"{subject}\n{body}";
        # the extended is_noise must catch it there too (single SoT
        # → both Sentry layers covered, no separate wiring).
        composite = (
            "OperationalError: (psycopg2.OperationalError) server closed "
            "the connection unexpectedly\n"
            "  File \"app/api/dashboard.py\", line 412, in overview\n"
            "    result = db.execute(stmt)\n"
        )
        assert is_noise(composite) is True

    def test_case_insensitive_on_fixed_libpq_phrasing(self):
        assert is_noise(
            "Server Closed The Connection Unexpectedly"
        ) is True

    def test_real_operational_errors_are_NOT_noise(self):
        """NON-VACUITY / no-false-drop — the whole point. A real
        OperationalError from a SQL/schema/logic bug must STILL
        surface as an incident. Matched by message shape, not by the
        `OperationalError` type, precisely so these are not masked."""
        for real_bug in (
            'OperationalError: (psycopg2.errors.UndefinedColumn) '
            'column "foo" does not exist',
            'OperationalError: (psycopg2.errors.UndefinedTable) '
            'relation "bar" does not exist',
            "OperationalError: (psycopg2.errors.DeadlockDetected) "
            "deadlock detected",
            "psycopg2.errors.UniqueViolation: duplicate key value "
            "violates unique constraint",
            "OperationalError: could not serialize access due to "
            "concurrent update",
        ):
            assert is_noise(real_bug) is False, (
                f"real DB bug MUST surface, not be filtered: {real_bug!r}"
            )

    def test_does_not_swallow_unrelated_connection_prose(self):
        # A merchant-facing message that merely mentions "connection"
        # without a drop signature must not be filtered.
        assert is_noise(
            " shopify_client: connection pool reached max size"
        ) is False
        assert is_noise(
            "Klaviyo connection verified for shop x.myshopify.com"
        ) is False


# ---------------------------------------------------------------------------
# Class 4 — synthetic load-harness shop TLS noise (born 2026-05-19)
# ---------------------------------------------------------------------------


class TestLoadtestShopTlsNoise:
    """Ground-truthed 2026-05-19: 26 incident rows (recurrence up to 72)
    of `shopify_client: network error ... shop=_loadtest_NNNNN.myshopify
    .com: [SSL: CERTIFICATE_VERIFY_FAILED] ... Hostname mismatch` — the
    dominant driver tripping the capillary `sentry_incidents` probe
    YELLOW. Generated DURING a load_test_harness run when production
    workers race the synthetic merchants before teardown. Doubly-
    anchored: synthetic-shop host AND TLS-cert-fail phrase."""

    def test_exact_ground_truthed_shopify_client_string_is_noise(self):
        assert is_noise(
            "shopify_client: network error GET products.json "
            "shop=_loadtest_00019.myshopify.com: [SSL: "
            "CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "Hostname mismatch, certificate is not valid for "
            "'_loadtest_00019.myshopify.com'. (_ssl.c:1000) — exhausted 3"
        ) is True

    def test_exact_ground_truthed_webhook_health_string_is_noise(self):
        assert is_noise(
            "webhook_health: API error shop=_loadtest_00197.myshopify.com: "
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "Hostname mismatch, certificate is not valid for "
            "'_loadtest_00197.myshopify.com'. (_ssl.c:1000)"
        ) is True

    def test_real_merchant_tls_failure_is_NOT_noise(self):
        """NON-VACUITY / no-false-drop. A genuine TLS error to a REAL
        merchant shop must STILL surface — only the `_loadtest_`
        synthetic host is filtered. This is the safety anchor: a real
        Shopify cert/TLS regression is a real incident."""
        assert is_noise(
            "shopify_client: network error GET products.json "
            "shop=realmerchant.myshopify.com: [SSL: "
            "CERTIFICATE_VERIFY_FAILED] certificate verify failed "
            "(_ssl.c:1000)"
        ) is False
        assert is_noise(
            "webhook_health: API error shop=acme-store.myshopify.com: "
            "[SSL: CERTIFICATE_VERIFY_FAILED] Hostname mismatch"
        ) is False

    def test_loadtest_host_without_cert_phrase_is_NOT_noise(self):
        """Both anchors required. A `_loadtest_` shop in a message that
        is NOT a TLS-cert failure (e.g. a real logic bug surfaced while
        a load run happened to be in flight) must STILL surface — we do
        not blanket-suppress everything mentioning `_loadtest_`."""
        assert is_noise(
            "KeyError: 'revenue' for shop=_loadtest_00001.myshopify.com"
        ) is False
        assert is_noise(
            "AttributeError: 'NoneType' has no attribute 'total' "
            "shop=_loadtest_00042.myshopify.com"
        ) is False

    def test_cert_phrase_without_loadtest_host_is_NOT_noise(self):
        """The other anchor: a generic cert-verify phrase WITHOUT a
        synthetic host must not match (could be a real infra issue)."""
        assert is_noise(
            "requests.exceptions.SSLError: CERTIFICATE_VERIFY_FAILED "
            "for api.anthropic.com"
        ) is False


# ---------------------------------------------------------------------------
# Class 5 — by-design dashboard warming-503 (born 2026-05-19)
# ---------------------------------------------------------------------------


class TestDashboardWarming503Noise:
    """The `b28dc07` Redis-down defence raises HTTPException(503,
    "dashboard warming — retry shortly") as a deterministic graceful
    degradation — the client retries, the merchant never sees an error.
    Same honest tradeoff as classes 2-3: a purpose-built degradation
    response is not a bug. Ground-truthed 2026-05-19: rows #255/#256."""

    def test_exact_ground_truthed_string_is_noise(self):
        # Sentry issue.title form (em-dash, the source string at
        # app/api/dashboard.py:1149).
        assert is_noise(
            "HTTPException: dashboard warming — retry shortly"
        ) is True
        # Bare detail form
        assert is_noise("dashboard warming — retry shortly") is True

    def test_dash_variants_are_noise(self):
        # Capture paths vary in how they normalize the em-dash.
        assert is_noise("dashboard warming - retry shortly") is True
        assert is_noise("dashboard warming – retry shortly") is True
        assert is_noise("Dashboard Warming — Retry Shortly") is True

    def test_generic_5xx_http_exception_is_NOT_noise(self):
        """NON-VACUITY / no-false-drop. A generic HTTPException or any
        other 503 must STILL surface — only the exact purpose-built
        warming phrase is filtered. A real warm-path regression's
        signal is /system/health, not this counter; but a DIFFERENT
        5xx is a real incident."""
        assert is_noise("HTTPException: 500 Internal Server Error") is False
        assert is_noise("HTTPException: 503 Service Unavailable") is False
        assert is_noise(
            "HTTPException: merchant not found"
        ) is False
        assert is_noise(
            "HTTPException: rate limit exceeded — retry shortly"
        ) is False
