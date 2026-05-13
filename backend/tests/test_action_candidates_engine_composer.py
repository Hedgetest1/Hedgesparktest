"""
Composer-level integration tests for `generate_action_candidates`.

The 2026-05-12 A3 close decomposed this 290+ LOC god function into 8
pure stage helpers + 3 external dependencies + a composer. The existing
`test_action_candidates_engine_helpers.py` (421 LOC) locks every stage's
contract in isolation. This file locks the *composition* — how the 8
stages chain together — so a future refactor that re-shapes the
pipeline can prove the wiring still holds without booting Postgres.

Pattern: monkeypatch every stage seam, drive the composer with sentinel
inputs/outputs, assert each stage receives the upstream stage's output,
and assert the early-exit branches behave correctly.

Born 2026-05-13 as a fix for the R-blocker:sprint>1d gap surfaced in
the 2026-05-12 god-function refactor sprint.
"""
from __future__ import annotations

from app.services import action_candidates_engine as ace


# ---------------------------------------------------------------------------
# Wiring fixture: monkeypatch every stage with a recorder, return the
# sequence of stage calls + the per-stage payload the composer feeds.
# ---------------------------------------------------------------------------


def _patch_all_stages(monkeypatch, *, signals, supporting, buckets, raw, enrichment,
                      eff_map=None, final_builder=None):
    """Wire every stage seam to deterministic payloads.

    Records the call order + arguments so tests can assert stage-N receives
    stage-(N-1)'s output. `final_builder` lets a test override the per-raw
    candidate transform.
    """
    calls: list[tuple[str, tuple, dict]] = []

    def _rec(name, fn):
        def _wrapped(*args, **kwargs):
            calls.append((name, args, dict(kwargs)))
            return fn(*args, **kwargs)
        return _wrapped

    # External per-shop deps
    monkeypatch.setattr(ace, "get_shop_aov", _rec("get_shop_aov", lambda db, s: 42.0))
    monkeypatch.setattr(
        ace, "get_real_product_conversion_map",
        _rec("get_real_product_conversion_map", lambda db, s: {"conv_map": True}),
    )
    monkeypatch.setattr(
        ace, "get_or_train_model",
        _rec("get_or_train_model", lambda db, s: object()),
    )
    monkeypatch.setattr(
        ace, "_maybe_refresh_signals",
        _rec("_maybe_refresh_signals", lambda s: None),
    )

    # Stage 1-2: fetches
    monkeypatch.setattr(
        ace, "_fetch_active_signals",
        _rec("_fetch_active_signals", lambda db, s, now: list(signals)),
    )
    monkeypatch.setattr(
        ace, "_fetch_supporting_tables",
        _rec("_fetch_supporting_tables", lambda db, s: dict(supporting)),
    )

    # Stage 3: bucket builder
    monkeypatch.setattr(
        ace, "_build_signal_buckets",
        _rec("_build_signal_buckets",
             lambda sigs, m, p: dict(buckets)),
    )

    # Stage 4: gates → raw candidates
    monkeypatch.setattr(
        ace, "_apply_action_gates",
        _rec("_apply_action_gates", lambda b, sup: list(raw)),
    )

    # Stage 5: enrichment
    monkeypatch.setattr(
        ace, "_fetch_enrichment",
        _rec("_fetch_enrichment", lambda db, s: dict(enrichment)),
    )

    # Stage 6: per-raw candidate transform
    def _default_final_builder(raw_c, enrich, *, real_conv_map, calibration, aov):
        # Build a minimal final candidate echoing the raw input.
        return {
            "product_url": raw_c["product_url"],
            "action_type": raw_c["action_type"],
            "urgency": raw_c.get("urgency", 50.0),
            "confidence": raw_c.get("confidence", 0.5),
            "expected_loss": 100.0,
        }
    monkeypatch.setattr(
        ace, "_build_final_candidate",
        _rec("_build_final_candidate", final_builder or _default_final_builder),
    )

    # Stage 7a: effectiveness map
    monkeypatch.setattr(
        ace, "_load_effectiveness_map",
        _rec("_load_effectiveness_map", lambda db: dict(eff_map or {})),
    )

    # Stage 7b: rank + finalize (use real impl so we can assert ordering)
    real_rank_and_finalize = ace._rank_and_finalize
    monkeypatch.setattr(
        ace, "_rank_and_finalize",
        _rec("_rank_and_finalize", real_rank_and_finalize),
    )

    return calls


