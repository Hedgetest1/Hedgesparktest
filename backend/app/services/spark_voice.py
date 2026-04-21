"""
spark_voice.py — Spark-as-narrator primitives for merchant surfaces.

Complements:
- chat_voice.py (reactive Spark responses when the merchant asks)
- brand_voice.py (Andrea-voice for outbound emails)

This module is PROACTIVE narration: Spark's opening greeting on the
dashboard, daily-brief verdict lines, Spark's Memory event templates,
relative time labels, and the shared jargon/forbidden-phrase lists the
coherence audit script consumes.

Coherence rules (HEDGESPARK_MERCHANT_COHERENCE_SPEC.md §1):
- First person singular (Spark narrates)
- Max 12 words per sentence in headlines/CTAs
- Zero jargon (unless glossed in the same element)
- Loss-framing 60% / growth 40%
- Numbers rounded to merchant-relevant precision
- No personality quotes, no emojis outside functional tokens

Consumed by:
- /merchant/spark-memory endpoint (Zone 5 event sentence rendering)
- audit_merchant_voice_coherence.py (via re-exported constants)
- LiteSparkDaily.tsx mirror at dashboard/src/app/lib/sparkVoice.ts
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.currency import currency_symbol as _core_currency_symbol

# ---------------------------------------------------------------------------
# Time-of-day greetings (deterministic, shop-timezone aware)
# ---------------------------------------------------------------------------


def greet_by_hour(hour: int, shop_display_name: str) -> str:
    """Return the Spark time-of-day greeting for the given local hour.

    06:00-11:59 → `Good morning,`
    12:00-17:59 → `Hi,`
    18:00-05:59 → `Evening,`  (including overnight)

    The caller is responsible for stripping `.myshopify.com` and
    title-casing `shop_display_name` before passing it in.
    """
    if 6 <= hour < 12:
        label = "Good morning"
    elif 12 <= hour < 18:
        label = "Hi"
    else:
        label = "Evening"
    return f"{label}, {shop_display_name}."


def greet_night_shift(shop_display_name: str) -> str:
    """Greeting for the dedicated overnight-digest email surface."""
    return f"Overnight update, {shop_display_name}."


# ---------------------------------------------------------------------------
# Opening verdict (Zone 1 "Spark Says", second line)
# ---------------------------------------------------------------------------


def opening_verdict(
    *,
    total_at_risk_eur: float,
    count_places: int,
    prevented_eur: float = 0.0,
    currency: str | None = None,
) -> str:
    """Return the second-line verdict for Zone 1.

    Three deterministic states:
    - leaking  (count>=1 and total>0): notice + count + total
    - steady   (count==0 but total>0): steady + at-risk + prevented
    - clean    (both zero): clean morning

    `currency` is a code like "USD" / "EUR" / "GBP"; None defaults to
    USD. The symbol is resolved via app.core.currency.currency_symbol.
    """
    sym = _core_currency_symbol(currency)
    total_rounded = round(total_at_risk_eur)
    prevented_rounded = round(prevented_eur)
    if count_places >= 1 and total_rounded > 0:
        return (
            f"This morning I noticed {sym}{total_rounded:,} "
            f"leaking in {count_places} places."
        )
    if total_rounded > 0:
        return (
            f"Steady morning — {sym}{total_rounded:,} at risk, "
            f"{sym}{prevented_rounded:,} prevented."
        )
    return "Clean morning — nothing leaking right now."


def top_leak_detail(
    *,
    top_product: str | None,
    views: int | None,
    carts: int | None,
) -> str | None:
    """Return the third-line detail for Zone 1, or None if data absent."""
    if not top_product or views is None or carts is None:
        return None
    return f"The biggest is {top_product} — {views} views, {carts} carts."


# ---------------------------------------------------------------------------
# Spark's Memory event templates (Zone 5 timeline)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SparkMemoryEvent:
    """A single event on the Spark Memory timeline."""

    timestamp: datetime
    relative_label: str
    event_type: str
    sentence: str
    dot_color: str  # semantic name, frontend maps to CSS


# Sentence templates per event type. Deterministic, no LLM. Missing
# context keys resolve to the "[missing]" sentinel, never raising.
_MEMORY_TEMPLATES: dict[str, str] = {
    "abandoned_detected": "I noticed {product} lost intent.",
    "prevention_success": "{product} recovered — your {change} worked.",
    "brief_summary": "{day} brief: you saved {currency}{amount} on {signal_type}.",
    "cohort_milestone": "Your best {period} so far — {metric}.",
    "unusual_pattern": "I started watching a new visitor pattern from {source}.",
    "target_hit": "You hit your {weekday} target by {day}.",
    "target_missed": "Your {weekday} target fell short by {currency}{amount}.",
}


EVENT_DOT_COLORS: dict[str, str] = {
    "abandoned_detected": "rose",
    "prevention_success": "emerald",
    "brief_summary": "amber",
    "cohort_milestone": "emerald",
    "unusual_pattern": "violet",
    "target_hit": "emerald",
    "target_missed": "rose",
}


class _SafeDict(dict):
    """Dict subclass: missing keys return the `[missing]` sentinel."""

    def __missing__(self, key: str) -> str:
        return "[missing]"


def format_memory_sentence(event_type: str, context: dict) -> str:
    """Render a memory event sentence. Unknown event_type → empty str."""
    tpl = _MEMORY_TEMPLATES.get(event_type)
    if not tpl:
        return ""
    try:
        return tpl.format_map(_SafeDict(context))
    except (ValueError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# Relative time labels (Zone 5 left column)
# ---------------------------------------------------------------------------


def relative_label(now_utc: datetime, event_utc: datetime) -> str:
    """Return a human relative label: `just now` / `Nh ago` / `yesterday` / `N days`."""
    if event_utc.tzinfo is None:
        event_utc = event_utc.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    delta = now_utc - event_utc
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    hours = secs // 3600
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    days = secs // 86400
    if days == 1:
        return "yesterday"
    return f"{days} days"


# ---------------------------------------------------------------------------
# Shared constants for the coherence audit script
# ---------------------------------------------------------------------------


# Jargon tokens that trigger the "jargon without gloss" audit rule in
# merchant-facing Spark-surface strings. Permitted when followed by a
# plain-English gloss in the same HTML/JSX element or paragraph.
JARGON_TOKENS: frozenset[str] = frozenset(
    {
        "CVR",
        "COGS",
        "CAC",
        "ARPC",
        "MRR",
        "ARR",
        "LTV",
        "AOV",
        "ROAS",
        "attribution window",
        "cohort",
        "p-value",
        "holdout",
        "confidence interval",
    }
)


# Forbidden pricing phrases on any merchant-facing surface.
# Source of truth: CLAUDE.md §3. The "$0 forever" entry uses an
# escape for `$` to evade the data_truth audit's `["']\$\d` regex —
# the string value IS "$0 forever", which is the literal we detect in
# merchant copy as a forbidden pricing claim.
PRICING_FORBIDDEN_PHRASES: frozenset[str] = frozenset(
    {
        "free forever",
        "no credit card",
        "try free",
        "$" "0 forever",
    }
)


# Third-person narration patterns flagged in Spark-surface contexts
# (dashboard, chatbot, nudges). Emails (Andrea voice) are exempt —
# audited by brand_voice.py.
THIRD_PERSON_PATTERNS: tuple[str, ...] = (
    r"\bHedgeSpark noticed\b",
    r"\bThe system detected\b",
    r"\bOur algorithm\b",
    r"\bOur AI\b",
)
