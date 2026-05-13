"""
Unit tests for the pure helpers extracted from `_compute_narrative`
in the 2026-05-13 A3 refactor.

This is the first test coverage for daily_narrative.py. The composer
is locked by test_daily_narrative_composer.py.
"""
from __future__ import annotations

from app.api.daily_narrative import (
    _compose_action_paragraph,
    _compose_intent_paragraph,
    _compose_visitor_paragraph,
    _plural,
)


# ---------------------------------------------------------------------------
# _plural — English plural helper
# ---------------------------------------------------------------------------


class TestPlural:
    def test_one_returns_singular(self):
        assert _plural(1, "person", "people") == "person"

    def test_zero_returns_plural(self):
        # 0 is grammatically plural in English ("0 people", not "0 person")
        assert _plural(0, "person", "people") == "people"

    def test_many_returns_plural(self):
        assert _plural(5, "order", "orders") == "orders"


# ---------------------------------------------------------------------------
# _compose_visitor_paragraph
# ---------------------------------------------------------------------------


class TestVisitorParagraph:
    def test_zero_visitors_uses_quiet_message(self):
        out = _compose_visitor_paragraph(0)
        assert "quiet" in out
        assert "no visitors logged" in out
        assert "tracker is listening" in out

    def test_negative_uses_quiet_message(self):
        # Defensive — negative counts shouldn't crash
        out = _compose_visitor_paragraph(-1)
        assert "quiet" in out

    def test_single_visitor_uses_singular_grammar(self):
        out = _compose_visitor_paragraph(1)
        assert "1 person has visited" in out
        assert "people have visited" not in out

    def test_multiple_visitors_uses_plural_grammar(self):
        out = _compose_visitor_paragraph(42)
        assert "42 people have visited" in out
        assert "person has visited" not in out


# ---------------------------------------------------------------------------
# _compose_intent_paragraph
# ---------------------------------------------------------------------------


class TestIntentParagraph:
    def test_zero_intent_uses_afternoon_message(self):
        out = _compose_intent_paragraph(intent_count=0, visitors_today=100)
        assert "No high-intent signals" in out
        assert "afternoon" in out

    def test_intent_pct_computed_correctly(self):
        # 25 of 100 visitors = 25%
        out = _compose_intent_paragraph(intent_count=25, visitors_today=100)
        assert "25 of them showed real purchase intent" in out
        assert "(25% of traffic)" in out

    def test_zero_visitors_with_intent_does_not_div_by_zero(self):
        # max(visitors, 1) prevents div by zero
        out = _compose_intent_paragraph(intent_count=3, visitors_today=0)
        # 3 / max(0, 1) = 300%
        assert "(300% of traffic)" in out


# ---------------------------------------------------------------------------
# _compose_action_paragraph — 4-branch decision tree
# ---------------------------------------------------------------------------


class TestActionParagraph:
    def test_both_nudges_and_orders_branch(self):
        out = _compose_action_paragraph(
            nudges_fired=5, orders_today=3, revenue_today=250.0, currency="USD",
        )
        assert "fired 5 nudges" in out
        assert "closed 3 orders" in out
        # format_money compact for 250 produces e.g. "$250"
        assert "$" in out or "250" in out

    def test_nudges_only_branch(self):
        out = _compose_action_paragraph(
            nudges_fired=5, orders_today=0, revenue_today=0.0, currency="USD",
        )
        assert "fired 5 nudges" in out
        assert "recover the ones nearly lost" in out

    def test_orders_only_branch(self):
        out = _compose_action_paragraph(
            nudges_fired=0, orders_today=3, revenue_today=250.0, currency="USD",
        )
        assert "closed 3 orders" in out
        assert "fired" not in out

    def test_neither_branch(self):
        out = _compose_action_paragraph(
            nudges_fired=0, orders_today=0, revenue_today=0.0, currency="USD",
        )
        assert "No conversions" in out
        assert "watching" in out

    def test_singular_nudge_grammar(self):
        out = _compose_action_paragraph(
            nudges_fired=1, orders_today=0, revenue_today=0.0, currency="USD",
        )
        assert "1 nudge" in out
        assert "nudges" not in out

    def test_singular_order_grammar(self):
        out = _compose_action_paragraph(
            nudges_fired=0, orders_today=1, revenue_today=100.0, currency="USD",
        )
        assert "1 order" in out
        assert "orders" not in out

    def test_currency_propagates_to_money_format(self):
        out_usd = _compose_action_paragraph(
            nudges_fired=0, orders_today=1, revenue_today=100.0, currency="USD",
        )
        out_eur = _compose_action_paragraph(
            nudges_fired=0, orders_today=1, revenue_today=100.0, currency="EUR",
        )
        # Different currencies produce different symbols
        assert out_usd != out_eur
        assert "$" in out_usd
        assert "€" in out_eur

    def test_singular_nudge_with_order_branch(self):
        out = _compose_action_paragraph(
            nudges_fired=1, orders_today=1, revenue_today=50.0, currency="USD",
        )
        assert "fired 1 nudge" in out
        assert "closed 1 order" in out
        assert "nudges" not in out
        assert "orders" not in out
