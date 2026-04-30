"""
Smoke tests: auth enforcement + 200 contract on 25 revenue-critical Pro endpoints.

For each endpoint we verify:
  1. Authenticated Pro merchant → HTTP 200 (data may be empty/warm — that's OK)
  2. No cookie       → HTTP 401 or 403 (never 200)

get_read_db override
--------------------
Several Pro endpoints (roi_hero, cac_ltv, mta) use `get_read_db` instead of
`get_db` for the ε1 read-replica path. conftest.py only overrides `get_db`,
so we patch `get_read_db` here in a session-scoped autouse fixture before the
first test runs. The override injects the same test transaction session so that
all reads stay inside the rollback boundary.
"""
from __future__ import annotations

import pytest

from app.core.database import get_read_db
from app.main import app as fastapi_app
from tests.conftest import SHOP_A


# ---------------------------------------------------------------------------
# Extend the TestClient's dependency overrides to also cover get_read_db.
# We do this via a module-level autouse fixture that piggybacks on conftest's
# `client` fixture (which itself depends on `db`).  We just need to make sure
# get_read_db is overridden before any request hits the read-replica path.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_read_db(db):
    """Override get_read_db to use the same test transaction as get_db."""

    def _get_read_db_override():
        yield db

    fastapi_app.dependency_overrides[get_read_db] = _get_read_db_override
    yield
    # conftest.client already calls dependency_overrides.clear() after each
    # test, but we remove our specific key defensively to avoid leaking state
    # if this fixture outlives the client fixture in edge cases.
    fastapi_app.dependency_overrides.pop(get_read_db, None)


def _assert_shape(resp, *required_keys: str) -> dict:
    """
    Status + shape assertion for smoke tests.

    Status-only assertions only catch crashes. Shape assertions catch
    DATA REGRESSIONS: a SELECT query that silently returns an empty dict,
    a response_model that drifts away from the runtime payload, a field
    that got renamed upstream but not here. The cost of one extra `assert`
    per endpoint is negligible; the catch rate is worth it.

    Usage:
        data = _assert_shape(resp, "roi_ratio", "headline_message")
    """
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    assert isinstance(data, dict), f"expected dict response, got {type(data).__name__}"
    missing = [k for k in required_keys if k not in data]
    assert not missing, (
        f"response missing required keys: {missing}. "
        f"Got keys: {sorted(data.keys())[:15]}"
    )
    return data


# ---------------------------------------------------------------------------
# Smoke test class
# ---------------------------------------------------------------------------


