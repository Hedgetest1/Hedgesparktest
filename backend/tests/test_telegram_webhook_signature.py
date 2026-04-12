"""Security: Telegram webhook signature verification (2026-04-11)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import telegram_webhook as tw
from app.main import app


client = TestClient(app, raise_server_exceptions=False)


def test_rejects_when_secret_env_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    resp = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
    )
    assert resp.status_code == 503


def test_rejects_when_header_missing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "correct-secret")
    resp = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
    )
    assert resp.status_code == 401


def test_rejects_when_header_mismatch(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "correct-secret")
    resp = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert resp.status_code == 401


def test_accepts_with_matching_header(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "correct-secret")
    resp = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
    )
    # 200 or any non-401/503 means the signature gate passed
    assert resp.status_code not in (401, 503)


def test_signature_uses_timing_safe_compare(monkeypatch):
    """Make sure our code path goes through hmac.compare_digest, not `==`."""
    import inspect
    src = inspect.getsource(tw._verify_telegram_signature)
    assert "compare_digest" in src
