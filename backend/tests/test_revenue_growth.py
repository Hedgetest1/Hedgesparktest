"""
Tests for merchant scoring, revenue triggers, and event-driven emails.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TestMerchantScoring:

    def test_scoring_returns_valid_structure(self, db):
        """score_merchant returns a MerchantScore with all fields."""
        from app.models.merchant import Merchant
        from app.services.merchant_scoring import score_merchant

        m = Merchant(shop_domain="score-test.myshopify.com", contact_email="s@test.com")
        db.add(m)
        db.flush()

        score = score_merchant(db, "score-test.myshopify.com")
        assert score.shop_domain == "score-test.myshopify.com"
        assert 0 <= score.total_score <= 100
        assert score.tier in ("HIGH", "MEDIUM", "LOW")
        assert 0 <= score.traffic_score <= 100
        assert 0 <= score.revenue_score <= 100
        assert 0 <= score.opportunity_score <= 100
        assert 0 <= score.engagement_score <= 100

    def test_new_merchant_no_data_is_low(self, db):
        """Merchant with zero data scores LOW."""
        from app.models.merchant import Merchant
        from app.services.merchant_scoring import score_merchant

        m = Merchant(shop_domain="empty-score.myshopify.com")
        db.add(m)
        db.flush()

        score = score_merchant(db, "empty-score.myshopify.com")
        assert score.tier == "LOW"
        assert score.traffic_score == 0
        assert score.revenue_score == 0

    def test_tier_thresholds(self):
        """Verify tier boundary values."""
        from app.services.merchant_scoring import get_tier
        assert get_tier(0) == "LOW"
        assert get_tier(29) == "LOW"
        assert get_tier(30) == "MEDIUM"
        assert get_tier(69) == "MEDIUM"
        assert get_tier(70) == "HIGH"
        assert get_tier(100) == "HIGH"

    def test_score_all_returns_sorted(self, db):
        """score_all_merchants returns list sorted by score descending."""
        from app.models.merchant import Merchant
        from app.services.merchant_scoring import score_all_merchants

        for i in range(3):
            db.add(Merchant(shop_domain=f"batch-{i}.myshopify.com"))
        db.flush()

        scores = score_all_merchants(db, limit=10)
        assert isinstance(scores, list)
        # Verify sorting
        for i in range(len(scores) - 1):
            assert scores[i].total_score >= scores[i + 1].total_score

    def test_ops_scores_endpoint(self, client, db):
        """GET /ops/merchant-scores returns JSON array."""
        from app.models.merchant import Merchant
        m = Merchant(shop_domain="ops-score.myshopify.com")
        db.add(m)
        db.flush()

        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get("/ops/merchant-scores?limit=5", headers={"X-API-Key": _key})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_ops_single_score_endpoint(self, client, db):
        """GET /ops/merchant/{shop}/score returns single score."""
        from app.models.merchant import Merchant
        m = Merchant(shop_domain="single-score.myshopify.com")
        db.add(m)
        db.flush()

        _key = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
        resp = client.get(
            "/ops/merchant/single-score.myshopify.com/score",
            headers={"X-API-Key": _key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["shop_domain"] == "single-score.myshopify.com"
        assert "tier" in data


# Revenue triggers (trigger_traffic_spike, trigger_high_intent_leak,
# trigger_return_visitor_surge) removed 2026-04-22 per founder directive:
# dashboard content emails go through digest@ daily + weekly only, no
# event-driven alert channel. The full service, worker wiring, governance
# entries, and this TestRevenueTriggers class were deleted together.
