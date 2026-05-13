"""
Unit tests for the pure helpers extracted from `get_active_nudge_public`
in the 2026-05-13 A3 refactor.

This is the first test coverage for the public nudge endpoint helpers.
The endpoint itself is a storefront hot path (polled by spark-nudge.js)
so every branch (no-visitor / ineligible / holdout / eligible) needs
isolation-level coverage.
"""
from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

from app.api.nudges import (
    _build_eligible_response,
    _build_gating_block,
    _build_holdout_response,
    _build_product_level_response,
    _normalize_product_url,
    _resolve_default_variant,
    _resolve_eligible_variant,
)


# ---------------------------------------------------------------------------
# _normalize_product_url
# ---------------------------------------------------------------------------


class TestNormalizeProductUrl:
    def test_already_canonical_passes_through_unchanged(self):
        # The helper only normalizes paths that don't start with /products/.
        # An already-canonical path passes through verbatim (including any
        # query string or fragment — those are tracker-callbacks
        # responsibility to handle).
        assert _normalize_product_url("/products/wallet") == "/products/wallet"
        assert _normalize_product_url("/products/wallet?v=42") == "/products/wallet?v=42"

    def test_full_url_extracts_canonical_path(self):
        # Full URL with /products/{handle} → canonical extraction
        out = _normalize_product_url("https://shop.myshopify.com/products/wallet")
        assert out == "/products/wallet"

    def test_full_url_strips_query_string_via_extraction(self):
        # Non-canonical input with query → extracted to canonical form
        out = _normalize_product_url("https://shop.myshopify.com/products/wallet?variant=42")
        assert out == "/products/wallet"

    def test_full_url_strips_fragment_via_extraction(self):
        out = _normalize_product_url("https://shop.myshopify.com/products/wallet#reviews")
        assert out == "/products/wallet"

    def test_no_products_path_returns_original(self):
        # If no /products/ segment found, we don't invent one
        assert _normalize_product_url("/collections/bags") == "/collections/bags"

    def test_handles_unusual_input_defensively(self):
        # Should not crash on empty / weird input
        out = _normalize_product_url("")
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# _resolve_default_variant — no-visitor + non-AB paths
# ---------------------------------------------------------------------------


def _nudge_stub(copy_variant="primary", copy_config_dict=None, holdout_pct=0,
                expires_at=None, nudge_id=42):
    return SimpleNamespace(
        id=nudge_id,
        copy_variant=copy_variant,
        copy_config_dict=lambda: (copy_config_dict or {"headline": "Primary"}),
        holdout_pct=holdout_pct,
        expires_at=expires_at,
    )


class TestDefaultVariant:
    def test_ab_returns_index_zero(self):
        variants = [
            {"variant_name": "A", "copy_config": {"headline": "First"}},
            {"variant_name": "B", "copy_config": {"headline": "Second"}},
        ]
        out = _resolve_default_variant(_nudge_stub(), variants, is_ab=True)
        assert out["variant_name"] == "A"

    def test_non_ab_uses_legacy_primary(self):
        out = _resolve_default_variant(
            _nudge_stub(copy_variant="legacy_primary",
                        copy_config_dict={"x": 1}),
            variants=[], is_ab=False,
        )
        assert out["variant_name"] == "legacy_primary"
        assert out["copy_config"] == {"x": 1}


# ---------------------------------------------------------------------------
# _build_product_level_response
# ---------------------------------------------------------------------------


class TestProductLevelResponse:
    def test_shape(self):
        nudge = _nudge_stub(expires_at=datetime(2025, 6, 1))
        assigned = {"variant_name": "primary", "copy_config": {"headline": "X"}}
        out = _build_product_level_response(nudge, assigned, is_ab=False)
        assert out["active"] is True
        assert out["eligible"] is True
        assert out["render_allowed"] is True
        assert out["nudge_id"] == 42
        assert out["copy_variant"] == "primary"
        assert out["copy_config"] == {"headline": "X"}
        # Expires ISO + Z suffix
        assert out["expires_at"].endswith("Z")

    def test_ab_flag_when_ab(self):
        nudge = _nudge_stub()
        assigned = {"variant_name": "A", "copy_config": {"x": 1}}
        out = _build_product_level_response(nudge, assigned, is_ab=True)
        assert out["ab_experiment"] is True

    def test_no_ab_flag_when_not_ab(self):
        nudge = _nudge_stub()
        assigned = {"variant_name": "primary", "copy_config": {"x": 1}}
        out = _build_product_level_response(nudge, assigned, is_ab=False)
        assert "ab_experiment" not in out

    def test_invalid_copy_config_falls_back_to_nudge_default(self):
        nudge = _nudge_stub(copy_config_dict={"fallback": True})
        assigned = {"variant_name": "X", "copy_config": "not-a-dict"}
        out = _build_product_level_response(nudge, assigned, is_ab=False)
        assert out["copy_config"] == {"fallback": True}

    def test_no_expires_at_is_none(self):
        nudge = _nudge_stub(expires_at=None)
        assigned = {"variant_name": "X", "copy_config": {}}
        out = _build_product_level_response(nudge, assigned, is_ab=False)
        assert out["expires_at"] is None


