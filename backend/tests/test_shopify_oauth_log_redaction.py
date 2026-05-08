"""Regression pin for SEC-MED #7 (TIER_2): OAuth code/state/hmac
redaction in shopify_oauth HMAC-mismatch warning log.

Pre-fix: log message echoed `message[:200]` which contained the raw
OAuth `code=<short_lived_bearer>` parameter. Single-use + ~10 min
TTL but bearer-equivalent during that window; aggregator logs
(Sentry, Datadog) could surface it.

Post-fix: redact `code|state|hmac=...` to `code=<REDACTED>` before
logging; emit structural metadata (length, sorted param keys) for
diagnosis.
"""
from __future__ import annotations

import logging
from unittest.mock import patch


class _FakeURL:
    def __init__(self, query: str):
        self.query = query


class _FakeRequest:
    def __init__(self, query: str):
        self.url = _FakeURL(query)


def test_oauth_hmac_mismatch_log_redacts_code():
    """When HMAC verification fails, the warning log MUST NOT contain
    the raw OAuth code, state, or hmac parameter values.

    Capture via a stub log object whose `.warning(...)` records the
    formatted message — bypasses the Python logging stack entirely
    so we get deterministic capture.
    """
    from app.api import shopify_oauth as _so

    captured: list[str] = []

    class _StubLogger:
        def warning(self, fmt, *args, **kwargs):
            try:
                captured.append(fmt % args if args else fmt)
            except Exception:
                captured.append(repr((fmt, args)))
        def error(self, *a, **k): pass
        def info(self, *a, **k): pass
        def debug(self, *a, **k): pass

    qs = (
        "code=ABCDEF1234567890_BEARER_TOKEN_NEVER_LEAK"
        "&state=STATE_NONCE_DEADBEEF"
        "&shop=redaction-test.myshopify.com"
        "&timestamp=1700000000"
        "&hmac=" + ("deadbeef" * 8)  # wrong
    )

    with patch.object(_so, "log", _StubLogger()), \
         patch.object(_so, "_SHOPIFY_API_SECRET", "test-redaction-secret"):
        ok = _so._validate_hmac_from_request(_FakeRequest(qs))

    assert ok is False, "wrong hmac must fail verification"
    all_messages = "\n".join(captured)

    # Forbidden: raw code, state values must NOT appear.
    assert "ABCDEF1234567890_BEARER_TOKEN_NEVER_LEAK" not in all_messages, (
        f"OAuth code MUST be redacted; appeared in log:\n{all_messages}"
    )
    assert "STATE_NONCE_DEADBEEF" not in all_messages, (
        f"OAuth state MUST be redacted; appeared in log:\n{all_messages}"
    )
    # Required: redaction marker present.
    assert "<REDACTED>" in all_messages or "redacted=" in all_messages, (
        f"log must contain redaction marker\n{all_messages}"
    )


def test_oauth_hmac_match_no_warning_log():
    """When HMAC matches, the mismatch warning path does not fire,
    so the code value cannot leak there."""
    import hashlib
    import hmac as _hmac
    from app.api import shopify_oauth as _so

    secret = "test-match-secret"
    pairs = [
        ("code", "should-not-appear"),
        ("shop", "ok-shop.myshopify.com"),
        ("timestamp", "1700000000"),
    ]
    pairs.sort(key=lambda p: p[0])
    message = "&".join(f"{k}={v}" for k, v in pairs)
    digest = _hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    qs = "&".join(f"{k}={v}" for k, v in pairs) + f"&hmac={digest}"

    captured: list[str] = []

    class _StubLogger:
        def warning(self, fmt, *args, **kwargs):
            try:
                captured.append(fmt % args if args else fmt)
            except Exception:
                captured.append(repr((fmt, args)))
        def error(self, *a, **k): pass
        def info(self, *a, **k): pass
        def debug(self, *a, **k): pass

    with patch.object(_so, "log", _StubLogger()), \
         patch.object(_so, "_SHOPIFY_API_SECRET", secret):
        ok = _so._validate_hmac_from_request(_FakeRequest(qs))

    assert ok is True
    assert "should-not-appear" not in "\n".join(captured)
