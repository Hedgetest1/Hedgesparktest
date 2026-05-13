"""
Unit tests for the pure helpers extracted from `get_customer_churn_forecast`
in the 2026-05-13 A3 refactor.

End-to-end coverage exists at /customer-churn-forecast via the prior
test_customer_churn_forecast.py (20 tests). This file locks the new
structural-unit helpers: stampede-lock helpers + identity hash +
predicted-lapse + per-customer record builder.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api.lite_extras import (
    _build_churn_risk_customer,
    _build_cold_start_response,
    _churn_acquire_lock,
    _churn_release_lock,
    _churn_poll_cache_while_locked,
    _compute_predicted_lapse_iso,
    _hash_churn_identity,
    ChurnRiskCustomer,
    CustomerChurnForecastResponse,
)


# ---------------------------------------------------------------------------
# _hash_churn_identity
# ---------------------------------------------------------------------------


class TestHashIdentity:
    def test_prefix_and_length(self):
        h = _hash_churn_identity("anything")
        assert h.startswith("cust_")
        assert len(h) == len("cust_") + 8

    def test_deterministic(self):
        assert _hash_churn_identity("a@x.com") == _hash_churn_identity("a@x.com")

    def test_different_inputs_different_hashes(self):
        assert _hash_churn_identity("a@x.com") != _hash_churn_identity("b@x.com")

    def test_pii_never_in_hash(self):
        # Raw email components MUST NOT appear in the output
        h = _hash_churn_identity("private@example.com")
        assert "private" not in h
        assert "example" not in h


# ---------------------------------------------------------------------------
# _compute_predicted_lapse_iso
# ---------------------------------------------------------------------------


class TestPredictedLapse:
    def test_none_when_no_last_order(self):
        assert _compute_predicted_lapse_iso(None, 30.0) is None

    def test_none_when_zero_gap(self):
        assert _compute_predicted_lapse_iso(datetime(2025, 1, 1), 0.0) is None

    def test_none_when_negative_gap(self):
        assert _compute_predicted_lapse_iso(datetime(2025, 1, 1), -5.0) is None

    def test_2_5x_median_gap_anchor(self):
        # last_order + 2.5 × 30 = +75 days
        out = _compute_predicted_lapse_iso(datetime(2025, 1, 1), 30.0)
        assert out is not None
        # Parse back and check delta
        parsed = datetime.fromisoformat(out)
        assert (parsed - datetime(2025, 1, 1)).days == 75


# ---------------------------------------------------------------------------
# _churn_acquire_lock — SETNX semantics + fail-open
# ---------------------------------------------------------------------------


class TestAcquireLock:
    def test_none_rc_returns_true(self):
        # No Redis client → caller proceeds with compute (no blocking)
        assert _churn_acquire_lock(None, "lock_key") is True

    def test_redis_setnx_true(self):
        rc = MagicMock()
        rc.set.return_value = True
        assert _churn_acquire_lock(rc, "lock_key") is True
        rc.set.assert_called_once_with("lock_key", "1", nx=True, ex=30)

    def test_redis_setnx_false(self):
        # Another worker holds the lock
        rc = MagicMock()
        rc.set.return_value = False
        assert _churn_acquire_lock(rc, "lock_key") is False

    def test_redis_failure_fails_open(self):
        # Redis error → caller proceeds (better to compute than block)
        rc = MagicMock()
        rc.set.side_effect = RuntimeError("redis down")
        assert _churn_acquire_lock(rc, "lock_key") is True


# ---------------------------------------------------------------------------
# _churn_release_lock — best-effort
# ---------------------------------------------------------------------------


class TestReleaseLock:
    def test_none_rc_noop(self):
        # Must NOT raise
        _churn_release_lock(None, "lock_key")

    def test_release_called(self):
        rc = MagicMock()
        _churn_release_lock(rc, "lock_key")
        rc.delete.assert_called_once_with("lock_key")

    def test_silent_on_failure(self):
        rc = MagicMock()
        rc.delete.side_effect = RuntimeError("redis down")
        # Must NOT raise
        _churn_release_lock(rc, "lock_key")


# ---------------------------------------------------------------------------
# _churn_poll_cache_while_locked — polling budget contract
# ---------------------------------------------------------------------------


class TestPollCacheWhileLocked:
    def test_returns_cached_on_first_hit(self, monkeypatch):
        # Cache fills on the first poll
        import app.api.lite_extras as le
        sleeps = []
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
        cached_payload = {
            "currency": "USD", "has_data": True, "customers_with_2plus": 5,
            "customers_at_risk_count": 1, "revenue_at_risk": 100.0, "customers": [],
        }
        call_count = {"n": 0}
        def _fake_get(key):
            call_count["n"] += 1
            return cached_payload if call_count["n"] >= 1 else None
        monkeypatch.setattr(le, "cache_get", _fake_get)

        out = _churn_poll_cache_while_locked("k", CustomerChurnForecastResponse)
        assert out is not None
        assert out.currency == "USD"
        # First-poll hit means ≤ 1 sleep
        assert len(sleeps) <= 1

    def test_returns_none_after_budget(self, monkeypatch):
        # Cache never fills — caller falls through after 15 attempts
        import app.api.lite_extras as le
        sleeps = []
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(le, "cache_get", lambda k: None)

        out = _churn_poll_cache_while_locked("k", CustomerChurnForecastResponse)
        assert out is None
        # 15 attempts at 0.2s each = 3s budget
        assert len(sleeps) == 15
        assert all(s == 0.2 for s in sleeps)


# ---------------------------------------------------------------------------
# _build_cold_start_response
# ---------------------------------------------------------------------------


class TestColdStartResponse:
    def test_shape(self):
        out = _build_cold_start_response(
            currency="EUR", customers_with_2plus=3,
            response_cls=CustomerChurnForecastResponse,
        )
        assert out.currency == "EUR"
        assert out.has_data is False
        assert out.customers_with_2plus == 3
        assert out.customers_at_risk_count == 0
        assert out.revenue_at_risk == 0.0
        assert out.customers == []


# ---------------------------------------------------------------------------
# _build_churn_risk_customer — score gate + record shape
# ---------------------------------------------------------------------------


def _row(days_since_last=30.0, median_gap_days=10.0, last_order_at=None,
         total_spent=100.0, order_count=3,
         identity="cust1@x.com", display_email="cust1@x.com",
         shopify_customer_id=None):
    return {
        "days_since_last": days_since_last,
        "median_gap_days": median_gap_days,
        "last_order_at": last_order_at or datetime(2025, 1, 15),
        "total_spent": total_spent,
        "order_count": order_count,
        "identity": identity,
        "display_email": display_email,
        "shopify_customer_id": shopify_customer_id,
    }


class TestBuildChurnRiskCustomer:
    def test_not_at_risk_returns_none(self):
        # days_since_last < median_gap → factor < 1 → not_at_risk
        out = _build_churn_risk_customer(
            _row(days_since_last=5, median_gap_days=30),
            ChurnRiskCustomer,
        )
        assert out is None

    def test_slipping_band_surfaces_record(self):
        # factor 1.2 → slipping band → score >= 30
        out = _build_churn_risk_customer(
            _row(days_since_last=36, median_gap_days=30),
            ChurnRiskCustomer,
        )
        assert out is not None
        assert out.risk_band == "slipping"
        assert out.risk_score >= 30

    def test_at_risk_band_surfaces_record(self):
        # factor 2.0 → at_risk band
        out = _build_churn_risk_customer(
            _row(days_since_last=60, median_gap_days=30),
            ChurnRiskCustomer,
        )
        assert out is not None
        assert out.risk_band == "at_risk"

    def test_lapsed_band_capped_at_95(self):
        # factor 10 → lapsed band, score capped at 95
        out = _build_churn_risk_customer(
            _row(days_since_last=300, median_gap_days=30),
            ChurnRiskCustomer,
        )
        assert out is not None
        assert out.risk_band == "lapsed"
        assert out.risk_score == 95

    def test_record_uses_hashed_identity(self):
        out = _build_churn_risk_customer(
            _row(days_since_last=60, median_gap_days=30,
                 identity="alice@example.com"),
            ChurnRiskCustomer,
        )
        assert out.customer_email_hash.startswith("cust_")
        # Raw email never crosses the wire
        assert "alice" not in out.customer_email_hash

    def test_shopify_customer_id_propagates(self):
        out = _build_churn_risk_customer(
            _row(days_since_last=60, median_gap_days=30,
                 shopify_customer_id="12345"),
            ChurnRiskCustomer,
        )
        assert out.customer_id_shopify == "12345"

    def test_predicted_lapse_computed(self):
        out = _build_churn_risk_customer(
            _row(days_since_last=60, median_gap_days=30,
                 last_order_at=datetime(2025, 1, 1)),
            ChurnRiskCustomer,
        )
        # last + 2.5 × 30 = +75d
        parsed = datetime.fromisoformat(out.predicted_lapse_at)
        assert (parsed - datetime(2025, 1, 1)).days == 75

    def test_total_spent_rounded_to_2dp(self):
        out = _build_churn_risk_customer(
            _row(days_since_last=60, median_gap_days=30, total_spent=123.456),
            ChurnRiskCustomer,
        )
        assert out.total_spent == 123.46
