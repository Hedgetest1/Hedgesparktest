"""
analytics_assistant.py — AI analytics assistant for merchants.

Strada 4 dominance move (2026-04-20). Closes the "Triple Whale Moby"
gap at the €39 tier. Merchant asks a question in plain English, Spark
pulls the relevant numbers from our deterministic services (RARS,
Brief, Benchmarks, Cohorts, Attribution) AND answers with a narrative
grounded in real data — never invented.

Why this doesn't drift into hallucination:
  - Every number in the prompt is sourced from a deterministic
    function. The LLM only composes prose; it never invents metrics.
  - The system prompt instructs Spark to refuse answering questions
    that need data we didn't provide — better "I can't see that yet"
    than a plausible-sounding fiction.
  - PII guard + budget cap applied before every provider call.
  - If LLM is budget-exhausted or unreachable, we return a
    deterministic fallback built from the same context (less rich
    prose, same accuracy).

Public interface:
    answer(db, shop, question) -> AnalyticsAnswer
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("analytics_assistant")

_MAX_QUESTION_LEN = 500
_MAX_TOKENS = 600
_MODEL = "claude-sonnet-4-20250514"
_TIMEOUT = 25.0

_SYSTEM_PROMPT = """\
You are Spark, HedgeSpark's analytics assistant for Shopify merchants.
Your job is to answer the merchant's question by interpreting the
numbers provided in the context. Rules:

