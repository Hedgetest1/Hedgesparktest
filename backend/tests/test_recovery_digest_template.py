"""
Contract tests for `email_templates._render_recovery_digest` currency
correctness.

The bug class closed 2026-05-20: the template hardcoded `€{rars_eur}` for
ALL merchants. Non-EUR shops (USD/GBP/SGD/...) received the wrong symbol
in the subject + body + plain text. `rars_total_eur` is a legacy misnomer
(see MerchantState lines 77-80) — the value is in the shop's NATIVE
currency, not EUR. Fix: thread `shop_currency` through brain dispatch +
governance schema + template render, then route through
`app.core.currency.format_money()`.

These tests are MUTATION-SENSITIVE — strip `format_money` and re-hardcode
€, and the assertions fire. See feedback_serial_lying_pattern.md: passing
tests must be empirically proven sensitive.
"""
from __future__ import annotations

import pytest

from app.services.email_templates import render_email


def _render(currency: str, rars: float = 1500.0, shop_name: str = "Acme"):
    """Render recovery_digest with a given shop currency and return all
    three artifacts (subject, html, plain) for assertions."""
    return render_email("recovery_digest", {
        "shop_name": shop_name,
        "rars_eur": rars,
        "last_action_hours": 80,
        "shop_currency": currency,
    })


@pytest.mark.parametrize("currency,expected_symbol,forbidden_symbols", [
    ("USD", "$", ["€", "£", "¥"]),
    ("EUR", "€", ["$", "£"]),
    ("GBP", "£", ["€", "$"]),
    ("CAD", "CA$", ["€", "£"]),
    ("AUD", "A$", ["€", "£"]),
])
def test_recovery_digest_renders_shop_native_symbol(
    currency, expected_symbol, forbidden_symbols,
):
    """A merchant on currency X must see X's symbol — not € — in the
    subject and body. The pre-fix template hardcoded €; the post-fix
    routes through `format_money()` which knows the symbol map."""
    subject, html, plain = _render(currency, rars=2500)

    assert expected_symbol in subject, (
        f"{currency} merchant subject missing expected '{expected_symbol}': "
        f"subject={subject!r}"
    )
    assert expected_symbol in plain, (
        f"{currency} plain-text missing expected '{expected_symbol}': "
        f"plain={plain[:200]!r}"
    )

    # For non-EUR merchants, € must NOT appear ANYWHERE in the rendered
    # email — that was the original bug. (For EUR merchants, € IS the
    # right symbol so we only check the negative cases.)
    if currency != "EUR":
        assert "€" not in subject, (
            f"{currency} subject leaks €: {subject!r}"
        )
        assert "€" not in plain, (
            f"{currency} plain leaks €: {plain[:300]!r}"
        )

    # The HTML body must also carry the correct symbol (the bug
    # affected the body block AND the subject; both must match).
    assert expected_symbol in html, (
        f"{currency} HTML body missing expected '{expected_symbol}': "
        f"html-snippet={html[:300]!r}"
    )


def test_recovery_digest_uses_format_money_not_hardcoded_euro():
    """Structural mutation pin — assert the renderer's source references
    `format_money` from `app.core.currency`. If a future refactor reverts
    to a hardcoded `f"€{rars_eur}"`, this test fires.

    The behavioural parametrize test above ALSO catches the regression at
    runtime, but the structural assertion is the redundant L2 pin
    (feedback_pipeline_multi_layer_recognition.md).
    """
    import inspect
    from app.services import email_templates as et

    src = inspect.getsource(et._render_recovery_digest)
    assert "format_money" in src, (
        "_render_recovery_digest must route through format_money() — "
        "hardcoded symbols are the original bug class. Source:\n" + src
    )
    # And the explicit anti-regression — no bare €{ pattern.
    assert 'f"€{' not in src and "f'€{" not in src, (
        "_render_recovery_digest contains a hardcoded €-interpolation — "
        "the exact bug class closed 2026-05-20. Source:\n" + src
    )


def test_recovery_digest_zero_rars_renders_correct_zero():
    """Edge case: rars_eur < 1 was the secondary hardcoded `€0` branch
    pre-fix. Post-fix must render the shop's symbol with 0."""
    subject, _, plain = _render("GBP", rars=0)
    assert "£0" in subject or "£ 0" in subject, (
        f"GBP zero-rars subject must render £0, got {subject!r}"
    )
    assert "€0" not in subject, (
        f"GBP zero-rars subject must NOT leak €0: {subject!r}"
    )


def test_recovery_digest_unknown_currency_falls_back_safely():
    """Unknown ISO code → currency_symbol returns "<CODE> " (e.g. "JOD ").
    The template must not crash and must surface the code so a merchant
    on an unsupported currency sees the value (not the wrong symbol)."""
    subject, _, plain = _render("JOD", rars=500)  # Jordanian Dinar — not in _SYMBOLS
    # JOD isn't in _SYMBOLS so currency_symbol returns "JOD ".
    assert "JOD" in subject, (
        f"Unknown currency must surface the code; subject={subject!r}"
    )
    assert "€" not in subject and "$" not in subject, (
        f"Unknown currency must NOT default to € or $: {subject!r}"
    )


def test_recovery_digest_missing_shop_currency_defaults_to_usd():
    """Direct callers (tests, synthetic fixtures) that omit shop_currency
    must NOT crash — the renderer falls back to "USD" (matching
    app.core.currency.DEFAULT_CURRENCY). The brain dispatch always
    passes shop_currency, but the defensive default keeps the template
    callable from anywhere."""
    subject, _, _ = render_email("recovery_digest", {
        "shop_name": "Test",
        "rars_eur": 1000,
        "last_action_hours": 96,
        # No shop_currency — direct-caller path.
    })
    # USD = "$"
    assert "$" in subject, (
        f"Missing shop_currency must default to USD/$: {subject!r}"
    )


def test_recovery_digest_governance_schema_has_shop_currency():
    """Wired-end-to-end pin: the governance ALLOWED_FIELDS must list
    `shop_currency` for recovery_digest. Without this, the orchestrator's
    schema validator strips the field before the renderer sees it — bug
    re-emerges silently. Born 2026-05-20 alongside the template fix."""
    from app.services.email_governance import ALLOWED_FIELDS

    schema = ALLOWED_FIELDS.get("recovery_digest", set())
    assert "shop_currency" in schema, (
        "ALLOWED_FIELDS['recovery_digest'] missing shop_currency — schema "
        "drift would silently re-introduce the hardcoded-€ bug. Current "
        f"schema: {schema}"
    )
    # The other required fields stay (regression check on the schema).
    assert {"shop_name", "rars_eur", "last_action_hours"}.issubset(schema), (
        f"recovery_digest schema regressed core fields: {schema}"
    )
