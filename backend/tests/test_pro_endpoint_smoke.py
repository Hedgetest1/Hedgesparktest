"""
Smoke tests: auth enforcement + 200 contract on 10 revenue-critical Pro endpoints.

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
