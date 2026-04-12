"""
Tests for Revenue Genome — the unreachable feature.
Tests the compute engine, scoring helpers, API endpoint, and caching.
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import text

from tests.conftest import SHOP_A, auth_cookies

from app.services.revenue_genome import (
    _score,
    _gene,
    compute_revenue_genome,
)


# ═══════════════════════════════════════════════════════════════════
# Unit tests — scoring helpers
# ═══════════════════════════════════════════════════════════════════

class TestScoreHelper:
    def test_score_at_low_bound(self):
        assert _score(10, 10, 100) == 0

    def test_score_at_high_bound(self):
        assert _score(100, 10, 100) == 100

    def test_score_midpoint(self):
        assert _score(55, 10, 100) == 50

    def test_score_below_low_clamps_to_zero(self):
        assert _score(0, 10, 100) == 0

    def test_score_above_high_clamps_to_100(self):
        assert _score(200, 10, 100) == 100

    def test_score_equal_bounds_returns_50(self):
        assert _score(50, 50, 50) == 50

    def test_score_inverted_bounds_returns_50(self):
        assert _score(50, 100, 10) == 50


class TestGeneHelper:
    def test_gene_strong(self):
        g = _gene("test", 75, 42, "units", "looks good", "keep going")
        assert g["status"] == "strong"
        assert g["name"] == "test"
        assert g["score"] == 75
        assert g["value"] == 42

    def test_gene_moderate(self):
        g = _gene("test", 50, 42, "units", "ok", "improve")
        assert g["status"] == "moderate"

    def test_gene_weak(self):
        g = _gene("test", 20, 42, "units", "bad", "fix it")
        assert g["status"] == "weak"

    def test_gene_boundary_70_is_strong(self):
        assert _gene("t", 70, 0, "", "", "")["status"] == "strong"

    def test_gene_boundary_40_is_moderate(self):
        assert _gene("t", 40, 0, "", "", "")["status"] == "moderate"

    def test_gene_boundary_39_is_weak(self):
        assert _gene("t", 39, 0, "", "", "")["status"] == "weak"


# ═══════════════════════════════════════════════════════════════════
# Integration tests — full compute pipeline
# ═══════════════════════════════════════════════════════════════════

def _seed_events(db, shop, n_views=100, n_carts=20, n_purchases=5):
    """Insert minimal events to exercise all genome clusters."""
    now_ms = int(time.time() * 1000)
    for i in range(n_views):
        db.execute(text("""
            INSERT INTO events (shop_domain, visitor_id, event_type, timestamp, url, device_type, source_type)
            VALUES (:shop, :vid, 'product_view', :ts, 'https://shop.com/p', :device, :src)
        """), {
            "shop": shop,
            "vid": f"v{i % 50}",
            "ts": now_ms - i * 60000,
            "device": "mobile" if i % 3 == 0 else "desktop",
            "src": ["paid", "organic", "direct"][i % 3],
        })
    for i in range(n_carts):
        db.execute(text("""
            INSERT INTO events (shop_domain, visitor_id, event_type, timestamp, url)
            VALUES (:shop, :vid, 'add_to_cart', :ts, 'https://shop.com/p')
        """), {"shop": shop, "vid": f"v{i}", "ts": now_ms - i * 60000})
    for i in range(n_purchases):
        db.execute(text("""
            INSERT INTO events (shop_domain, visitor_id, event_type, timestamp, url)
            VALUES (:shop, :vid, 'purchase', :ts, 'https://shop.com/p')
        """), {"shop": shop, "vid": f"v{i}", "ts": now_ms - i * 60000})
    db.flush()


def _seed_product_metrics(db, shop, n_products=10):
    """Seed product_metrics for Product Genome cluster."""
    for i in range(n_products):
        db.execute(text("""
            INSERT INTO product_metrics (shop_domain, product_url, views_7d, purchases_7d)
            VALUES (:shop, :url, :views, :purchases)
        """), {
            "shop": shop,
            "url": f"https://shop.com/products/product-{i}",
            "views": 50 if i == 0 else 10,
            "purchases": 0 if i == 0 else max(1, i % 3),
        })
    db.flush()


def _seed_orders(db, shop, n_orders=20):
    """Seed shop_orders for Customer + Risk genome clusters."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(n_orders):
        db.execute(text("""
            INSERT INTO shop_orders (shop_domain, shopify_order_id, customer_email, total_price, created_at)
            VALUES (:shop, :oid, :email, :price, :created)
        """), {
            "shop": shop,
            "oid": str(10000 + i),
            "email": f"c{i % 5}@test.com",
            "price": 50 + (i * 7 % 30),
            "created": now - timedelta(days=i * 3),
        })
    db.flush()


