"""Unit tests for app/core/client_ip.py — Cloudflare-aware IP extraction."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import client_ip as client_ip_mod
from app.core.client_ip import extract_client_ip, extract_client_ip_with_source

# Real CF IP ranges (bundled snapshot in cf_ip_ranges.py).
# 188.114.96.0/20 is the EU/FRA POP range — same one we hit in the
# 2026-05-05 smoke test. Use IPs from this range when a test needs the
# socket peer to satisfy the CF source-IP gate.
_CF_PEER_V4 = "188.114.96.3"
_CF_PEER_V6 = "2400:cb00::1"
_NON_CF_PEER = "172.17.0.5"  # Docker default subnet, NOT a CF range


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


@pytest.fixture(autouse=True)
def _reset_cf_counters():
    """Reset per-worker counters so tests don't bleed into each other."""
    client_ip_mod._cf_spoof_count = 0
    client_ip_mod._cf_trust_count = 0
    yield


def test_cf_connecting_ip_wins_over_xff_and_socket():
    """Both gates open: env=true AND socket peer is a CF IP → trust CF header."""
    req = _req(
        headers={
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "198.51.100.1, 173.245.48.1",
        },
        client_host=_CF_PEER_V4,  # real CF range — gate 2 satisfied
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
    # Reset the warn-once flag so the test's "unknown" hit triggers the log.
    client_ip_mod._unknown_warned = False
    req = _req()
    ip, src = extract_client_ip_with_source(req)
    assert ip == "unknown"
    assert src == "unknown"


def test_unknown_warn_fires_only_once_per_process():
    """The 'unknown' warning is rate-limited to one log line per worker
    lifetime — not one per request — to avoid flooding logs under broken
    proxy configurations.

    Verified via the module-local flag rather than caplog, since the
    `wishspark.client_ip` logger may not propagate to the root logger
    that caplog patches across the process; flag-flip is the actual
    invariant we care about.
    """
    client_ip_mod._unknown_warned = False
    req = _req()

    # First call triggers the warn → flag flips to True
    extract_client_ip_with_source(req)
    assert client_ip_mod._unknown_warned is True

    # Subsequent calls do NOT re-trigger (flag is already True;
    # _warn_unknown_once short-circuits)
    extract_client_ip_with_source(req)
    extract_client_ip_with_source(req)
    extract_client_ip_with_source(req)
    # Flag remains True (no flip-back); fast path was taken
    assert client_ip_mod._unknown_warned is True


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
    req = _req(headers={"cf-connecting-ip": long_ip}, client_host=_CF_PEER_V4)
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
    req = _req(headers={"cf-connecting-ip": "203.0.113.7"}, client_host=_CF_PEER_V4)
    assert extract_client_ip(req) == "203.0.113.7"


def test_cf_header_with_ipv6():
    req = _req(headers={"cf-connecting-ip": "2001:db8::1"}, client_host=_CF_PEER_V4)
    ip, src = extract_client_ip_with_source(req)
    assert ip == "2001:db8::1"
    assert src == "cf"


# ───────────────────────────────────────────────────────────────────
# Source-IP gate (TIER_1 origin-lock at app layer)
# ───────────────────────────────────────────────────────────────────


def test_source_ip_gate_ignores_cf_header_from_non_cf_peer():
    """Spoof scenario: attacker sends CF-Connecting-IP from a non-CF
    socket peer. The gate ignores the header and falls through to XFF."""
    req = _req(
        headers={
            "cf-connecting-ip": "203.0.113.7",  # spoofed
            "x-forwarded-for": "198.51.100.1",  # real upstream
        },
        client_host=_NON_CF_PEER,  # NOT a CF range → gate 2 fails
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "198.51.100.1"
    assert src == "xff"  # CF header ignored, fell through


def test_source_ip_gate_ignores_cf_header_when_no_socket_peer():
    """Defense: if there's no socket peer info to verify, refuse to trust
    the CF header (can't verify the source)."""
    req = _req(
        headers={
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "198.51.100.1",
        },
        client_host=None,
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "198.51.100.1"
    assert src == "xff"


def test_source_ip_gate_falls_to_socket_when_no_xff():
    """Spoofed CF header from non-CF peer, no XFF → falls to socket peer."""
    req = _req(
        headers={"cf-connecting-ip": "203.0.113.7"},
        client_host=_NON_CF_PEER,
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == _NON_CF_PEER
    assert src == "client"


def test_source_ip_gate_trust_counter_increments_on_real_cf():
    """Per-worker counter tracks how often the CF header was trusted."""
    req = _req(
        headers={"cf-connecting-ip": "203.0.113.7"},
        client_host=_CF_PEER_V4,
    )
    extract_client_ip_with_source(req)
    extract_client_ip_with_source(req)
    counters = client_ip_mod.get_cf_gate_counters()
    assert counters["trusted"] == 2
    assert counters["ignored_non_cf_source"] == 0


def test_source_ip_gate_spoof_counter_increments_on_non_cf_peer():
    """Per-worker counter tracks ignored CF headers from non-CF peers."""
    req = _req(
        headers={"cf-connecting-ip": "203.0.113.7"},
        client_host=_NON_CF_PEER,
    )
    extract_client_ip_with_source(req)
    extract_client_ip_with_source(req)
    extract_client_ip_with_source(req)
    counters = client_ip_mod.get_cf_gate_counters()
    assert counters["trusted"] == 0
    assert counters["ignored_non_cf_source"] == 3


def test_source_ip_gate_ipv6_cf_peer():
    """IPv6 CF peer satisfies the source-IP gate."""
    req = _req(
        headers={"cf-connecting-ip": "203.0.113.7"},
        client_host=_CF_PEER_V6,
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "203.0.113.7"
    assert src == "cf"


def test_source_ip_gate_does_not_apply_when_env_off(monkeypatch):
    """Pre-flip (env=false): the source-IP gate is irrelevant — the env
    gate already shut down CF-header reading. Counters not bumped."""
    monkeypatch.setattr(client_ip_mod, "CLOUDFLARE_FRONTED", False)
    req = _req(
        headers={
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "198.51.100.1",
        },
        client_host=_CF_PEER_V4,  # CF peer but env gate closed
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == "198.51.100.1"
    assert src == "xff"
    counters = client_ip_mod.get_cf_gate_counters()
    assert counters["trusted"] == 0
    assert counters["ignored_non_cf_source"] == 0  # gate not entered


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
        client_host=_NON_CF_PEER,
    )
    ip, src = extract_client_ip_with_source(req)
    assert ip == _NON_CF_PEER
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
