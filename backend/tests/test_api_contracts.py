"""
Contract tests for critical API response shapes.

These tests enforce the EXACT key presence, value types, and safe empty-state
shapes that the dashboard and operator workflows depend on.

If a test here fails, it means a backend change broke the contract with
the frontend — the fix must be in the backend, not in the test.

Endpoints covered:
    Dashboard (every page load):
        GET /merchant/me
        GET /dashboard/overview
        GET /orders/summary
        GET /orders/daily-revenue
        GET /analytics/alerts
        GET /analytics/weekly-trend

    Pro foundations:
        GET /attribution/summary/pro
        GET /pro/cohorts/monthly
        GET /orders/forecast/pro

    Operator / AI self-management:
        GET /ops/llm-budget
        GET /ops/incidents
        GET /system/health
"""
import os
import pytest
from tests.conftest import SHOP_A, SHOP_B

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_keys(data: dict, required_keys: set, label: str = ""):
    """Assert all required keys are present in the response dict."""
    missing = required_keys - set(data.keys())
    assert not missing, f"{label} missing keys: {missing}"


def _assert_type(value, expected_type, label: str = ""):
    """Assert a value is the expected type (or None if nullable)."""
    if expected_type is None:
        return  # skip check
    assert isinstance(value, expected_type), f"{label}: expected {expected_type.__name__}, got {type(value).__name__}: {repr(value)[:100]}"


def _assert_list_of_dicts(value, required_keys: set, label: str = ""):
    """Assert value is a list of dicts with required keys."""
    assert isinstance(value, list), f"{label}: expected list, got {type(value).__name__}"
    for i, item in enumerate(value):
        assert isinstance(item, dict), f"{label}[{i}]: expected dict, got {type(item).__name__}"
        _assert_keys(item, required_keys, f"{label}[{i}]")


# ===========================================================================
# Dashboard core — every page load
# ===========================================================================

class TestMerchantMe:
    """GET /merchant/me — session bootstrap, called on every dashboard load."""

    def test_authenticated_shape(self, client, merchant_a, auth_a):
        resp = client.get("/merchant/me", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"shop_domain", "plan", "billing_active", "install_status"})
        _assert_type(data["shop_domain"], str)
        _assert_type(data["plan"], str)
        _assert_type(data["billing_active"], bool)
        assert data["shop_domain"] == SHOP_A

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/merchant/me")
        assert resp.status_code in (401, 403)