class TestProEndpointSmoke:
    """Smoke tests: auth enforcement + 200 contract on 10 revenue-critical Pro endpoints."""

    # ------------------------------------------------------------------
    # 1. GET /pro/roi-hero
    # ------------------------------------------------------------------

    def test_roi_hero_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/roi-hero", cookies=auth_a)
        _assert_shape(
            resp,
            "shop_domain", "roi_ratio", "headline_message",
            "total_saved_eur_30d", "breakdown", "plan_cost_eur_monthly",
        )

    def test_roi_hero_unauth(self, client):
        resp = client.get("/pro/roi-hero")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 2. GET /pro/daily-narrative
    # ------------------------------------------------------------------

    def test_daily_narrative_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/daily-narrative", cookies=auth_a)
        data = _assert_shape(
            resp, "shop_domain", "headline", "paragraphs", "stats", "generated_at",
        )
        assert isinstance(data["paragraphs"], list)

    def test_daily_narrative_unauth(self, client):
        resp = client.get("/pro/daily-narrative")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 3. GET /pro/cac-ltv
    # ------------------------------------------------------------------

    def test_cac_ltv_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/cac-ltv", cookies=auth_a)
        _assert_shape(
            resp, "shop_domain", "cac_eur", "avg_ltv_eur", "ratio",
            "status", "headline",
        )

    def test_cac_ltv_unauth(self, client):
        resp = client.get("/pro/cac-ltv")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 4. GET /pro/mta?model=last_touch
    # ------------------------------------------------------------------

    def test_mta_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/mta?model=last_touch", cookies=auth_a)
        _assert_shape(resp, "shop_domain", "sources", "total_revenue_eur")

    def test_mta_unauth(self, client):
        resp = client.get("/pro/mta?model=last_touch")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 5. GET /pro/visitor-journeys
    # ------------------------------------------------------------------

    def test_visitor_journeys_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/visitor-journeys", cookies=auth_a)
        data = _assert_shape(
            resp, "shop_domain", "journeys", "total_found", "window_days",
        )
        assert isinstance(data["journeys"], list)

    def test_visitor_journeys_unauth(self, client):
        resp = client.get("/pro/visitor-journeys")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 6. GET /pro/margin/snapshot
    # ------------------------------------------------------------------

    def test_margin_snapshot_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/margin/snapshot", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_margin_snapshot_unauth(self, client):
        resp = client.get("/pro/margin/snapshot")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 7. GET /pro/nudge-dna
    # ------------------------------------------------------------------

    def test_nudge_dna_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/nudge-dna", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_nudge_dna_unauth(self, client):
        resp = client.get("/pro/nudge-dna")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 8. GET /pro/abandoned-intent
    #    (router has no prefix — path is defined as /pro/abandoned-intent)
    # ------------------------------------------------------------------

    def test_abandoned_intent_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/abandoned-intent", cookies=auth_a)
        _assert_shape(resp, "shop_domain", "products", "session_insights", "headline")

    def test_abandoned_intent_unauth(self, client):
        resp = client.get("/pro/abandoned-intent")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 9. GET /pro/revenue-autopsy
    #    (router has no prefix — path is defined as /pro/revenue-autopsy)
    # ------------------------------------------------------------------

    def test_revenue_autopsy_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/revenue-autopsy", cookies=auth_a)
        _assert_shape(resp, "shop_domain", "products", "summary", "headline")

    def test_revenue_autopsy_unauth(self, client):
        resp = client.get("/pro/revenue-autopsy")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 10. GET /pro/cohorts/monthly
    #     Optional `months` param — default is 6, no 422 without it.
    # ------------------------------------------------------------------

    def test_cohorts_monthly_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/cohorts/monthly", cookies=auth_a)
        _assert_shape(resp, "cohorts", "overall", "window_months")

    def test_cohorts_monthly_unauth(self, client):
        resp = client.get("/pro/cohorts/monthly")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 11. GET /analytics/revenue-at-risk — RARS score
    # ------------------------------------------------------------------

    def test_revenue_at_risk_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/revenue-at-risk", cookies=auth_a)
        data = _assert_shape(
            resp, "shop_domain", "total_at_risk_eur",
            "prevented_eur_this_month", "components",
        )
        assert isinstance(data["components"], list)

    def test_revenue_at_risk_unauth(self, client):
        resp = client.get("/analytics/revenue-at-risk")
        assert resp.status_code in (401, 403)

    def test_revenue_at_risk_legacy_alias_200(self, client, merchant_a, auth_a):
        """The deprecated /pro/revenue-at-risk path still resolves to the
        same handler for any client still on the old URL."""
        resp = client.get("/pro/revenue-at-risk", cookies=auth_a)
        _assert_shape(
            resp, "shop_domain", "total_at_risk_eur",
            "prevented_eur_this_month", "components",
        )

    # ------------------------------------------------------------------
    # 12. GET /pro/refund-losses — refund analytics
    # ------------------------------------------------------------------

    def test_refund_losses_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/refund-losses", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_refund_losses_unauth(self, client):
        resp = client.get("/pro/refund-losses")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 13. GET /pro/risk-forecast — risk prediction
    # ------------------------------------------------------------------

    def test_risk_forecast_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/risk-forecast", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_risk_forecast_unauth(self, client):
        resp = client.get("/pro/risk-forecast")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 14. GET /pro/price-sensitivity — price elasticity
    # ------------------------------------------------------------------

    def test_price_sensitivity_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/price-sensitivity", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_price_sensitivity_unauth(self, client):
        resp = client.get("/pro/price-sensitivity")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 15. GET /pro/pnl — P&L statement
    # ------------------------------------------------------------------

    def test_pnl_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/pnl", cookies=auth_a)
        _assert_shape(
            resp, "window_days", "currency", "gross_revenue",
            "net_profit", "net_margin_pct", "verdict",
        )

    def test_pnl_unauth(self, client):
        resp = client.get("/pro/pnl")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 16. GET /pro/segments — customer segments
    #     Requires product_url query param (no default → must supply one).
    # ------------------------------------------------------------------

    def test_segments_200(self, client, merchant_a, auth_a):
        resp = client.get(
            "/pro/segments",
            params={"product_url": "/products/test-handle"},
            cookies=auth_a,
        )
        _assert_shape(resp, "shop_domain")

    def test_segments_unauth(self, client):
        resp = client.get(
            "/pro/segments",
            params={"product_url": "/products/test-handle"},
        )
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 17. GET /pro/customer-churn — churn predictions
    # ------------------------------------------------------------------

    def test_customer_churn_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/customer-churn", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_customer_churn_unauth(self, client):
        resp = client.get("/pro/customer-churn")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 18. GET /pro/nudges — active nudges list
    # ------------------------------------------------------------------

    def test_nudges_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/nudges", cookies=auth_a)
        assert resp.status_code == 200
        # Shape may be list or dict depending on router; assert non-empty
        # structure either way — the smoke contract is "endpoint returns
        # something parseable".
        data = resp.json()
        assert data is not None
        assert isinstance(data, (list, dict))

    def test_nudges_unauth(self, client):
        resp = client.get("/pro/nudges")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 19. GET /pro/trust/contracts — trust contracts
    #     Router prefix is /pro/trust; path is /contracts.
    # ------------------------------------------------------------------

    def test_trust_contracts_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/trust/contracts", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        # Trust contracts returns a list of contract dicts
        assert isinstance(data, list) or isinstance(data, dict)

    def test_trust_contracts_unauth(self, client):
        resp = client.get("/pro/trust/contracts")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 20. GET /pro/rules — merchant rules
    #     Router prefix is /pro/rules; path is "" (root of prefix).
    # ------------------------------------------------------------------

    def test_rules_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/rules", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_rules_unauth(self, client):
        resp = client.get("/pro/rules")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 21. GET /pro/roi-report — ROI breakdown
    # ------------------------------------------------------------------

    def test_roi_report_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/roi-report", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_roi_report_unauth(self, client):
        resp = client.get("/pro/roi-report")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 22. GET /pro/shares — share metrics
    # ------------------------------------------------------------------

    def test_shares_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/shares", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_shares_unauth(self, client):
        resp = client.get("/pro/shares")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 23. GET /pro/proof-report — proof of value
    #     Router prefix is /pro/proof-report; path is "".
    # ------------------------------------------------------------------

    def test_proof_report_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/proof-report", cookies=auth_a)
        _assert_shape(resp, "has_proof", "headline", "generated_at")

    def test_proof_report_unauth(self, client):
        resp = client.get("/pro/proof-report")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 24. GET /pro/instant-intelligence — instant insights
    # ------------------------------------------------------------------

    def test_instant_intelligence_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/instant-intelligence", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_instant_intelligence_unauth(self, client):
        resp = client.get("/pro/instant-intelligence")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 25. GET /pro/heatmap — page heatmap data
    #     Router prefix is /pro/heatmap; path is "".
    #     product_url is required (no default).
    # ------------------------------------------------------------------

    def test_heatmap_200(self, client, merchant_a, auth_a):
        resp = client.get(
            "/pro/heatmap",
            params={"product_url": "/products/test-handle"},
            cookies=auth_a,
        )
        _assert_shape(resp, "product_url", "scroll", "window_hours")

    def test_heatmap_unauth(self, client):
        resp = client.get(
            "/pro/heatmap",
            params={"product_url": "/products/test-handle"},
        )
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 25b. GET /pro/heatmap/spatial — 10×10 click/move grid (Lite slot 13)
    # ------------------------------------------------------------------

    def test_heatmap_spatial_click_200(self, client, merchant_a, auth_a):
        resp = client.get(
            "/pro/heatmap/spatial",
            params={
                "product_url": "/products/test-handle",
                "event_type": "click",
            },
            cookies=auth_a,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_type"] == "click"
        assert body["grid_size"] == 10
        assert "buckets" in body
        assert "total_events" in body

    def test_heatmap_spatial_move_200(self, client, merchant_a, auth_a):
        resp = client.get(
            "/pro/heatmap/spatial",
            params={
                "product_url": "/products/test-handle",
                "event_type": "move",
            },
            cookies=auth_a,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_type"] == "mousemove"

    def test_heatmap_spatial_unknown_type_returns_empty(self, client, merchant_a, auth_a):
        resp = client.get(
            "/pro/heatmap/spatial",
            params={
                "product_url": "/products/test-handle",
                "event_type": "bogus",
            },
            cookies=auth_a,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_events"] == 0
        assert body["buckets"] == []

    def test_heatmap_spatial_unauth(self, client):
        resp = client.get(
            "/pro/heatmap/spatial",
            params={"product_url": "/products/test-handle", "event_type": "click"},
        )
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 26. GET /analytics/clicks — click insights
    #     Router prefix is /analytics; path is /clicks.
    # ------------------------------------------------------------------

    def test_click_insights_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/clicks", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_click_insights_unauth(self, client):
        resp = client.get("/analytics/clicks")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 27. GET /conversion-probability/top — top conversion candidates
    #     Router prefix is /conversion-probability; path is /top.
    # ------------------------------------------------------------------

    def test_conversion_probability_top_200(self, client, merchant_a, auth_a):
        resp = client.get("/conversion-probability/top", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_conversion_probability_top_unauth(self, client):
        resp = client.get("/conversion-probability/top")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 28. GET /analytics/funnel — conversion funnel
    #     Router prefix is /analytics; path is /funnel.
    # ------------------------------------------------------------------

    def test_funnel_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/funnel", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_funnel_unauth(self, client):
        resp = client.get("/analytics/funnel")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 29. GET /analytics/source-quality — traffic source quality
    #     Router prefix is /analytics; path is /source-quality.
    # ------------------------------------------------------------------

    def test_source_quality_200(self, client, merchant_a, auth_a):
        resp = client.get("/analytics/source-quality", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_source_quality_unauth(self, client):
        resp = client.get("/analytics/source-quality")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 30. GET /products/store-intelligence — store-level intelligence
    #     Router prefix is /products; path is /store-intelligence.
    # ------------------------------------------------------------------

    def test_store_intelligence_200(self, client, merchant_a, auth_a):
        resp = client.get("/products/store-intelligence", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_store_intelligence_unauth(self, client):
        resp = client.get("/products/store-intelligence")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 31. GET /pro/kg/stats — knowledge graph stats
    #     No prefix; path is /pro/kg/stats.
    # ------------------------------------------------------------------

    def test_knowledge_graph_stats_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/kg/stats", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_knowledge_graph_stats_unauth(self, client):
        resp = client.get("/pro/kg/stats")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 32. GET /pro/anomalies/fusion — anomaly fusion
    #     No prefix; path is /pro/anomalies/fusion.
    # ------------------------------------------------------------------

    def test_anomaly_fusion_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/anomalies/fusion", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_anomaly_fusion_unauth(self, client):
        resp = client.get("/pro/anomalies/fusion")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 33. GET /pro/causal/explain — causal explainer
    #     No prefix; path is /pro/causal/explain.
    # ------------------------------------------------------------------

    def test_causal_explainer_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/causal/explain", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_causal_explainer_unauth(self, client):
        resp = client.get("/pro/causal/explain")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 34. GET /pro/revenue-genome — revenue genome breakdown
    #     No prefix; path is /pro/revenue-genome.
    # ------------------------------------------------------------------

    def test_revenue_genome_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/revenue-genome", cookies=auth_a)
        _assert_shape(resp, "shop_domain")

    def test_revenue_genome_unauth(self, client):
        resp = client.get("/pro/revenue-genome")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 35. GET /pro/playbook/{signal_type} — peer playbook
    #     No prefix; path is /pro/playbook/{signal_type}.
    #     Use a safe signal_type that won't 422.
    # ------------------------------------------------------------------

    def test_playbook_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/playbook/cart_abandon", cookies=auth_a)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_playbook_unauth(self, client):
        resp = client.get("/pro/playbook/cart_abandon")
        assert resp.status_code in (401, 403)
