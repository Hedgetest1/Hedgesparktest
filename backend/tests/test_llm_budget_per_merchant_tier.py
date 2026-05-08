"""Per-merchant per-tier LLM budget enforcement.

Founder direttiva 2026-05-08: each merchant has a tier-based monthly LLM
budget that protects unit economics from a runaway rule. The product
claim "system gets smarter every week" + chatbot is delivered WITHIN
this cap.

Tier caps (€/merchant/month) — PLAN_MONTHLY_BUDGETS_EUR:
  lite    → €5  (€39 plan)
  pro     → €10 (€99 plan)
  scale   → €50 (€249+ plan)
  free    → €0.50 (trial / dev / inactive — minimal)

These tests pin the contract: a Lite merchant who exhausts €5 must be
blocked even if the network has global budget remaining.
"""
from __future__ import annotations

import time

import pytest


def _clear_merchant_state(shop):
    """Clear the merchant monthly key + plan cache for a clean test."""
    from app.core.redis_client import _client
    from app.core.llm_budget import _merchant_plan_cache, _merchant_monthly_key
    rc = _client()
    if rc is not None:
        rc.delete(_merchant_monthly_key(shop))
    _merchant_plan_cache.pop(shop, None)


def test_tier_caps_match_founder_directive():
    """€5 Lite, €10 Pro, €50 Scale, €0.50 free."""
    from app.core.llm_budget import PLAN_MONTHLY_BUDGETS_EUR
    assert PLAN_MONTHLY_BUDGETS_EUR["lite"] == 5.0
    assert PLAN_MONTHLY_BUDGETS_EUR["pro"] == 10.0
    assert PLAN_MONTHLY_BUDGETS_EUR["scale"] == 50.0
    assert PLAN_MONTHLY_BUDGETS_EUR["free"] == 0.50


def test_lite_merchant_blocked_at_5_eur():
    """A Lite merchant who has spent €5 must be blocked on the next call."""
    from app.core.llm_budget import (
        _check_merchant_budget, _merchant_monthly_key, _merchant_plan_cache,
    )
    from app.core.redis_client import _client
    shop = "_test_lite_at_cap_.myshopify.com"
    _clear_merchant_state(shop)
    _merchant_plan_cache[shop] = ("lite", time.monotonic() + 60)

    rc = _client()
    if rc is not None:
        rc.set(_merchant_monthly_key(shop), "5.5")  # over the €5 cap
    try:
        ok, reason = _check_merchant_budget(shop)
        assert ok is False
        assert "merchant_tier_cap_reached" in reason
        assert "lite" in reason
    finally:
        _clear_merchant_state(shop)


def test_pro_merchant_blocked_at_10_eur():
    from app.core.llm_budget import (
        _check_merchant_budget, _merchant_monthly_key, _merchant_plan_cache,
    )
    from app.core.redis_client import _client
    shop = "_test_pro_at_cap_.myshopify.com"
    _clear_merchant_state(shop)
    _merchant_plan_cache[shop] = ("pro", time.monotonic() + 60)

    rc = _client()
    if rc is not None:
        rc.set(_merchant_monthly_key(shop), "10.5")
    try:
        ok, reason = _check_merchant_budget(shop)
        assert ok is False
        assert "pro" in reason
    finally:
        _clear_merchant_state(shop)


def test_lite_merchant_at_3_eur_still_allowed():
    """Sanity: under-cap merchants must pass."""
    from app.core.llm_budget import (
        _check_merchant_budget, _merchant_monthly_key, _merchant_plan_cache,
    )
    from app.core.redis_client import _client
    shop = "_test_lite_under_cap_.myshopify.com"
    _clear_merchant_state(shop)
    _merchant_plan_cache[shop] = ("lite", time.monotonic() + 60)

    rc = _client()
    if rc is not None:
        rc.set(_merchant_monthly_key(shop), "3.0")
    try:
        ok, reason = _check_merchant_budget(shop)
        assert ok is True
    finally:
        _clear_merchant_state(shop)


def test_unknown_merchant_defaults_to_free_tier():
    """Defense: a merchant whose plan can't be resolved gets the FREE
    cap (€0.50/mo), not Lite or Pro. Fail-closed budget gate."""
    from app.core.llm_budget import _get_merchant_plan
    plan = _get_merchant_plan("_totally_unknown_merchant_.myshopify.com")
    assert plan == "free"


def test_get_merchant_usage_returns_full_state():
    """Operator visibility: get_merchant_usage returns plan + cap + spent + remaining."""
    from app.core.llm_budget import (
        get_merchant_usage, _merchant_monthly_key, _merchant_plan_cache,
    )
    from app.core.redis_client import _client
    shop = "_test_usage_view_.myshopify.com"
    _clear_merchant_state(shop)
    _merchant_plan_cache[shop] = ("pro", time.monotonic() + 60)
    rc = _client()
    if rc is not None:
        rc.set(_merchant_monthly_key(shop), "3.25")
    try:
        usage = get_merchant_usage(shop)
        assert usage["plan"] == "pro"
        assert usage["monthly_cap_eur"] == 10.0
        assert abs(usage["monthly_spent_eur"] - 3.25) < 0.001
        assert abs(usage["monthly_remaining_eur"] - 6.75) < 0.001
        assert usage["cap_reached"] is False
    finally:
        _clear_merchant_state(shop)


def test_check_budget_short_circuits_on_merchant_cap_BEFORE_global():
    """When shop_domain provided AND merchant cap reached, check_budget
    must block WITHOUT reaching the global cap. A Lite merchant exhausts
    €5 even if the network has €40 of €50 global remaining."""
    from app.core.llm_budget import (
        check_budget, _merchant_monthly_key, _merchant_plan_cache,
    )
    from app.core.redis_client import _client
    shop = "_test_check_budget_first_guard_.myshopify.com"
    _clear_merchant_state(shop)
    _merchant_plan_cache[shop] = ("lite", time.monotonic() + 60)
    rc = _client()
    if rc is not None:
        rc.set(_merchant_monthly_key(shop), "5.5")  # over Lite cap
    try:
        ok, reason = check_budget("any_module", shop_domain=shop)
        assert ok is False
        assert "merchant_tier_cap_reached" in reason
        assert "monthly_eur_cap_reached" not in reason
    finally:
        _clear_merchant_state(shop)


def test_record_usage_with_shop_increments_per_merchant_counter():
    """record_usage(shop_domain=X) must increment the per-merchant
    Redis counter so the next check_budget sees the cost."""
    from app.core.llm_budget import (
        record_usage, get_merchant_usage,
        _merchant_monthly_key, _merchant_plan_cache,
    )
    shop = "_test_record_per_shop_.myshopify.com"
    _clear_merchant_state(shop)
    _merchant_plan_cache[shop] = ("lite", time.monotonic() + 60)
    try:
        record_usage(
            module="merchant_chatbot",
            tokens_used=2500,
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            shop_domain=shop,
        )
        usage = get_merchant_usage(shop)
        assert usage["monthly_spent_eur"] > 0
    finally:
        _clear_merchant_state(shop)


def test_legacy_plan_aliases_preserved():
    """merchants with old plan labels (core, plus, agency) still resolve
    to the right cap so legacy DB rows keep working."""
    from app.core.llm_budget import get_plan_budget_eur
    assert get_plan_budget_eur("core") == 5.0   # alias for lite
    assert get_plan_budget_eur("plus") == 10.0  # alias for pro
    assert get_plan_budget_eur("agency") == 50.0  # alias for scale
