"""Locks the operator/dev-shop email blocklist (2026-05-06).

The founder reported receiving merchant-shaped digest emails at
`tedialarana@gmail.com`. Root cause: their dev tenant
`hedgespark-dev.myshopify.com` is a real merchant row (plan=pro,
billing_active=true) and the merchant_digest cycle correctly
filtered by billing_active → matched the dev tenant → emailed the
founder.

Fix: `app/core/operator_blocklist.py` provides two predicates the
email pipeline uses to gate every outbound merchant-facing channel.
This test pins the predicate behavior + the constants. Adding a new
operator tenant requires updating the module + adding a test case
here.
"""
from __future__ import annotations

from app.core.operator_blocklist import (
    is_operator_dev_shop,
    is_operator_email,
    operator_dev_shops,
    operator_emails,
)


def test_operator_dev_shop_predicate_matches_known_shops():
    """Hardcoded operator shops must match. Adding new entries
    requires updating both the module + this test."""
    assert is_operator_dev_shop("hedgespark-dev.myshopify.com") is True


def test_operator_dev_shop_case_insensitive():
    """Predicate lower-cases the input — Shopify domains are
    technically lowercased but defensive parsing matches uppercase."""
    assert is_operator_dev_shop("HEDGESPARK-DEV.MYSHOPIFY.COM") is True
    assert is_operator_dev_shop("HedgeSpark-Dev.MyShopify.com") is True


def test_operator_dev_shop_real_merchants_pass():
    """Real merchant domains must NOT match the operator gate."""
    assert is_operator_dev_shop("merchant.myshopify.com") is False
    assert is_operator_dev_shop("acme-store.myshopify.com") is False
    assert is_operator_dev_shop("hedgespark-dev2.myshopify.com") is False  # similar but distinct


def test_operator_dev_shop_handles_none_and_empty():
    assert is_operator_dev_shop(None) is False
    assert is_operator_dev_shop("") is False
    assert is_operator_dev_shop("   ") is False
    # Defensive: non-str
    assert is_operator_dev_shop(42) is False  # type: ignore[arg-type]


def test_operator_email_address_matches_founder():
    """Founder email is the canonical operator address."""
    assert is_operator_email("tedialarana@gmail.com") is True


def test_operator_email_address_case_insensitive():
    assert is_operator_email("TediaLarana@Gmail.com") is True
    assert is_operator_email("TEDIALARANA@GMAIL.COM") is True


def test_operator_email_real_addresses_pass():
    """Random merchant addresses must NOT match."""
    assert is_operator_email("owner@example.com") is False
    assert is_operator_email("merchant@store.com") is False
    assert is_operator_email("tedi@otherco.com") is False


def test_operator_email_handles_none_and_empty():
    assert is_operator_email(None) is False
    assert is_operator_email("") is False
    assert is_operator_email("   ") is False


def test_operator_constants_are_immutable():
    """Frozensets — guarantees no runtime mutation can sneak in
    a new operator without code review."""
    assert isinstance(operator_dev_shops(), frozenset)
    assert isinstance(operator_emails(), frozenset)
    # Both must be non-empty (founder + dev tenant exist)
    assert len(operator_dev_shops()) >= 1
    assert len(operator_emails()) >= 1


def test_email_orchestrator_blocks_operator_dev_shop(monkeypatch):
    """Integration smoke: submit_intent for an operator/dev shop
    short-circuits at _resolve_merchant — no DB writes, no Resend
    call, intent counted as suppressed."""
    from unittest.mock import MagicMock
    from app.services.email_orchestrator import (
        EmailIntent,
        _resolve_merchant,
    )

    fake_db = MagicMock()
    intent = EmailIntent(
        shop_domain="hedgespark-dev.myshopify.com",
        email_type="lite_morning_digest",
        to_email="tedialarana@gmail.com",
        subject="Should not send",
        html="<p>blocked</p>",
        plain_text="blocked",
        from_address="HedgeSpark <test@hedgesparkhq.com>",
        producer="test",
    )
    result = _resolve_merchant(fake_db, "hedgespark-dev.myshopify.com", [intent])
    assert result["sent"] == 0
    assert result["suppressed"] >= 1


def test_send_email_address_guard_blocks_operator(monkeypatch):
    """The last-line address guard in app.core.email.send_email
    blocks even if a script directly constructs an operator email."""
    from app.core.email import send_email
    # Bypass the API-key short-circuit by patching it
    monkeypatch.setenv("RESEND_API_KEY", "fake_key_for_test")
    # The guard returns None when the to= address is operator-email
    result = send_email(
        to="tedialarana@gmail.com",
        subject="test",
        html="<p>blocked</p>",
        text="blocked",
    )
    assert result is None
