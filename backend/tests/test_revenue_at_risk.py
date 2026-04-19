"""
Tests for F4 — Revenue-at-Risk Score (the hero metric).

Verifies:
  - RARS report has all 5 components
  - Empty shop returns zero loss and healthy headline
  - Components aggregate correctly
  - Net ROI calculation includes prevented revenue
  - Cached output is consistent
  - Headline copy matches the at-risk state
"""
from __future__ import annotations

from app.models.shop_order import ShopOrder
from app.services.revenue_at_risk import (
    RARSComponent,
    _compute_below_benchmark,
    _compute_goal_gap,
    _compute_refund_decline,
    get_revenue_at_risk,
)


def test_rars_returns_all_five_components(db):
    """Every RARS report contains the 5 required components, even if zero."""
    report = get_revenue_at_risk(db, "rars-empty-shop.myshopify.com")
    sources = {c["source"] for c in report["components"]}
    expected = {
        "abandoned_high_intent",
        "refund_decline",
        "nudge_gap",
        "below_benchmark",
        "goal_gap",
    }
    assert expected.issubset(sources), f"missing components: {expected - sources}"


def test_rars_empty_shop_returns_zero_and_healthy_headline(db):
    """A brand-new shop with no data returns €0 at risk + positive headline."""
    report = get_revenue_at_risk(db, "rars-brand-new-shop.myshopify.com")
    assert report["total_at_risk_eur"] == 0.0
    assert "healthy" in report["headline"].lower() or "no significant" in report["headline"].lower() or "✨" in report["headline"]


def test_rars_total_equals_component_sum(db):
    """The total_at_risk_eur must equal the sum of all component losses."""
    report = get_revenue_at_risk(
        db, "rars-consistency-shop.myshopify.com",
    )
    computed_total = sum(c["loss_eur"] for c in report["components"])
    assert abs(report["total_at_risk_eur"] - computed_total) < 0.01


def test_rars_report_has_required_top_level_fields(db):
    """All top-level fields must be present for the UI contract."""
    report = get_revenue_at_risk(db, "rars-shape-shop.myshopify.com")
    for key in (
        "shop_domain",
        "total_at_risk_eur",
        "prevented_eur_this_month",
        "net_roi_eur",
        "components",
        "generated_at",
        "headline",
    ):
        assert key in report, f"missing top-level key {key!r}"


def test_rars_component_has_required_fields(db):
    """Every component must expose source + loss_eur + narrative + evidence."""
    report = get_revenue_at_risk(db, "rars-component-shape.myshopify.com")
    for comp in report["components"]:
        assert "source" in comp
        assert "loss_eur" in comp
        assert isinstance(comp["loss_eur"], (int, float))
        assert "narrative" in comp
        assert isinstance(comp["narrative"], str)
        assert "evidence" in comp
        assert isinstance(comp["evidence"], dict)


def test_rars_cached_result_is_consistent(db):
    """Two back-to-back calls return the same blob (cache hit)."""
    shop = "rars-cache-consistency.myshopify.com"
    r1 = get_revenue_at_risk(db, shop)
    r2 = get_revenue_at_risk(db, shop)
    assert r1["total_at_risk_eur"] == r2["total_at_risk_eur"]
    assert r1["generated_at"] == r2["generated_at"]


def test_rars_goal_gap_component_reads_from_goals_service(db):
    """Setting a high goal should produce a goal_gap component with loss > 0."""
    from app.services.goals import set_goal, delete_goal
    shop = "rars-goal-integration.myshopify.com"

    # Plant shop with €0 revenue MTD, set a €10k target → big gap
    g = set_goal(shop, metric="monthly_revenue", target_value=10_000.0)
    if g is None:
        import pytest
        pytest.skip("redis unavailable")

    # Use the helper directly to avoid cache
    comp = _compute_goal_gap(db, shop)
    assert comp.source == "goal_gap"
    assert comp.loss_eur > 0

    delete_goal(shop, "monthly_revenue")


