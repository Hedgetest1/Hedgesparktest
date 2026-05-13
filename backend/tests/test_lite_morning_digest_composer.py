"""
Composer-level integration tests for `_build_email`.

The 2026-05-13 A3 refactor decomposed the 289-LOC god function into a
composer + 9 pure renderers. test_lite_morning_digest_helpers.py (38
tests) locks every renderer in isolation. This file locks the
*composition* — how the composer wires renderers, the clean-slate
short-circuit, plain-text mirror, and HTML envelope — so a future
refactor can prove the contract holds without booting Postgres.

Pattern: monkeypatch `_gather_rich_context` to return a deterministic
context dict, drive `_build_email` with a controlled brief, assert
subject/HTML/plain output across populated and clean-slate branches.
"""
from __future__ import annotations

from app.services import lite_morning_digest as lmd


# ---------------------------------------------------------------------------
# Helpers — context fixtures
# ---------------------------------------------------------------------------


def _ctx_populated() -> dict:
    return {
        "currency": "EUR",
        "rars": {
            "total": 1500.0,
            "prevented": 300.0,
            "components": [
                {"source": "abandoned_high_intent", "loss_eur": 800.0,
                 "narrative": "Carts left in checkout"},
                {"source": "refund_decline", "loss_eur": 400.0},
            ],
        },
        "benchmarks": {
            "band": "S-band",
            "peer_count": 50,
            "total_recovery": 1200.0,
            "metrics": {},
        },
        "retention": {"w1": 0.25, "w4": 0.10, "w12": 0.05, "best_cohort": "2025-W10"},
        "inventory": {
            "at_risk": [
                {"product_title": "Premium Wallet", "days_of_cover": 4.2},
            ],
            "out_of_stock_count": 2,
            "lead_time_days": 14,
            "tracked": 25,
        },
    }


def _ctx_empty() -> dict:
    return {"currency": "USD"}


def _brief_populated() -> dict:
    return {
        "signals_count": 5,
        "headline": "Cart slipping today",
        "top_product_label": "Premium Wallet",
        "top_action": "Add scarcity nudge",
    }


def _brief_empty() -> dict:
    return {"signals_count": 0, "headline": "", "top_product_label": "", "top_action": ""}


def _patch_context(monkeypatch, ctx: dict, *, stub_wrap: bool = True):
    """Bypass _gather_rich_context. Optionally short-circuit _wrap_html so
    the composer tests are hermetic from email-template internals."""
    monkeypatch.setattr(lmd, "_gather_rich_context", lambda db, s: dict(ctx))
    if stub_wrap:
        # Provide a deterministic wrap so tests can substring-match the body
        import app.services.email_templates as et
        monkeypatch.setattr(
            et, "_wrap_html",
            lambda subj, body, show_logo=True: f"<WRAP subj='{subj}' logo={show_logo}>{body}</WRAP>",
        )


# ---------------------------------------------------------------------------
# Subject + shop_name resolution
# ---------------------------------------------------------------------------


