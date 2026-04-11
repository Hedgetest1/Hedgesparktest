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


class TestRevenueTriggers:

    def test_no_triggers_for_new_store(self, db):
        """Store with no product metrics generates no triggers."""
        from app.services.revenue_triggers import _find_best_trigger

        trigger = _find_best_trigger(db, "nonexistent-store.myshopify.com")
        assert trigger is None

    def test_high_intent_leak_detected(self, db):
        """Product with cart_adds but 0 purchases triggers high_intent_leak."""
        from app.services.revenue_triggers import _find_best_trigger

        shop = "leak-test.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, install_status, contact_email) "
            "VALUES (:shop, 'active', 'leak@test.com')"
        ), {"shop": shop})

        # Create product metrics with carts but no purchases
        db.execute(text("""
            INSERT INTO product_metrics (shop_domain, product_url, views_24h, cart_conversions_24h, purchases_24h, revenue_24h)
            VALUES (:shop, '/products/leaky-product', 50, 5, 0, 0)
        """), {"shop": shop})

        db.execute(text(
            "INSERT INTO products (shop_domain, product_url, title, shopify_product_id) "
            "VALUES (:shop, '/products/leaky-product', 'Leaky Product', '123')"
        ), {"shop": shop})
        db.flush()

        trigger = _find_best_trigger(db, shop)
        assert trigger is not None
        assert trigger["type"] == "high_intent_leak"
        assert trigger["carts"] == 5
        assert "Leaky Product" in trigger["product_name"]
        assert trigger["weekly_loss"] > 0

    def test_traffic_spike_detected(self, db):
        """Product with views_1h >= 3x hourly average triggers traffic_spike."""
        from app.services.revenue_triggers import _find_best_trigger

        shop = "spike-test.myshopify.com"
        db.execute(text(
            "INSERT INTO merchants (shop_domain, install_status, contact_email) "
            "VALUES (:shop, 'active', 'spike@test.com')"
        ), {"shop": shop})

        # 10 views in 1h vs 24 views in 24h (hourly avg = 1, spike = 10x)
        db.execute(text("""
            INSERT INTO product_metrics (shop_domain, product_url, views_1h, views_24h, cart_conversions_24h, purchases_24h, revenue_24h)
            VALUES (:shop, '/products/hot-item', 10, 24, 0, 0, 0)
        """), {"shop": shop})
        db.flush()

        trigger = _find_best_trigger(db, shop)
        assert trigger is not None
        assert trigger["type"] == "traffic_spike"
        assert trigger["views_1h"] == 10

    def test_cooldown_prevents_spam(self):
        """Same merchant doesn't get two trigger emails within 48 hours."""
        from app.services.revenue_triggers import _is_on_cooldown, _set_cooldown
        from unittest.mock import MagicMock

        shop = "cooldown-test.myshopify.com"

        # Mock Redis with a simple dict store
        store = {}
        mock_rc = MagicMock()
        mock_rc.get = lambda k: store.get(k)
        mock_rc.set = lambda k, v, ex=None: store.update({k: v})

        with patch("app.core.redis_client._client", return_value=mock_rc):
            assert not _is_on_cooldown(shop)
            _set_cooldown(shop)
            assert _is_on_cooldown(shop)

    def test_trigger_email_passes_guardrails(self):
        """All trigger message templates pass response guardrails."""
        from app.services.response_guardrails import validate_response

        # Test a typical trigger message
        msg = (
            'Your product "Summer Dress" had 5 add-to-carts in the last 24 hours '
            'but 0 purchases. Something between cart and checkout is blocking the sale.\n\n'
            'At your store\'s average order value, this could mean lost revenue per week.\n\n'
            'Open your dashboard to see what HedgeSpark recommends.'
        )
        result = validate_response(msg, context="email")
        assert result.safe, f"Trigger message failed guardrails: {result.violations}"
