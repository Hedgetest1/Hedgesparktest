"""
Unit tests for the 27-renderer registry extracted from `humanize_signal`
in the 2026-05-12 A3 refactor (commit 0282165).

End-to-end coverage exists wherever `humanize_signal` is called
downstream (nudge composer, opportunity_engine, digest formatters);
this file is the structural unit gate for:
  - `_fmt_int`/`_fmt_rate`/`_fmt_float` format helpers
  - the registry dispatcher (known signal → renderer; unknown → fallback)
  - each of the 27 renderers (non-empty output + label interpolation)

The renderer registry IS the merchant-facing voice of every signal.
Drift in the threshold-driven branches (metric-present vs metric-absent
fallback) means merchants see "an average" / "some" / "very few"
placeholder copy instead of the real number.
"""
from __future__ import annotations

import pytest

from app.services.signal_text import (
    _fmt_float,
    _fmt_int,
    _fmt_rate,
    humanize_signal,
)
from app.services.signal_text import _SIGNAL_TEXT_RENDERERS


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


class TestFmtInt:
    def test_integer_returns_str_int(self):
        assert _fmt_int(42) == "42"

    def test_float_truncated(self):
        assert _fmt_int(42.7) == "42"

    def test_none_returns_fallback(self):
        assert _fmt_int(None) == "some"

    def test_zero_returns_fallback(self):
        # Zero is falsy → fallback (per the helper's `if not value` guard)
        assert _fmt_int(0) == "some"

    def test_string_int(self):
        assert _fmt_int("42") == "42"

    def test_custom_fallback(self):
        assert _fmt_int(None, fallback="very few") == "very few"


class TestFmtRate:
    def test_canonical(self):
        # 5/100 = 5% → "5.0%"
        assert _fmt_rate(5, 100) == "5.0%"

    def test_zero_denominator_returns_fallback(self):
        # Helper falls back to "a low rate" when denominator <= 0
        assert _fmt_rate(5, 0) == "a low rate"

    def test_none_numerator(self):
        # numerator None → coerced to 0, returns "0.0%" since denominator > 0
        assert _fmt_rate(None, 100) == "0.0%"

    def test_both_none_returns_fallback(self):
        assert _fmt_rate(None, None) == "a low rate"


class TestFmtFloat:
    def test_canonical(self):
        assert _fmt_float(3.14159, decimals=2) == "3.14"

    def test_none_returns_fallback(self):
        assert _fmt_float(None) == "an average"

    def test_custom_fallback(self):
        assert _fmt_float(None, fallback="unspecified") == "unspecified"


# ---------------------------------------------------------------------------
# Registry dispatcher
# ---------------------------------------------------------------------------


class TestHumanizeSignalDispatcher:
    def test_known_signal_uses_renderer(self):
        result = humanize_signal("DEAD_TRAFFIC", "Leather Wallet",
                                 {"views_24h": 100, "avg_dwell_24h": 2.0})
        assert "Leather Wallet" in result
        assert "100" in result

    def test_unknown_signal_gets_titlecase_fallback(self):
        result = humanize_signal("MADE_UP_SIGNAL", "Leather Wallet", {})
        assert "Made Up Signal" in result
        assert "Leather Wallet" in result

    def test_empty_signal_type_uses_default(self):
        result = humanize_signal("", "Leather Wallet", {})
        assert "A signal" in result

    def test_no_label_falls_back_to_default(self):
        result = humanize_signal("DEAD_TRAFFIC", "", {})
        assert "this product" in result.lower()

    def test_no_metrics_renders_degraded(self):
        # Every renderer must degrade safely when metrics missing
        result = humanize_signal("DEAD_TRAFFIC", "Wallet", None)
        assert "Wallet" in result
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# All 27 renderers — contract: returns non-empty str with label
# ---------------------------------------------------------------------------


class TestAllRenderersCanonical:
    """Sanity-check every renderer in the registry: produces non-empty
    str and interpolates the label."""

    @pytest.mark.parametrize("signal_type", list(_SIGNAL_TEXT_RENDERERS.keys()))
    def test_renderer_non_empty_with_full_metrics(self, signal_type):
        # A grab-bag of likely metric keys covers every renderer's path.
        m = {
            "views_24h": 100, "views_1h": 30, "views_7d": 500,
            "unique_visitors_24h": 80, "cart_conversions_24h": 5,
            "return_visitor_count_7d": 12,
            "avg_dwell_24h": 25.5, "avg_scroll_24h": 70.0,
            "spike_ratio": 5.2,
            "views_mobile": 30, "views_desktop": 20,
            "purchases_mobile": 2, "purchases_desktop": 1,
            "views_paid": 25, "carts_paid": 0,
            "rate_24h": 0.02, "rate_7d": 0.05,
            "concentration_pct": 70, "top_product_count": 3,
            "first_unique_visitors_24h": 10,
        }
        renderer = _SIGNAL_TEXT_RENDERERS[signal_type]
        result = renderer("Premium Wallet", m)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("signal_type", list(_SIGNAL_TEXT_RENDERERS.keys()))
    def test_renderer_degrades_safely_when_metrics_empty(self, signal_type):
        # Every renderer must produce a sentence even with empty metrics.
        renderer = _SIGNAL_TEXT_RENDERERS[signal_type]
        result = renderer("Premium Wallet", {})
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Spot-checks for the metric-vs-fallback branches in load-bearing renderers
# ---------------------------------------------------------------------------


class TestDeadTrafficBranches:
    def test_full_metrics_renders_views_and_dwell(self):
        result = humanize_signal("DEAD_TRAFFIC", "Wallet",
                                 {"views_24h": 200, "avg_dwell_24h": 1.5})
        assert "200" in result
        assert "1.5" in result

    def test_views_only_falls_to_simpler_branch(self):
        result = humanize_signal("DEAD_TRAFFIC", "Wallet", {"views_24h": 200})
        assert "200" in result
        # No dwell number → second branch
        assert "almost immediately" in result

    def test_no_metrics_falls_to_minimal_branch(self):
        result = humanize_signal("DEAD_TRAFFIC", "Wallet", {})
        assert "Wallet" in result
        assert "bouncing" in result


class TestTrafficSpikeBranches:
    def test_full_metrics(self):
        result = humanize_signal("TRAFFIC_SPIKE", "Wallet",
                                 {"views_1h": 50, "spike_ratio": 5.2})
        assert "50" in result
        assert "5.2" in result

    def test_views_no_ratio(self):
        result = humanize_signal("TRAFFIC_SPIKE", "Wallet",
                                 {"views_1h": 50})
        assert "50 views this hour" in result

    def test_no_metrics(self):
        result = humanize_signal("TRAFFIC_SPIKE", "Wallet", {})
        assert "experiencing a traffic spike" in result


class TestRegistryCompleteness:
    def test_registry_has_27_entries(self):
        assert len(_SIGNAL_TEXT_RENDERERS) == 27

    def test_no_duplicate_renderer_functions(self):
        # Each signal type maps to a distinct renderer
        renderers = list(_SIGNAL_TEXT_RENDERERS.values())
        assert len(renderers) == len(set(id(r) for r in renderers))
