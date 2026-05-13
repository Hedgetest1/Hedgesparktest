"""
Composer-level integration tests for `_compute_narrative`.

The 2026-05-13 A3 refactor decomposed the 213-LOC god function into a
30-LOC composer + 10 pure helpers. test_daily_narrative_helpers.py
(18 tests) locks the paragraph builders. This file locks the
composition: 5 fetcher seams + currency resolution + causal overlay
wiring + response shape.
"""
from __future__ import annotations

from datetime import datetime

from app.api import daily_narrative as dn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_fetchers(
    monkeypatch,
    *,
    visitors=0, intent=0, nudges=0,
    orders=(0, 0.0), top_action=None,
    currency="USD",
    causal_overlay=(None, [], None),
):
    """Wire every fetcher + currency + causal overlay to deterministic values."""
    monkeypatch.setattr(dn, "get_shop_currency", lambda db, s: currency)
    monkeypatch.setattr(dn, "_fetch_visitors_today", lambda db, s, ms: visitors)
    monkeypatch.setattr(dn, "_fetch_intent_count", lambda db, s, start: intent)
    monkeypatch.setattr(dn, "_fetch_nudges_fired", lambda db, s, start: nudges)
    monkeypatch.setattr(dn, "_fetch_orders_today", lambda db, s, c, start: orders)
    monkeypatch.setattr(dn, "_fetch_top_action", lambda db, s, lb: top_action)
    monkeypatch.setattr(dn, "_load_causal_overlay", lambda db, s: causal_overlay)


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_all_top_level_keys(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert set(out.keys()) == {
            "shop_domain", "headline", "paragraphs", "stats",
            "top_next_action", "why", "fusion_alerts", "currency",
            "generated_at",
        }

    def test_stats_keys(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert set(out["stats"].keys()) == {
            "visitors_today", "intent_signals_today", "nudges_fired_today",
            "orders_today", "revenue_today_eur",
        }

    def test_shop_domain_round_tripped(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="round-trip.myshopify.com")
        assert out["shop_domain"] == "round-trip.myshopify.com"


# ---------------------------------------------------------------------------
# Empty-state — every fetcher returns 0
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_no_data_produces_3_paragraphs(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        # No causal overlay → 3 paragraphs (visitor + intent + action)
        assert len(out["paragraphs"]) == 3

    def test_empty_state_paragraphs_use_cold_messages(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        joined = " ".join(out["paragraphs"])
        assert "quiet" in joined  # visitor branch
        assert "No high-intent" in joined  # intent branch
        assert "No conversions" in joined  # action branch

    def test_empty_state_top_action_none(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["top_next_action"] is None
        assert out["why"] is None
        assert out["fusion_alerts"] == []


# ---------------------------------------------------------------------------
# Stats round-trip through composer
# ---------------------------------------------------------------------------


class TestStatsPropagation:
    def test_visitor_count_propagates(self, monkeypatch):
        _patch_fetchers(monkeypatch, visitors=42)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["stats"]["visitors_today"] == 42

    def test_intent_count_propagates(self, monkeypatch):
        _patch_fetchers(monkeypatch, intent=15)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["stats"]["intent_signals_today"] == 15

    def test_nudges_count_propagates(self, monkeypatch):
        _patch_fetchers(monkeypatch, nudges=8)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["stats"]["nudges_fired_today"] == 8

    def test_orders_and_revenue_propagate(self, monkeypatch):
        _patch_fetchers(monkeypatch, orders=(3, 250.49))
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["stats"]["orders_today"] == 3
        assert out["stats"]["revenue_today_eur"] == 250.49

    def test_top_action_string_propagates(self, monkeypatch):
        _patch_fetchers(monkeypatch, top_action="wallet is showing high engagement no action")
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["top_next_action"] == "wallet is showing high engagement no action"


# ---------------------------------------------------------------------------
# Currency wiring
# ---------------------------------------------------------------------------


class TestCurrency:
    def test_currency_propagates(self, monkeypatch):
        _patch_fetchers(monkeypatch, currency="GBP")
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["currency"] == "GBP"

    def test_currency_defaults_to_usd_when_none(self, monkeypatch):
        _patch_fetchers(monkeypatch, currency=None)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["currency"] == "USD"


# ---------------------------------------------------------------------------
# Causal overlay
# ---------------------------------------------------------------------------


class TestCausalOverlay:
    def test_overlay_appends_fourth_paragraph(self, monkeypatch):
        why = {"label": "x", "narrative": "abc", "next_action": "do y"}
        _patch_fetchers(
            monkeypatch,
            causal_overlay=(why, [{"alert": "z"}], "Why: abc Next step — do y"),
        )
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert len(out["paragraphs"]) == 4
        assert out["paragraphs"][3] == "Why: abc Next step — do y"
        assert out["why"] == why
        assert out["fusion_alerts"] == [{"alert": "z"}]

    def test_overlay_without_extra_paragraph_keeps_3(self, monkeypatch):
        # When causal returns fusion_alerts but no hypotheses → no 4th paragraph
        _patch_fetchers(
            monkeypatch,
            causal_overlay=(None, [{"alert": "z"}], None),
        )
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert len(out["paragraphs"]) == 3
        assert out["why"] is None
        assert out["fusion_alerts"] == [{"alert": "z"}]

    def test_causal_failure_returns_clean_state(self, monkeypatch):
        # Failed overlay → no why, no fusion_alerts, no extra paragraph
        _patch_fetchers(monkeypatch, causal_overlay=(None, [], None))
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["why"] is None
        assert out["fusion_alerts"] == []
        assert len(out["paragraphs"]) == 3


# ---------------------------------------------------------------------------
# Populated end-to-end
# ---------------------------------------------------------------------------


class TestPopulatedEndToEnd:
    def test_populated_state_produces_full_response(self, monkeypatch):
        _patch_fetchers(
            monkeypatch,
            visitors=120, intent=18, nudges=7, orders=(4, 489.50),
            top_action="leather-wallet is showing high engagement",
            currency="EUR",
        )
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["stats"]["visitors_today"] == 120
        assert out["stats"]["intent_signals_today"] == 18
        assert out["stats"]["nudges_fired_today"] == 7
        assert out["stats"]["orders_today"] == 4
        assert out["stats"]["revenue_today_eur"] == 489.5
        # Paragraphs reflect populated state
        joined = " ".join(out["paragraphs"])
        assert "120 people have visited" in joined
        assert "18 of them showed real purchase intent" in joined
        assert "(15% of traffic)" in joined  # 18/120 = 15%
        assert "fired 7 nudges" in joined
        assert "closed 4 orders" in joined
        assert "€" in joined  # EUR symbol
        assert out["top_next_action"] == "leather-wallet is showing high engagement"


# ---------------------------------------------------------------------------
# Headline + generated_at
# ---------------------------------------------------------------------------


class TestHeadlineAndTimestamp:
    def test_headline_format(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        assert out["headline"].startswith("Here's your store today")

    def test_generated_at_is_iso(self, monkeypatch):
        _patch_fetchers(monkeypatch)
        out = dn._compute_narrative(db=None, shop="x.myshopify.com")
        datetime.fromisoformat(out["generated_at"])
