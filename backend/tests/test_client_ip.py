"""Unit tests for app/core/client_ip.py — Cloudflare-aware IP extraction."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import client_ip as client_ip_mod
from app.core.client_ip import extract_client_ip, extract_client_ip_with_source


def _req(headers: dict | None = None, client_host: str | None = None):
    """Build a minimal Request-like stub. Real fastapi.Request requires a
    Scope+Receive plus async machinery — overkill for unit testing the
    pure-function helper. We replicate only what the helper reads."""
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=client_host) if client_host else None,
    )


@pytest.fixture(autouse=True)
def _cloudflare_fronted_on(monkeypatch):
    """Most tests assume the gate is open (post-flip behavior). Tests
    that need the gate closed override via a local monkeypatch."""
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", True)


def test_cf_connecting_ip_wins_over_xff_and_socket():
    req = _req(
        headers={
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "198.51.100.1, 173.245.48.1",
        },
        client_host="172.17.0.5",
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "203.0.113.7"
    assert src == "cf"


def test_xff_first_hop_when_no_cf_header():
    req = _req(
        headers={"x-forwarded-for": "198.51.100.1, 192.0.2.5"},
        client_host="172.17.0.5",
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "198.51.100.1"
    assert src == "xff"


def test_client_host_when_no_proxy_headers():
    req = _req(client_host="192.0.2.99")
    ip, src = extract_client_ip_with_source(req)
    assert ip == "192.0.2.99"
    assert src == "client"


def test_unknown_when_nothing_available():
    req = _req()
    ip, src = extract_client_ip_with_source(req)
    assert ip == "unknown"
    assert src == "unknown"


def test_whitespace_only_headers_fall_through():
    req = _req(
        headers={"cf-connecting-ip": "   ", "x-forwarded-for": "   "},
        client_host="192.0.2.50",
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "192.0.2.50"
    assert src == "client"


def test_xff_first_hop_strips_whitespace_and_returns_only_first():
    req = _req(headers={"x-forwarded-for": "  198.51.100.1  ,  192.0.2.5  "})
    ip, src = extract_client_ip_with_source(req)
    assert ip == "198.51.100.1"
    assert src == "xff"


def test_length_cap_64_chars():
    long_ip = "a" * 200
    req = _req(headers={"cf-connecting-ip": long_ip})
    ip, src = extract_client_ip_with_source(req)
    assert len(ip) == 64
    assert src == "cf"


def test_empty_xff_with_only_commas_falls_through():
    req = _req(headers={"x-forwarded-for": ","}, client_host="192.0.2.50")
    ip, src = extract_client_ip_with_source(req)
    # First-hop split is "" — falls through to client
    assert ip == "192.0.2.50"
    assert src == "client"


def test_extract_client_ip_returns_string_only():
    req = _req(headers={"cf-connecting-ip": "203.0.113.7"})
    assert extract_client_ip(req) == "203.0.113.7"


def test_cf_header_with_ipv6():
    req = _req(headers={"cf-connecting-ip": "2001:db8::1"})
    ip, src = extract_client_ip_with_source(req)
    assert ip == "2001:db8::1"
    assert src == "cf"


# ───────────────────────────────────────────────────────────────────
# CLOUDFLARE_FRONTED gate — pre-flip vs post-flip behavior
# ───────────────────────────────────────────────────────────────────


def test_gate_off_ignores_cf_header_falls_through_to_xff(monkeypatch):
    """Pre-flip: CLOUDFLARE_FRONTED=false → helper ignores CF-Connecting-IP
    and behaves like pre-Cloudflare (XFF first hop)."""
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", False)
    req = _req(
        headers={
            "cf-connecting-ip": "203.0.113.7",   # ignored when gate off
            "x-forwarded-for": "198.51.100.1, 192.0.2.5",
        },
        client_host="172.17.0.5",
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "198.51.100.1"
    assert src == "xff"


def test_gate_off_falls_through_to_socket_when_no_xff(monkeypatch):
    """Pre-flip with no XFF: helper reads request.client.host directly,
    same as pre-Cloudflare. No regression vs pre-commit."""
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", False)
    req = _req(
        headers={"cf-connecting-ip": "203.0.113.7"},  # ignored
        client_host="172.17.0.5",
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "172.17.0.5"
    assert src == "client"


def test_read_cloudflare_fronted_recognizes_truthy_values(monkeypatch):
    """The env-gate accepts the standard truthy variants."""
    for truthy in ("1", "true", "TRUE", "True", "yes", "YES", "on", "  true  "):
        monkeypatch.setenv("CLOUDFLARE_FRONTED", truthy)
        assert client_ip_mod._read_cloudflare_fronted() is True, truthy


def test_read_cloudflare_fronted_default_false(monkeypatch):
    """Default safe: any unrecognised value (or unset) is false."""
    for falsy in ("", "0", "false", "no", "off", "maybe", "fronted"):
        monkeypatch.setenv("CLOUDFLARE_FRONTED", falsy)
        assert client_ip_mod._read_cloudflare_fronted() is False, falsy
    monkeypatch.delenv("CLOUDFLARE_FRONTED", raising=False)
    assert client_ip_mod._read_cloudflare_fronted() is False
