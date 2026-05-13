"""
Composer-level integration tests for `_render_beta_welcome`.

The 2026-05-13 A3 refactor decomposed the 273-LOC god function into
a 20-LOC composer + 9 section helpers + plain-text mirror.
test_beta_welcome_helpers.py (38 tests) locks every helper in
isolation. This file locks the composition: ctx wiring (shop_name,
merchant_name), section ordering, subject constant, output type
contract.
"""
from __future__ import annotations

from app.services import email_templates as et


# ---------------------------------------------------------------------------
# Subject + return shape
# ---------------------------------------------------------------------------


class TestSubjectAndShape:
    def test_subject_constant(self):
        subject, _, _ = et._render_beta_welcome({"shop_name": "X", "merchant_name": "Y"})
        assert subject == "You're in — HedgeSpark Private Beta"

    def test_returns_three_strings(self):
        out = et._render_beta_welcome({"shop_name": "X", "merchant_name": "Y"})
        assert isinstance(out, tuple)
        assert len(out) == 3
        subject, html, plain = out
        assert isinstance(subject, str) and subject
        assert isinstance(html, str) and html
        assert isinstance(plain, str) and plain

    def test_subject_independent_of_ctx(self):
        s1, _, _ = et._render_beta_welcome({"shop_name": "A", "merchant_name": "B"})
        s2, _, _ = et._render_beta_welcome({"shop_name": "C", "merchant_name": "D"})
        assert s1 == s2  # subject doesn't interpolate ctx


# ---------------------------------------------------------------------------
# ctx propagation
# ---------------------------------------------------------------------------


class TestCtxPropagation:
    def test_merchant_name_used_in_greeting(self):
        _, html, plain = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "Marco"},
        )
        assert "Hi Marco," in html
        assert "Hi Marco," in plain

    def test_missing_merchant_name_falls_back(self):
        _, html, plain = et._render_beta_welcome(
            {"shop_name": "X"},  # no merchant_name
        )
        assert "Hi," in html
        assert "Hi," in plain
        assert "Hi Marco" not in html

    def test_empty_merchant_name_falls_back(self):
        _, html, plain = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": ""},
        )
        assert "Hi," in html
        assert "Hi," in plain

    def test_shop_name_in_step_1(self):
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "MyTestShop", "merchant_name": "M"},
        )
        assert "MyTestShop" in html

    def test_missing_shop_name_falls_back(self):
        _, html, _ = et._render_beta_welcome({"merchant_name": "M"})
        # Default fallback is "your store"
        assert "your store" in html


# ---------------------------------------------------------------------------
# Section ordering — HTML must include every section in order
# ---------------------------------------------------------------------------


class TestSectionOrdering:
    def _section_positions(self, html: str) -> list[int]:
        markers = [
            "What HedgeSpark does",
            "What happens when you start",
            "How we build",
            "Your command center",
            "Make sure you receive our emails",
            "Your beta advantage",
            "Confidentiality & security",
        ]
        return [html.index(m) for m in markers]

    def test_all_sections_present(self):
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "M"},
        )
        positions = self._section_positions(html)
        # All > -1 implies present (index would raise otherwise)
        assert all(p > 0 for p in positions)

    def test_sections_in_correct_order(self):
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "M"},
        )
        positions = self._section_positions(html)
        assert positions == sorted(positions), (
            f"section markers out of order: {positions}"
        )

    def test_intro_precedes_first_section(self):
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "Marco"},
        )
        greeting_pos = html.index("Hi Marco,")
        first_section_pos = html.index("What HedgeSpark does")
        assert greeting_pos < first_section_pos


# ---------------------------------------------------------------------------
# CTA + signature at the end
# ---------------------------------------------------------------------------


class TestCtaAndSignature:
    def test_cta_button_present(self):
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "M"},
        )
        assert "Start your onboarding" in html

    def test_andrea_signature_after_security(self):
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "M"},
        )
        sec_pos = html.index("Confidentiality & security")
        sig_pos = html.index("Andrea")
        assert sec_pos < sig_pos, "signature must follow security section"

    def test_cta_in_plain_text(self):
        _, _, plain = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "M"},
        )
        assert "Start your onboarding:" in plain


# ---------------------------------------------------------------------------
# HTML envelope — _wrap_html is invoked with show_logo=True
# ---------------------------------------------------------------------------


class TestHtmlEnvelope:
    def test_wrap_html_called_with_logo(self, monkeypatch):
        captured: dict = {"args": None, "kwargs": None}

        def _spy_wrap(subj, body, show_logo=False):
            captured["subj"] = subj
            captured["body"] = body
            captured["show_logo"] = show_logo
            return f"<WRAP subj='{subj}' logo={show_logo}>{body}</WRAP>"

        monkeypatch.setattr(et, "_wrap_html", _spy_wrap)
        _, html, _ = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "Marco"},
        )
        assert captured["subj"] == "You're in — HedgeSpark Private Beta"
        assert captured["show_logo"] is True
        # Body contains the intro greeting
        assert "Hi Marco," in captured["body"]
        # Returned html is the wrapped form
        assert html.startswith("<WRAP")
        assert html.endswith("</WRAP>")


# ---------------------------------------------------------------------------
# Plain-text mirror parity
# ---------------------------------------------------------------------------


class TestPlainTextMirror:
    def test_plain_starts_with_greeting(self):
        _, _, plain = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "Marco"},
        )
        assert plain.startswith("Hi Marco,")

    def test_plain_ends_with_signature(self):
        _, _, plain = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "Marco"},
        )
        assert plain.rstrip().endswith("HedgeSpark")

    def test_plain_contains_all_section_caps_in_order(self):
        _, _, plain = et._render_beta_welcome(
            {"shop_name": "X", "merchant_name": "Marco"},
        )
        markers = [
            "WHAT HEDGESPARK DOES",
            "WHAT HAPPENS WHEN YOU START",
            "HOW WE BUILD",
            "YOUR COMMAND CENTER",
            "MAKE SURE YOU RECEIVE OUR EMAILS",
            "YOUR BETA ADVANTAGE",
            "CONFIDENTIALITY & SECURITY",
        ]
        positions = [plain.index(m) for m in markers]
        assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# Determinism — same ctx, same output
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_invocations_identical(self):
        ctx = {"shop_name": "TestShop", "merchant_name": "Marco"}
        a = et._render_beta_welcome(dict(ctx))
        b = et._render_beta_welcome(dict(ctx))
        assert a == b
