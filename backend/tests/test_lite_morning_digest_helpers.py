"""
Unit tests for the section renderers extracted from `_build_email`
in the 2026-05-13 A3 refactor.

The composer level is tested by test_lite_morning_digest_composer.py.
This file is the structural-unit gate: each pure renderer produces
the byte-identical HTML (or "") expected by its data slice. A
regression in any renderer fails here before the composer test
even runs.
"""
from __future__ import annotations

from app.services.lite_morning_digest import (
    _render_benchmarks_html,
    _render_cta_html,
    _render_lead_story_html,
    _render_rars_hero_html,
    _render_retention_html,
    _render_retention_row_html,
    _render_risk_components_html,
    _render_stock_html,
    _retention_tier,
)


# ---------------------------------------------------------------------------
# _render_rars_hero_html
# ---------------------------------------------------------------------------


class TestRarsHero:
    def test_zero_total_returns_empty(self):
        assert _render_rars_hero_html(0, 0, "USD") == ""

    def test_negative_total_returns_empty(self):
        assert _render_rars_hero_html(-100, 0, "USD") == ""

    def test_positive_total_renders_hero(self):
        out = _render_rars_hero_html(1234, 0, "USD")
        assert "Revenue at Risk" in out
        assert "USD 1,234" in out
        assert "/month" in out

    def test_prevented_block_only_when_positive(self):
        out_with = _render_rars_hero_html(1234, 500, "USD")
        out_without = _render_rars_hero_html(1234, 0, "USD")
        assert "already prevented USD 500" in out_with
        assert "already prevented" not in out_without

    def test_currency_round_tripped(self):
        out = _render_rars_hero_html(1000, 0, "EUR")
        assert "EUR 1,000" in out


# ---------------------------------------------------------------------------
# _render_lead_story_html
# ---------------------------------------------------------------------------


class TestLeadStory:
    def test_empty_product_returns_empty(self):
        assert _render_lead_story_html("", "any action", "any headline") == ""

    def test_product_only_renders_title(self):
        out = _render_lead_story_html("Premium Wallet", "", "")
        assert "Today's lead story — Premium Wallet" in out
        # No action paragraph
        assert ";color:#c8d1dc" not in out  # action style block

    def test_action_appended_when_present(self):
        out = _render_lead_story_html("Premium Wallet", "Lower price 10%", "")
        assert "Lower price 10%" in out

    def test_headline_appended_only_when_different_from_product(self):
        same = _render_lead_story_html("Premium Wallet", "", "Premium Wallet")
        diff = _render_lead_story_html("Premium Wallet", "", "Cart slipping today")
        assert "Cart slipping today" in diff
        # When headline == product, no extra paragraph
        assert "Cart slipping today" not in same


# ---------------------------------------------------------------------------
# _render_risk_components_html
# ---------------------------------------------------------------------------


class TestRiskComponents:
    def test_empty_comps_returns_empty(self):
        assert _render_risk_components_html([], 0, "USD") == ""

    def test_single_comp_renders_count_singular(self):
        comps = [{"source": "abandoned_high_intent", "loss_eur": 500.0}]
        out = _render_risk_components_html(comps, 500, "USD")
        assert "top 1" in out
        assert "1 source " in out  # singular
        assert "Abandoned high-intent carts" in out
        assert "USD 500/mo" in out

    def test_multiple_comps_uses_plural(self):
        comps = [
            {"source": "abandoned_high_intent", "loss_eur": 500.0},
            {"source": "refund_decline", "loss_eur": 200.0},
        ]
        out = _render_risk_components_html(comps, 700, "USD")
        assert "top 2" in out
        assert "2 sources" in out

    def test_unknown_source_falls_back_to_source_key(self):
        comps = [{"source": "mystery", "loss_eur": 100.0}]
        out = _render_risk_components_html(comps, 100, "USD")
        assert "mystery" in out  # raw source string surfaces

    def test_impact_line_only_when_top_loss_positive(self):
        with_loss = _render_risk_components_html(
            [{"source": "x", "loss_eur": 500.0}], 500, "USD",
        )
        zero_loss = _render_risk_components_html(
            [{"source": "x", "loss_eur": 0.0}], 0, "USD",
        )
        assert "Fixing the top leak" in with_loss
        assert "Fixing the top leak" not in zero_loss

    def test_narrative_fallback_when_missing(self):
        comps = [{"source": "x", "loss_eur": 100.0}]
        out = _render_risk_components_html(comps, 100, "USD")
        assert "Component contributing to this month" in out

    def test_narrative_used_when_present(self):
        comps = [{"source": "x", "loss_eur": 100.0, "narrative": "Custom story"}]
        out = _render_risk_components_html(comps, 100, "USD")
        assert "Custom story" in out


# ---------------------------------------------------------------------------
# _render_benchmarks_html
# ---------------------------------------------------------------------------


