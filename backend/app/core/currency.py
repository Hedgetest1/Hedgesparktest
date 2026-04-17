"""
Centralized currency display helpers.

Single source of truth for:
- ISO 4217 → display symbol mapping
- money-amount formatting with the shop's native symbol

Every merchant-facing surface (emails, digest, dashboard f-strings,
triggers, narratives, proof statements, LLM prompts) MUST use
`format_money()` or `currency_symbol()` from this module.

Hardcoding "€" / "$" is a data-truth bug: a GBP merchant seeing
"€1,840 at risk this month" will lose trust instantly.

When the shop currency is not available (pre-install, no orders,
pre-migration merchant), we fall back to "USD" + its `$` symbol —
safer than "EUR" because USD is the Shopify default locale.
"""
from __future__ import annotations


# ISO 4217 → display symbol. Covers 99% of Shopify merchant currencies.
# For unknown codes we return "<CODE> " so a merchant in SGD/HKD/etc.
# sees "SGD 42.00" instead of the wrong symbol.
_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "JPY": "¥",
    "CNY": "¥",
    "CHF": "CHF ",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
    "PLN": "zł",
    "CZK": "Kč",
    "HUF": "Ft",
    "BRL": "R$",
    "MXN": "MX$",
    "INR": "₹",
    "SGD": "S$",
    "HKD": "HK$",
    "KRW": "₩",
    "ZAR": "R",
    "AED": "د.إ ",
    "ILS": "₪",
}

DEFAULT_CURRENCY: str = "USD"


def currency_symbol(currency: str | None) -> str:
    """
    Map an ISO 4217 code to its display symbol.

    - None / empty → DEFAULT_CURRENCY's symbol ("$")
    - Unknown code → "<CODE> " (e.g. "SGD " → safer than wrong symbol)
    """
    code = (currency or DEFAULT_CURRENCY).upper().strip()
    if code in _SYMBOLS:
        return _SYMBOLS[code]
    return code + " "


def format_money(
    amount: float | int | None,
    currency: str | None = None,
    *,
    decimals: int | None = None,
    compact: bool = False,
) -> str:
    """
    Format a money amount with the shop's native currency symbol.

    - amount=None → returns the symbol + "0" (never crashes)
    - decimals=None → auto: 0 for >=1000 or whole numbers, 2 otherwise
    - compact=True → "€1.2k" / "$3.4M" for dashboard KPIs

    Examples:
      format_money(1840.25, "EUR")              -> "€1,840"
      format_money(12.5, "USD", decimals=2)     -> "$12.50"
      format_money(1200, "GBP", compact=True)   -> "£1.2k"
      format_money(None, "EUR")                 -> "€0"
    """
    sym = currency_symbol(currency)
    value = float(amount or 0)

    if compact:
        if abs(value) >= 1_000_000:
            return f"{sym}{value / 1_000_000:.1f}M"
        if abs(value) >= 1_000:
            return f"{sym}{value / 1_000:.1f}k"
        return f"{sym}{value:.0f}"

    if decimals is None:
        decimals = 0 if abs(value) >= 1000 or value == int(value) else 2

    return f"{sym}{value:,.{decimals}f}"


__all__ = [
    "DEFAULT_CURRENCY",
    "currency_symbol",
    "format_money",
]
