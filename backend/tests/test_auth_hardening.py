"""Tests for auth hardening primitives."""
from __future__ import annotations

import os
from unittest.mock import patch

from app.core import auth_hardening as ah


class FakeRedis:
    def __init__(self):
        self.zset = {}
        self.sets = {}
    def zadd(self, key, mapping):
        self.zset.setdefault(key, {}).update(mapping)
    def zremrangebyscore(self, key, lo, hi):
        z = self.zset.get(key, {})
        self.zset[key] = {k: v for k, v in z.items() if v > hi}
    def zcard(self, key):
        return len(self.zset.get(key, {}))
    def expire(self, key, ttl):
        pass
    def sismember(self, key, val):
        return val in self.sets.get(key, set())
    def sadd(self, key, val):
        self.sets.setdefault(key, set()).add(val)


def test_record_session_single_login_is_clean():
    fake = FakeRedis()
    with patch("app.core.auth_hardening._redis", return_value=fake):
        ev = ah.record_session_creation("shop.myshopify.com", "1.2.3.4", "Mozilla/5.0")
    assert ev.anomalous is False
    assert ev.velocity_count == 1


def test_record_session_velocity_flag():
    fake = FakeRedis()
    with patch("app.core.auth_hardening._redis", return_value=fake):
        for i in range(6):
            ev = ah.record_session_creation(
                "shop.myshopify.com", f"1.2.3.{i}", "Mozilla/5.0"
            )
    assert ev.anomalous is True
    assert any("velocity" in r for r in ev.reasons)


def test_record_session_novel_device_plus_velocity():
    fake = FakeRedis()
    with patch("app.core.auth_hardening._redis", return_value=fake):
        ah.record_session_creation("shop.myshopify.com", "1.1.1.1", "UA1")
        ev = ah.record_session_creation("shop.myshopify.com", "2.2.2.2", "UA2")
    assert ev.anomalous is True
    assert "novel_device_plus_velocity" in ev.reasons


def test_audit_secrets_detects_missing(monkeypatch):
    for name, _, _ in ah._CRITICAL_SECRETS:
        monkeypatch.delenv(name, raising=False)
    rows = ah.audit_secrets()
    assert all(r["status"] == "missing" for r in rows)


def test_audit_secrets_detects_weak(monkeypatch):
    monkeypatch.setenv("MERCHANT_SESSION_SECRET", "changeme")
    rows = ah.audit_secrets()
    row = next(r for r in rows if r["name"] == "MERCHANT_SESSION_SECRET")
    assert row["status"] == "weak"


def test_audit_secrets_ok_path(monkeypatch):
    for name, min_len, _ in ah._CRITICAL_SECRETS:
        monkeypatch.setenv(name, "X" * (min_len + 4))
    rows = ah.audit_secrets()
    assert all(r["status"] == "ok" for r in rows)


def test_auth_posture_summary(monkeypatch):
    for name, _, _ in ah._CRITICAL_SECRETS:
        monkeypatch.delenv(name, raising=False)
    posture = ah.auth_posture()
    assert posture["secrets"]["missing"] > 0
    assert posture["session_anomaly"]["velocity_threshold"] > 0