class TestBenchmarks:
    def test_empty_bench_returns_empty(self):
        assert _render_benchmarks_html({}, "USD") == ""

    def test_zero_recovery_returns_empty(self):
        assert _render_benchmarks_html({"total_recovery": 0, "peer_count": 100}, "USD") == ""

    def test_positive_recovery_renders(self):
        out = _render_benchmarks_html(
            {"total_recovery": 1500, "peer_count": 50, "band": "S-band"}, "EUR",
        )
        assert "You vs. Similar Shops" in out
        assert "50 shops" in out
        assert "S-band" in out
        assert "EUR 1,500" in out

    def test_band_default_when_missing(self):
        out = _render_benchmarks_html(
            {"total_recovery": 100, "peer_count": 25}, "USD",
        )
        assert "your band" in out


# ---------------------------------------------------------------------------
# _retention_tier + _render_retention_row_html
# ---------------------------------------------------------------------------


class TestRetentionTier:
    def test_strong_band(self):
        assert _retention_tier(0.30) == ("#16a34a", "strong")
        assert _retention_tier(0.5) == ("#16a34a", "strong")

    def test_typical_band(self):
        assert _retention_tier(0.15) == ("#f59e0b", "typical")
        assert _retention_tier(0.29) == ("#f59e0b", "typical")

    def test_weak_band(self):
        assert _retention_tier(0.0) == ("#dc2626", "weak")
        assert _retention_tier(0.14) == ("#dc2626", "weak")


class TestRetentionRow:
    def test_row_contains_label_and_pct(self):
        out = _render_retention_row_html("Week 1 repurchase", 0.25)
        assert "Week 1 repurchase" in out
        assert "25%" in out
        assert "typical" in out

    def test_strong_uses_emerald(self):
        out = _render_retention_row_html("X", 0.5)
        assert "#16a34a" in out
        assert "strong" in out

    def test_weak_uses_red(self):
        out = _render_retention_row_html("X", 0.05)
        assert "#dc2626" in out
        assert "weak" in out

    def test_bar_pct_capped_at_100(self):
        # rate=0.5 → 50*3=150 → capped 100
        out = _render_retention_row_html("X", 0.5)
        assert "width:100%" in out


# ---------------------------------------------------------------------------
# _render_retention_html
# ---------------------------------------------------------------------------


class TestRetention:
    def test_empty_dict_returns_empty(self):
        assert _render_retention_html({}) == ""

    def test_all_zero_returns_empty(self):
        assert _render_retention_html({"w1": 0, "w4": 0, "w12": 0}) == ""

    def test_any_nonzero_renders(self):
        out = _render_retention_html({"w1": 0.25, "w4": 0, "w12": 0})
        assert "Retention" in out
        assert "Week 1 repurchase" in out
        assert "Week 4 repurchase" in out
        assert "Week 12 repurchase" in out

    def test_rates_shown_as_integers(self):
        out = _render_retention_html({"w1": 0.234, "w4": 0.15, "w12": 0.08})
        assert "23%" in out
        assert "15%" in out
        assert "8%" in out


# ---------------------------------------------------------------------------
# _render_stock_html
# ---------------------------------------------------------------------------


class TestStockHealth:
    def test_none_returns_empty(self):
        assert _render_stock_html(None) == ""

    def test_no_risk_and_no_oos_returns_empty(self):
        assert _render_stock_html({"at_risk": [], "out_of_stock_count": 0}) == ""

    def test_oos_only_renders(self):
        out = _render_stock_html({"at_risk": [], "out_of_stock_count": 3})
        assert "Stock health" in out
        assert "3 SKUs out of stock" in out

    def test_oos_singular_when_one(self):
        out = _render_stock_html({"at_risk": [], "out_of_stock_count": 1})
        assert "1 SKU out of stock" in out

    def test_at_risk_rows_rendered(self):
        out = _render_stock_html({
            "at_risk": [
                {"product_title": "Wallet", "days_of_cover": 5.4},
                {"product_title": "Bag", "days_of_cover": 7.0},
            ],
            "out_of_stock_count": 0,
        })
        assert "Wallet" in out
        assert "5 days of cover" in out  # 5.4 → 5 via :.0f
        assert "Bag" in out
        assert "7 days of cover" in out

    def test_at_risk_row_skipped_when_doc_is_none(self):
        out = _render_stock_html({
            "at_risk": [{"product_title": "Skip", "days_of_cover": None}],
            "out_of_stock_count": 0,
        })
        # No render — skipped row + no oos → empty
        assert out == ""


# ---------------------------------------------------------------------------
# _render_cta_html
# ---------------------------------------------------------------------------


class TestCTA:
    def test_cta_contains_dashboard_url_and_label(self):
        out = _render_cta_html()
        assert "Open your dashboard" in out
        assert "app.hedgesparkhq.com/app/lite" in out
