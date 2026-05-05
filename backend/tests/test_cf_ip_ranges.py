"""Unit tests for app/core/cf_ip_ranges.py — Cloudflare IP membership."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core import cf_ip_ranges as mod


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets a fresh module cache."""
    mod._networks = None
    mod._last_refresh_ts = None
    mod._last_refresh_source = "uninitialized"
    yield
    mod._networks = None


# ──────────────────────────────────────────────────────────────────
# Membership — happy path on bundled snapshot
# ──────────────────────────────────────────────────────────────────


def test_known_cf_v4_returns_true():
    # 188.114.96.3 → 188.114.96.0/20 in bundled list (FRA POP we tested)
    assert mod.is_from_cloudflare("188.114.96.3") is True
    assert mod.is_from_cloudflare("104.16.0.1") is True
    assert mod.is_from_cloudflare("173.245.48.1") is True


def test_non_cf_v4_returns_false():
    assert mod.is_from_cloudflare("8.8.8.8") is False
    assert mod.is_from_cloudflare("1.1.1.1") is False  # CF DNS, not proxy
    assert mod.is_from_cloudflare("192.168.1.1") is False
    assert mod.is_from_cloudflare("172.17.0.5") is False


def test_known_cf_v6_returns_true():
    assert mod.is_from_cloudflare("2400:cb00::1") is True
    assert mod.is_from_cloudflare("2606:4700::abcd") is True


def test_non_cf_v6_returns_false():
    assert mod.is_from_cloudflare("2001:db8::1") is False
    assert mod.is_from_cloudflare("::1") is False  # loopback


# ──────────────────────────────────────────────────────────────────
# Edge cases & input hygiene
# ──────────────────────────────────────────────────────────────────


def test_empty_or_none_returns_false():
    assert mod.is_from_cloudflare("") is False
    assert mod.is_from_cloudflare(None) is False
    assert mod.is_from_cloudflare("   ") is False


def test_garbage_input_returns_false():
    assert mod.is_from_cloudflare("not-an-ip") is False
    assert mod.is_from_cloudflare("999.999.999.999") is False
    assert mod.is_from_cloudflare("foo bar") is False


def test_whitespace_around_valid_ip_works():
    assert mod.is_from_cloudflare("  188.114.96.3  ") is True


# ──────────────────────────────────────────────────────────────────
# Cache state / get_state
# ──────────────────────────────────────────────────────────────────


def test_get_state_loads_bundled_on_first_query():
    state = mod.get_state()
    assert state["loaded"] is False
    mod.is_from_cloudflare("188.114.96.3")  # triggers _ensure_loaded
    state = mod.get_state()
    assert state["loaded"] is True
    assert state["v4_count"] == len(mod._BUNDLED_V4)
    assert state["v6_count"] == len(mod._BUNDLED_V6)
    assert state["last_refresh_source"] == "bundled-init"


# ──────────────────────────────────────────────────────────────────
# Refresh from cloudflare.com — happy path + degrade-open
# ──────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, v4_text: str, v6_text: str, raise_on=None):
        self.v4_text = v4_text
        self.v6_text = v6_text
        self.raise_on = raise_on

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url):
        if self.raise_on and self.raise_on in url:
            raise RuntimeError("simulated network error")
        if "ips-v4" in url:
            return _FakeResponse(self.v4_text)
        return _FakeResponse(self.v6_text)


def test_refresh_success_replaces_cache():
    fake_v4 = "1.2.3.0/24\n5.6.7.0/24\n"
    fake_v6 = "2001:db8::/32\n"
    with patch.object(mod.httpx, "Client", lambda **kw: _FakeClient(fake_v4, fake_v6)):
        result = mod.refresh_from_cloudflare()
    assert result["source"] == "network"
    assert result["v4_count"] == 2
    assert result["v6_count"] == 1
    # Now is_from_cloudflare should reflect the NEW list, not bundled
    assert mod.is_from_cloudflare("1.2.3.4") is True
    assert mod.is_from_cloudflare("188.114.96.3") is False  # bundled list replaced


def test_refresh_network_failure_keeps_existing_cache():
    """On network failure, cache is preserved (or initialised from bundled)."""
    # Pre-load bundled
    mod._ensure_loaded()
    initial_count = len(mod._networks)

    with patch.object(
        mod.httpx, "Client",
        lambda **kw: _FakeClient("", "", raise_on="ips-v4"),
    ):
        result = mod.refresh_from_cloudflare()
    assert "error" in result
    assert result["source"] in {"bundled-init", "network"}
    # Cache is still functional
    assert mod.is_from_cloudflare("188.114.96.3") is True
    assert len(mod._networks) == initial_count


def test_refresh_empty_response_keeps_existing_cache():
    """Defensive: cloudflare.com returns 200 with empty body → degrade-open."""
    mod._ensure_loaded()
    with patch.object(mod.httpx, "Client", lambda **kw: _FakeClient("", "")):
        result = mod.refresh_from_cloudflare()
    assert "error" in result
    # Still functional via bundled
    assert mod.is_from_cloudflare("188.114.96.3") is True


def test_refresh_invalid_cidr_lines_skipped():
    """Garbage lines don't crash the parser; valid lines are kept."""
    fake_v4 = "1.2.3.0/24\nGARBAGE\n5.6.7.0/24\n\n"
    fake_v6 = "2001:db8::/32\nNOT_A_CIDR\n"
    with patch.object(mod.httpx, "Client", lambda **kw: _FakeClient(fake_v4, fake_v6)):
        result = mod.refresh_from_cloudflare()
    assert result["source"] == "network"
    assert result["v4_count"] == 2
    assert result["v6_count"] == 1
