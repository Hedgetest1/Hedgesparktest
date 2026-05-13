"""
Unit tests for the pure helpers extracted from `revenue_radar_top`
in the 2026-05-13 A3 refactor.

First test coverage for revenue_radar.py — module had 0 prior tests.
The new suite IS the contract spec for the Pro radar response.
"""
from __future__ import annotations

from app.api.revenue_radar import (
    _build_radar_item,
    _build_radar_lookup_maps,
    _filter_radar_subsets,
)


# ---------------------------------------------------------------------------
# _build_radar_lookup_maps — 3 row-lists → 3 string-keyed maps
# ---------------------------------------------------------------------------


class TestLookupMaps:
    def test_three_empty_inputs_yield_three_empty_maps(self):
        m, mk, p = _build_radar_lookup_maps([], [], [])
        assert m == mk == p == {}

    def test_metrics_keyed_by_product_url(self):
        rows = [{"product_url": "/p/a", "views_24h": 50}]
        m, _, _ = _build_radar_lookup_maps(rows, [], [])
        assert "/p/a" in m
        assert m["/p/a"]["views_24h"] == 50

    def test_market_keyed_by_product_id(self):
        rows = [{"product_id": "/p/a", "uniqueness_score": 80}]
        _, mk, _ = _build_radar_lookup_maps([], rows, [])
        assert mk["/p/a"]["uniqueness_score"] == 80

    def test_price_keyed_by_product_id(self):
        rows = [{"product_id": "/p/a", "price_pressure_score": 75}]
        _, _, p = _build_radar_lookup_maps([], [], rows)
        assert p["/p/a"]["price_pressure_score"] == 75

    def test_numeric_id_coerced_to_string_key(self):
        # SQL may return product_id as int (numeric pk); the key is
        # always str(pid) so the lookup is stable downstream.
        rows = [{"product_id": 12345, "price_pressure_score": 75}]
        _, _, p = _build_radar_lookup_maps([], [], rows)
        assert "12345" in p


# ---------------------------------------------------------------------------
# _build_radar_item — response item shape
# ---------------------------------------------------------------------------


def _outcome(**overrides):
    base = {
        "product_id": "/p/x", "product_name": "X",
        "revenue_opportunity_score": 75.0,
        "revenue_opportunity_band": "HIGH",
        "conversion_probability": 0.12,
        "time_to_conversion": "24h",
        "recommended_action": "HIGHLIGHT_UNIQUENESS_AND_SCARCITY",
        "expected_uplift": 50.0,
        "primary_driver": "high_intent",
        "primary_barrier": "price",
        "price_pressure_score": 65.0,
        "uniqueness_score": 80.0,
        "comparability_score": 35.0,
        "auto_action_candidate": True,
    }
    base.update(overrides)
    return base


class TestBuildRadarItem:
    def test_shape(self):
        loss = {"expected_loss": 100.0, "loss_band": "MEDIUM", "urgency_score": 60.0}
        out = _build_radar_item(_outcome(), loss)
        # All required fields present
        required = {
            "product_id", "product_name", "revenue_opportunity_score",
            "revenue_opportunity_band", "conversion_probability",
            "time_to_conversion", "recommended_action", "expected_uplift",
            "primary_driver", "primary_barrier", "price_pressure_score",
            "uniqueness_score", "comparability_score", "auto_action_candidate",
            "expected_loss", "loss_band", "urgency_score",
        }
        assert set(out.keys()) == required

    def test_loss_fields_propagate(self):
        loss = {"expected_loss": 333.0, "loss_band": "HIGH", "urgency_score": 88.0}
        out = _build_radar_item(_outcome(), loss)
        assert out["expected_loss"] == 333.0
        assert out["loss_band"] == "HIGH"
        assert out["urgency_score"] == 88.0


# ---------------------------------------------------------------------------
# _filter_radar_subsets — 3 prescriptive subsets, each cap 3
# ---------------------------------------------------------------------------


class TestFilterSubsets:
    def _ranked(self, n: int, **overrides):
        items = []
        for i in range(n):
            it = {
                "product_id": f"/p/{i}",
                "recommended_action": "OTHER",
                "price_pressure_score": 0,
                "auto_action_candidate": False,
            }
            it.update(overrides)
            items.append(it)
        return items

    def test_empty_ranked_yields_three_empty_subsets(self):
        push, price, auto = _filter_radar_subsets([])
        assert push == price == auto == []

    def test_push_now_filters_recommended_action(self):
        ranked = self._ranked(
            5, recommended_action="HIGHLIGHT_UNIQUENESS_AND_SCARCITY",
        )
        push, _, _ = _filter_radar_subsets(ranked)
        assert len(push) == 3  # cap at 3
        for it in push:
            assert it["recommended_action"] == "HIGHLIGHT_UNIQUENESS_AND_SCARCITY"

    def test_push_now_excludes_other_actions(self):
        ranked = self._ranked(
            3, recommended_action="MONITOR",
        )
        push, _, _ = _filter_radar_subsets(ranked)
        assert push == []

    def test_price_watch_threshold_60(self):
        # 3 items above threshold + 2 below
        ranked = [
            {**self._ranked(1, price_pressure_score=75)[0], "product_id": "/p/a"},
            {**self._ranked(1, price_pressure_score=60)[0], "product_id": "/p/b"},
            {**self._ranked(1, price_pressure_score=80)[0], "product_id": "/p/c"},
            {**self._ranked(1, price_pressure_score=59)[0], "product_id": "/p/d"},
            {**self._ranked(1, price_pressure_score=30)[0], "product_id": "/p/e"},
        ]
        _, price_watch, _ = _filter_radar_subsets(ranked)
        ids = {it["product_id"] for it in price_watch}
        assert "/p/a" in ids
        assert "/p/b" in ids  # boundary inclusive (>=60)
        assert "/p/c" in ids
        assert "/p/d" not in ids
        assert "/p/e" not in ids

    def test_auto_action_candidates(self):
        ranked = self._ranked(4, auto_action_candidate=True)
        _, _, auto = _filter_radar_subsets(ranked)
        # All 4 are candidates, cap at 3
        assert len(auto) == 3
        for it in auto:
            assert it["auto_action_candidate"] is True

    def test_auto_action_strict_true_check(self):
        # Truthy non-True values (e.g. 1) MUST be excluded — strict `is True`
        ranked = [
            {**self._ranked(1, auto_action_candidate=True)[0], "product_id": "/a"},
            {**self._ranked(1, auto_action_candidate=1)[0], "product_id": "/b"},  # truthy ≠ True
            {**self._ranked(1, auto_action_candidate=None)[0], "product_id": "/c"},
        ]
        _, _, auto = _filter_radar_subsets(ranked)
        ids = {it["product_id"] for it in auto}
        assert ids == {"/a"}  # strict-True only

    def test_subsets_independent(self):
        # One item can appear in multiple subsets
        ranked = [{
            "product_id": "/p/x",
            "recommended_action": "HIGHLIGHT_UNIQUENESS_AND_SCARCITY",
            "price_pressure_score": 75,
            "auto_action_candidate": True,
        }]
        push, price, auto = _filter_radar_subsets(ranked)
        assert len(push) == 1
        assert len(price) == 1
        assert len(auto) == 1
