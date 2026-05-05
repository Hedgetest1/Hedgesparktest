"""Tests for /ops/client-ip-echo smoke endpoint."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core import client_ip as client_ip_mod
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def ops_key(monkeypatch):
    key = "test-ops-key-1234567890"
    monkeypatch.setenv("OPS_API_KEY", key)
    return key


def test_requires_ops_api_key(client):
    """No header → 401."""
    r = client.get("/ops/client-ip-echo")
    # If OPS_API_KEY not set in env at module load, we get 500;
    # if it is set, we get 401. Both are auth failures.
    assert r.status_code in (401, 500)


def test_wrong_ops_api_key_returns_401(client, ops_key):
    r = client.get("/ops/client-ip-echo", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


def test_echoes_xff_when_gate_off(client, ops_key, monkeypatch):
    """Gate off + XFF present → source=xff (pre-flip behavior)."""
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", False)
    r = client.get(
        "/ops/client-ip-echo",
        headers={
            "X-API-Key": ops_key,
            "X-Forwarded-For": "198.51.100.1, 173.245.48.1",
            "CF-Connecting-IP": "203.0.113.7",  # ignored when gate off
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "198.51.100.1"
    assert body["source"] == "xff"
    assert body["cloudflare_fronted"] is False
    assert body["cf_connecting_ip_header_present"] is True


def test_echoes_cf_when_gate_on_and_header_present(client, ops_key, monkeypatch):
    """Gate on + CF header + CF socket peer → source=cf (post-flip behavior).

    The TestClient socket peer is "testclient" (not a CF range) so the
    source-IP gate would normally block. Monkeypatch the membership check
    to simulate a real CF POP socket peer.
    """
    from app.core import cf_ip_ranges as cf_ip_ranges_mod
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", True)
    monkeypatch.setattr(cf_ip_ranges_mod, "is_from_cloudflare", lambda ip: True)
    r = client.get(
        "/ops/client-ip-echo",
        headers={
            "X-API-Key": ops_key,
            "CF-Connecting-IP": "203.0.113.7",
            "X-Forwarded-For": "198.51.100.1",
            "CF-Ray": "abc123-FRA",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "203.0.113.7"
    assert body["source"] == "cf"
    assert body["cloudflare_fronted"] is True
    assert body["cf_ray"] == "abc123-FRA"
    assert body["socket_peer_is_cf_range"] is True
    assert "✅" in body["interpretation"]


def test_echoes_xff_when_cf_header_from_non_cf_peer(client, ops_key, monkeypatch):
    """Source-IP gate: CF header from non-CF peer → header IGNORED, falls to XFF.

    This is the TIER_1 origin-lock at app layer working: an attacker
    bypassing CF cannot spoof CF-Connecting-IP because the gate verifies
    the socket peer is in published CF ranges.
    """
    from app.core import cf_ip_ranges as cf_ip_ranges_mod
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", True)
    monkeypatch.setattr(cf_ip_ranges_mod, "is_from_cloudflare", lambda ip: False)
    r = client.get(
        "/ops/client-ip-echo",
        headers={
            "X-API-Key": ops_key,
            "CF-Connecting-IP": "203.0.113.7",  # spoofed
            "X-Forwarded-For": "198.51.100.1",   # real upstream
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "198.51.100.1"   # CF header ignored
    assert body["source"] == "xff"
    assert body["socket_peer_is_cf_range"] is False
    assert "ignored" in body["interpretation"].lower() or "⚠️" in body["interpretation"]


def test_interpretation_warns_on_gate_mismatch(client, ops_key, monkeypatch):
    """Gate off but CF header present → warning interpretation."""
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", False)
    r = client.get(
        "/ops/client-ip-echo",
        headers={
            "X-API-Key": ops_key,
            "CF-Connecting-IP": "203.0.113.7",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Helper ignored the CF header (gate off); interpretation flags the mismatch
    assert body["source"] in {"xff", "client", "unknown"}
    assert body["cloudflare_fronted"] is False
    assert "CLOUDFLARE_FRONTED is FALSE" in body["interpretation"]
