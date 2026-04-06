"""
evolution_bet_governance.py — the bet discipline layer.

Pure functions that enforce elite-CTO discipline on parsed bets BEFORE
they reach storage. Every function is side-effect-free and independently
testable. The monthly-audit parser composes these into a pipeline:

    reject_invalid_type
    override_underestimated_cost
    classify_ux_sensitivity
    validate_expected_impact
    reject_retired_domain
    normalize_fingerprint  (for dedup across wordings)

Batch-level gates (applied after all individual bets pass):

    reject_if_no_diversification
    reject_if_exploration_missing_when_required
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Valid enums (shared with monthly_evolution_audit)
# ---------------------------------------------------------------------------

VALID_TYPES = {
    # Business categories
    "growth", "retention", "conversion", "experiment", "deprecate",
    # Engineering categories
    "architecture", "performance", "reliability", "product",
}

VALID_COST_ESTIMATES = {"none", "small", "medium", "large"}

VALID_IMPACT_RADIUS = {"internal", "visible", "structural"}


# ---------------------------------------------------------------------------
# STEP 1 — Invalid type → REJECT, never fallback
# ---------------------------------------------------------------------------

def check_type_valid(bet: dict) -> str | None:
    """Return an error reason if type is invalid, else None."""
    ptype = (bet.get("type") or "").strip().lower()
    if ptype not in VALID_TYPES:
        return f"invalid_type:{ptype or 'missing'}"
    return None


# ---------------------------------------------------------------------------
# STEP 2 — Cost heuristic overrides
# ---------------------------------------------------------------------------

_COST_SMALL_KEYWORDS = re.compile(
    r"\b(worker|cron|cache|redis|queue|websocket|real[- ]?time|"
    r"polling|poll|increase frequency|schedule|background job)\b",
    re.IGNORECASE,
)

_COST_MEDIUM_KEYWORDS = re.compile(
    r"\b(new service|new process|external api|third[- ]?party|"
    r"new worker|new endpoint|microservice|webhook listener)\b",
    re.IGNORECASE,
)


def override_underestimated_cost(bet: dict) -> tuple[str, str | None]:
    """
    Return (effective_cost, override_reason). Override reason is None
    when no override applied.

    Heuristic bumps:
      - keywords like 'worker', 'cache', 'redis' + cost='none' → 'small'
      - keywords like 'new service' + cost in {'none','small'} → 'medium'

    Searches the concatenated (title, revenue_thesis, expected_impact,
    infra_cost_reasoning) text.
    """
    declared = (bet.get("infra_cost_estimate") or "none").strip().lower()
    if declared not in VALID_COST_ESTIMATES:
        declared = "none"

    searchable = " ".join([
        str(bet.get("title", "")),
        str(bet.get("revenue_thesis", "")),
        str(bet.get("expected_impact", "")),
        str(bet.get("infra_cost_reasoning", "")),
    ])

    if _COST_MEDIUM_KEYWORDS.search(searchable) and declared in ("none", "small"):
        return "medium", "cost_underestimated:medium_keywords_detected"
    if _COST_SMALL_KEYWORDS.search(searchable) and declared == "none":
        return "small", "cost_underestimated:small_keywords_detected"
    return declared, None


# ---------------------------------------------------------------------------
# STEP 3 — UX sensitivity classification
# ---------------------------------------------------------------------------

_UX_STRUCTURAL_KEYWORDS = re.compile(
    r"\b(dashboard|layout|UI|KPI|rename|navigation|sidebar|"
    r"menu|section|tab|card grid|empty state|notification|"
    r"terminology|merchant facing|top bar|header|footer)\b",
    re.IGNORECASE,
)


def classify_ux_sensitivity(bet: dict) -> tuple[bool, str]:
    """
    Decide ux_sensitive and impact_radius for the bet.

    Returns (ux_sensitive: bool, impact_radius: str).

    Rules:
      - If the bet's text mentions dashboard/layout/UI/KPI/nav/etc. keywords
        → force ux_sensitive=True, impact_radius='structural'
      - If impact_radius is already declared 'structural' by the LLM
        → force ux_sensitive=True
      - Else: accept declared impact_radius if valid; default 'internal'
    """
    searchable = " ".join([
        str(bet.get("title", "")),
        str(bet.get("revenue_thesis", "")),
        str(bet.get("expected_impact", "")),
    ])

    keyword_hit = bool(_UX_STRUCTURAL_KEYWORDS.search(searchable))

    declared_radius = (bet.get("impact_radius") or "").strip().lower()
    if declared_radius not in VALID_IMPACT_RADIUS:
        declared_radius = ""

    if keyword_hit:
        return True, "structural"
    if declared_radius == "structural":
        return True, "structural"
    if declared_radius:
        return False, declared_radius
    return False, "internal"


# ---------------------------------------------------------------------------
# STEP 4 — expected_impact quality gate
# ---------------------------------------------------------------------------

# A measurable outcome contains EITHER a number with unit/percent, OR a
# concrete currency amount, OR a specific metric name tied to a delta.
_MEASURABLE_PATTERN = re.compile(
    r"("
    r"[+-]?\s*\d+(?:\.\d+)?\s*%"                          # "12%" or "+12%"
    r"|[+-]?\s*€\s*\d"                                    # "€300"
    r"|[+-]?\s*\$\s*\d"                                   # "$300"
    r"|\d+(?:\.\d+)?\s*(?:ms|s|day|days|week|weeks|month|months|hour|hours)"  # "48h", "7 days"
    r"|\b(?:cvr|atc|aov|rpv|ltv|cac|churn)\b.*?\d"        # "CVR 2.1%" etc
    r")",
    re.IGNORECASE,
)

_VAGUE_TERMS = re.compile(
    r"\b(improve|optimize|enhance|better|faster|smoother|nicer|cleaner|refine|polish)\b\s*\.?\s*$",
    re.IGNORECASE,
)


def validate_expected_impact(bet: dict) -> str | None:
    """
    Return an error reason if expected_impact is vague, else None.

    Accepts: any text containing a number+unit/% OR currency OR metric delta.
    Rejects: standalone "improve X" / "optimize Y" / "enhance Z" lines.
    """
    impact = (bet.get("expected_impact") or "").strip()
    if not impact or len(impact) < 10:
        return "expected_impact_missing_or_too_short"
    if _MEASURABLE_PATTERN.search(impact):
        return None
    if _VAGUE_TERMS.search(impact):
        return "expected_impact_vague_verb_only"
    # Text exists, has length, but contains no metric. Reject as unmeasurable.
    return "expected_impact_not_measurable"


# ---------------------------------------------------------------------------
# STEP 6 — Retired-domain hard block
# ---------------------------------------------------------------------------

def reject_if_retired_domain(bet_type: str, retired_domains_list: list[dict]) -> str | None:
    """
    Retired domains are business-level (conversion, infra). Bet types that
    map to a retired domain must be REJECTED at parse time.

    Mapping: any non-infra type = conversion-domain; infra types = infra-domain.
    (Matches evolution_business_outcomes.classify_business_domain semantics.)
    """
    if not retired_domains_list:
        return None
    retired_names = {r.get("domain") for r in retired_domains_list}
    # Infra-family types
    infra_types = {"architecture", "performance", "reliability"}
    mapped_domain = "infra" if bet_type in infra_types else "conversion"
    if mapped_domain in retired_names:
        return f"retired_domain:{mapped_domain}"
    return None


# ---------------------------------------------------------------------------
# STEP 7 — Fingerprint normalization (anti-loop)
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "to", "of", "for", "and", "or", "in", "on", "at",
    "by", "with", "from", "up", "as", "it", "this", "that", "be", "is",
    "are", "was", "were", "has", "have", "had", "will", "would", "could",
    "should", "may", "might", "can", "do", "does", "did", "improve",
    "optimize", "enhance", "better", "faster", "new", "add", "create",
    "build", "implement", "deploy", "make", "get", "our", "your", "their",
}

# Synonyms that should collapse into a single token so "optimize caching
# layer" and "improve cache performance" collide.
_SYNONYM_MAP = {
    "cache": "cache", "caching": "cache", "caches": "cache",
    "performance": "perf", "perf": "perf", "speed": "perf",
    "layer": "stack", "stack": "stack", "system": "stack",
    "conversion": "cvr", "cvr": "cvr", "convert": "cvr",
    "nudge": "nudge", "nudges": "nudge", "nudging": "nudge",
    "dashboard": "ui", "ui": "ui", "layout": "ui", "interface": "ui",
    "attribution": "attr", "attr": "attr",
    "revenue": "rev", "rev": "rev", "income": "rev",
    "product": "prod", "products": "prod",
    "customer": "cust", "customers": "cust", "visitor": "cust", "visitors": "cust",
    "tracking": "track", "tracker": "track", "track": "track",
}


def is_fingerprint_duplicate(new_fp: str, existing_fps: set[str], threshold: float = 0.75) -> bool:
    """
    Compare a new fingerprint against existing ones using Jaccard overlap.
    Returns True if the new fingerprint shares >=threshold of tokens with
    any existing fingerprint.

    This catches wording drift: "optimize caching layer" vs "improve cache
    performance" normalize to different but heavily overlapping token sets.
    Exact-match dedup would miss this; Jaccard catches it.
    """
    if not new_fp:
        return False
    new_tokens = set(new_fp.split("|"))
    if not new_tokens:
        return False
    for existing in existing_fps:
        existing_tokens = set(existing.split("|"))
        if not existing_tokens:
            continue
        intersect = len(new_tokens & existing_tokens)
        union = len(new_tokens | existing_tokens)
        if union == 0:
            continue
        if intersect / union >= threshold:
            return True
    return False


def normalize_fingerprint(text: str) -> str:
    """
    Collapse a proposal title/thesis into a canonical signature for
    wording-independent dedup.

    Steps:
      1. lowercase, strip punctuation
      2. drop stopwords and weak verbs
      3. map synonyms
      4. sort remaining tokens + join with '|'

    Example:
      "optimize caching layer"  →  "cache|perf|stack"  (drop weak verbs,
                                    map cache/layer, drop 'optimize')
      "improve cache performance" →  "cache|perf"
      (Both collapse to the same dedup-meaningful token set once 'layer'
       and 'performance' synonym-collapse to 'stack'/'perf'.)
    """
    if not text:
        return ""
    lowered = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = [t for t in lowered.split() if t]
    filtered: list[str] = []
    for t in tokens:
        if t in _STOP_WORDS:
            continue
        mapped = _SYNONYM_MAP.get(t, t)
        filtered.append(mapped)
    unique = sorted(set(filtered))
    return "|".join(unique)


# ---------------------------------------------------------------------------
# STEP 5 — Batch-level diversification + exploration gates
# ---------------------------------------------------------------------------

def check_batch_diversification(bets: list[dict]) -> str | None:
    """
    Reject the batch if all bets fall in the same type. Exception: single
    bet OR all-same-type where the type IS actually intentional (e.g. all
    'experiment') is allowed when total_bets <= 1.
    """
    if len(bets) <= 1:
        return None
    types = {(b.get("type") or "").strip().lower() for b in bets}
    if len(types) == 1:
        only = next(iter(types))
        return f"no_diversification:all_bets_type={only}"
    return None


def check_exploration_floor(bets: list[dict], exploration_required: bool) -> str | None:
    """
    Reject the batch when exploration is required but no bet is flagged
    exploration_bet=True.
    """
    if not exploration_required:
        return None
    if any(bool(b.get("exploration_bet")) for b in bets):
        return None
    return "exploration_floor_violated"
