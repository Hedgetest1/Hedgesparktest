"""
Unit tests for the section-dispatch architecture introduced in the
2026-05-12 A3 refactor (commit 316e8c7).

End-to-end coverage exists wherever the weekly digest assembles
(weekly_digest_worker integration tests); this file is the structural
unit gate for:
  - `_ctx_from` — digest dict → typed _Ctx assembly
  - `_PLAIN_ORDER` / `_HTML_ORDER` — section ordering tuples
  - `format_digest` — composer respecting section skip-when-empty
"""
from __future__ import annotations

from app.services.digest_formatter import (
    _ctx_from,
    _HTML_ORDER,
    _PLAIN_ORDER,
    format_digest,
)


# ---------------------------------------------------------------------------
# _ctx_from
# ---------------------------------------------------------------------------


def _minimal_digest(**overrides) -> dict:
    """Build a digest dict with the minimum required fields, overridable."""
    base = {
        "shop_domain": "example.myshopify.com",
        "currency": "USD",
        "period_start": "2026-05-06",
        "period_end": "2026-05-12",
        "this_week": {"revenue": 1000.0, "order_count": 10, "aov": 100.0},
        "last_week": {"revenue": 800.0, "order_count": 8, "aov": 100.0},
    }
    base.update(overrides)
    return base


class TestCtxFrom:
    def test_shop_normalization_strips_myshopify_suffix(self):
        ctx = _ctx_from(_minimal_digest(shop_domain="example.myshopify.com"))
        assert ctx.shop == "example"

    def test_period_concatenates_dates(self):
        ctx = _ctx_from(_minimal_digest())
        assert ctx.period == "2026-05-06 – 2026-05-12"

    def test_plan_defaults_to_lite(self):
        ctx = _ctx_from(_minimal_digest())
        assert ctx.plan == "lite"

    def test_plan_override(self):
        ctx = _ctx_from(_minimal_digest(merchant_plan="pro"))
        assert ctx.plan == "pro"

    def test_visitors_default_zero(self):
        ctx = _ctx_from(_minimal_digest())
        assert ctx.visitors == 0

    def test_empty_optionals_default_to_empty_structures(self):
        ctx = _ctx_from(_minimal_digest())
        assert ctx.risk == {}
        assert ctx.rars_hero == {}
        assert ctx.goal_progress == []
        assert ctx.sip_insights == []
        assert ctx.top_products == []
        assert ctx.rec is None
        assert ctx.whats_working is None

    def test_data_confidence_default_solid(self):
        ctx = _ctx_from(_minimal_digest())
        assert ctx.confidence == "solid"

    def test_delta_passed_through(self):
        ctx = _ctx_from(_minimal_digest(revenue_delta_pct=25))
        assert ctx.delta == 25


# ---------------------------------------------------------------------------
# Section order registries
# ---------------------------------------------------------------------------


class TestSectionOrder:
    def test_plain_order_is_tuple(self):
        assert isinstance(_PLAIN_ORDER, tuple)
        assert len(_PLAIN_ORDER) >= 10  # At least 10 sections

    def test_html_order_is_tuple(self):
        assert isinstance(_HTML_ORDER, tuple)
        assert len(_HTML_ORDER) >= 10

    def test_plain_renderers_are_callable(self):
        for renderer in _PLAIN_ORDER:
            assert callable(renderer)

    def test_html_renderers_are_callable(self):
        for renderer in _HTML_ORDER:
            assert callable(renderer)

    def test_plain_starts_with_header(self):
        # Header is always first
        assert _PLAIN_ORDER[0].__name__ == "_plain_header"

    def test_plain_ends_with_footer(self):
        # Footer is always last
        assert _PLAIN_ORDER[-1].__name__ == "_plain_footer"

    def test_html_starts_with_header(self):
        assert _HTML_ORDER[0].__name__ == "_html_header"

    def test_html_ends_with_cta(self):
        # CTA is always last in HTML
        assert _HTML_ORDER[-1].__name__ == "_html_cta"


# ---------------------------------------------------------------------------
# Per-renderer skip-on-empty contract — each section renderer must return
# "" when its data is empty, so the composer omits it cleanly.
# ---------------------------------------------------------------------------


class TestSkipOnEmpty:
    """Renderers with optional data sources must return '' when missing.
    The header/footer/upgrade/cta are always-present sections, so they
    are exempted from this check."""

    _ALWAYS_PRESENT = {
        # These renderers are always non-empty by design — header/footer
        # render the period summary, CTA always invites the user,
        # upgrade always renders if plan==lite (default), and cold_start
        # is the fallback that fires when the shop has insufficient data
        # for richer sections.
        "_plain_header",
        "_plain_footer",
        "_plain_upgrade",
        "_plain_cold_start",
        "_html_header",
        "_html_cta",
        "_html_upgrade",
        "_html_cold_start",
    }

    def test_plain_renderers_skip_when_data_empty(self):
        ctx = _ctx_from(_minimal_digest())
        for renderer in _PLAIN_ORDER:
            if renderer.__name__ in self._ALWAYS_PRESENT:
                continue
            result = renderer(ctx)
            assert result == "", (
                f"{renderer.__name__} returned non-empty on empty data: {result!r}"
            )

    def test_html_renderers_skip_when_data_empty(self):
        ctx = _ctx_from(_minimal_digest())
        for renderer in _HTML_ORDER:
            if renderer.__name__ in self._ALWAYS_PRESENT:
                continue
            result = renderer(ctx)
            assert result == "", (
                f"{renderer.__name__} returned non-empty on empty data: {result!r}"
            )


# ---------------------------------------------------------------------------
# format_digest — composer
# ---------------------------------------------------------------------------


class TestFormatDigest:
    def test_returns_html_and_plain_tuple(self):
        result = format_digest(_minimal_digest())
        assert isinstance(result, tuple)
        assert len(result) == 2
        html, plain = result
        assert isinstance(html, str)
        assert isinstance(plain, str)

    def test_plain_contains_header_content(self):
        _, plain = format_digest(_minimal_digest())
        # Shop normalized, period rendered, revenue summary
        assert "example" in plain
        assert "2026-05-06" in plain
        assert "1,000.00" in plain  # revenue formatted

    def test_html_wrapped_with_logo(self):
        html, _ = format_digest(_minimal_digest())
        # _wrap_html with show_logo=True; the title contains the shop name
        assert "example" in html

    def test_recommendation_section_included_when_present(self):
        digest = _minimal_digest(recommendation={
            "headline": "Add a hero banner",
            "body": "Visitors drop off at first scroll.",
        })
        _, plain = format_digest(digest)
        assert "Add a hero banner" in plain

    def test_recommendation_section_omitted_when_missing(self):
        _, plain = format_digest(_minimal_digest())
        assert "Add a hero banner" not in plain
