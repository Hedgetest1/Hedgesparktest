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
        assert resp.status_code == 200

    def test_roi_hero_unauth(self, client):
        resp = client.get("/pro/roi-hero")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 2. GET /pro/daily-narrative
    # ------------------------------------------------------------------

    def test_daily_narrative_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/daily-narrative", cookies=auth_a)
        assert resp.status_code == 200

    def test_daily_narrative_unauth(self, client):
        resp = client.get("/pro/daily-narrative")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 3. GET /pro/cac-ltv
    # ------------------------------------------------------------------

    def test_cac_ltv_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/cac-ltv", cookies=auth_a)
        assert resp.status_code == 200

    def test_cac_ltv_unauth(self, client):
        resp = client.get("/pro/cac-ltv")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 4. GET /pro/mta?model=last_touch
    # ------------------------------------------------------------------

    def test_mta_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/mta?model=last_touch", cookies=auth_a)
        assert resp.status_code == 200

    def test_mta_unauth(self, client):
        resp = client.get("/pro/mta?model=last_touch")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 5. GET /pro/visitor-journeys
    # ------------------------------------------------------------------

    def test_visitor_journeys_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/visitor-journeys", cookies=auth_a)
        assert resp.status_code == 200

    def test_visitor_journeys_unauth(self, client):
        resp = client.get("/pro/visitor-journeys")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 6. GET /pro/margin/snapshot
    # ------------------------------------------------------------------

    def test_margin_snapshot_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/margin/snapshot", cookies=auth_a)
        assert resp.status_code == 200

    def test_margin_snapshot_unauth(self, client):
        resp = client.get("/pro/margin/snapshot")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 7. GET /pro/nudge-dna
    # ------------------------------------------------------------------

    def test_nudge_dna_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/nudge-dna", cookies=auth_a)
        assert resp.status_code == 200

    def test_nudge_dna_unauth(self, client):
        resp = client.get("/pro/nudge-dna")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 8. GET /pro/abandoned-intent
    #    (router has no prefix — path is defined as /pro/abandoned-intent)
    # ------------------------------------------------------------------

    def test_abandoned_intent_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/abandoned-intent", cookies=auth_a)
        assert resp.status_code == 200

    def test_abandoned_intent_unauth(self, client):
        resp = client.get("/pro/abandoned-intent")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 9. GET /pro/revenue-autopsy
    #    (router has no prefix — path is defined as /pro/revenue-autopsy)
    # ------------------------------------------------------------------

    def test_revenue_autopsy_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/revenue-autopsy", cookies=auth_a)
        assert resp.status_code == 200

    def test_revenue_autopsy_unauth(self, client):
        resp = client.get("/pro/revenue-autopsy")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 10. GET /pro/cohorts/monthly
    #     Optional `months` param — default is 6, no 422 without it.
    # ------------------------------------------------------------------

    def test_cohorts_monthly_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/cohorts/monthly", cookies=auth_a)
        assert resp.status_code == 200

    def test_cohorts_monthly_unauth(self, client):
        resp = client.get("/pro/cohorts/monthly")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 11. GET /pro/revenue-at-risk — RARS score
    # ------------------------------------------------------------------

    def test_revenue_at_risk_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/revenue-at-risk", cookies=auth_a)
        assert resp.status_code == 200

    def test_revenue_at_risk_unauth(self, client):
        resp = client.get("/pro/revenue-at-risk")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 12. GET /pro/refund-losses — refund analytics
    # ------------------------------------------------------------------

    def test_refund_losses_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/refund-losses", cookies=auth_a)
        assert resp.status_code == 200

    def test_refund_losses_unauth(self, client):
        resp = client.get("/pro/refund-losses")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 13. GET /pro/risk-forecast — risk prediction
    # ------------------------------------------------------------------

    def test_risk_forecast_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/risk-forecast", cookies=auth_a)
        assert resp.status_code == 200

    def test_risk_forecast_unauth(self, client):
        resp = client.get("/pro/risk-forecast")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 14. GET /pro/price-sensitivity — price elasticity
    # ------------------------------------------------------------------

    def test_price_sensitivity_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/price-sensitivity", cookies=auth_a)
        assert resp.status_code == 200

    def test_price_sensitivity_unauth(self, client):
        resp = client.get("/pro/price-sensitivity")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 15. GET /pro/pnl — P&L statement
    # ------------------------------------------------------------------

    def test_pnl_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/pnl", cookies=auth_a)
        assert resp.status_code == 200

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
        assert resp.status_code == 200

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
        assert resp.status_code == 200

    def test_customer_churn_unauth(self, client):
        resp = client.get("/pro/customer-churn")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 18. GET /pro/nudges — active nudges list
    # ------------------------------------------------------------------

    def test_nudges_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/nudges", cookies=auth_a)
        assert resp.status_code == 200

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

    def test_rules_unauth(self, client):
        resp = client.get("/pro/rules")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 21. GET /pro/roi-report — ROI breakdown
    # ------------------------------------------------------------------

    def test_roi_report_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/roi-report", cookies=auth_a)
        assert resp.status_code == 200

    def test_roi_report_unauth(self, client):
        resp = client.get("/pro/roi-report")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 22. GET /pro/shares — share metrics
    # ------------------------------------------------------------------

    def test_shares_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/shares", cookies=auth_a)
        assert resp.status_code == 200

    def test_shares_unauth(self, client):
        resp = client.get("/pro/shares")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 23. GET /pro/proof-report — proof of value
    #     Router prefix is /pro/proof-report; path is "".
    # ------------------------------------------------------------------

    def test_proof_report_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/proof-report", cookies=auth_a)
        assert resp.status_code == 200

    def test_proof_report_unauth(self, client):
        resp = client.get("/pro/proof-report")
        assert resp.status_code in (401, 403)

    # ------------------------------------------------------------------
    # 24. GET /pro/instant-intelligence — instant insights
    # ------------------------------------------------------------------

    def test_instant_intelligence_200(self, client, merchant_a, auth_a):
        resp = client.get("/pro/instant-intelligence", cookies=auth_a)
        assert resp.status_code == 200

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
        assert resp.status_code == 200

    def test_heatmap_unauth(self, client):
        resp = client.get(
            "/pro/heatmap",
            params={"product_url": "/products/test-handle"},
        )
        assert resp.status_code in (401, 403)
