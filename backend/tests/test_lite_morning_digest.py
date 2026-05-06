"""Locks the Lite morning digest cycle (2026-05-06).

This test file fills the evolution-pipeline coverage gap reported by
the founder ("Evolution service `lite_morning_digest.py` has no
dedicated test file. Do not proceed."). Pinning:

1. Operator/dev tenants (`hedgespark-dev.myshopify.com`) NEVER
   receive a Lite morning digest — root-cause class that mailed the
   founder's address with real merchant data on 2026-05-06.
2. Operator-email-address fallback gate also strips merchants whose
   `contact_email` matches a known operator address.
3. Pro merchants (`plan="pro"`) skip the cycle — they get the
   weekly digest instead; daily Lite would be noise.
4. Cycle is idempotent within a date — `_digest_sent_today` Redis
   dedup is honored.
5. Empty merchants set returns clean summary without exceptions.

The test uses MagicMock for the DB session + email orchestrator to
keep the test hermetic. Every external dependency (Redis, brief
engine, orchestrator) is mocked so the test runs cleanly without
infrastructure.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.lite_morning_digest import run_lite_morning_digest_cycle


def _mk_merchant(
    shop: str,
    email: str | None = "owner@example.com",
    *,
    install_status: str = "active",
    plan: str | None = "lite",
    billing_active: bool = False,
):
    m = MagicMock()
    m.shop_domain = shop
    m.contact_email = email
    m.install_status = install_status
    m.plan = plan
    m.billing_active = billing_active
    return m


def _mk_db_with_merchants(merchants):
    """Build a MagicMock db.query(Merchant) chain that returns
    `merchants` on the first .all() call, then [] (loop terminator)."""
    db = MagicMock()
    pages = [merchants, []]

    def _all_side_effect():
        return pages.pop(0) if pages else []

    chain = db.query.return_value
    chain.filter.return_value = chain
    chain.order_by.return_value = chain
    chain.offset.return_value = chain
    chain.limit.return_value = chain
    chain.all.side_effect = _all_side_effect
    return db


@pytest.fixture
def patches():
    """Common test patches: brief engine, email orchestrator, Redis dedup."""
    with patch(
        "app.services.brief_engine.generate_brief",
        return_value={
            "shop_domain": "test.myshopify.com",
            "signals_count": 0,
            "top_product_label": "Test product",
            "currency": "USD",
        },
    ) as gen_brief, patch(
        "app.services.email_orchestrator.submit_intent",
        return_value="ok",
    ) as submit, patch(
        "app.services.lite_morning_digest._digest_sent_today",
        return_value=False,
    ) as already_sent, patch(
        "app.services.lite_morning_digest._mark_sent",
    ), patch(
        "app.services.lite_morning_digest._build_email",
        return_value=("subj", "<html>", "plain"),
    ):
        yield {
            "submit_intent": submit,
            "generate_brief": gen_brief,
            "digest_sent_today": already_sent,
        }


def test_operator_dev_shop_excluded_from_query(patches):
    """REGRESSION GUARD (2026-05-06): the founder's dev tenant
    `hedgespark-dev.myshopify.com` was receiving the morning digest.
    The cycle's query MUST exclude operator/dev shops via the
    `~Merchant.shop_domain.in_(operator_dev_shops())` clause.

    We verify by passing the operator shop into the merchant list —
    in real production the query already excluded it, but here we
    assert the in-loop secondary filter (address-level) catches it
    if the query layer ever regresses."""
    db = _mk_db_with_merchants([
        _mk_merchant("hedgespark-dev.myshopify.com", email="tedialarana@gmail.com"),
        _mk_merchant("real-merchant.myshopify.com", email="owner@example.com"),
    ])
    summary = run_lite_morning_digest_cycle(db)
    # Only the real merchant is processed; operator email gate stripped the dev one
    assert summary["sent"] == 1
    # submit_intent called once, with the real merchant's address
    submit_calls = patches["submit_intent"].call_args_list
    assert len(submit_calls) == 1
    intent = submit_calls[0].args[1]
    assert intent.to_email == "owner@example.com"


def test_operator_email_address_excluded(patches):
    """Even if a non-dev shop has an operator email address (e.g. a
    real merchant whose owner just happens to use a known operator
    address), the address-level gate must catch it."""
    db = _mk_db_with_merchants([
        _mk_merchant("real-shop.myshopify.com", email="tedialarana@gmail.com"),
    ])
    summary = run_lite_morning_digest_cycle(db)
    assert summary["sent"] == 0
    patches["submit_intent"].assert_not_called()


def test_pro_merchants_skipped(patches):
    """plan != 'lite' merchants don't get the daily digest — they
    have the weekly digest already; stacking would be noise."""
    db = _mk_db_with_merchants([
        _mk_merchant("pro-shop.myshopify.com", plan="pro"),
    ])
    summary = run_lite_morning_digest_cycle(db)
    assert summary["sent"] == 0
    patches["submit_intent"].assert_not_called()


def test_lite_merchants_processed(patches):
    """Lite merchants (plan='lite' or None) ARE processed."""
    db = _mk_db_with_merchants([
        _mk_merchant("lite1.myshopify.com", plan="lite"),
        _mk_merchant("lite2.myshopify.com", plan=None),  # default = lite
    ])
    summary = run_lite_morning_digest_cycle(db)
    assert summary["sent"] == 2
    assert patches["submit_intent"].call_count == 2


def test_already_sent_today_skipped(patches):
    """Idempotency: a merchant that already received today's digest
    is skipped via Redis dedup, not double-mailed."""
    db = _mk_db_with_merchants([
        _mk_merchant("dedup-shop.myshopify.com"),
    ])
    patches["digest_sent_today"].return_value = True  # simulate already sent
    summary = run_lite_morning_digest_cycle(db)
    assert summary["sent"] == 0
    assert summary["skipped"] == 1
    patches["submit_intent"].assert_not_called()


def test_empty_merchants_returns_clean_summary(patches):
    """No eligible merchants → no exceptions, clean zero-sent summary."""
    db = _mk_db_with_merchants([])
    summary = run_lite_morning_digest_cycle(db)
    assert summary["processed"] == 0
    assert summary["sent"] == 0
    assert summary["skipped"] == 0
    assert summary["failed"] == 0
    patches["submit_intent"].assert_not_called()


def test_brief_failure_per_merchant_does_not_block_others(patches):
    """If generate_brief raises for one merchant, the cycle continues
    for the rest. Bug-class preventer: per-merchant try/except in the
    cycle loop."""
    call_count = [0]

    def _maybe_raise(_db, shop):
        call_count[0] += 1
        if shop == "broken.myshopify.com":
            raise RuntimeError("simulated brief failure")
        return {
            "shop_domain": shop,
            "signals_count": 0,
            "top_product_label": "p",
            "currency": "USD",
        }

    patches["generate_brief"].side_effect = _maybe_raise

    db = _mk_db_with_merchants([
        _mk_merchant("broken.myshopify.com"),
        _mk_merchant("ok.myshopify.com"),
    ])
    summary = run_lite_morning_digest_cycle(db)
    # 2 processed, 1 sent (the OK one), 1 failed (the broken one)
    assert summary["processed"] == 2
    assert summary["sent"] == 1
    assert summary["failed"] == 1
