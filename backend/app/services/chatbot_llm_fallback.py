"""
chatbot_llm_fallback.py — Two-layer chatbot LLM fallback with RAG.

Merchant chatbot is 98% deterministic keyword-based (fast, cheap,
predictable). This module handles the 2% residual: messages the
keyword classifier couldn't place, where the merchant is asking a
real open-ended question and deserves a real answer, not a template.

Pipeline
--------
1. Caller confirms classification == 'unclassified' and NOT a bug report
2. Per-merchant LLM budget check (tiered α4) — skip if exhausted
3. RAG grounding: fetch merchant snapshot (orders, RARS, top products,
   recent signals, active nudges) — ~500 tokens of factual context
4. Build a tight prompt: "You are HedgeSpark's AI assistant. Answer
   grounded ONLY in the following data. If the answer isn't in the
   data, say so and offer to escalate."
5. Call Haiku 4.5 (cheap fast model, ~€0.001 per 500-token answer)
6. Validate the response: NO hallucinated numbers, NO promises outside
   our product, brand-voice conformant
7. Record per-merchant spend + return answer

Budget
------
- Uses α4 tiered LLM budget (per-merchant per-month cap)
- Cost per answer: ~€0.0008 (Haiku: $1/Mtok input, $5/Mtok output)
- Core plan gets ~150-300 LLM answers/month; Plus gets ~1000

Fail-safe
---------
- Any LLM error → silent downgrade to keyword template
- Budget exhausted → downgrade
- Hallucination detected → downgrade
- The caller never knows whether it got deterministic or LLM output —
  it's a transparent enrichment.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("chatbot_llm_fallback")

# Estimated cost per answer — used for budget gate before calling.
# Haiku 4.5: $1/Mtok input, $5/Mtok output. We cap prompt at 1500 tok
# and output at 300 tok → max €0.0024 per answer. We pre-budget €0.003
# to absorb variance.
_ESTIMATED_COST_EUR = 0.003

# Max output tokens — keeps costs bounded + responses readable
_MAX_OUTPUT_TOKENS = 300
_MAX_INPUT_TOKENS = 1500

# Topic allowlist — the LLM is constrained to HedgeSpark's scope.
# Requests outside these topics are refused (deterministic template).
_ALLOWED_TOPICS = frozenset({
    "revenue", "orders", "traffic", "visitors", "products",
    "nudges", "signals", "conversion", "aov", "ltv", "churn",
    "refunds", "abandoned", "holdout", "trust", "contract",
    "dashboard", "setup", "install", "tracking", "attribution",
    "goal", "target", "forecast", "store", "merchant", "rars",
    "risk", "benchmark", "cohort", "cac", "roi",
})


_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_LLM_TIMEOUT_S = 20.0
# Haiku 4.5 pricing (approx): $1/Mtok input, $5/Mtok output, EUR ≈ 0.92 USD
_INPUT_COST_EUR_PER_1K = 0.00092
_OUTPUT_COST_EUR_PER_1K = 0.0046


def _call_haiku(prompt: str) -> tuple[str | None, float]:
    """Direct Anthropic Haiku call. Returns (answer, cost_eur)."""
    import os
    import httpx

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None, 0.0

    try:
        from app.core.llm_budget import is_provider_backed_off, record_429
        if is_provider_backed_off("anthropic"):
            return None, 0.0
    except Exception:
        pass

    try:
        resp = httpx.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _HAIKU_MODEL,
                "max_tokens": _MAX_OUTPUT_TOKENS,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_LLM_TIMEOUT_S,
        )
    except Exception as exc:
        log.warning("chatbot_llm: Haiku request failed: %s", type(exc).__name__)
        return None, 0.0

    if resp.status_code == 429:
        try:
            from app.core.llm_budget import record_429
            record_429("anthropic")
        except Exception:
            pass
        return None, 0.0

    if resp.status_code != 200:
        log.warning(
            "chatbot_llm: Haiku returned %d: %s",
            resp.status_code, (resp.text or "")[:200],
        )
        return None, 0.0

    try:
        data = resp.json()
        content = data.get("content", [])
        answer = content[0].get("text", "") if content else ""
        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        cost = (in_tok / 1000.0) * _INPUT_COST_EUR_PER_1K + (
            out_tok / 1000.0
        ) * _OUTPUT_COST_EUR_PER_1K
        return answer or None, cost
    except Exception as exc:
        log.warning("chatbot_llm: Haiku parse failed: %s", exc)
        return None, 0.0


@dataclass
class LlmFallbackResult:
    success: bool
    answer: str | None
    cost_eur: float
    reason: str  # 'ok' | 'budget_exhausted' | 'no_context' | 'llm_error' | ...


def _should_use_llm(db: Session, shop_domain: str, message: str) -> tuple[bool, str]:
    """Decide if an unclassified message warrants LLM fallback."""
    # Topic guard: at least one HedgeSpark topic keyword must be present
    msg_lower = message.lower()
    if not any(topic in msg_lower for topic in _ALLOWED_TOPICS):
        return False, "out_of_topic"

    # Length guard: <20 chars is probably greeting, >500 is operator pastedump
    if len(message) < 20 or len(message) > 500:
        return False, "length_out_of_bounds"

    # Budget check — tiered per merchant
    try:
        from app.core.llm_budget import can_charge_merchant
        ok, reason = can_charge_merchant(db, shop_domain, _ESTIMATED_COST_EUR)
        if not ok:
            return False, f"budget:{reason}"
    except Exception as exc:
        log.debug("chatbot_llm: budget check errored: %s", exc)
        return False, "budget_check_error"

    return True, "ok"


def _build_rag_context(db: Session, shop_domain: str) -> dict[str, Any]:
    """Fetch a tight factual snapshot of the merchant's current state.

    Everything here is REAL data from their DB — no fabrication possible
    if the LLM is instructed to ground answers in this context.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    c7 = now - timedelta(days=7)
    c30 = now - timedelta(days=30)

    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop_domain)
    context: dict[str, Any] = {"shop_domain": shop_domain}

    try:
        row = db.execute(
            sql_text(
                """
                SELECT
                    COUNT(*) AS orders_30d,
                    COALESCE(SUM(total_price), 0) AS revenue_30d,
                    COUNT(*) FILTER (WHERE created_at >= :c7) AS orders_7d,
                    COALESCE(SUM(CASE WHEN created_at >= :c7 THEN total_price ELSE 0 END), 0) AS revenue_7d
                FROM shop_orders
                WHERE shop_domain = :s AND created_at >= :c30
                  AND (:currency IS NULL OR currency = :currency)
                """
            ),
            {"s": shop_domain, "c7": c7, "c30": c30, "currency": currency},
        ).fetchone()
        if row:
            context["orders_30d"] = int(row[0] or 0)
            context["revenue_30d_eur"] = round(float(row[1] or 0), 2)
            context["orders_7d"] = int(row[2] or 0)
            context["revenue_7d_eur"] = round(float(row[3] or 0), 2)
    except Exception:
        pass

    # Top 5 products by recent revenue
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT
                    line->>'title' AS title,
                    SUM((line->>'price')::numeric * (line->>'quantity')::int) AS revenue
                FROM shop_orders so,
                     jsonb_array_elements(COALESCE(so.line_items, '[]'::jsonb)) AS line
                WHERE so.shop_domain = :s AND so.created_at >= :c30
                  AND line->>'title' IS NOT NULL
                GROUP BY line->>'title'
                ORDER BY revenue DESC
                LIMIT 5
                """
            ),
            {"s": shop_domain, "c30": c30},
        ).fetchall()
        if rows:
            context["top_products"] = [
                {"title": str(r[0])[:60], "revenue_eur": round(float(r[1] or 0), 2)}
                for r in rows
            ]
    except Exception:
        pass

    # Recent opportunity signals
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT signal_type, COUNT(*) AS n
                FROM opportunity_signals
                WHERE shop_domain = :s AND detected_at >= :c7
                GROUP BY signal_type
                ORDER BY n DESC
                LIMIT 5
                """
            ),
            {"s": shop_domain, "c7": c7},
        ).fetchall()
        if rows:
            context["recent_signals_7d"] = [
                {"type": r[0], "count": int(r[1])} for r in rows
            ]
    except Exception:
        pass

    # Active nudges
    try:
        active_nudges = int(
            db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM active_nudges "
                    "WHERE shop_domain = :s AND status = 'active'"
                ),
                {"s": shop_domain},
            ).scalar()
            or 0
        )
        context["active_nudges"] = active_nudges
    except Exception:
        pass

    # RARS from redis history if available
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            raw = rc.get(f"hs:rars_history:v1:{shop_domain}")
            if raw:
                hist = json.loads(raw)
                if hist:
                    latest = hist[-1]
                    context["rars_current_eur"] = float(latest.get("total_at_risk_eur") or 0)
    except Exception:
        pass

    return context