# ---------------------------------------------------------------------------
# Blocklist short-circuit
# ---------------------------------------------------------------------------


class TestBlocklistShortCircuit:
    def test_legacy_shop_returns_empty_without_any_stage_call(self, monkeypatch):
        """legacy.myshopify.com is the canonical dev placeholder; composer
        MUST return [] without running ANY stage."""
        calls = _patch_all_stages(
            monkeypatch,
            signals=[], supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={}, raw=[], enrichment={"vps_map": {}, "ml_map": {}},
        )
        out = ace.generate_action_candidates("legacy.myshopify.com", db=None)
        assert out == []
        # No fetch/build calls — the blocklist MUST short-circuit before
        # any stage runs.
        assert calls == []


# ---------------------------------------------------------------------------
# Early-exit branches
# ---------------------------------------------------------------------------


class TestEarlyExit:
    def test_empty_buckets_returns_empty_skipping_gates_and_enrichment(self, monkeypatch):
        calls = _patch_all_stages(
            monkeypatch,
            signals=[], supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={},
            raw=[("would-not-run",)],
            enrichment={"vps_map": {}, "ml_map": {}},
        )
        out = ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        assert out == []
        names = [c[0] for c in calls]
        assert "_apply_action_gates" not in names, (
            "empty buckets MUST short-circuit before gates"
        )
        assert "_fetch_enrichment" not in names

    def test_empty_raw_candidates_returns_empty_skipping_enrichment(self, monkeypatch):
        calls = _patch_all_stages(
            monkeypatch,
            signals=[{"any": "sig"}],
            supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={("url", "ACT"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=[],  # gates filter everything
            enrichment={"vps_map": {}, "ml_map": {}},
        )
        out = ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        assert out == []
        names = [c[0] for c in calls]
        assert "_fetch_enrichment" not in names, (
            "empty raw candidates MUST short-circuit before enrichment fetch"
        )


# ---------------------------------------------------------------------------
# Top-20 cap before enrichment
# ---------------------------------------------------------------------------


class TestTop20Cap:
    def test_more_than_20_raw_capped_to_20_before_enrichment(self, monkeypatch):
        """When _apply_action_gates returns 25 raw candidates, the composer
        sorts by signal_strength desc and caps at 20 BEFORE calling
        _build_final_candidate. Verify _build_final_candidate is called
        exactly 20 times AND the cap keeps the strongest signals."""
        raw_25 = [
            {
                "product_url": f"/products/p{i}",
                "action_type": "SCARCITY_NUDGE",
                "supporting_signals": [],
                "confidence": 0.5,
                "urgency": 50.0,
                "signal_strength": float(i),   # higher i = stronger signal
                "_metrics": {}, "_pi": {}, "_upd": {},
            }
            for i in range(25)
        ]
        built = []
        def _builder(raw_c, *_a, **_kw):
            built.append(raw_c["signal_strength"])
            return {
                "product_url": raw_c["product_url"],
                "action_type": raw_c["action_type"],
                "urgency": raw_c["urgency"],
                "confidence": raw_c["confidence"],
                "expected_loss": 100.0,
            }
        _patch_all_stages(
            monkeypatch,
            signals=[{"any": "x"}],
            supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={("url", "ACT"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=raw_25,
            enrichment={"vps_map": {}, "ml_map": {}},
            final_builder=_builder,
        )
        out = ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        assert len(built) == 20, (
            f"expected exactly 20 candidates after cap, got {len(built)}"
        )
        # Cap keeps highest signal_strength (24..5 inclusive).
        assert sorted(built, reverse=True) == built  # already sorted desc
        assert min(built) == 5.0
        assert max(built) == 24.0
        assert len(out) == 20


# ---------------------------------------------------------------------------
# Stage wiring — each stage receives upstream stage's output
# ---------------------------------------------------------------------------


class TestStageWiring:
    def test_signal_rows_flow_to_bucket_builder(self, monkeypatch):
        sentinel_signal = {"product_url": "/products/x", "signal_type": "HIGH_ENGAGEMENT_NO_ACTION",
                           "signal_strength": 0.9, "explanation": ""}
        captured = {"signals": None, "metrics_map": None, "pi_map": None}

        def _bucket_builder(sigs, m, p):
            captured["signals"] = sigs
            captured["metrics_map"] = m
            captured["pi_map"] = p
            return {}

        monkeypatch.setattr(ace, "get_shop_aov", lambda db, s: 42.0)
        monkeypatch.setattr(ace, "get_real_product_conversion_map", lambda db, s: {})
        monkeypatch.setattr(ace, "get_or_train_model", lambda db, s: object())
        monkeypatch.setattr(ace, "_maybe_refresh_signals", lambda s: None)
        monkeypatch.setattr(ace, "_fetch_active_signals",
                            lambda db, s, now: [sentinel_signal])
        monkeypatch.setattr(ace, "_fetch_supporting_tables", lambda db, s: {
            "metrics_map": {"M": 1}, "pi_map": {"P": 2}, "upd_map": {"U": 3},
        })
        monkeypatch.setattr(ace, "_build_signal_buckets", _bucket_builder)

        ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        assert captured["signals"] == [sentinel_signal]
        assert captured["metrics_map"] == {"M": 1}
        assert captured["pi_map"] == {"P": 2}

    def test_supporting_flows_to_gate_application(self, monkeypatch):
        sentinel_supporting = {
            "metrics_map": {"X": 1}, "pi_map": {"Y": 2}, "upd_map": {"Z": 3},
        }
        captured = {"sup": None, "buckets": None}

        def _gates(buckets, sup):
            captured["sup"] = sup
            captured["buckets"] = buckets
            return []

        _patch_all_stages(
            monkeypatch,
            signals=[{"any": "x"}],
            supporting=sentinel_supporting,
            buckets={("url", "ACT"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=[],
            enrichment={"vps_map": {}, "ml_map": {}},
        )
        monkeypatch.setattr(ace, "_apply_action_gates", _gates)
        ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        assert captured["sup"] == sentinel_supporting
        assert ("url", "ACT") in captured["buckets"]

    def test_enrichment_passed_to_final_builder(self, monkeypatch):
        sentinel_enrichment = {"vps_map": {"V": 1}, "ml_map": {"M": 2}}
        captured = {"enrich": None, "aov": None}

        def _builder(raw_c, enrich, *, real_conv_map, calibration, aov):
            captured["enrich"] = enrich
            captured["aov"] = aov
            return {
                "product_url": raw_c["product_url"],
                "action_type": raw_c["action_type"],
                "urgency": raw_c["urgency"], "confidence": raw_c["confidence"],
                "expected_loss": 100.0,
            }

        _patch_all_stages(
            monkeypatch,
            signals=[{"any": "x"}],
            supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={("url", "ACT"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=[{
                "product_url": "/products/x", "action_type": "SCARCITY_NUDGE",
                "supporting_signals": [], "confidence": 0.5, "urgency": 50.0,
                "signal_strength": 0.9, "_metrics": {}, "_pi": {}, "_upd": {},
            }],
            enrichment=sentinel_enrichment,
            final_builder=_builder,
        )
        ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        assert captured["enrich"] == sentinel_enrichment
        assert captured["aov"] == 42.0


# ---------------------------------------------------------------------------
# Final builder may return None (e.g. FLASH_INCENTIVE post-gate) → filtered
# ---------------------------------------------------------------------------


class TestFinalBuilderNoneFiltered:
    def test_none_returns_dropped_from_final_list(self, monkeypatch):
        """_build_final_candidate may return None for post-enrichment
        gates (e.g. FLASH_INCENTIVE urgency<60). Composer MUST filter."""
        raw_3 = [
            {
                "product_url": f"/products/p{i}", "action_type": "FLASH_INCENTIVE",
                "supporting_signals": [], "confidence": 0.5, "urgency": 50.0,
                "signal_strength": float(i),
                "_metrics": {}, "_pi": {}, "_upd": {},
            }
            for i in range(3)
        ]

        def _builder(raw_c, *_a, **_kw):
            # Reject the middle candidate
            if raw_c["product_url"] == "/products/p1":
                return None
            return {
                "product_url": raw_c["product_url"],
                "action_type": raw_c["action_type"],
                "urgency": raw_c["urgency"], "confidence": raw_c["confidence"],
                "expected_loss": 100.0,
            }

        _patch_all_stages(
            monkeypatch,
            signals=[{"any": "x"}],
            supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={("url", "ACT"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=raw_3,
            enrichment={"vps_map": {}, "ml_map": {}},
            final_builder=_builder,
        )
        out = ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        urls = {c["product_url"] for c in out}
        assert "/products/p1" not in urls
        assert urls == {"/products/p0", "/products/p2"}


# ---------------------------------------------------------------------------
# Rank + effectiveness wiring (the final stage of the composer)
# ---------------------------------------------------------------------------


class TestRankAndEffectiveness:
    def test_rank_attached_in_descending_score_order(self, monkeypatch):
        """rank=1 goes to the highest rank_score; rank monotonically
        increases as we walk the returned list."""
        raw_3 = [
            {
                "product_url": f"/products/p{i}", "action_type": "SCARCITY_NUDGE",
                "supporting_signals": [], "confidence": 0.9, "urgency": 90.0,
                "signal_strength": 0.9,
                "_metrics": {}, "_pi": {}, "_upd": {},
            }
            for i in range(3)
        ]

        def _builder(raw_c, *_a, **_kw):
            # Build candidates with different urgency so ranks differ.
            idx = int(raw_c["product_url"][-1])
            return {
                "product_url": raw_c["product_url"],
                "action_type": raw_c["action_type"],
                "urgency": 90.0 - idx * 10.0,   # p0=90, p1=80, p2=70
                "confidence": 0.9,
                "expected_loss": 100.0,
            }

        _patch_all_stages(
            monkeypatch,
            signals=[{"any": "x"}],
            supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={("u", "A"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=raw_3,
            enrichment={"vps_map": {}, "ml_map": {}},
            final_builder=_builder,
        )
        out = ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        ranks = [c["rank"] for c in out]
        assert ranks == [1, 2, 3]
        # Highest urgency first
        assert out[0]["product_url"] == "/products/p0"
        assert out[-1]["product_url"] == "/products/p2"

    def test_historical_effectiveness_attached_only_when_action_type_in_map(self, monkeypatch):
        raw_2 = [
            {
                "product_url": "/products/scarcity", "action_type": "SCARCITY_NUDGE",
                "supporting_signals": [], "confidence": 0.7, "urgency": 70.0,
                "signal_strength": 0.7,
                "_metrics": {}, "_pi": {}, "_upd": {},
            },
            {
                "product_url": "/products/retarget", "action_type": "RETARGET_HOT_TRAFFIC",
                "supporting_signals": [], "confidence": 0.6, "urgency": 60.0,
                "signal_strength": 0.6,
                "_metrics": {}, "_pi": {}, "_upd": {},
            },
        ]
        _patch_all_stages(
            monkeypatch,
            signals=[{"any": "x"}],
            supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={("u", "A"): {"signal_strength": 1.0, "supporting_signals": []}},
            raw=raw_2,
            enrichment={"vps_map": {}, "ml_map": {}},
            eff_map={"SCARCITY_NUDGE": 0.55},  # only one action type has history
        )
        out = ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        by_at = {c["action_type"]: c for c in out}
        assert "historical_effectiveness" in by_at["SCARCITY_NUDGE"]
        assert by_at["SCARCITY_NUDGE"]["historical_effectiveness"] == 0.55
        assert "historical_effectiveness" not in by_at["RETARGET_HOT_TRAFFIC"]


# ---------------------------------------------------------------------------
# Refresh-signals invocation contract
# ---------------------------------------------------------------------------


class TestRefreshSignals:
    def test_maybe_refresh_signals_called_exactly_once_per_invocation(self, monkeypatch):
        calls = _patch_all_stages(
            monkeypatch,
            signals=[], supporting={"metrics_map": {}, "pi_map": {}, "upd_map": {}},
            buckets={}, raw=[], enrichment={"vps_map": {}, "ml_map": {}},
        )
        ace.generate_action_candidates("ok-shop.myshopify.com", db=None)
        refresh_calls = [c for c in calls if c[0] == "_maybe_refresh_signals"]
        assert len(refresh_calls) == 1
        assert refresh_calls[0][1] == ("ok-shop.myshopify.com",)
