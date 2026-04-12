"""Tests for F6 — segment comparison."""
from __future__ import annotations

from unittest.mock import patch

from app.services.segment_compare import (
    _snapshot_from_segments_response,
    compare_two_products,
)


def test_snapshot_from_empty_response():
    snap = _snapshot_from_segments_response({}, "/products/p1")
    assert snap.product_url == "/products/p1"
    assert snap.hot_visitors == 0
    assert snap.warm_visitors == 0
    assert snap.cold_visitors == 0


def test_snapshot_extracts_fields():
    raw = {
        "hot":  {"visitor_count": 5, "estimated_revenue_window": 100, "cvr_estimate": 0.1},
        "warm": {"visitor_count": 20, "estimated_revenue_window": 200},
        "cold": {"visitor_count": 50, "estimated_revenue_window": 50},
    }
    snap = _snapshot_from_segments_response(raw, "/products/p2")
    assert snap.hot_visitors == 5
    assert snap.warm_visitors == 20
    assert snap.cold_visitors == 50
    assert snap.estimated_revenue_window == 350.0
    assert snap.hot_cvr_estimate == 0.1


def _fake_seg_resp(rev: float, hot: int = 0, warm: int = 0, cold: int = 0):
    return {
        "hot":  {"visitor_count": hot, "estimated_revenue_window": rev * 0.5},
        "warm": {"visitor_count": warm, "estimated_revenue_window": rev * 0.3},
        "cold": {"visitor_count": cold, "estimated_revenue_window": rev * 0.2},
    }


def test_compare_winner_a(db):
    """A has higher revenue → A is winner."""
    with patch(
        "app.services.audience_segments.segment_product_visitors",
        side_effect=[_fake_seg_resp(500, hot=10), _fake_seg_resp(200, hot=4)],
    ):
        result = compare_two_products(db, "test.myshopify.com", "/products/a", "/products/b")
    assert result["delta"]["winner"] == "A"
    assert result["delta"]["revenue_delta_eur"] > 0
    assert result["delta"]["loss_gap_eur"] > 0
    assert "Product A" in result["delta"]["narrative"]


def test_compare_winner_b(db):
    with patch(
        "app.services.audience_segments.segment_product_visitors",
        side_effect=[_fake_seg_resp(100), _fake_seg_resp(800)],
    ):
        result = compare_two_products(db, "test.myshopify.com", "/products/a", "/products/b")
    assert result["delta"]["winner"] == "B"
    assert result["delta"]["revenue_delta_eur"] < 0
    assert "Product A is the" in result["delta"]["narrative"] or "Product B is pulling" in result["delta"]["narrative"]


def test_compare_tie(db):
    with patch(
        "app.services.audience_segments.segment_product_visitors",
        side_effect=[_fake_seg_resp(300), _fake_seg_resp(303)],  # within 10 eur
    ):
        result = compare_two_products(db, "test.myshopify.com", "/products/a", "/products/b")
    assert result["delta"]["winner"] == "tie"


def test_compare_shape_has_required_fields(db):
    with patch(
        "app.services.audience_segments.segment_product_visitors",
        side_effect=[_fake_seg_resp(100), _fake_seg_resp(150)],
    ):
        result = compare_two_products(db, "test.myshopify.com", "/products/a", "/products/b")
    for key in ("shop_domain", "window_hours", "product_a", "product_b", "delta", "generated_at"):
        assert key in result
    for key in ("hot_visitors_delta", "revenue_delta_eur", "winner", "loss_gap_eur", "narrative"):
        assert key in result["delta"]
