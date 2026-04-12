"""Tests for A3 — merchant weekly digest USES killer features.

Locks the contract: assemble_digest queries the new RARS / refund_loss /
benchmarks / goals services, format_digest renders them when present,
and gracefully suppresses each section when empty (no fake numbers).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import weekly_digest as wd
from app.services.digest_formatter import format_digest


def _base_digest_stub() -> dict:
    """Minimal digest dict that the formatter will accept."""
    return {
        "shop_domain": "test-shop.myshopify.com",
        "generated_at": "2026-04-11T00:00:00Z",
        "period_start": "Apr 04",
        "period_end": "Apr 11, 2026",
        "currency": "EUR",
        "this_week": {"revenue": 1000.0, "order_count": 10, "aov": 100.0},
        "last_week": {"revenue": 800.0, "order_count": 8, "aov": 100.0},
        "revenue_delta_pct": 25.0,
        "unique_visitors": 100,
        "conversion_rate": 10.0,
        "data_confidence": "solid",
        "top_products": [],
        "insight": None,
        "recommendation": None,
        "revenue_at_risk": {"opportunities": []},
        "whats_working": None,
        "proof": {},
        "proof_report": {},
        "merchant_plan": "pro",
        "sip_insights": [],
    }


# ---- Wrappers (the safe getters in weekly_digest) ----

def test_safe_get_rars_returns_none_on_zero():
    db = MagicMock()
    with patch("app.services.revenue_at_risk.get_revenue_at_risk",
               return_value={"total_at_risk_eur": 0, "components": []}):
        assert wd._safe_get_rars(db, "x.myshopify.com") is None


def test_safe_get_rars_returns_report_when_populated():
    db = MagicMock()
    with patch("app.services.revenue_at_risk.get_revenue_at_risk",
               return_value={
                   "total_at_risk_eur": 1500.0,
                   "headline": "€1500 at risk",
                   "components": [{"source": "abandoned_high_intent", "loss_eur": 800}],
                   "_prevent_evidence": "internal_debug",
               }):
        result = wd._safe_get_rars(db, "x.myshopify.com")
    assert result is not None
    assert result["total_at_risk_eur"] == 1500.0
    # Internal debug field stripped
    assert "_prevent_evidence" not in result


def test_safe_get_rars_returns_none_on_exception():
    db = MagicMock()
    with patch("app.services.revenue_at_risk.get_revenue_at_risk", side_effect=RuntimeError("boom")):
        assert wd._safe_get_rars(db, "x.myshopify.com") is None


def test_safe_get_benchmarks_enforces_k_anonymity_floor():
    db = MagicMock()
    with patch("app.services.benchmarks.get_merchant_benchmark_report",
               return_value={"peer_count": 5, "band": "small"}):
        assert wd._safe_get_benchmarks(db, "x.myshopify.com") is None


def test_safe_get_benchmarks_returns_when_above_floor():
    db = MagicMock()
    with patch("app.services.benchmarks.get_merchant_benchmark_report",
               return_value={"peer_count": 25, "band": "small", "total_recovery_potential_eur": 800}):
        result = wd._safe_get_benchmarks(db, "x.myshopify.com")
    assert result is not None
    assert result["peer_count"] == 25


def test_safe_get_refund_loss_returns_none_when_zero_products():
    db = MagicMock()
    with patch("app.services.refund_loss.get_refund_loss_report",
               return_value={"product_count": 0, "products": []}):
        assert wd._safe_get_refund_loss(db, "x.myshopify.com") is None


def test_safe_get_goal_progress_returns_none_when_no_goals():
    db = MagicMock()
    with patch("app.services.goals.compute_goal_progress", return_value=[]):
        assert wd._safe_get_goal_progress(db, "x.myshopify.com") is None


def test_safe_get_goal_progress_normalizes_dataclass():
    db = MagicMock()
    fake_goal = MagicMock()
    fake_goal.metric = "monthly_revenue"
    fake_goal.target_value = 50000
    fake_goal.current_value = 25000
    fake_goal.projected_value = 45000
    fake_goal.status = "at_risk"
    with patch("app.services.goals.compute_goal_progress", return_value=[fake_goal]):
        result = wd._safe_get_goal_progress(db, "x.myshopify.com")
    assert result is not None
    assert len(result) == 1
    assert result[0]["metric"] == "monthly_revenue"
    assert result[0]["progress_pct"] == 50.0
    assert result[0]["status"] == "at_risk"


# ---- Formatter rendering ----

def test_formatter_renders_rars_hero_when_present():
    digest = _base_digest_stub()
    digest["rars_hero"] = {
        "total_at_risk_eur": 1840.0,
        "prevented_eur_this_month": 640.0,
        "headline": "€1840 at risk",
        "components": [],
    }
    html, plain = format_digest(digest)
    assert "REVENUE AT RISK RIGHT NOW" in plain
    assert "1,840" in plain
    assert "640" in plain
    assert "Revenue at Risk" in html
    assert "1,840" in html


def test_formatter_omits_rars_hero_when_absent():
    digest = _base_digest_stub()
    # No rars_hero key
    html, plain = format_digest(digest)
    assert "REVENUE AT RISK RIGHT NOW" not in plain
    assert "Revenue at Risk" not in html


def test_formatter_renders_goal_progress_with_status_badges():
    digest = _base_digest_stub()
    digest["goal_progress"] = [
        {"metric": "monthly_revenue", "target_value": 50000, "current_value": 25000,
         "projected_value": 45000, "status": "at_risk", "progress_pct": 50.0},
    ]
    html, plain = format_digest(digest)
    assert "YOUR MONTHLY TARGETS" in plain
    assert "Monthly Revenue" in plain
    assert "at risk" in plain.lower()
    assert "Your Monthly Targets" in html


def test_formatter_renders_product_decline():
    digest = _base_digest_stub()
    digest["product_decline"] = {
        "total_loss_eur_per_month": 420.0,
        "product_count": 2,
        "products": [
            {"product_title": "Ceramic Mug", "loss_eur": 280.0},
            {"product_title": "Silk Pillowcase", "loss_eur": 140.0},
        ],
    }
    html, plain = format_digest(digest)
    assert "PRODUCTS LOSING MOMENTUM" in plain
    assert "Ceramic Mug" in plain
    assert "420" in plain
    assert "Products Losing You Money" in html


def test_formatter_renders_peer_benchmarks():
    digest = _base_digest_stub()
    digest["peer_benchmarks"] = {
        "peer_count": 25,
        "band": "Beauty SMB",
        "total_recovery_potential_eur": 720.0,
    }
    html, plain = format_digest(digest)
    assert "YOU vs SIMILAR SHOPS" in plain
    assert "Beauty SMB" in plain
    assert "720" in plain
    assert "You vs. Similar Shops" in html


def test_formatter_renders_risk_forecast_only_when_status_ok():
    digest = _base_digest_stub()
    digest["rars_forecast"] = {
        "status": "ok",
        "direction": "rising",
        "forecast_7d_eur": 2200.0,
        "week_delta_pct": 18.0,
    }
    html, plain = format_digest(digest)
    assert "NEXT WEEK FORECAST" in plain
    assert "rising" in plain
    assert "2,200" in plain


def test_formatter_omits_forecast_when_insufficient_history():
    digest = _base_digest_stub()
    digest["rars_forecast"] = {"status": "insufficient_history"}
    html, plain = format_digest(digest)
    assert "NEXT WEEK FORECAST" not in plain


def test_formatter_all_killer_sections_together():
    """Render all 5 killer sections in one digest. Verifies they
    don't collide with the legacy sections."""
    digest = _base_digest_stub()
    digest["rars_hero"] = {"total_at_risk_eur": 1500, "prevented_eur_this_month": 400, "headline": "..."}
    digest["rars_forecast"] = {"status": "ok", "direction": "stable", "forecast_7d_eur": 1500, "week_delta_pct": 0}
    digest["peer_benchmarks"] = {"peer_count": 18, "band": "Beauty SMB", "total_recovery_potential_eur": 600}
    digest["product_decline"] = {
        "total_loss_eur_per_month": 200,
        "product_count": 1,
        "products": [{"product_title": "Mug", "loss_eur": 200}],
    }
    digest["goal_progress"] = [
        {"metric": "monthly_revenue", "target_value": 50000, "current_value": 30000,
         "projected_value": 48000, "status": "on_track", "progress_pct": 60.0},
    ]
    html, plain = format_digest(digest)

    # All 5 sections present in plain
    for marker in [
        "REVENUE AT RISK RIGHT NOW",
        "NEXT WEEK FORECAST",
        "YOU vs SIMILAR SHOPS",
        "PRODUCTS LOSING MOMENTUM",
        "YOUR MONTHLY TARGETS",
    ]:
        assert marker in plain, f"missing {marker}"

    # All 5 sections present in HTML
    for marker in [
        "Revenue at Risk",
        "Next Week Forecast",
        "You vs. Similar Shops",
        "Products Losing You Money",
        "Your Monthly Targets",
    ]:
        assert marker in html, f"missing {marker}"

    # Legacy sections still work alongside (they were in the base stub)
    assert "Weekly Revenue Digest" in html
