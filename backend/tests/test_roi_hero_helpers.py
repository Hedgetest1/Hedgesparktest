"""
Unit tests for the pure helpers extracted from `_compute_roi_hero` in
the 2026-05-12 A3 refactor (commit 2edb181).

End-to-end coverage exists via the `/pro/roi-hero` endpoint tests; this
file is the structural unit gate for:
  - `_build_breakdown` (compose the breakdown list from 3 €-sources)
  - `_headline_message` (5 ROI bands → human-readable copy)
  - `_compute_roi_hero` composer wiring (via monkeypatched helpers)

The headline message is what every merchant sees first on the ROI hero
card — silent drift in the band thresholds would change the entire
loss-prevention narrative.
"""
from __future__ import annotations

from app.api import roi_hero as roi_hero_mod
from app.api.roi_hero import _build_breakdown, _headline_message


# ---------------------------------------------------------------------------
# _build_breakdown
# ---------------------------------------------------------------------------


class TestBuildBreakdown:
    def test_all_three_sources_present(self):
        items = _build_breakdown(100.0, 200.0, 50.0)
        assert len(items) == 3
        sources = {item["source"] for item in items}
        assert sources == {"nudge_lift", "delegated_autonomy", "rars_prevented"}

    def test_zero_source_omitted(self):
        items = _build_breakdown(0.0, 200.0, 50.0)
        assert len(items) == 2
        assert all(item["source"] != "nudge_lift" for item in items)

    def test_negative_treated_as_omitted(self):
        # The helper checks `> 0`, so a negative value falls out.
        items = _build_breakdown(-1.0, 200.0, 50.0)
        assert all(item["source"] != "nudge_lift" for item in items)

    def test_empty_when_all_zero(self):
        assert _build_breakdown(0.0, 0.0, 0.0) == []

    def test_only_one_source(self):
        items = _build_breakdown(0.0, 0.0, 75.0)
        assert len(items) == 1
        item = items[0]
        assert item["source"] == "rars_prevented"
        assert item["amount_eur"] == 75.0
        assert "icon" in item
        assert "description" in item

    def test_item_shape_canonical(self):
        items = _build_breakdown(123.45, 0.0, 0.0)
        item = items[0]
        assert set(item.keys()) == {"source", "amount_eur", "description", "icon"}
        assert item["amount_eur"] == 123.45


# ---------------------------------------------------------------------------
# _headline_message — 5 ROI bands
# ---------------------------------------------------------------------------


class TestHeadlineMessage:
    def test_zero_total_30d(self):
        msg = _headline_message(0.0, 0.0)
        assert "collecting" in msg

    def test_negative_total_30d(self):
        # `total_30d <= 0` branch
        msg = _headline_message(-50.0, 0.0)
        assert "collecting" in msg

    def test_20x_or_better_band(self):
        msg = _headline_message(2000.0, 20.0)
        assert "20×" in msg
        assert "Wild" in msg

    def test_5x_to_20x_band(self):
        msg = _headline_message(500.0, 10.0)
        assert "10.0×" in msg
        assert "saved" in msg

    def test_1x_to_5x_band(self):
        msg = _headline_message(100.0, 2.5)
        assert "2.5×" in msg
        assert "black" in msg

    def test_below_1x_band(self):
        # In the black branch requires ratio>=1; sub-1 gets fallback
        msg = _headline_message(50.0, 0.5)
        assert "cash machine" in msg

    def test_band_boundary_exactly_1x(self):
        # 1.0 → "in the black" branch
        msg = _headline_message(100.0, 1.0)
        assert "1.0×" in msg
        assert "black" in msg

    def test_band_boundary_exactly_5x(self):
        # 5.0 → "saved you 5.0×" branch
        msg = _headline_message(245.0, 5.0)
        assert "5.0×" in msg
        assert "saved" in msg

    def test_band_boundary_exactly_20x(self):
        # 20.0 → "Wild" branch
        msg = _headline_message(2000.0, 20.0)
        assert "Wild" in msg


# ---------------------------------------------------------------------------
# _compute_roi_hero — composer wiring
# ---------------------------------------------------------------------------


