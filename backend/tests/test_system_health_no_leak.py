"""Sprint A audit C3 — POST /system/health was leaking SQLAlchemy /
psycopg / redis-py exception strings to anonymous callers
(`detail: str(exc)[:200]`). A Triple Whale CTO doing diligence will
flag any path where DB connection-string fragments, schema/table
names, host:port, or socket addresses leak unauthenticated.

This regression test mocks each subsystem to raise on probe and
asserts the response is COARSE (machine-readable status codes), not
raw exception strings.
"""
from __future__ import annotations

import pytest


def _assert_no_exception_leak(payload: dict, subsystem_key: str):
    """Assert the subsystem block has NO `detail` field carrying raw
    exception text. Allows `code` (coarse machine-readable status)."""
    sub = payload["subsystems"].get(subsystem_key, {})
    # `detail` was the leaky field; must be absent on error paths.
    assert "detail" not in sub, (
        f"system_health.{subsystem_key} leaked exception detail: "
        f"{sub.get('detail')!r}"
    )


def test_health_database_error_returns_coarse_code(client, monkeypatch):
    """Force DB probe to raise; response must carry `code: db_unreachable`
    NOT raw psycopg exception text."""
    from app.api import health
    from sqlalchemy.exc import OperationalError

    class _BoomEngine:
        def connect(self):
            raise OperationalError(
                # Realistic psycopg2 error message that would leak
                # connection-string fragments if echoed.
                "could not connect to server: Connection refused\n"
                "Is the server running on host \"db.internal.host\" "
                "(10.0.0.5) and accepting TCP/IP connections on port 5432?",
                None, None,
            )

    monkeypatch.setattr(health, "engine", _BoomEngine())
    response = client.get("/system/health")
    payload = response.json()

    _assert_no_exception_leak(payload, "database")
    assert payload["subsystems"]["database"].get("code") == "db_unreachable"
    assert payload["status"] == "critical"
    assert response.status_code == 503


def test_health_redis_error_returns_coarse_code(client, monkeypatch):
    """Force Redis probe to raise; response must carry `code: redis_error`
    NOT raw redis-py socket address text."""
    class _BoomRedis:
        def ping(self):
            raise ConnectionError(
                "Error connecting to redis-internal.host.local:6379"
            )

    from app.core import redis_client as rc_mod
    monkeypatch.setattr(rc_mod, "_client", lambda: _BoomRedis())

    response = client.get("/system/health")
    payload = response.json()

    _assert_no_exception_leak(payload, "redis")
    assert payload["subsystems"]["redis"].get("code") == "redis_error"


def test_health_happy_path_returns_no_detail_field(client):
    """Sanity: even on success, no subsystem should have a `detail`
    field — `detail` is reserved for the (now removed) leak path."""
    response = client.get("/system/health")
    if response.status_code != 200:
        return  # CI may have a degraded subsystem; check if 200 only

    payload = response.json()
    for sub_name, sub_data in payload.get("subsystems", {}).items():
        if isinstance(sub_data, dict):
            assert "detail" not in sub_data, (
                f"system_health.{sub_name} carries `detail` field — "
                f"this MUST be removed (Sprint A C3 leak fix)"
            )