class TestDashboardOverview:
    """GET /dashboard/overview — primary data load."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/dashboard/overview?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"summary", "top_products"})
        _assert_type(data["summary"], dict)
        _assert_type(data["top_products"], list)

    def test_summary_keys(self, client, merchant_a, auth_a):
        resp = client.get(f"/dashboard/overview?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        summary = data["summary"]
        # These keys are read by the dashboard — must be present even if zero
        for key in ("total_visitors", "hot_visitors", "warm_visitors", "cold_visitors", "avg_intent_score"):
            assert key in summary, f"summary missing key: {key}"

    def test_empty_state_is_safe(self, client, merchant_a, auth_a):
        """With no data, response must be valid — not null, not error."""
        resp = client.get(f"/dashboard/overview?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["top_products"], list)


class TestOrdersSummary:
    """GET /orders/summary — revenue hero on dashboard."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/summary?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"has_orders", "currency", "last_7d", "last_30d"})
        _assert_type(data["has_orders"], bool)
        _assert_type(data["currency"], str)
        _assert_type(data["last_7d"], dict)
        _assert_type(data["last_30d"], dict)

    def test_window_keys(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/summary?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        for window in ("last_7d", "last_30d"):
            _assert_keys(data[window], {"order_count", "total_revenue", "avg_order_value"}, window)

    def test_empty_state(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/summary?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        # Must not crash with no orders — just report zeros
        assert data["last_7d"]["order_count"] >= 0
        assert data["last_7d"]["total_revenue"] >= 0


class TestDailyRevenue:
    """GET /orders/daily-revenue — trend chart."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/daily-revenue?shop={SHOP_A}&days=7", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"points", "currency", "days"})
        _assert_type(data["points"], list)
        _assert_type(data["currency"], str)
        _assert_type(data["days"], int)

    def test_points_structure(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/daily-revenue?shop={SHOP_A}&days=7", cookies=auth_a)
        data = resp.json()
        # Should return exactly 7 points (zero-filled)
        assert len(data["points"]) == 7
        for point in data["points"]:
            _assert_keys(point, {"day", "revenue", "orders"}, "daily_revenue point")


class TestProductConversions:
    """GET /orders/product-conversions — per-product funnel.

    Born 2026-04-28 night after a missing comma in the CTE chain
    landed on prod and emitted 500s for hours, surfaced as
    "Couldn't load this card" on the Pro floor. The endpoint had
    zero test coverage. This contract test is the runtime layer of
    the 3-layer preventer (preflight regex + this test + manual
    inspection)."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(
            f"/orders/product-conversions?days=7&shop={SHOP_A}", cookies=auth_a
        )
        assert resp.status_code == 200, (
            f"product-conversions returned {resp.status_code} "
            f"with body: {resp.text[:200]}"
        )
        data = resp.json()
        _assert_keys(data, {"products", "days", "currency", "has_data"})
        _assert_type(data["products"], list)
        _assert_type(data["days"], int)
        _assert_type(data["currency"], str)
        _assert_type(data["has_data"], bool)

    def test_empty_state(self, client, merchant_a, auth_a):
        """Zero events / orders must not crash the SQL."""
        resp = client.get(
            f"/orders/product-conversions?days=7&shop={SHOP_A}", cookies=auth_a
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["products"], list)


class TestAnalyticsAlerts:
    """GET /analytics/alerts — alert section."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/analytics/alerts?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"alerts"})
        _assert_type(data["alerts"], list)

    def test_empty_state(self, client, merchant_a, auth_a):
        resp = client.get(f"/analytics/alerts?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        assert isinstance(data["alerts"], list)  # empty list, not null


class TestWeeklyTrend:
    """GET /analytics/weekly-trend — weekly chart."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/analytics/weekly-trend?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"trend"})
        _assert_type(data["trend"], list)


# ===========================================================================
# Pro foundations
# ===========================================================================

class TestAttributionSummary:
    """GET /attribution/summary/pro — attribution overview."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/attribution/summary/pro?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {
            "window_days", "generated_at", "orders_total", "orders_attributed",
            "orders_unattributed", "attribution_rate",
            "top_sources_first_touch", "top_sources_last_touch", "top_campaigns",
            "first_vs_last_match_rate",
        })

    def test_types(self, client, merchant_a, auth_a):
        resp = client.get(f"/attribution/summary/pro?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        _assert_type(data["orders_total"], int)
        _assert_type(data["attribution_rate"], (int, float))
        _assert_type(data["top_sources_first_touch"], list)
        _assert_type(data["top_campaigns"], list)

    def test_empty_state(self, client, merchant_a, auth_a):
        resp = client.get(f"/attribution/summary/pro?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        assert data["orders_total"] >= 0
        assert data["attribution_rate"] >= 0


class TestMonthlyCohortsLTV:
    """GET /pro/cohorts/monthly — LTV cohort analysis."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/monthly?shop={SHOP_A}&months=3", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"window_months", "customer_coverage", "cohorts", "overall"})
        _assert_type(data["customer_coverage"], dict)
        _assert_type(data["cohorts"], list)
        _assert_type(data["overall"], dict)

    def test_coverage_keys(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/monthly?shop={SHOP_A}&months=3", cookies=auth_a)
        data = resp.json()
        _assert_keys(data["customer_coverage"], {
            "total_orders", "identifiable_orders", "unidentifiable_orders", "coverage_rate",
        }, "customer_coverage")

    def test_overall_keys(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/monthly?shop={SHOP_A}&months=3", cookies=auth_a)
        data = resp.json()
        _assert_keys(data["overall"], {
            "total_customers", "repeat_customers", "repeat_rate",
            "avg_orders_per_customer", "avg_revenue_per_customer",
        }, "overall")

    def test_empty_state(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/monthly?shop={SHOP_A}&months=3", cookies=auth_a)
        data = resp.json()
        assert isinstance(data["cohorts"], list)
        assert data["overall"]["total_customers"] >= 0


class TestRevenueForecast:
    """GET /orders/forecast/pro — revenue forecast."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/forecast/pro?shop={SHOP_A}", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {
            "generated_at", "currency", "history",
            "forecast_7d", "forecast_30d", "trend",
            "confidence", "confidence_reason", "seasonality_available",
        })

    def test_history_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/orders/forecast/pro?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        history = data["history"]
        _assert_keys(history, {"days_available", "days_with_revenue", "daily_series", "total_revenue"})
        _assert_type(history["daily_series"], list)

    def test_insufficient_data_is_null_not_error(self, client, merchant_a, auth_a):
        """With no order history, forecast fields must be null — not crash."""
        resp = client.get(f"/orders/forecast/pro?shop={SHOP_A}", cookies=auth_a)
        data = resp.json()
        # Forecast may be null when insufficient data — that's correct
        if data["confidence"] is None:
            assert data["forecast_7d"] is None
            assert data["forecast_30d"] is None
            assert data["trend"] is None

    def test_auth_required(self, client, merchant_b, auth_b):
        """Lite merchant cannot access forecast."""
        resp = client.get(f"/orders/forecast/pro?shop={SHOP_B}", cookies=auth_b)
        assert resp.status_code == 403


class TestBehavioralCohorts:
    """GET /pro/cohorts/behavioral — behavioral LTV segmentation."""

    def test_response_shape(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/behavioral?shop={SHOP_A}&days=90", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"window_days", "generated_at", "data_coverage", "segments", "insights"})
        _assert_type(data["data_coverage"], dict)
        _assert_type(data["segments"], dict)
        _assert_type(data["insights"], list)

    def test_segments_structure(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/behavioral?shop={SHOP_A}&days=90", cookies=auth_a)
        data = resp.json()
        segments = data["segments"]
        _assert_keys(segments, {"by_engagement", "by_visit_pattern", "by_source"}, "segments")
        _assert_type(segments["by_engagement"], list)
        _assert_type(segments["by_visit_pattern"], list)
        _assert_type(segments["by_source"], list)

    def test_coverage_keys(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/behavioral?shop={SHOP_A}&days=90", cookies=auth_a)
        data = resp.json()
        _assert_keys(data["data_coverage"], {
            "total_customers", "segmentable_customers", "coverage_rate",
        }, "data_coverage")

    def test_empty_state(self, client, merchant_a, auth_a):
        resp = client.get(f"/pro/cohorts/behavioral?shop={SHOP_A}&days=90", cookies=auth_a)
        data = resp.json()
        assert data["data_coverage"]["total_customers"] >= 0
        assert len(data["insights"]) >= 1  # always has at least one insight

    def test_auth_required(self, client, merchant_b, auth_b):
        resp = client.get(f"/pro/cohorts/behavioral?shop={SHOP_B}&days=90", cookies=auth_b)
        assert resp.status_code == 403


# ===========================================================================
# Operator / AI self-management
# ===========================================================================

class TestOpsLLMBudget:
    """GET /ops/llm-budget — LLM cost control visibility."""

    def test_response_shape(self, client):
        headers = {"X-API-Key": _OP_KEY}
        resp = client.get("/ops/llm-budget", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {
            "date", "month", "global_calls_today", "global_max_per_day",
            "blocked_today", "monthly_cost_eur", "monthly_cap_eur",
            "monthly_remaining_eur", "monthly_cap_reached",
            "provider_429_state", "modules",
        })
        _assert_type(data["monthly_cap_reached"], bool)
        _assert_type(data["monthly_cost_eur"], (int, float))
        _assert_type(data["modules"], dict)

    def test_auth_required(self, client):
        resp = client.get("/ops/llm-budget")
        assert resp.status_code in (401, 403)


class TestOpsIncidents:
    """GET /ops/incidents — support incident visibility."""

    def test_response_shape(self, client):
        headers = {"X-API-Key": _OP_KEY}
        resp = client.get("/ops/incidents", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        _assert_keys(data, {"count", "incidents"})
        _assert_type(data["count"], int)
        _assert_type(data["incidents"], list)

    def test_empty_state(self, client):
        headers = {"X-API-Key": _OP_KEY}
        resp = client.get("/ops/incidents", headers=headers)
        data = resp.json()
        assert data["count"] >= 0
        assert isinstance(data["incidents"], list)


class TestSystemHealth:
    """GET /system/health — production health check."""

    def test_response_shape(self, client):
        resp = client.get("/system/health")
        assert resp.status_code in (200, 503)  # 503 if critical
        data = resp.json()
        _assert_keys(data, {"checked_at", "status", "subsystems"})
        _assert_type(data["status"], str)
        assert data["status"] in ("ok", "degraded", "critical")
        _assert_type(data["subsystems"], dict)

    def test_subsystem_keys(self, client):
        resp = client.get("/system/health")
        data = resp.json()
        subsystems = data["subsystems"]
        _assert_keys(subsystems, {"database", "redis", "workers", "event_ingestion"}, "subsystems")
        _assert_keys(subsystems["database"], {"status"}, "subsystems.database")

    def test_always_accessible(self, client):
        """Health endpoint must NEVER require auth."""
        resp = client.get("/system/health")
        assert resp.status_code in (200, 503)