class TestComposeROIHero:
    """
    Validate the composer wires the helpers correctly without hitting the
    DB. Monkeypatches each helper to a deterministic constant and checks
    the response shape + key arithmetic (total, ratio, momentum delta).
    """

    def _patch_helpers(
        self, monkeypatch,
        *,
        eur_from_lift_returns=None,
        trust_returns=0.0,
        rars_returns=0.0,
        plan_cost=49.0,
        currency="USD",
        top_win=None,
    ):
        """
        eur_from_lift_returns: callable(since, until, aov_days) → float OR
            single float. If callable, called per invocation.
        """
        if eur_from_lift_returns is None:
            eur_from_lift_returns = 0.0

        if callable(eur_from_lift_returns):
            elf = eur_from_lift_returns
        else:
            def elf(db, shop, currency, *, since=None, until=None, aov_days=30):
                return float(eur_from_lift_returns)

        monkeypatch.setattr(roi_hero_mod, "_eur_from_lift", elf)
        monkeypatch.setattr(
            roi_hero_mod, "_trust_savings",
            lambda db, shop, *, since=None: float(trust_returns),
        )
        monkeypatch.setattr(
            roi_hero_mod, "_rars_prevented_recent",
            lambda shop: float(rars_returns),
        )
        monkeypatch.setattr(
            roi_hero_mod, "_plan_cost_monthly",
            lambda db, shop: float(plan_cost),
        )
        monkeypatch.setattr(
            roi_hero_mod, "get_shop_currency",
            lambda db, shop: currency,
        )
        monkeypatch.setattr(
            roi_hero_mod, "_top_win",
            lambda db, shop, currency, *, since: top_win,
        )

    def test_response_shape(self, monkeypatch):
        self._patch_helpers(monkeypatch, eur_from_lift_returns=100.0,
                            trust_returns=50.0, rars_returns=25.0)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="example.com")
        assert set(result.keys()) == {
            "shop_domain",
            "total_saved_eur_30d",
            "total_saved_eur_7d",
            "total_saved_eur_all_time",
            "delta_7d_vs_prior_pct",
            "breakdown",
            "top_win",
            "plan_cost_eur_monthly",
            "roi_ratio",
            "headline_message",
            "currency",
            "generated_at",
        }
        assert result["shop_domain"] == "example.com"
        assert result["currency"] == "USD"

    def test_total_30d_sums_three_sources(self, monkeypatch):
        self._patch_helpers(monkeypatch, eur_from_lift_returns=100.0,
                            trust_returns=50.0, rars_returns=25.0)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        # 30d total = actions + trust + rars
        assert result["total_saved_eur_30d"] == 175.0
        # 30d sources visible in breakdown
        assert len(result["breakdown"]) == 3

    def test_roi_ratio_against_plan_cost(self, monkeypatch):
        self._patch_helpers(monkeypatch, eur_from_lift_returns=98.0,
                            trust_returns=0.0, rars_returns=0.0,
                            plan_cost=49.0)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        # ratio = 98 / 49 = 2.0
        assert result["roi_ratio"] == 2.0

    def test_momentum_delta_computed_when_prior_positive(self, monkeypatch):
        # Different returns per window via a callable
        call_log: list[dict] = []

        def elf(db, shop, currency, *, since=None, until=None, aov_days=30):
            call_log.append({"since": since, "until": until, "aov_days": aov_days})
            # Stage call ordering in _compute_roi_hero:
            #   1) since=c_30d, aov_days=30   → 30d actions
            #   2) since=c_7d, aov_days=30    → 7d
            #   3) since=c_14d, until=c_7d    → prior 7d
            #   4) aov_days=None              → all-time
            n = len(call_log)
            if n == 1: return 1000.0
            if n == 2: return 200.0     # current 7d
            if n == 3: return 100.0     # prior 7d
            return 0.0

        self._patch_helpers(monkeypatch, eur_from_lift_returns=elf)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        # delta = (200 - 100) / 100 * 100 = +100%
        assert result["delta_7d_vs_prior_pct"] == 100.0

    def test_momentum_delta_none_when_prior_zero(self, monkeypatch):
        self._patch_helpers(monkeypatch, eur_from_lift_returns=0.0)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        assert result["delta_7d_vs_prior_pct"] is None

    def test_headline_picks_correct_band(self, monkeypatch):
        # 30d total = 980; plan_cost=49 → ratio=20.0 → "Wild" band
        self._patch_helpers(monkeypatch, eur_from_lift_returns=980.0)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        assert "Wild" in result["headline_message"]
        assert result["roi_ratio"] == 20.0

    def test_breakdown_omits_zero_sources(self, monkeypatch):
        # Only nudge_lift > 0
        self._patch_helpers(monkeypatch, eur_from_lift_returns=100.0,
                            trust_returns=0.0, rars_returns=0.0)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        assert len(result["breakdown"]) == 1
        assert result["breakdown"][0]["source"] == "nudge_lift"

    def test_all_time_floored_by_30d(self, monkeypatch):
        # actions all-time = 0, trust all-time = 0, but 30d = 500
        # → all_time = max(0+0, 500) = 500
        call_log: list[dict] = []

        def elf(db, shop, currency, *, since=None, until=None, aov_days=30):
            call_log.append({"aov_days": aov_days})
            # aov_days=30 → return 500, aov_days=None (all-time) → 0
            if aov_days is None:
                return 0.0
            return 500.0

        self._patch_helpers(monkeypatch, eur_from_lift_returns=elf)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        # 30d total is 500; all-time floored to 500 via max()
        assert result["total_saved_eur_all_time"] == 500.0

    def test_currency_fallback_to_usd_when_none(self, monkeypatch):
        self._patch_helpers(monkeypatch, currency=None)
        result = roi_hero_mod._compute_roi_hero(db=None, shop="x.com")
        assert result["currency"] == "USD"