1. NEVER invent a number. If the context doesn't contain data that
   answers the question, say so explicitly ("I don't have visibility
   on X yet") and suggest what data would need to arrive first.
2. Be terse. 3–5 sentences max for the main answer. No preamble.
3. Loss-framed when relevant — prefer "you're losing €X" over "your
   conversion is Y%".
4. End with a blank line, then three short suggested-followup
   questions the merchant might ask next, each on its own line
   prefixed with "Q:". Example:

   Q: Which product is most at risk?
   Q: How do I compare to peers?
   Q: What should I do in the next 24 hours?

5. Never use markdown formatting (no bold, no headers, no bullets)
   outside the Q: lines. Plain text only.
"""


@dataclass
class AnalyticsAnswer:
    answer: str
    data_sources: list[str] = field(default_factory=list)
    suggested_followups: list[str] = field(default_factory=list)
    degraded: bool = False  # True when LLM unavailable and we fell back


def _gather_context(db: Session, shop: str) -> tuple[str, list[str]]:
    """Build a dense, structured context block from all Lite surfaces.
    Returns (context_text, list_of_source_labels)."""
    lines: list[str] = []
    sources: list[str] = []

    def _safe(label: str, fn):
        try:
            return fn()
        except Exception as exc:
            log.warning("analytics_assistant: %s failed for %s: %s", label, shop, exc)
            return None

    # Revenue at Risk
    rars = _safe("rars", lambda: __import__("app.services.revenue_at_risk", fromlist=["get_revenue_at_risk"]).get_revenue_at_risk(db, shop))
    if rars:
        sources.append("revenue_at_risk")
        total = rars.get("total_at_risk_eur") or 0
        prev = rars.get("prevented_eur_this_month") or 0
        ccy = rars.get("currency") or "USD"
        comps = [c for c in (rars.get("components") or []) if c.get("loss_eur", 0) > 0]
        comps.sort(key=lambda c: c["loss_eur"], reverse=True)
        lines.append(f"[REVENUE AT RISK]")
        lines.append(f"  total_at_risk_this_month: {total} {ccy}")
        lines.append(f"  prevented_this_month: {prev} {ccy}")
        for c in comps[:5]:
            lines.append(f"  component {c['source']}: {c['loss_eur']} {ccy}")

    # Daily brief
    brief = _safe("brief", lambda: __import__("app.services.brief_engine", fromlist=["generate_brief"]).generate_brief(db, shop))
    if brief:
        sources.append("daily_brief")
        lines.append(f"[DAILY BRIEF]")
        lines.append(f"  signals_count: {brief.get('signals_count') or 0}")
        if brief.get("headline"):
            lines.append(f"  headline: {brief['headline']}")
        if brief.get("top_product_label"):
            lines.append(f"  top_product: {brief['top_product_label']}")
        if brief.get("top_action"):
            lines.append(f"  top_action: {brief['top_action']}")

    # Benchmarks
    bench = _safe("benchmarks", lambda: __import__("app.services.benchmarks", fromlist=["get_extended_benchmark_report"]).get_extended_benchmark_report(db, shop))
    if bench and bench.get("band"):
        sources.append("peer_benchmarks")
        lines.append(f"[PEER BENCHMARKS · band {bench.get('band')} · {bench.get('peer_count') or 0} peers]")
        for name, m in (bench.get("metrics") or {}).items():
            if isinstance(m, dict):
                lines.append(f"  {name}: you={m.get('value')} percentile_rank={m.get('percentile_rank')} recovery_to_p75={m.get('recovery_to_p75_eur', 0)}")
        pc = bench.get("product_concentration")
        if pc:
            lines.append(f"  pareto_80pct: {pc.get('products_for_80pct_revenue')} products / {pc.get('total_products')}")

    # Cohorts
    coh = _safe("cohorts", lambda: __import__("app.services.cohort_engine", fromlist=["get_cohort_summary"]).get_cohort_summary(db, shop))
    if coh and (coh.get("total_customers") or 0) > 0:
        sources.append("cohort_retention")
        lines.append(f"[COHORT RETENTION]")
        lines.append(f"  customers: {coh.get('total_customers')}")
        lines.append(f"  week_1_retention: {coh.get('avg_week_1_retention')}")
        lines.append(f"  week_4_retention: {coh.get('avg_week_4_retention')}")
        lines.append(f"  week_12_retention: {coh.get('avg_week_12_retention')}")
        lines.append(f"  week_26_retention: {coh.get('avg_week_26_retention')}")
        lines.append(f"  best_cohort: {coh.get('best_cohort')}")

    # Attribution
    attr = _safe("attribution", lambda: __import__("app.services.utm_attribution", fromlist=["get_attribution_summary"]).get_attribution_summary(db, shop, days=30))
    if attr:
        sources.append("attribution")
        lines.append(f"[ATTRIBUTION · last 30 days]")
        lines.append(f"  orders_total: {attr.get('orders_total') or 0}")
        lines.append(f"  attribution_rate: {attr.get('attribution_rate') or 0}")
        lines.append(f"  first_vs_last_match_rate: {attr.get('first_vs_last_match_rate') or 0}")
        for s in (attr.get("top_sources_first_touch") or [])[:3]:
            lines.append(f"  first_touch_source {s.get('label')}: orders={s.get('orders')} revenue={s.get('revenue')}")

    # P&L
    pnl = _safe("pnl", lambda: __import__("app.services.pnl_engine", fromlist=["get_pnl_report"]).get_pnl_report(db, shop, window_days=30))
    if pnl and pnl.get("has_data"):
        sources.append("pnl")
        lines.append(f"[PROFIT & LOSS · last 30 days]")
        lines.append(f"  gross_revenue: {pnl.get('gross_revenue')}")
        lines.append(f"  gross_profit: {pnl.get('gross_profit')}")
        lines.append(f"  gross_margin_pct: {pnl.get('gross_margin_pct')}")
        lines.append(f"  net_profit: {pnl.get('net_profit')}")
        lines.append(f"  net_margin_pct: {pnl.get('net_margin_pct')}")

    if not lines:
        lines.append("[NO DATA] The shop has not produced enough data yet to assemble a meaningful context.")

    return "\n".join(lines), sources


def _parse_llm_response(raw: str) -> tuple[str, list[str]]:
    """Split the LLM output into (main_answer, followup_questions).
    Follows the system-prompt format:
        main answer lines...
        <blank>
        Q: first followup
        Q: second followup
        Q: third followup
    Is tolerant to whitespace / missing Q: lines.
    """
    if not raw:
        return "", []
    lines = [ln for ln in raw.strip().splitlines()]
    # Split on first blank line preceding a Q: section
    answer_lines: list[str] = []
    followups: list[str] = []
    in_followups = False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("Q:"):
            in_followups = True
            followups.append(stripped[2:].strip())
        elif in_followups and stripped == "":
            continue
        elif not in_followups:
            answer_lines.append(ln)
    answer = "\n".join(answer_lines).strip()
    return answer, followups[:3]


def _fallback_answer(context: str, sources: list[str]) -> AnalyticsAnswer:
    """Deterministic fallback when LLM is unavailable. Constructs a
    brief narrative from the most salient lines in the context."""
    lines = context.splitlines()
    highlights: list[str] = []
    for ln in lines:
        if "total_at_risk_this_month" in ln:
            highlights.append(f"Revenue-at-risk this month is {ln.split(':', 1)[1].strip()}.")
        if "headline:" in ln:
            highlights.append(f"Today's brief headline: {ln.split(':', 1)[1].strip()}.")
        if "week_4_retention:" in ln:
            val = ln.split(":", 1)[1].strip()
            try:
                rate = float(val) * 100
                highlights.append(f"Week-4 retention: {rate:.0f}%.")
            except ValueError:
                pass
    if not highlights:
        highlights.append("I don't have enough data loaded yet to answer in detail.")
    return AnalyticsAnswer(
        answer=" ".join(highlights),
        data_sources=sources,
        suggested_followups=[
            "What's my biggest leak right now?",
            "How do I compare to peers?",
            "Which products drive my LTV?",
        ],
        degraded=True,
    )


def _call_anthropic(prompt: str) -> str:
    """Call Claude with the assistant system prompt. Returns empty
    string on any failure — caller falls back to deterministic."""
    import httpx
    from app.core.llm_budget import (
        check_budget, is_provider_backed_off, record_429, record_usage,
    )
    from app.core.llm_pii_guard import check_for_pii

    can_proceed, reason = check_budget("analytics_assistant")
    if not can_proceed:
        log.info("analytics_assistant: budget gate blocked (%s)", reason)
        return ""

    if is_provider_backed_off("anthropic"):
        log.info("analytics_assistant: anthropic backed off")
        return ""

    pii_hits = check_for_pii(prompt)
    if pii_hits:
        log.warning("analytics_assistant: PII guard blocked prompt (%d hits)", len(pii_hits))
        return ""

    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ""

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": _MAX_TOKENS,
                "temperature": 0.2,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            usage = data.get("usage") or {}
            total_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            record_usage(
                "analytics_assistant",
                tokens_used=total_tokens,
                provider="anthropic",
                model=_MODEL,
            )
            return text
        if resp.status_code == 429:
            record_429("anthropic")
        else:
            log.warning("analytics_assistant: anthropic %d", resp.status_code)
        return ""
    except Exception as exc:
        log.warning("analytics_assistant: call failed: %s", type(exc).__name__)
        return ""


def answer(db: Session, shop: str, question: str) -> AnalyticsAnswer:
    """Main entry point. Clamps the question length, gathers context,
    calls LLM, parses response. Falls back deterministically when the
    LLM is unavailable so the endpoint never 500s."""
    q = (question or "").strip()[:_MAX_QUESTION_LEN]
    if not q:
        return AnalyticsAnswer(
            answer="Ask me about your revenue, retention, peer benchmarks, or any surface on your dashboard.",
            data_sources=[],
            suggested_followups=[
                "What's my biggest leak right now?",
                "How is my retention looking?",
                "Where am I vs peers?",
            ],
        )

    context, sources = _gather_context(db, shop)
    prompt = f"CONTEXT (merchant's current data):\n{context}\n\nQUESTION: {q}"

    raw = _call_anthropic(prompt)
    if not raw:
        return _fallback_answer(context, sources)

    parsed_answer, followups = _parse_llm_response(raw)
    if not parsed_answer:
        return _fallback_answer(context, sources)

    if not followups:
        followups = [
            "What's my biggest leak right now?",
            "How do I compare to peers?",
            "Which products drive my LTV?",
        ]

    return AnalyticsAnswer(
        answer=parsed_answer,
        data_sources=sources,
        suggested_followups=followups,
        degraded=False,
    )
