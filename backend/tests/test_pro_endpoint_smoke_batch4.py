"""
Smoke tests — batch 4 — 20 previously-untested endpoints.

Extends the coverage started in test_pro_endpoint_smoke.py (35 routes)
to 55 routes total. Same contract per endpoint:
  1. Authenticated Pro merchant → HTTP 200 + parseable response shape
  2. No cookie → HTTP 401 or 403

Every _200 test uses the shared `_assert_200_parseable` helper: asserts
a non-None dict OR list. Where the specific response shape is well-
known, we additionally assert key field presence. Where it's a bare
list/dict-any, we only assert non-emptiness of the contract (returns
something parseable, not 500).

Priority targeted: endpoints in the dashboard data flow that the
existing 35-endpoint smoke suite didn't cover — forecast/goals/benchmarks/
ads/analytics rollups.
"""
from __future__ import annotations

import pytest

from app.core.database import get_read_db
from app.main import app as fastapi_app


@pytest.fixture(autouse=True)
def _override_read_db(db):
    def _get_read_db_override():
        yield db
    fastapi_app.dependency_overrides[get_read_db] = _get_read_db_override
    yield
    fastapi_app.dependency_overrides.pop(get_read_db, None)


def _assert_200_parseable(resp) -> object:
    """Smoke contract: status 200 + body is a parseable dict OR list."""
    assert resp.status_code == 200, (
        f"expected 200, got {resp.status_code}: {resp.text[:200]}"
    )
    data = resp.json()
    assert data is not None, "response body must not be None"
    assert isinstance(data, (dict, list)), (
        f"expected dict or list, got {type(data).__name__}"
    )
    return data


def _assert_shape(resp, *required_keys: str) -> dict:
    data = _assert_200_parseable(resp)
    assert isinstance(data, dict), f"expected dict, got {type(data).__name__}"
    missing = [k for k in required_keys if k not in data]
    assert not missing, f"missing keys: {missing}. Got: {sorted(data.keys())[:15]}"
    return data