@patch("app.core.redis_client._client", return_value=None)
class TestComputeRevenueGenome:
    """Full pipeline tests — exercises SQL queries against real DB."""

    def test_returns_all_clusters(self, _redis, db, merchant_a):
        _seed_events(db, SHOP_A)
        _seed_product_metrics(db, SHOP_A)
        _seed_orders(db, SHOP_A)

        result = compute_revenue_genome(db, SHOP_A)

        assert result["shop_domain"] == SHOP_A
        assert "overall_score" in result
        assert "archetype" in result
        assert "gene_clusters" in result
        clusters = result["gene_clusters"]
        assert set(clusters.keys()) == {"traffic", "conversion", "product", "customer", "intervention", "risk"}

    def test_overall_score_is_average(self, _redis, db, merchant_a):
        _seed_events(db, SHOP_A)
        _seed_product_metrics(db, SHOP_A)
        _seed_orders(db, SHOP_A)

        result = compute_revenue_genome(db, SHOP_A)

        all_scores = []
        for cluster in result["gene_clusters"].values():
            for gene in cluster.get("genes", []):
                all_scores.append(gene["score"])
        if all_scores:
            expected = round(sum(all_scores) / len(all_scores))
            assert result["overall_score"] == expected
        else:
            assert result["overall_score"] == 0

    def test_priority_actions_from_weakest(self, _redis, db, merchant_a):
        _seed_events(db, SHOP_A)
        _seed_product_metrics(db, SHOP_A)
        _seed_orders(db, SHOP_A)

        result = compute_revenue_genome(db, SHOP_A)

        actions = result["priority_actions"]
        # All actions must have score < 60
        for a in actions:
            assert a["score"] < 60
        # At most 3
        assert len(actions) <= 3

    def test_archetype_classification(self, _redis, db, merchant_a):
        result = compute_revenue_genome(db, SHOP_A)
        assert result["archetype"] in {"Revenue Machine", "Growth Ready", "Emerging", "Early Stage"}

    def test_empty_shop_graceful(self, _redis, db, merchant_a):
        """Shop with zero data should not crash — returns insufficient_data clusters."""
        result = compute_revenue_genome(db, SHOP_A)
        assert result["shop_domain"] == SHOP_A
        assert isinstance(result["overall_score"], int)

    def test_gene_counts_consistent(self, _redis, db, merchant_a):
        _seed_events(db, SHOP_A)
        _seed_product_metrics(db, SHOP_A)
        _seed_orders(db, SHOP_A)

        result = compute_revenue_genome(db, SHOP_A)

        all_scores = []
        for cluster in result["gene_clusters"].values():
            for gene in cluster.get("genes", []):
                all_scores.append(gene["score"])

        assert result["total_genes"] == len(all_scores)
        assert result["strong_genes"] == len([s for s in all_scores if s >= 70])
        assert result["weak_genes"] == len([s for s in all_scores if s < 40])


# ═══════════════════════════════════════════════════════════════════
# Caching tests
# ═══════════════════════════════════════════════════════════════════

class TestRevenueGenomeCache:
    def test_returns_cached_result(self, db, merchant_a):
        cached = json.dumps({"shop_domain": SHOP_A, "cached": True})
        mock_redis = MagicMock()
        mock_redis.get.return_value = cached

        with patch("app.core.redis_client._client", return_value=mock_redis):
            result = compute_revenue_genome(db, SHOP_A)

        assert result["cached"] is True
        mock_redis.get.assert_called_once()

    def test_writes_to_cache_on_miss(self, db, merchant_a):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        with patch("app.core.redis_client._client", return_value=mock_redis):
            compute_revenue_genome(db, SHOP_A)

        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args
        assert args[0][1] == 6 * 3600  # 6h TTL


# ═══════════════════════════════════════════════════════════════════
# API endpoint test
# ═══════════════════════════════════════════════════════════════════

class TestRevenueGenomeEndpoint:
    def test_pro_user_gets_genome(self, client, db, merchant_a):
        _seed_events(db, SHOP_A)
        cookies = auth_cookies(SHOP_A)

        with patch("app.core.redis_client._client", return_value=None):
            resp = client.get("/pro/revenue-genome", cookies=cookies)

        assert resp.status_code == 200
        data = resp.json()
        assert data["shop_domain"] == SHOP_A
        assert "gene_clusters" in data

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/pro/revenue-genome")
        assert resp.status_code in (401, 403, 307)