class TestSubjectAndShopName:
    def test_subject_uses_titlecased_shop_name(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        subject, _, _ = lmd._build_email(
            "my-test-shop.myshopify.com", _brief_empty(), db=None,
        )
        assert subject == "Your Morning Intelligence — My Test Shop"

    def test_shop_name_strips_myshopify_suffix(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        subject, _, _ = lmd._build_email(
            "premium-store.myshopify.com", _brief_empty(), db=None,
        )
        assert "Premium Store" in subject
        assert "myshopify" not in subject

    def test_html_uses_logo_envelope(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        _, html, _ = lmd._build_email("x.myshopify.com", _brief_empty(), db=None)
        assert "logo=True" in html


# ---------------------------------------------------------------------------
# Clean-slate branch
# ---------------------------------------------------------------------------


class TestCleanSlate:
    def test_clean_slate_html_uses_reassurance_block(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        _, html, _ = lmd._build_email("x.myshopify.com", _brief_empty(), db=None)
        assert "Clean slate" in html
        assert "Your funnel is healthy this morning" in html
        assert "Spark is watching" in html

    def test_clean_slate_html_excludes_section_renderers(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        _, html, _ = lmd._build_email("x.myshopify.com", _brief_empty(), db=None)
        # No risk / benchmark / retention / stock blocks
        assert "Revenue at Risk" not in html
        assert "Where it's leaking" not in html
        assert "You vs. Similar Shops" not in html
        assert "Retention · week 1 / 4 / 12" not in html
        assert "Stock health" not in html

    def test_clean_slate_plain_text_uses_short_message(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        _, _, plain = lmd._build_email("x.myshopify.com", _brief_empty(), db=None)
        assert "Clean slate — your funnel is healthy" in plain
        assert "REVENUE AT RISK" not in plain
        assert "WHERE IT'S LEAKING" not in plain

    def test_clean_slate_html_includes_cta(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        _, html, _ = lmd._build_email("x.myshopify.com", _brief_empty(), db=None)
        assert "Open your dashboard" in html

    def test_clean_slate_triggered_only_when_all_zero(self, monkeypatch):
        """signals_count=0 + rars_total=0 + no comps → clean-slate.
        Any one being non-zero flips to populated branch."""
        # Just signals — flips to populated
        _patch_context(monkeypatch, _ctx_empty())
        _, html, _ = lmd._build_email(
            "x.myshopify.com",
            {"signals_count": 1, "headline": "", "top_product_label": "", "top_action": ""},
            db=None,
        )
        assert "Clean slate" not in html


# ---------------------------------------------------------------------------
# Populated branch — every section wired in
# ---------------------------------------------------------------------------


class TestPopulatedComposition:
    def test_html_contains_all_sections_when_data_present(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_populated())
        _, html, _ = lmd._build_email(
            "x.myshopify.com", _brief_populated(), db=None,
        )
        assert "Revenue at Risk" in html
        assert "Today's lead story — Premium Wallet" in html
        assert "Where it's leaking" in html
        assert "You vs. Similar Shops" in html
        assert "Retention · week 1 / 4 / 12" in html
        assert "Stock health" in html
        assert "Open your dashboard" in html

    def test_plain_text_mirrors_populated_sections(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_populated())
        _, _, plain = lmd._build_email(
            "x.myshopify.com", _brief_populated(), db=None,
        )
        assert "REVENUE AT RISK" in plain
        assert "Today's lead story — Premium Wallet" in plain
        assert "WHERE IT'S LEAKING" in plain
        assert "YOU VS PEERS" in plain
        assert "RETENTION:" in plain
        assert "STOCK HEALTH" in plain
        assert "Dashboard:" in plain

    def test_currency_propagates_through_composer(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_populated())  # currency: EUR
        _, html, plain = lmd._build_email(
            "x.myshopify.com", _brief_populated(), db=None,
        )
        # EUR appears in both surfaces
        assert "EUR 1,500" in html
        assert "EUR 1,500" in plain

    def test_currency_defaults_to_usd_when_missing(self, monkeypatch):
        ctx = _ctx_populated()
        ctx["currency"] = None
        _patch_context(monkeypatch, ctx)
        _, html, plain = lmd._build_email(
            "x.myshopify.com", _brief_populated(), db=None,
        )
        assert "USD 1,500" in html
        assert "USD 1,500" in plain


# ---------------------------------------------------------------------------
# Partial-context — only some sections present
# ---------------------------------------------------------------------------


class TestPartialContext:
    def test_rars_only_no_benchmarks_or_retention(self, monkeypatch):
        ctx = _ctx_empty()
        ctx["rars"] = {
            "total": 800.0, "prevented": 0,
            "components": [{"source": "x", "loss_eur": 800.0}],
        }
        _patch_context(monkeypatch, ctx)
        _, html, _ = lmd._build_email(
            "x.myshopify.com",
            {"signals_count": 1, "headline": "", "top_product_label": "", "top_action": ""},
            db=None,
        )
        assert "Revenue at Risk" in html
        assert "Where it's leaking" in html
        assert "You vs. Similar Shops" not in html
        assert "Retention · week 1 / 4 / 12" not in html
        assert "Stock health" not in html

    def test_retention_only(self, monkeypatch):
        ctx = _ctx_empty()
        ctx["retention"] = {"w1": 0.3, "w4": 0.2, "w12": 0.1}
        _patch_context(monkeypatch, ctx)
        _, html, _ = lmd._build_email(
            "x.myshopify.com",
            {"signals_count": 1, "headline": "", "top_product_label": "", "top_action": ""},
            db=None,
        )
        assert "Retention · week 1 / 4 / 12" in html
        assert "Revenue at Risk" not in html

    def test_inventory_only(self, monkeypatch):
        ctx = _ctx_empty()
        ctx["inventory"] = {
            "at_risk": [{"product_title": "X", "days_of_cover": 3.0}],
            "out_of_stock_count": 0,
        }
        _patch_context(monkeypatch, ctx)
        _, html, _ = lmd._build_email(
            "x.myshopify.com",
            {"signals_count": 1, "headline": "", "top_product_label": "", "top_action": ""},
            db=None,
        )
        assert "Stock health" in html
        assert "Revenue at Risk" not in html


# ---------------------------------------------------------------------------
# Lead story uses brief input, not rich context
# ---------------------------------------------------------------------------


class TestLeadStoryWiring:
    def test_lead_story_uses_brief_top_product(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        brief = {
            "signals_count": 1,
            "top_product_label": "Custom Product X",
            "top_action": "Custom action Y",
            "headline": "Custom headline Z",
        }
        _, html, plain = lmd._build_email("x.myshopify.com", brief, db=None)
        assert "Custom Product X" in html
        assert "Custom action Y" in html
        assert "Custom Product X" in plain

    def test_brief_empty_strings_treated_as_missing(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        brief = {
            "signals_count": 1,
            "top_product_label": "   ",   # whitespace
            "top_action": "", "headline": "",
        }
        _, html, _ = lmd._build_email("x.myshopify.com", brief, db=None)
        # Stripped whitespace → no lead story
        assert "Today's lead story" not in html


# ---------------------------------------------------------------------------
# Output type contract
# ---------------------------------------------------------------------------


class TestOutputTypes:
    def test_returns_three_strings(self, monkeypatch):
        _patch_context(monkeypatch, _ctx_empty())
        out = lmd._build_email("x.myshopify.com", _brief_empty(), db=None)
        assert isinstance(out, tuple)
        assert len(out) == 3
        subject, html, plain = out
        assert isinstance(subject, str) and subject
        assert isinstance(html, str) and html
        assert isinstance(plain, str) and plain