class TestProEndpointSmokeBatch4:
    """20 more revenue-adjacent endpoints with status+shape smokes."""

    # ─── /analytics/* rollups ─────────────────────────────────────────

    def test_analytics_alerts_pro_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/alerts/pro", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_analytics_alerts_pro_unauth(self, client):
        resp = client.get("/analytics/alerts/pro")
        assert resp.status_code in (401, 403)

    def test_analytics_live_opportunities_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/live-opportunities", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_analytics_live_opportunities_unauth(self, client):
        resp = client.get("/analytics/live-opportunities")
        assert resp.status_code in (401, 403)

    def test_analytics_sessions_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/sessions", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_analytics_sessions_unauth(self, client):
        resp = client.get("/analytics/sessions")
        assert resp.status_code in (401, 403)

    def test_analytics_top_pages_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/top-pages", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_analytics_top_pages_unauth(self, client):
        resp = client.get("/analytics/top-pages")
        assert resp.status_code in (401, 403)

    def test_analytics_visitor_scores_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/visitor-scores", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_analytics_visitor_scores_unauth(self, client):
        resp = client.get("/analytics/visitor-scores")
        assert resp.status_code in (401, 403)

    def test_analytics_weekly_trend_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/weekly-trend", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_analytics_weekly_trend_unauth(self, client):
        resp = client.get("/analytics/weekly-trend")
        assert resp.status_code in (401, 403)

    # ─── /intent/* ───────────────────────────────────────────────────

    def test_intent_products_top_200(self, client, merchant_a, auth_a):
        resp = client.get("/intent/products/top", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_intent_products_top_unauth(self, client):
        resp = client.get("/intent/products/top")
        assert resp.status_code in (401, 403)

    # ─── /pro/ads/* ──────────────────────────────────────────────────

    def test_ads_spend_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/ads/spend", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_ads_spend_unauth(self, client):
        resp = client.get("/pro/ads/spend")
        assert resp.status_code in (401, 403)

    # ─── /pro/benchmarks ─────────────────────────────────────────────

    def test_benchmarks_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/benchmarks", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_benchmarks_unauth(self, client):
        resp = client.get("/pro/benchmarks")
        assert resp.status_code in (401, 403)

    # ─── /pro/causal-lift ────────────────────────────────────────────

    def test_causal_lift_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/causal-lift", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_causal_lift_unauth(self, client):
        resp = client.get("/pro/causal-lift")
        assert resp.status_code in (401, 403)

    # ─── /pro/cohorts ────────────────────────────────────────────────

    def test_cohorts_ltv_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/cohorts/ltv", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_cohorts_ltv_unauth(self, client):
        resp = client.get("/pro/cohorts/ltv")
        assert resp.status_code in (401, 403)

    def test_cohorts_summary_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/cohorts/summary", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_cohorts_summary_unauth(self, client):
        resp = client.get("/pro/cohorts/summary")
        assert resp.status_code in (401, 403)

    # ─── /pro/forecast/* ─────────────────────────────────────────────

    def test_forecast_revenue_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/forecast/revenue", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_forecast_revenue_unauth(self, client):
        resp = client.get("/pro/forecast/revenue")
        assert resp.status_code in (401, 403)

    def test_forecast_churn_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/forecast/churn", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_forecast_churn_unauth(self, client):
        resp = client.get("/pro/forecast/churn")
        assert resp.status_code in (401, 403)

    # ─── /pro/goals ──────────────────────────────────────────────────

    def test_goals_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/goals", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_goals_unauth(self, client):
        resp = client.get("/pro/goals")
        assert resp.status_code in (401, 403)

    def test_goals_progress_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/goals/progress", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_goals_progress_unauth(self, client):
        resp = client.get("/pro/goals/progress")
        assert resp.status_code in (401, 403)

    # ─── /pro/lift ───────────────────────────────────────────────────

    def test_lift_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/lift", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_lift_unauth(self, client):
        resp = client.get("/pro/lift")
        assert resp.status_code in (401, 403)

    # ─── /pro/mta/compare ────────────────────────────────────────────

    def test_mta_compare_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/mta/compare", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_mta_compare_unauth(self, client):
        resp = client.get("/pro/mta/compare")
        assert resp.status_code in (401, 403)

    # ─── /scale/night-shift ──────────────────────────────────────────
    # Moved to Scale tier 2026-05-09 (founder partition directive — no
    # doppione Pro/Scale). merchant_a fixture is scale-tier (conftest)
    # so the 200-assertion still holds; gating is now require_scale_
    # session, but Scale satisfies it. Path-existence smoke kept here.

    def test_night_shift_latest_200(self, client, merchant_a, auth_a):
        resp = client.get("/scale/night-shift/latest", cookies=auth_a)
        _assert_200_parseable(resp)

    def test_night_shift_latest_unauth(self, client):
        resp = client.get("/scale/night-shift/latest")
        assert resp.status_code in (401, 403)

    # ─── /pro/rules/catalog ──────────────────────────────────────────

    def test_rules_catalog_200(self, client, merchant_a, auth_a):
        """Static catalog of allowed triggers/actions — used by the UI
        rule builder. Returns a dict with triggers/actions/ops."""
        resp = client.get("/pro/rules/catalog", cookies=auth_a)
        data = _assert_shape(resp, "triggers", "actions", "ops")
        assert isinstance(data["triggers"], list)
        assert isinstance(data["actions"], list)

    def test_rules_catalog_is_public_by_design(self, client):
        """Rule catalog is intentionally PUBLIC — the UI builder
        references it before the merchant is authenticated (splash/
        onboarding). If this ever starts requiring auth, the UI
        builder breaks silently. Locking in the decision."""
        resp = client.get("/pro/rules/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "triggers" in data and "actions" in data
