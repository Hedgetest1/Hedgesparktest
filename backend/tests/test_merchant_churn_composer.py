"""
Composer-level integration tests for `compute_churn_score`.

The 2026-05-13 A3 refactor decomposed the 215-LOC god function into
a 25-LOC composer + 7 pure helpers. test_merchant_churn_helpers.py
(42 tests) locks each scorer + classifier in isolation. This file
locks the *composition* — how scorers chain, score aggregation,
risk classification, and signal merging.
"""
from __future__ import annotations

from app.services import merchant_churn_predictor as mcp


# ---------------------------------------------------------------------------
# Helper — wire all 5 scorers to deterministic outputs
# ---------------------------------------------------------------------------


def _patch_scorers(
    monkeypatch,
    *,
    revenue=({"revenue": 0}, []),
    tracker=({"tracker": 0}, []),
    digest=({"digest": 0}, []),
    tenure_billing=({"tenure": 0, "billing": 0}, []),
    onboarding=({"onboarding": 0}, []),
):
    monkeypatch.setattr(mcp, "_score_revenue", lambda db, s, now: revenue)
    monkeypatch.setattr(mcp, "_score_tracker", lambda db, s, now: tracker)
    monkeypatch.setattr(mcp, "_score_digest", lambda db, s: digest)
    monkeypatch.setattr(mcp, "_score_tenure_billing",
                        lambda db, s, now: tenure_billing)
    monkeypatch.setattr(mcp, "_score_onboarding", lambda db, s: onboarding)


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_all_top_level_keys_present(self, monkeypatch):
        _patch_scorers(monkeypatch)
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert set(out.keys()) == {
            "shop_domain", "churn_risk_score", "risk_level",
            "signals", "score_breakdown", "recommended_action", "computed_at",
        }

    def test_shop_domain_round_tripped(self, monkeypatch):
        _patch_scorers(monkeypatch)
        out = mcp.compute_churn_score(db=None, shop_domain="round-trip.myshopify.com")
        assert out["shop_domain"] == "round-trip.myshopify.com"

    def test_computed_at_is_iso(self, monkeypatch):
        _patch_scorers(monkeypatch)
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        from datetime import datetime
        datetime.fromisoformat(out["computed_at"])


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------


class TestScoreAggregation:
    def test_zero_when_all_healthy(self, monkeypatch):
        _patch_scorers(monkeypatch)
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert out["churn_risk_score"] == 0
        assert out["risk_level"] == "low"
        assert out["signals"] == []

    def test_sum_across_scorers(self, monkeypatch):
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 20}, [{"signal": "rev", "weight": 20}]),
            tracker=({"tracker": 10}, [{"signal": "trk", "weight": 10}]),
            digest=({"digest": 5}, [{"signal": "dig", "weight": 5}]),
            tenure_billing=({"tenure": 8, "billing": 0}, [{"signal": "ten", "weight": 8}]),
            onboarding=({"onboarding": 0}, []),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        # 20 + 10 + 5 + 8 + 0 = 43 → moderate band
        assert out["churn_risk_score"] == 43
        assert out["risk_level"] == "moderate"

    def test_score_capped_at_100(self, monkeypatch):
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 30}, []),
            tracker=({"tracker": 25}, []),
            digest=({"digest": 20}, []),
            tenure_billing=({"tenure": 15, "billing": 10}, []),
            onboarding=({"onboarding": 10}, []),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        # Sum would be 110, capped to 100
        assert out["churn_risk_score"] == 100
        assert out["risk_level"] == "critical"

    def test_score_breakdown_preserves_all_keys(self, monkeypatch):
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 30}, []),
            tracker=({"tracker": 15}, []),
            digest=({"digest": 10}, []),
            tenure_billing=({"tenure": 8, "billing": 0}, []),
            onboarding=({"onboarding": 5}, []),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert out["score_breakdown"] == {
            "revenue": 30, "tracker": 15, "digest": 10,
            "tenure": 8, "billing": 0, "onboarding": 5,
        }


# ---------------------------------------------------------------------------
# Signal aggregation + sort + cap
# ---------------------------------------------------------------------------


class TestSignalAggregation:
    def test_signals_merged_from_all_scorers(self, monkeypatch):
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 20}, [{"signal": "rev", "weight": 20}]),
            tracker=({"tracker": 10}, [{"signal": "trk", "weight": 10}]),
            digest=({"digest": 5}, [{"signal": "dig", "weight": 5}]),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        sigs = [s["signal"] for s in out["signals"]]
        assert "rev" in sigs
        assert "trk" in sigs
        assert "dig" in sigs

    def test_signals_sorted_by_weight_descending(self, monkeypatch):
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 5}, [{"signal": "low_rev", "weight": 5}]),
            tracker=({"tracker": 25}, [{"signal": "high_trk", "weight": 25}]),
            digest=({"digest": 10}, [{"signal": "mid_dig", "weight": 10}]),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        weights = [s["weight"] for s in out["signals"]]
        assert weights == sorted(weights, reverse=True)
        # Highest first
        assert out["signals"][0]["signal"] == "high_trk"

    def test_signals_capped_at_5(self, monkeypatch):
        # Build 6 distinct signals from 5 scorers (revenue adds 2)
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 30}, [
                {"signal": "rev_a", "weight": 30},
                {"signal": "rev_b", "weight": 25},
            ]),
            tracker=({"tracker": 25}, [{"signal": "trk", "weight": 25}]),
            digest=({"digest": 20}, [{"signal": "dig", "weight": 20}]),
            tenure_billing=({"tenure": 15, "billing": 10}, [
                {"signal": "ten", "weight": 15},
                {"signal": "bil", "weight": 10},
            ]),
            onboarding=({"onboarding": 10}, [{"signal": "onb", "weight": 10}]),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert len(out["signals"]) == 5
        # Top 5 by weight: 30, 25, 25, 20, 15
        weights = [s["weight"] for s in out["signals"]]
        assert weights[0] == 30
        assert weights[-1] >= 15


# ---------------------------------------------------------------------------
# Risk classification end-to-end via composer
# ---------------------------------------------------------------------------


class TestRiskClassification:
    def test_low_score_low_level(self, monkeypatch):
        _patch_scorers(monkeypatch, revenue=({"revenue": 10}, []))
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert out["churn_risk_score"] == 10
        assert out["risk_level"] == "low"

    def test_moderate_threshold(self, monkeypatch):
        _patch_scorers(monkeypatch, revenue=({"revenue": 30}, []))
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert out["risk_level"] == "moderate"

    def test_high_threshold(self, monkeypatch):
        _patch_scorers(monkeypatch, revenue=({"revenue": 30}, []),
                       tracker=({"tracker": 25}, []))
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert out["churn_risk_score"] == 55
        assert out["risk_level"] == "high"

    def test_critical_threshold(self, monkeypatch):
        _patch_scorers(
            monkeypatch,
            revenue=({"revenue": 30}, []),
            tracker=({"tracker": 25}, []),
            digest=({"digest": 20}, []),
        )
        out = mcp.compute_churn_score(db=None, shop_domain="x.myshopify.com")
        assert out["churn_risk_score"] == 75
        assert out["risk_level"] == "critical"
        assert "Immediate outreach" in out["recommended_action"]
