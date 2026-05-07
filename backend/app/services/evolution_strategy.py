# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
evolution_strategy.py — the North Star.

ONE file. ONE strategy. Every Monthly Opus bet is scored against it.
Changing the strategy REQUIRES editing this file and committing to main —
it is intentionally NOT modifiable via LLM output, admin panel, or
runtime config. A 3-month strategy lock is enforced via STRATEGY_LOCKED_UNTIL.

Why one file?
-------------
An elite CTO picks ONE war and protects that choice against drift. The
system must refuse "good ideas that are outside the strategy" as firmly
as it refuses "bad ideas that are inside it". Holding that line is the
discipline that turns infrastructure into dominance.
"""
from __future__ import annotations

import re
from datetime import date

# ===========================================================================
# THE NORTH STAR — do not change without a strategic RFC + human commit
# ===========================================================================

STRATEGY_VERSION = 1
STRATEGY_NAME = "conversion_recovery_from_behavioral_signals"
STRATEGY_LOCKED_UNTIL = date(2026, 7, 5)  # minimum 3-month lock

STRATEGY_SUMMARY = (
    "We recover revenue from visitors who were about to bounce. The full "
    "loop: detect behavioural leaks in real time → intervene in-session "
    "(nudges, targeting, content) → measure causally via holdouts → "
    "reinforce wins, rollback losses. Every monthly bet must make this "
    "loop stronger, faster, or more accurate."
)

STRATEGY_NOT_DOING = [
    "ad spend attribution (Triple Whale's moat)",
    "LTV / cohort retention modelling (Lifetimely's moat)",
    "repurchase timing / win-back emails (Peel's moat)",
    "inventory management",
    "multi-channel marketing orchestration",
    "general-purpose BI dashboards",
    "customer service tooling",
]

# ===========================================================================
# Tier vocabulary — what we DO bet on
# ===========================================================================

# Tier 1 — the core loop. Bets here score highest.
_TIER_1_KEYWORDS = {
    "conversion", "cvr", "cart", "checkout", "nudge", "nudges", "targeting",
    "attribution", "behavioral", "behavioural", "intent", "leak", "recovery",
    "holdout", "causal", "abandon", "abandonment", "add-to-cart", "atc",
    "dwell", "scroll", "engagement", "bounce", "exit", "revenue",
    "high-intent", "return visitor", "return-visitor", "session",
    "in-session", "purchase path", "funnel",
}

# Tier 2 — measurement & signal quality that ENABLES Tier 1.
_TIER_2_KEYWORDS = {
    "data quality", "signal", "signals", "measurement", "tracking", "pixel",
    "event", "events", "visitor", "visitors", "product_metrics",
    "attribution_evidence", "evidence", "confidence", "significance",
    "sample size", "z-score", "trend-adjusted", "freshness",
}

# Tier 3 — infra. Only when it UNBLOCKS Tier 1/2 work.
_TIER_3_KEYWORDS = {
    "worker", "workers", "database", "latency", "p95", "memory", "scaling",
    "cache", "queue", "throughput", "load",
}

# Forbidden — competitor moats we explicitly do NOT chase.
_FORBIDDEN_KEYWORDS = {
    "ad spend", "facebook ads", "google ads", "meta ads", "tiktok ads",
    "ad attribution", "roas", "roi on ads",
    "ltv", "lifetime value", "lifetime-value", "cohort retention",
    "repurchase", "churn model", "churn prediction",
    "email campaign", "email campaigns", "win-back", "winback",
    "klaviyo campaign", "inventory management", "sku forecast",
    "multi-channel", "multichannel", "omnichannel",
    "customer service", "support ticket", "helpdesk",
}


# ===========================================================================
# Scoring
# ===========================================================================

def _normalize(text: str) -> str:
    return " " + re.sub(r"[^\w\s-]", " ", (text or "").lower()) + " "


def _count_hits(text: str, vocab: set[str]) -> int:
    n = 0
    for kw in vocab:
        # word-boundary-ish: surround the keyword with a space so a kw
        # like "cvr" doesn't match inside "cvrating".
        if f" {kw} " in text:
            n += 1
    return n


def score_alignment(bet: dict) -> tuple[float, str, list[str]]:
    """
    Score a bet against the current strategy.

    Returns (score, verdict, hits) where:
      score   0.0–10.0
      verdict aligned | tier3_only | off_strategy | forbidden
      hits    list of matched keywords (for audit/logging)

    Scoring policy:
      +3.0 per Tier-1 keyword hit (core conversion loop)
      +1.5 per Tier-2 keyword hit (measurement enabling Tier-1)
      +0.8 per Tier-3 keyword hit (infra — capped when ALONE)
      -10   on ANY forbidden keyword → off_strategy verdict, score 0

    Tier-3-only bets are capped at 4.0 — they cannot alone justify
    a month's bet slot unless there is a Tier-1 or Tier-2 presence.
    """
    searchable = _normalize(
        " ".join([
            str(bet.get("title", "")),
            str(bet.get("revenue_thesis", "") or bet.get("reasoning", "")),
            str(bet.get("expected_impact", "")),
            str(bet.get("why_this_bet_aligns_with_strategy", "")),
        ])
    )

    # Forbidden check first — instant rejection
    forbidden_hits = [kw for kw in _FORBIDDEN_KEYWORDS if f" {kw} " in searchable]
    if forbidden_hits:
        return 0.0, "forbidden", forbidden_hits

    tier1_hits = _count_hits(searchable, _TIER_1_KEYWORDS)
    tier2_hits = _count_hits(searchable, _TIER_2_KEYWORDS)
    tier3_hits = _count_hits(searchable, _TIER_3_KEYWORDS)

    # Raw score
    score = (3.0 * tier1_hits) + (1.5 * tier2_hits) + (0.8 * tier3_hits)

    # Tier-3-only cap
    if tier1_hits == 0 and tier2_hits == 0 and tier3_hits > 0:
        score = min(score, 4.0)
        verdict = "tier3_only"
    elif tier1_hits > 0 or tier2_hits > 0:
        verdict = "aligned"
    else:
        verdict = "off_strategy"
        score = 0.0

    score = min(10.0, round(score, 2))

    # Collect hit names for audit
    all_hits: list[str] = []
    for kw in _TIER_1_KEYWORDS | _TIER_2_KEYWORDS | _TIER_3_KEYWORDS:
        if f" {kw} " in searchable:
            all_hits.append(kw)
    return score, verdict, sorted(set(all_hits))


# Alignment threshold — below this we REJECT.
# 6.0 ≈ two Tier-1 keyword hits, or one Tier-1 + two Tier-2 hits.
STRATEGY_MIN_ALIGNMENT_SCORE = 6.0


def check_strategy_alignment(bet: dict) -> tuple[str | None, float, str, list[str]]:
    """
    Evaluate a bet against the strategy.

    Returns (error_reason_or_None, score, verdict, hits).
    error_reason_or_None = None when the bet is accepted.
    """
    score, verdict, hits = score_alignment(bet)
    if verdict == "forbidden":
        return f"strategy_forbidden_keywords:{','.join(hits[:3])}", score, verdict, hits
    if verdict == "off_strategy":
        return "strategy_off_axis:no_tier_keywords_matched", score, verdict, hits
    if score < STRATEGY_MIN_ALIGNMENT_SCORE:
        return f"strategy_alignment_too_low:score={score}<threshold={STRATEGY_MIN_ALIGNMENT_SCORE}", score, verdict, hits
    return None, score, verdict, hits


# ===========================================================================
# Prompt context — rendered into the monthly audit prompt
# ===========================================================================

def format_strategy_for_prompt() -> str:
    """Render the current strategy as a prompt block Monthly Opus must honour."""
    lines = [
        "═══════════════════════════════════════════════════════════════",
        f"CURRENT STRATEGY (v{STRATEGY_VERSION}) — locked until {STRATEGY_LOCKED_UNTIL.isoformat()}",
        "═══════════════════════════════════════════════════════════════",
        f"Name: {STRATEGY_NAME}",
        "",
        STRATEGY_SUMMARY,
        "",
        "Allowed domains (your bets MUST fall here):",
        "  Tier 1 (core):       conversion, nudges, targeting, attribution,",
        "                       behavioural detection, in-session recovery,",
        "                       holdout causality, funnel/cart/checkout",
        "  Tier 2 (supporting): signal quality, measurement infrastructure,",
        "                       tracking reliability, data trust",
        "  Tier 3 (infra):      only when it unblocks Tier 1 or Tier 2",
        "",
        "EXPLICITLY NOT DOING this cycle or any cycle while strategy is locked:",
    ]
    for item in STRATEGY_NOT_DOING:
        lines.append(f"  - {item}")
    lines.extend([
        "",
        "ALIGNMENT REQUIREMENT:",
        f"  Every bet MUST score >= {STRATEGY_MIN_ALIGNMENT_SCORE}/10 on strategy alignment.",
        "  Bets scoring below are REJECTED regardless of quality.",
        "  Bets mentioning forbidden competitor territory are REJECTED.",
        "",
        "Every bet MUST include a 'why_this_bet_aligns_with_strategy' field —",
        "one sentence tying the bet to the core loop (detect → intervene → measure).",
        "═══════════════════════════════════════════════════════════════",
    ])
    return "\n".join(lines)
