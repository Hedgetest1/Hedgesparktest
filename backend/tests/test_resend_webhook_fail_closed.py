"""Regression test: Resend inbound webhook must fail closed when the
signature secret is unset. Previously a missing secret silently
skipped verification and accepted forged traffic."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api import resend_webhooks as rw


def test_verify_raises_503_when_secret_missing():
    with patch.object(rw, "_WEBHOOK_SECRET", None), \
            patch.object(rw.os, "getenv", return_value=""):
        with pytest.raises(HTTPException) as exc_info:
            rw._verify_webhook("{}", {})
    assert exc_info.value.status_code == 503


def test_verify_passes_through_to_sdk_when_secret_present():
    """When the secret is present, verification must reach the SDK.
    The SDK call itself is allowed to raise 401; that's a separate
    code path covered by existing tests."""
    with patch.object(rw, "_WEBHOOK_SECRET", "fake-secret"):
        sdk_called = {"n": 0}

        class _FakeResend:
            class Webhooks:
                @staticmethod
                def verify(_payload):
                    sdk_called["n"] += 1

        import sys
        sys.modules["resend"] = _FakeResend  # type: ignore[assignment]
        try:
            rw._verify_webhook("{}", {
                "svix-id": "x",
                "svix-timestamp": "y",
                "svix-signature": "z",
            })
        finally:
            del sys.modules["resend"]
        assert sdk_called["n"] == 1