# ---------------------------------------------------------------------------
# _build_gating_block
# ---------------------------------------------------------------------------


class TestGatingBlock:
    def test_shape(self):
        decision = {
            "gating_source": "behavioral_index",
            "visitor_behavioral_index": 0.85,
            "threshold_used": 0.55,
            "calibration_state": "calibrated",
            "reason": "eligible_warm",
            "data_points": 42,
            "eligible": True,
        }
        out = _build_gating_block(decision)
        # All 6 keys present, `eligible` NOT included
        assert set(out.keys()) == {
            "source", "visitor_behavioral_index", "threshold_used",
            "calibration_state", "reason", "data_points",
        }
        assert "eligible" not in out


# ---------------------------------------------------------------------------
# _build_holdout_response
# ---------------------------------------------------------------------------


class TestHoldoutResponse:
    def test_shape(self):
        nudge = _nudge_stub(nudge_id=99)
        gating = {"source": "x", "visitor_behavioral_index": 0.7, "threshold_used": 0.5,
                  "calibration_state": "c", "reason": "r", "data_points": 1}
        out = _build_holdout_response(nudge, gating)
        assert out == {
            "active": True,
            "eligible": True,
            "render_allowed": False,
            "holdout": True,
            "nudge_id": 99,
            "gating": gating,
        }

    def test_render_allowed_false_is_the_critical_invariant(self):
        # Holdout = the WHOLE POINT is render_allowed=false. A regression
        # here would silently show nudges to control-group visitors and
        # corrupt every measurement.
        nudge = _nudge_stub()
        gating = {"source": "x", "visitor_behavioral_index": 0.7, "threshold_used": 0.5,
                  "calibration_state": "c", "reason": "r", "data_points": 1}
        out = _build_holdout_response(nudge, gating)
        assert out["render_allowed"] is False
        assert out["holdout"] is True


# ---------------------------------------------------------------------------
# _resolve_eligible_variant
# ---------------------------------------------------------------------------


class TestEligibleVariant:
    def test_non_ab_uses_legacy_variant(self):
        nudge = _nudge_stub(copy_variant="solo", copy_config_dict={"k": "v"})
        name, cfg = _resolve_eligible_variant(
            visitor_id="v_x", nudge=nudge, variants=[], is_ab=False,
        )
        assert name == "solo"
        assert cfg == {"k": "v"}

    def test_invalid_copy_config_falls_back_to_default(self, monkeypatch):
        # If _assign_variant returns a malformed copy_config, the helper
        # must fall back to the nudge's primary config.
        import app.api.nudges as nudges_mod
        monkeypatch.setattr(
            nudges_mod, "_assign_variant",
            lambda vid, nid, variants: {"variant_name": "broken", "copy_config": "garbage"},
        )
        nudge = _nudge_stub(copy_variant="primary", copy_config_dict={"safe": True})
        variants = [{"variant_name": "A", "copy_config": {"x": 1}},
                    {"variant_name": "B", "copy_config": {"y": 2}}]
        name, cfg = _resolve_eligible_variant(
            visitor_id="v_x", nudge=nudge, variants=variants, is_ab=True,
        )
        # Variant name preserved from assignment
        assert name == "broken"
        # But config falls back
        assert cfg == {"safe": True}


# ---------------------------------------------------------------------------
# _build_eligible_response
# ---------------------------------------------------------------------------


class TestEligibleResponse:
    def test_shape_with_gating(self):
        nudge = _nudge_stub(expires_at=datetime(2025, 6, 1))
        gating = {"source": "x", "visitor_behavioral_index": 0.85,
                  "threshold_used": 0.5, "calibration_state": "c",
                  "reason": "r", "data_points": 1}
        out = _build_eligible_response(
            nudge=nudge, variant_name="primary",
            copy_config={"k": "v"}, gating_block=gating, is_ab=False,
        )
        assert out["active"] is True
        assert out["eligible"] is True
        assert out["render_allowed"] is True
        assert out["nudge_id"] == 42
        assert out["copy_variant"] == "primary"
        assert out["copy_config"] == {"k": "v"}
        assert out["gating"] == gating
        assert "ab_experiment" not in out

    def test_ab_flag_present_when_ab(self):
        nudge = _nudge_stub()
        out = _build_eligible_response(
            nudge=nudge, variant_name="A", copy_config={"x": 1},
            gating_block={"source": "x", "visitor_behavioral_index": 0.85,
                          "threshold_used": 0.5, "calibration_state": "c",
                          "reason": "r", "data_points": 1},
            is_ab=True,
        )
        assert out["ab_experiment"] is True