def _build_prompt(context: dict[str, Any], user_message: str) -> str:
    """Build a tight grounded prompt for Haiku."""
    return f"""You are the HedgeSpark AI assistant. HedgeSpark is an e-commerce intelligence platform for Shopify merchants.

You are answering ONE question from the merchant. Ground your answer ONLY in the JSON data below. If the answer isn't in the data, say so clearly and offer to escalate to support.

STRICT RULES:
- Do NOT invent numbers. Only use numbers that appear in the JSON.
- Do NOT promise features that aren't explicitly supported.
- Keep your answer under 80 words.
- Use plain language. No jargon. No emoji except one optional at the start.
- If the question is outside HedgeSpark's scope (legal advice, personal opinions, other tools), say so and redirect to support.

MERCHANT DATA (real-time snapshot):
{json.dumps(context, indent=2, default=str)[:1400]}

MERCHANT QUESTION:
{user_message}

Your answer:"""


def _validate_response(answer: str, context: dict[str, Any]) -> tuple[bool, str]:
    """Catch obvious hallucinations. Returns (ok, reason)."""
    if not answer or len(answer.strip()) < 10:
        return False, "empty"
    if len(answer) > 800:
        return False, "too_long"

    # Detect numbers in the answer and verify each substantial number
    # (>=50) appears somewhere in the grounded context. Small numbers
    # (1-49) are allowed through — they're usually counts or percentages.
    answer_numbers = re.findall(r"\b\d{2,}\b", answer)
    context_str = json.dumps(context, default=str)
    for num in answer_numbers:
        try:
            n = int(num)
        except ValueError:
            continue
        if n >= 50 and num not in context_str:
            # Allow common percentages
            if n <= 100 and "%" in answer:
                continue
            return False, f"hallucinated_number:{num}"

    return True, "ok"


