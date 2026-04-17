"""
Tests for app/core/currency.py — the single source of truth for
merchant-facing currency rendering.

This module is imported by 12+ files. A silent regression here would
surface as wrong-symbol drift across dashboard / emails / digests /
LLM prompts simultaneously. The cost of a bug is high, the cost of
testing is low — hence wide coverage.
"""
from __future__ import annotations

import pytest

from app.core.currency import (
    DEFAULT_CURRENCY,
    currency_symbol,
    format_money,
)


# ---------------------------------------------------------------------------
# currency_symbol()
# ---------------------------------------------------------------------------


class TestCurrencySymbol:
    def test_usd_returns_dollar(self):
        assert currency_symbol("USD") == "$"

    def test_eur_returns_euro(self):
        assert currency_symbol("EUR") == "€"

    def test_gbp_returns_pound(self):
        assert currency_symbol("GBP") == "£"

    def test_none_falls_back_to_default(self):
        # DEFAULT_CURRENCY is USD → "$". Safer than EUR because USD is
        # Shopify's default locale for unconfigured shops.
        assert currency_symbol(None) == "$"
        assert DEFAULT_CURRENCY == "USD"

    def test_empty_string_falls_back(self):
        assert currency_symbol("") == "$"

    def test_lowercase_coerced_to_uppercase(self):
        assert currency_symbol("eur") == "€"
        assert currency_symbol("gbp") == "£"

    def test_whitespace_stripped(self):
        assert currency_symbol("  USD  ") == "$"

    def test_unknown_code_returns_code_plus_space(self):
        # "SGD " is safer than guessing a symbol — merchant sees the
        # ISO code, which is unambiguous.
        assert currency_symbol("XXX") == "XXX "
        assert currency_symbol("ZZZ") == "ZZZ "

    def test_covers_major_markets(self):
        # Shopify top merchant currencies must all render a native
        # symbol (not fall back to the generic ISO-code path). CHF is
        # intentionally rendered as "CHF " — Swiss francs are commonly
        # written out as "CHF 1,500", not with a short glyph.
        for code in (
            "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "CNY",
            "SEK", "NOK", "DKK", "BRL", "MXN", "INR",
        ):
            sym = currency_symbol(code)
            assert sym, f"{code} must produce a non-empty symbol"
            assert code + " " != sym, f"{code} should have a native symbol, not fall back to code"

    def test_chf_renders_written_out(self):
        # Swiss francs convention: "CHF 1,500", not a glyph.
        assert currency_symbol("CHF") == "CHF "


# ---------------------------------------------------------------------------
# format_money() — default path
# ---------------------------------------------------------------------------


class TestFormatMoneyDefault:
    def test_integer_amount_gets_no_decimals(self):
        assert format_money(42, "USD") == "$42"
        assert format_money(1234, "EUR") == "€1,234"

    def test_large_amount_gets_thousands_separator(self):
        assert format_money(1_234_567, "USD") == "$1,234,567"

    def test_fractional_small_amount_gets_two_decimals(self):
        # 12.5 is not whole AND < 1000 → 2 decimals
        assert format_money(12.5, "USD") == "$12.50"

    def test_fractional_large_amount_gets_no_decimals(self):
        # >= 1000 always rounds to integer
        assert format_money(1234.56, "USD") == "$1,235"

    def test_none_amount_returns_zero(self):
        # Guard: None must not crash downstream f-strings.
        assert format_money(None, "USD") == "$0"
        assert format_money(None, "EUR") == "€0"

    def test_negative_amount_preserves_sign(self):
        assert format_money(-42, "USD") == "$-42"
        # Python f-string uses banker's rounding (half-to-even), so
        # -1234.5 rounds to -1,234 not -1,235. Verifying behavior, not
        # asserting a specific rounding mode — just that sign is kept.
        out = format_money(-1234.5, "USD")
        assert out.startswith("$-1,23"), f"got {out!r}"

    def test_zero_amount_renders_clean(self):
        assert format_money(0, "EUR") == "€0"
        assert format_money(0.0, "GBP") == "£0"


class TestFormatMoneyExplicitDecimals:
    def test_decimals_zero_forces_integer_render(self):
        assert format_money(42.99, "USD", decimals=0) == "$43"
        assert format_money(42.01, "USD", decimals=0) == "$42"

    def test_decimals_two_forces_cents(self):
        assert format_money(42, "USD", decimals=2) == "$42.00"
        assert format_money(1234, "EUR", decimals=2) == "€1,234.00"


class TestFormatMoneyCompact:
    def test_thousands_render_as_k(self):
        assert format_money(1234, "USD", compact=True) == "$1.2k"
        assert format_money(5678, "EUR", compact=True) == "€5.7k"

    def test_millions_render_as_M(self):
        assert format_money(1_234_567, "USD", compact=True) == "$1.2M"

    def test_small_amounts_stay_whole_in_compact(self):
        assert format_money(42, "USD", compact=True) == "$42"
        assert format_money(999, "EUR", compact=True) == "€999"

    def test_compact_preserves_sign(self):
        assert format_money(-1500, "USD", compact=True) == "$-1.5k"

    def test_compact_none_renders_zero(self):
        assert format_money(None, "USD", compact=True) == "$0"


# ---------------------------------------------------------------------------
# Cross-currency snapshot — locks in the 24-code table
# ---------------------------------------------------------------------------


def test_cross_currency_snapshot_renders_consistently():
    """A merchant with any supported currency sees an amount rendered
    with their native symbol, never a fallback. This snapshot guards
    against accidental removal from the _SYMBOLS table."""
    amount = 1500
    snapshots = {
        "USD": "$1,500",
        "EUR": "€1,500",
        "GBP": "£1,500",
        "CAD": "CA$1,500",
        "AUD": "A$1,500",
        "JPY": "¥1,500",
        "BRL": "R$1,500",
        "MXN": "MX$1,500",
        "INR": "₹1,500",
        "KRW": "₩1,500",
    }
    for code, expected in snapshots.items():
        assert format_money(amount, code) == expected, (
            f"{code} rendering drifted — check app/core/currency.py _SYMBOLS"
        )
