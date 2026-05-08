"""Regression pins for the 3 CRITICAL findings of the 2026-05-08 audit.

CRITICAL #1: /agent/* router shipped UNGATED in production. /daily-brief
    leaked cross-merchant summary, /project-context disclosed internal
    architecture files, /sandbox/* accepted arbitrary payloads. Fix:
    router-level Depends(require_operator).

CRITICAL #2: sentry_webhooks._verify_sentry_signature returned silently
    when SENTRY_WEBHOOK_SECRET was unset, accepting unsigned payloads.
    Fix: raise 503 when secret unset (mirrors telegram_webhook contract).
"""
from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient


def _client():
    """Lazy import so test discovery doesn't need full env."""
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# CRITICAL #1 — /agent/* router auth gate
# ---------------------------------------------------------------------------

def test_agent_daily_brief_rejects_no_api_key(monkeypatch):
    """Without X-API-Key, /agent/daily-brief must NOT return data.

    Pre-fix: ungated, returned cross-merchant summary to anyone.
    Post-fix: require_operator → 401 / 503.
    """
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-for-gate")
    # Reload deps so the gate sees the env var.
    import importlib
    from app.core import deps
    importlib.reload(deps)

    c = _client()
    resp = c.get("/agent/daily-brief")
    assert resp.status_code in (401, 403), (
        f"/agent/daily-brief MUST reject unauthenticated requests, "
        f"got {resp.status_code}: {resp.text[:200]}"
    )


def test_agent_project_context_rejects_no_api_key(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-for-gate")
    import importlib
    from app.core import deps
    importlib.reload(deps)

    c = _client()
    resp = c.get("/agent/project-context")
    assert resp.status_code in (401, 403), (
        f"/agent/project-context MUST reject unauthenticated requests, "
        f"got {resp.status_code}"
    )


def test_agent_sandbox_create_rejects_no_api_key(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-for-gate")
    import importlib
    from app.core import deps
    importlib.reload(deps)

    c = _client()
    resp = c.post("/agent/sandbox/create", json={"goal": "evil"})
    assert resp.status_code in (401, 403, 422), (
        f"/agent/sandbox/create MUST reject unauthenticated requests, "
        f"got {resp.status_code}"
    )


def test_agent_router_has_dependency_set():
    """Structural pin: the router's `dependencies` list must include
    require_operator so EVERY current+future endpoint is gated.

    Compare by qualified name so importlib.reload in other tests doesn't
    invalidate the identity match.
    """
    from app.api.agent import router
    dep_names = [
        f"{getattr(d.dependency, '__module__', '?')}.{getattr(d.dependency, '__name__', '?')}"
        for d in (router.dependencies or [])
    ]
    assert "app.core.deps.require_operator" in dep_names, (
        "agent router MUST declare Depends(require_operator) at router "
        "level so future endpoints inherit the gate by default. "
        f"Current dependencies: {dep_names}"
    )


def test_agent_with_valid_api_key_does_not_401(monkeypatch):
    """Sanity counterpart: with a valid X-API-Key, the gate passes."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-for-gate")
    import importlib
    from app.core import deps
    importlib.reload(deps)

    c = _client()
    resp = c.get("/agent/scan-project", headers={"X-API-Key": "test-secret-for-gate"})
    # Must NOT be 401/403 — endpoint may legitimately 500 or 200, but auth
    # must let it through.
    assert resp.status_code not in (401, 403), (
        f"valid X-API-Key must pass the gate, got {resp.status_code}: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# CRITICAL #2 — sentry_webhooks fail-closed
# ---------------------------------------------------------------------------

def _bust_sentry_secret_cache():
    """Module-level _WEBHOOK_SECRET is cached on first read; bust it
    between tests so monkeypatch.setenv takes effect."""
    from app.api import sentry_webhooks
    sentry_webhooks._WEBHOOK_SECRET = None


def test_sentry_webhook_rejects_when_secret_unset(monkeypatch):
    """When SENTRY_WEBHOOK_SECRET is unset, the verify function must
    raise 503 — NOT silently accept the payload."""
    monkeypatch.delenv("SENTRY_WEBHOOK_SECRET", raising=False)
    _bust_sentry_secret_cache()
    from app.api import sentry_webhooks
    from fastapi import HTTPException
    import pytest as _pytest
    with _pytest.raises(HTTPException) as exc_info:
        sentry_webhooks._verify_sentry_signature(b"any-payload", "any-sig")
    assert exc_info.value.status_code == 503, (
        f"verify must return 503 when secret unset, got {exc_info.value.status_code}"
    )
    assert "sentry_webhook_secret_not_configured" in str(exc_info.value.detail)
    _bust_sentry_secret_cache()  # leave clean for other tests


def test_sentry_webhook_rejects_bad_signature_when_secret_set(monkeypatch):
    """Sanity: with secret set, a wrong signature must 401."""
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "test-sentry-secret")
    _bust_sentry_secret_cache()
    from app.api import sentry_webhooks
    from fastapi import HTTPException
    import pytest as _pytest
    with _pytest.raises(HTTPException) as exc_info:
        sentry_webhooks._verify_sentry_signature(b"payload", "wrong-sig")
    assert exc_info.value.status_code == 401, (
        f"bad signature must 401, got {exc_info.value.status_code}"
    )
    _bust_sentry_secret_cache()


def test_sentry_webhook_accepts_correct_signature(monkeypatch):
    """Sanity: with secret set + correct HMAC, verify returns None (no raise)."""
    import hashlib
    import hmac as _hmac
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "test-sentry-secret")
    _bust_sentry_secret_cache()
    from app.api import sentry_webhooks
    payload = b"some-real-payload"
    sig = _hmac.new(b"test-sentry-secret", payload, hashlib.sha256).hexdigest()
    # Must NOT raise.
    sentry_webhooks._verify_sentry_signature(payload, sig)
    _bust_sentry_secret_cache()
