"""
Unit tests for the section helpers extracted from `_render_beta_welcome`
in the 2026-05-13 A3 refactor.

The composer is locked by test_beta_welcome_composer.py. This file is
the structural-unit gate: each pure section helper produces the exact
HTML it owns. A regression in a section's copy or accent fails here
before the composer test runs.
"""
from __future__ import annotations

from app.services.email_templates import (
    _beta_welcome_advantage_html,
    _beta_welcome_command_center_html,
    _beta_welcome_cta_and_signature_html,
    _beta_welcome_deliverability_html,
    _beta_welcome_greeting,
    _beta_welcome_how_we_build_html,
    _beta_welcome_intro_html,
    _beta_welcome_plain_text,
    _beta_welcome_security_html,
    _beta_welcome_what_happens_html,
    _beta_welcome_what_we_do_html,
)


# ---------------------------------------------------------------------------
# Greeting
# ---------------------------------------------------------------------------


class TestGreeting:
    def test_named_greeting(self):
        assert _beta_welcome_greeting("Marco") == "Hi Marco,"

    def test_empty_name_fallback(self):
        assert _beta_welcome_greeting("") == "Hi,"

    def test_whitespace_name_treated_as_present(self):
        # Composer strips none; we keep behavior identical to prior
        assert _beta_welcome_greeting("Marco Rossi") == "Hi Marco Rossi,"


# ---------------------------------------------------------------------------
# Intro
# ---------------------------------------------------------------------------


class TestIntro:
    def test_named_merchant_in_intro(self):
        out = _beta_welcome_intro_html("Marco")
        assert "Hi Marco," in out
        assert "carefully selected" in out
        assert "private beta" in out

    def test_unnamed_merchant_in_intro(self):
        out = _beta_welcome_intro_html("")
        assert "Hi," in out
        assert "Hi Marco" not in out

    def test_ambition_statement_present(self):
        out = _beta_welcome_intro_html("Marco")
        assert "most technically advanced" in out
        assert "real merchants" in out

    def test_no_step_blocks_in_intro(self):
        out = _beta_welcome_intro_html("Marco")
        # Intro must NOT carry step content (sections are isolated)
        assert "We connect to your store" not in out


# ---------------------------------------------------------------------------
# What HedgeSpark does
# ---------------------------------------------------------------------------


class TestWhatWeDo:
    def test_section_title_present(self):
        out = _beta_welcome_what_we_do_html()
        assert "What HedgeSpark does" in out

    def test_three_bullets_present(self):
        out = _beta_welcome_what_we_do_html()
        assert "purchase intent that aren't converting" in out
        assert "revenue is leaking" in out
        assert "targeted nudges" in out

    def test_revenue_focus_closing(self):
        out = _beta_welcome_what_we_do_html()
        assert "more revenue from the traffic you already have" in out


# ---------------------------------------------------------------------------
# What happens when you start (6 steps)
# ---------------------------------------------------------------------------


class TestWhatHappens:
    def test_section_title_present(self):
        out = _beta_welcome_what_happens_html("MyShop")
        assert "What happens when you start" in out

    def test_shop_name_interpolated_in_step_1(self):
        out = _beta_welcome_what_happens_html("MyShop")
        assert "MyShop" in out

    def test_shop_name_only_used_once_in_step_1(self):
        # MyShop appears only in step 1 — not in 5 other steps
        out = _beta_welcome_what_happens_html("UniqueShopName123")
        assert out.count("UniqueShopName123") == 1

    def test_all_six_steps_have_titles(self):
        out = _beta_welcome_what_happens_html("X")
        assert "We connect to your store" in out
        assert "Visitor tracking activates" in out
        assert "You install the purchase pixel" in out
        assert "Lite insights start appearing" in out
        assert "Pro features unlock progressively" in out
        assert "The system compounds" in out


# ---------------------------------------------------------------------------
# How we build
# ---------------------------------------------------------------------------


class TestHowWeBuild:
    def test_section_title_present(self):
        out = _beta_welcome_how_we_build_html()
        assert "How we build" in out

    def test_layered_architecture_mentioned(self):
        out = _beta_welcome_how_we_build_html()
        assert "layered architecture" in out
        assert "every week" in out


