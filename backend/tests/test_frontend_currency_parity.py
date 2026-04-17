"""
Parity test: dashboard/src/app/app/_lib/formatters.ts must mirror the
backend `app/core/currency.py` symbol table.

Why: the frontend renders merchant-facing money, the backend renders
digest/email money. If the two symbol tables drift, a merchant sees
€12 on the dashboard and $12 in the email for the same amount.
That's the exact class of data-truth regression the data-truth audit
exists to prevent.

This test parses the CURRENCY_SYMBOLS map out of formatters.ts with
a simple regex (no JS runtime needed) and asserts every key/value
present on the backend is also present on the frontend.

If the frontend adds new codes later, the assertion is one-way: we
only require backend ⊆ frontend. A frontend-only code is fine (the
frontend can be more permissive for display flexibility); a
backend-only code is a bug (the backend could emit a symbol the
frontend doesn't know how to render).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.currency import _SYMBOLS  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FORMATTERS_TS = (
    REPO_ROOT / "dashboard" / "src" / "app" / "app" / "_lib" / "formatters.ts"
)


def _parse_frontend_symbol_table() -> dict[str, str]:
    """Extract the CURRENCY_SYMBOLS map from formatters.ts with a regex.

    The map is a simple `const CURRENCY_SYMBOLS: Record<string, string> = {
        USD: "$", EUR: "€", ...
    };` — no computation, no imports, easy to parse.
    """
    text = FORMATTERS_TS.read_text()
    # Grab the { ... } block following CURRENCY_SYMBOLS = {
    match = re.search(
        r"CURRENCY_SYMBOLS[^=]*=\s*\{([^}]*)\}",
        text,
        re.DOTALL,
    )
    if not match:
        raise AssertionError("CURRENCY_SYMBOLS not found in formatters.ts")
    body = match.group(1)
    entries = re.findall(r'(\w+)\s*:\s*"([^"]*)"', body)
    return {code: sym for code, sym in entries}


@pytest.fixture(scope="module")
def frontend_symbols() -> dict[str, str]:
    if not FORMATTERS_TS.exists():
        pytest.skip(f"{FORMATTERS_TS} not present in this checkout")
    return _parse_frontend_symbol_table()


def test_frontend_table_covers_every_backend_currency(frontend_symbols):
    missing = [code for code in _SYMBOLS if code not in frontend_symbols]
    assert not missing, (
        f"Frontend formatters.ts CURRENCY_SYMBOLS is missing: {missing}. "
        f"Every backend ISO code must have a frontend counterpart so the "
        f"dashboard doesn't drop to a generic fallback while the server "
        f"uses the native symbol."
    )


def test_frontend_table_matches_backend_values(frontend_symbols):
    mismatches = {}
    for code, backend_sym in _SYMBOLS.items():
        frontend_sym = frontend_symbols.get(code)
        if frontend_sym is None:
            continue  # covered by the previous test
        if frontend_sym != backend_sym:
            mismatches[code] = (backend_sym, frontend_sym)
    assert not mismatches, (
        "Backend/frontend symbol drift:\n" +
        "\n".join(
            f"  {code}: backend={bk!r} frontend={fe!r}"
            for code, (bk, fe) in mismatches.items()
        )
    )


def test_chf_intentionally_written_out_on_both_sides(frontend_symbols):
    """Swiss francs — we deliberately render 'CHF ' not a glyph on both
    sides (matches Swiss convention). Lock the decision in."""
    assert _SYMBOLS["CHF"] == "CHF "
    assert frontend_symbols.get("CHF") == "CHF "


def test_coverage_minimum_14_currencies():
    """Regression guard: the table must not shrink silently below the
    top-14 Shopify merchant currencies."""
    required = {
        "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "CNY",
        "SEK", "NOK", "DKK", "BRL", "MXN", "INR",
    }
    missing = required - set(_SYMBOLS.keys())
    assert not missing, f"Backend lost coverage of: {missing}"