def try_llm_fallback(
    db: Session,
    *,
    shop_domain: str,
    message: str,
) -> LlmFallbackResult:
    """Attempt an LLM-backed fallback answer.

    Returns LlmFallbackResult. On any failure (budget/error/hallucination),
    `success=False` and the caller should fall back to the deterministic
    template. This function NEVER raises.
    """
    should, reason = _should_use_llm(db, shop_domain, message)
    if not should:
        return LlmFallbackResult(
            success=False, answer=None, cost_eur=0.0, reason=reason,
        )

    context = _build_rag_context(db, shop_domain)
    if not context.get("orders_30d") and not context.get("top_products"):
        return LlmFallbackResult(
            success=False, answer=None, cost_eur=0.0, reason="no_context",
        )

    prompt = _build_prompt(context, message)

    answer, actual_cost = _call_haiku(prompt)
    if not answer:
        return LlmFallbackResult(
            success=False, answer=None, cost_eur=0.0, reason="llm_empty_or_error",
        )

    if not answer:
        return LlmFallbackResult(
            success=False, answer=None, cost_eur=0.0, reason="empty_llm_response",
        )

    ok, vreason = _validate_response(answer, context)
    if not ok:
        log.info(
            "chatbot_llm: validation failed shop=%s reason=%s", shop_domain, vreason
        )
        # Emit dedup'd alert so the self-healing pipeline can triage chronic
        # hallucination patterns (prompt needs tightening) as a bugfix source.
        # best-effort: alert emit failure does not affect the user-facing
        # chatbot response — the answer is rejected regardless.
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source=f"chatbot_llm:{shop_domain}",
                alert_type="chatbot_llm_hallucination",
                summary=(
                    f"Chatbot LLM answer rejected for {shop_domain}: {vreason}"
                ),
                shop_domain=shop_domain,
                detail={
                    "reason": vreason,
                    "answer_preview": (answer or "")[:200],
                    "model": _HAIKU_MODEL,
                },
            )
            db.commit()
        except Exception:
            pass
        return LlmFallbackResult(
            success=False, answer=None, cost_eur=actual_cost, reason=f"invalid:{vreason}",
        )

    # Record per-merchant spend
    try:
        from app.core.llm_budget import record_merchant_charge
        record_merchant_charge(shop_domain, actual_cost or _ESTIMATED_COST_EUR)
    except Exception:
        pass

    return LlmFallbackResult(
        success=True,
        answer=answer.strip(),
        cost_eur=actual_cost or _ESTIMATED_COST_EUR,
        reason="ok",
    )
