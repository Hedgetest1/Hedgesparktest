"""Contract tests for revenue_metrics WARNING rate-limit (per-shop SETNX).

Born 2026-05-15 — closes "revenue_metrics log spam at scale" pending
item from project_post_2026_05_14_audit_pending. The 3 WARNING sites
in get_shop_aov used to fire on every dashboard paint under burst load
(1000+ concurrent on no-order shops). Now rate-limited via Redis SETNX
with 1h TTL per (shop, currency, class). First emitter logs WARNING;
subsequent calls within window emit DEBUG. Fail-OPEN on Redis miss.

These tests pin the contract so a future change cannot silently regress
to the old per-paint WARNING spam.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.services import revenue_metrics


@pytest.fixture()
def _enable_cache(monkeypatch):
    """Force APP_ENV != 'test' so _cache_enabled() returns True and the
    rate-limiter actually hits Redis. Under default test config it short-
    circuits to fail-open (always emit) — the contract we test here is the
    real production behavior."""
    monkeypatch.setenv("APP_ENV", "production-like")
    yield


def test_should_emit_warning_first_call_returns_true(_enable_cache):
    """First time a rate_key is seen → emit (returns True)."""
    rk = "hs:warn:rev_metrics:no_orders:test-shop-emit-once.myshopify.com:USD"
    assert revenue_metrics._should_emit_warning(rk) is True


def test_should_emit_warning_second_call_within_ttl_returns_false(_enable_cache):
    """Second call with same rate_key inside TTL window → suppress (False)."""
    rk = "hs:warn:rev_metrics:no_orders:test-shop-emit-twice.myshopify.com:USD"
    first = revenue_metrics._should_emit_warning(rk)
    second = revenue_metrics._should_emit_warning(rk)
    assert first is True
    assert second is False


def test_should_emit_warning_distinct_keys_are_independent(_enable_cache):
    """Two distinct (shop, class) pairs → both emit (no cross-talk)."""
    rk_a = "hs:warn:rev_metrics:no_orders:shop-a.myshopify.com:USD"
    rk_b = "hs:warn:rev_metrics:no_orders:shop-b.myshopify.com:USD"
    assert revenue_metrics._should_emit_warning(rk_a) is True
    assert revenue_metrics._should_emit_warning(rk_b) is True


def test_should_emit_warning_fail_open_on_redis_exception(_enable_cache):
    """Redis exception → emit (True). Rate-limiter is optimisation, not
    correctness mechanism — better noisy than silent."""
    rk = "hs:warn:rev_metrics:no_orders:exc-shop.myshopify.com:USD"
    with patch("app.core.redis_client._client") as mock_client:
        mock_rc = MagicMock()
        mock_rc.set.side_effect = RuntimeError("simulated redis outage")
        mock_client.return_value = mock_rc
        assert revenue_metrics._should_emit_warning(rk) is True


def test_should_emit_warning_fail_open_on_no_client(_enable_cache):
    """Redis client None → emit (True). Same fail-open contract."""
    rk = "hs:warn:rev_metrics:no_orders:noclient-shop.myshopify.com:USD"
    with patch("app.core.redis_client._client", return_value=None):
        assert revenue_metrics._should_emit_warning(rk) is True


def test_should_emit_warning_returns_true_under_test_env(monkeypatch):
    """Under APP_ENV=test (default in tests), _cache_enabled() is False so
    the helper short-circuits to fail-open. Pinning this so the helper
    stays a no-op under SAVEPOINT-rollback test config."""
    monkeypatch.setenv("APP_ENV", "test")
    rk = "hs:warn:rev_metrics:no_orders:test-env-shop.myshopify.com:USD"
    # Even on repeat calls, fails open.
    assert revenue_metrics._should_emit_warning(rk) is True
    assert revenue_metrics._should_emit_warning(rk) is True


def test_get_shop_aov_no_orders_emits_warning_once(_enable_cache, db):
    """Integration: first call on a no-order shop → WARNING. Second call
    same (shop, currency) → DEBUG (rate-limited).

    Patch log.warning / log.debug directly: the app installs a JSON stderr
    handler at app.main import which strips pytest's caplog handler, so
    caplog never sees records. Direct mock is the robust path."""
    from app.models.merchant import Merchant

    shop = "no-orders-shop.myshopify.com"
    db.add(Merchant(shop_domain=shop, access_token="x", primary_currency="USD"))
    db.commit()

    with patch.object(revenue_metrics.log, "warning") as mock_warn, \
         patch.object(revenue_metrics.log, "debug") as mock_debug:
        # First call — no orders → WARNING emitted.
        aov_1 = revenue_metrics.get_shop_aov(db, shop, currency="USD")
        assert aov_1 == revenue_metrics.FALLBACK_AOV
        first_warn_calls = [c for c in mock_warn.call_args_list if "no orders found" in c.args[0]]
        assert len(first_warn_calls) == 1, (
            f"expected 1 WARNING on first paint, got {len(first_warn_calls)}: "
            f"all warn calls: {mock_warn.call_args_list}"
        )

        mock_warn.reset_mock()
        mock_debug.reset_mock()

        # Second call — same shop/currency → DEBUG (rate-limited).
        aov_2 = revenue_metrics.get_shop_aov(db, shop, currency="USD")
        assert aov_2 == revenue_metrics.FALLBACK_AOV
        second_warn_calls = [c for c in mock_warn.call_args_list if "no orders found" in c.args[0]]
        second_debug_calls = [c for c in mock_debug.call_args_list if "rate-limited" in c.args[0]]
        assert len(second_warn_calls) == 0, (
            f"second paint should be rate-limited; got {len(second_warn_calls)} WARNINGs"
        )
        assert len(second_debug_calls) == 1, (
            f"second paint should emit DEBUG; got {len(second_debug_calls)}"
        )


def test_get_shop_aov_distinct_shops_each_emit_warning(_enable_cache, db):
    """Two distinct no-order shops → both emit WARNING (rate-limit is per-shop)."""
    from app.models.merchant import Merchant

    shop_a = "ratelimit-shop-a.myshopify.com"
    shop_b = "ratelimit-shop-b.myshopify.com"
    db.add(Merchant(shop_domain=shop_a, access_token="x", primary_currency="USD"))
    db.add(Merchant(shop_domain=shop_b, access_token="x", primary_currency="USD"))
    db.commit()

    with patch.object(revenue_metrics.log, "warning") as mock_warn:
        revenue_metrics.get_shop_aov(db, shop_a, currency="USD")
        revenue_metrics.get_shop_aov(db, shop_b, currency="USD")

        shop_a_warns = [c for c in mock_warn.call_args_list if shop_a in str(c.args)]
        shop_b_warns = [c for c in mock_warn.call_args_list if shop_b in str(c.args)]
        assert len(shop_a_warns) == 1
        assert len(shop_b_warns) == 1
