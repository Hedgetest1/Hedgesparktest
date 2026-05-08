"""Chatbot LLM fallback — global + per-merchant budget governance.

Regression pin for:
  - 2026-04-23 audit: `try_llm_fallback` was missing both `check_budget`
    AND `record_usage` calls, producing a budget-bypass where Haiku
    volume never rolled up into the monthly cap.
  - 2026-05-08 founder direttiva: per-merchant per-tier budget (€5 Lite,
    €10 Pro, €50 Scale) must gate the call AND increment the per-shop
    counter — done by passing shop_domain to check_budget + record_usage.
    The explicit record_merchant_charge call was removed (record_usage
    handles per-merchant tracking when shop_domain is provided), so the
    test contract updated to pin shop_domain threading instead.

Contract (post 2026-05-08):
1. check_budget("chatbot_fallback", shop_domain=X) MUST gate the call.
2. record_usage("chatbot_fallback", ..., shop_domain=X) MUST fire on
   successful answers — record_usage handles BOTH global tracking AND
   per-merchant counter increment when shop_domain is provided.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import chatbot_llm_fallback as cb


def _make_rag_context() -> dict:
    return {
        "orders_30d": 42,
        "revenue_30d_eur": 1234.0,
        "top_products": [{"title": "Widget", "revenue_eur": 500.0}],
        "rars_30d_eur": 120.0,
        "shop_domain": "fixture.myshopify.com",
    }


def test_budget_exhaustion_short_circuits_without_api_call(db):
    """check_budget returning False must prevent _call_haiku from firing."""
    with patch.object(cb, "_should_use_llm", return_value=(True, "ok")), \
         patch.object(cb, "_build_rag_context", return_value=_make_rag_context()), \
         patch("app.core.llm_budget.check_budget", return_value=(False, "monthly_cap_reached")), \
         patch.object(cb, "_call_haiku") as mock_haiku:
        result = cb.try_llm_fallback(db, shop_domain="fixture.myshopify.com", message="how are my orders?")

    assert result.success is False
    assert result.reason.startswith("budget_exhausted:"), (
        f"expected budget_exhausted reason, got {result.reason!r}"
    )
    assert mock_haiku.call_count == 0, (
        "Haiku must NOT be called when budget is exhausted"
    )


def test_successful_call_records_global_usage(db):
    """Successful answer must fire record_usage with shop_domain.
    Per 2026-05-08 contract change: record_usage(shop_domain=X) handles
    both global tracking AND per-merchant counter — no separate
    record_merchant_charge call."""
    with patch.object(cb, "_should_use_llm", return_value=(True, "ok")), \
         patch.object(cb, "_build_rag_context", return_value=_make_rag_context()), \
         patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
         patch.object(cb, "_call_haiku", return_value=(
             "Your last 30 days show 42 orders totaling €1234. Top product Widget contributed €500.",
             0.0012,
             450,   # input_tokens (ground truth from Anthropic usage struct)
             37,    # output_tokens
         )), \
         patch.object(cb, "_validate_response", return_value=(True, "ok")), \
         patch("app.core.llm_budget.record_usage") as mock_record:
        result = cb.try_llm_fallback(db, shop_domain="fixture.myshopify.com", message="how are my orders?")

    assert result.success is True
    assert mock_record.call_count == 1, "record_usage must fire exactly once"
    args, kwargs = mock_record.call_args
    assert args[0] == "chatbot_fallback"
    assert kwargs.get("provider") == "anthropic"
    assert kwargs.get("model", "").startswith("claude-haiku")
    assert kwargs.get("tokens_used") == 450 + 37
    # NEW (2026-05-08): shop_domain MUST be threaded so record_usage
    # increments the per-merchant tier counter (€5 Lite / €10 Pro cap).
    assert kwargs.get("shop_domain") == "fixture.myshopify.com", (
        f"shop_domain must be threaded for per-merchant tier tracking, "
        f"got {kwargs.get('shop_domain')!r}"
    )


def test_llm_error_does_not_call_record_usage(db):
    """Empty/error LLM response must NOT consume global-budget counter."""
    with patch.object(cb, "_should_use_llm", return_value=(True, "ok")), \
         patch.object(cb, "_build_rag_context", return_value=_make_rag_context()), \
         patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
         patch.object(cb, "_call_haiku", return_value=(None, 0.0, 0, 0)), \
         patch("app.core.llm_budget.record_usage") as mock_record:
        result = cb.try_llm_fallback(db, shop_domain="fixture.myshopify.com", message="question")

    assert result.success is False
    assert result.reason == "llm_empty_or_error"
    assert mock_record.call_count == 0, (
        "record_usage must NOT fire when the LLM returned empty"
    )


def test_token_count_falls_back_to_estimate_when_usage_omitted(db):
    """If Anthropic returns no usage struct, len-based estimate is used.

    `_call_haiku` returns (0, 0) for input/output token counts when the
    parse path can't find `usage.input_tokens` — the caller must fall
    back to `len(answer)//4` rather than recording 0 (which would hide
    the spend from global-budget accounting).
    """
    answer_text = "A" * 200  # deterministic length → 200//4 = 50 tokens
    with patch.object(cb, "_should_use_llm", return_value=(True, "ok")), \
         patch.object(cb, "_build_rag_context", return_value=_make_rag_context()), \
         patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
         patch.object(cb, "_call_haiku", return_value=(answer_text, 0.001, 0, 0)), \
         patch.object(cb, "_validate_response", return_value=(True, "ok")), \
         patch("app.core.llm_budget.record_usage") as mock_record, \
         patch("app.core.llm_budget.record_merchant_charge"):
        result = cb.try_llm_fallback(db, shop_domain="fixture.myshopify.com", message="q")

    assert result.success is True
    assert mock_record.call_count == 1
    _, kwargs = mock_record.call_args
    assert kwargs.get("tokens_used") == 50, (
        f"fallback to len-estimate when usage absent; expected 50 "
        f"(len=200 // 4), got {kwargs.get('tokens_used')!r}"
    )


def test_validation_failure_does_not_record_usage(db):
    """Hallucination-rejected answers don't count toward global budget.

    Rationale: the merchant doesn't see an answer (rejected), so we
    treat this as a non-consumption from the governance standpoint.
    Per-merchant charge also reflects actual tokens burned, so the
    asymmetry is intentional.
    """
    with patch.object(cb, "_should_use_llm", return_value=(True, "ok")), \
         patch.object(cb, "_build_rag_context", return_value=_make_rag_context()), \
         patch("app.core.llm_budget.check_budget", return_value=(True, "ok")), \
         patch.object(cb, "_call_haiku", return_value=("Some answer", 0.001, 200, 10)), \
         patch.object(cb, "_validate_response", return_value=(False, "hallucinated_number")), \
         patch("app.core.llm_budget.record_usage") as mock_record, \
         patch("app.services.alerting.write_alert"):
        result = cb.try_llm_fallback(db, shop_domain="fixture.myshopify.com", message="question")

    assert result.success is False
    assert result.reason.startswith("invalid:")
    assert mock_record.call_count == 0