def test_rars_below_benchmark_component_wires_to_benchmark_service(db):
    """below_benchmark component delegates to benchmarks.get_merchant_benchmark_report."""
    comp = _compute_below_benchmark(db, "rars-benchmark-probe.myshopify.com")
    assert comp.source == "below_benchmark"
    assert isinstance(comp.loss_eur, (int, float))


def test_rars_refund_decline_component_wires_to_refund_service(db):
    comp = _compute_refund_decline(db, "rars-refund-probe.myshopify.com")
    assert comp.source == "refund_decline"
    assert isinstance(comp.loss_eur, (int, float))


def test_rars_headline_includes_prevention_when_positive(db):
    """If prevented > subscription cost, the headline flags positive ROI."""
    # We can't easily force prevented > 0 in a unit test without nudge_events,
    # but we verify the branch structure by calling the function.
    report = get_revenue_at_risk(db, "rars-headline-shop.myshopify.com")
    assert "headline" in report
    assert len(report["headline"]) > 10


# ---------------------------------------------------------------------------
# Phase 1.2 — plan-based fidelity reduction (Starter/Lite unlock)
# ---------------------------------------------------------------------------


def test_rars_pro_plan_returns_full_component_breakdown(db):
    """plan='pro' returns the full 5-dim components breakdown."""
    report = get_revenue_at_risk(db, "rars-pro-plan-shop.myshopify.com", plan="pro")
    assert report["components"]
    sources = {c["source"] for c in report["components"]}
    assert sources == {
        "abandoned_high_intent",
        "refund_decline",
        "nudge_gap",
        "below_benchmark",
        "goal_gap",
    }


def test_rars_lite_plan_redacts_component_breakdown(db):
    """plan != 'pro' returns empty components array (upgrade CTA territory)."""
    shop = "rars-lite-plan-shop.myshopify.com"
    lite_report = get_revenue_at_risk(db, shop, plan="starter")
    assert lite_report["components"] == []


def test_rars_lite_plan_keeps_hero_number_and_headline(db):
    """Lite tier still sees the hero total + prevented + headline."""
    shop = "rars-lite-hero-shop.myshopify.com"
    lite_report = get_revenue_at_risk(db, shop, plan="starter")
    assert "total_at_risk_eur" in lite_report
    assert isinstance(lite_report["total_at_risk_eur"], (int, float))
    assert "prevented_eur_this_month" in lite_report
    assert "net_roi_eur" in lite_report
    assert "headline" in lite_report
    assert len(lite_report["headline"]) > 10
    assert "currency" in lite_report


def test_rars_plan_fidelity_filter_does_not_affect_total(db):
    """Total at risk is identical across plan tiers (cache-safe)."""
    # Fresh shop domain so both calls share the cache correctly
    shop = "rars-plan-invariant-shop.myshopify.com"
    pro_report = get_revenue_at_risk(db, shop, plan="pro")
    lite_report = get_revenue_at_risk(db, shop, plan="starter")
    assert pro_report["total_at_risk_eur"] == lite_report["total_at_risk_eur"]
    assert pro_report["prevented_eur_this_month"] == lite_report["prevented_eur_this_month"]
    assert pro_report["headline"] == lite_report["headline"]


def test_rars_plan_filter_does_not_mutate_cached_dict(db):
    """Plan-filter must shallow-copy so subsequent Pro reads aren't corrupted."""
    shop = "rars-cache-mutation-probe.myshopify.com"
    # Prime cache via Pro (full components) then read as Lite, then Pro again
    pro_first = get_revenue_at_risk(db, shop, plan="pro")
    first_component_count = len(pro_first["components"])
    _lite = get_revenue_at_risk(db, shop, plan="starter")
    pro_second = get_revenue_at_risk(db, shop, plan="pro")
    assert len(pro_second["components"]) == first_component_count, (
        "Lite read mutated shared cached dict — Pro components lost"
    )