# ---------------------------------------------------------------------------
# Command center
# ---------------------------------------------------------------------------


class TestCommandCenter:
    def test_section_title_present(self):
        out = _beta_welcome_command_center_html()
        assert "Your command center" in out

    def test_chatbot_primary_interface_called_out(self):
        out = _beta_welcome_command_center_html()
        assert "in-app chatbot" in out
        assert "primary" in out

    def test_support_email_link_present(self):
        out = _beta_welcome_command_center_html()
        assert "mailto:" in out
        assert "support@hedgesparkhq.com" in out or "@hedgesparkhq.com" in out


# ---------------------------------------------------------------------------
# Deliverability
# ---------------------------------------------------------------------------


class TestDeliverability:
    def test_section_title_present(self):
        out = _beta_welcome_deliverability_html()
        assert "Make sure you receive our emails" in out

    def test_sender_addresses_called_out(self):
        out = _beta_welcome_deliverability_html()
        assert "digest@hedgesparkhq.com" in out
        assert "dev@hedgesparkhq.com" in out

    def test_spam_rescue_instructions_present(self):
        out = _beta_welcome_deliverability_html()
        assert "Add both addresses to your contacts" in out
        assert "Not spam" in out


# ---------------------------------------------------------------------------
# Advantage
# ---------------------------------------------------------------------------


class TestAdvantage:
    def test_section_title_present(self):
        out = _beta_welcome_advantage_html()
        assert "Your beta advantage" in out

    def test_four_benefit_bullets(self):
        out = _beta_welcome_advantage_html()
        assert "Full access" in out
        assert "Significant discounts" in out
        assert "Priority access" in out
        assert "Direct influence" in out

    def test_not_symbolic_anchor_phrase(self):
        out = _beta_welcome_advantage_html()
        assert "not symbolic" in out


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_section_title_present(self):
        out = _beta_welcome_security_html()
        assert "Confidentiality & security" in out

    def test_gdpr_mentioned(self):
        out = _beta_welcome_security_html()
        assert "GDPR" in out

    def test_invite_only_callout(self):
        out = _beta_welcome_security_html()
        assert "invite-only" in out

    def test_trust_is_earned_statement(self):
        out = _beta_welcome_security_html()
        assert "Trust is something we earn" in out


# ---------------------------------------------------------------------------
# CTA + signature
# ---------------------------------------------------------------------------


class TestCtaAndSignature:
    def test_cta_button_present(self):
        out = _beta_welcome_cta_and_signature_html()
        assert "Start your onboarding" in out

    def test_dashboard_url_in_cta(self):
        out = _beta_welcome_cta_and_signature_html()
        assert "app.hedgesparkhq.com" in out

    def test_signature_andrea(self):
        out = _beta_welcome_cta_and_signature_html()
        assert "Andrea" in out
        assert "Looking forward to building this together" in out


# ---------------------------------------------------------------------------
# Plain-text mirror
# ---------------------------------------------------------------------------


class TestPlainText:
    def test_named_greeting_at_top(self):
        out = _beta_welcome_plain_text("Marco")
        assert out.startswith("Hi Marco,\n\n")

    def test_unnamed_greeting(self):
        out = _beta_welcome_plain_text("")
        assert out.startswith("Hi,\n\n")

    def test_all_section_headers_in_caps(self):
        out = _beta_welcome_plain_text("Marco")
        assert "WHAT HEDGESPARK DOES" in out
        assert "WHAT HAPPENS WHEN YOU START" in out
        assert "HOW WE BUILD" in out
        assert "YOUR COMMAND CENTER" in out
        assert "MAKE SURE YOU RECEIVE OUR EMAILS" in out
        assert "YOUR BETA ADVANTAGE" in out
        assert "CONFIDENTIALITY & SECURITY" in out

    def test_six_numbered_steps(self):
        out = _beta_welcome_plain_text("Marco")
        for n in range(1, 7):
            assert f"{n}. " in out

    def test_dashboard_url_in_plain(self):
        out = _beta_welcome_plain_text("Marco")
        assert "app.hedgesparkhq.com" in out

    def test_signature_in_plain(self):
        out = _beta_welcome_plain_text("Marco")
        assert "Andrea" in out
        assert "HedgeSpark" in out
