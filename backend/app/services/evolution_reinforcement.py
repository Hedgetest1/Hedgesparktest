# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
evolution_reinforcement.py — Turn measured wins into actual priority weight.

Without this module, `decision_status='reinforce'` was just a label. This
module converts the historical distribution of (tech_outcome × business_outcome)
into a **category-level reinforcement weight** that actively biases:

  1. compute_priority_score()     — multiplies the score of NEW proposals
  2. Monthly Opus prompt         — categories sorted by weight + explicit
                                   instruction to prefer high-weight domains

Weight formula (0.0–1.0)
------------------------
For each business_domain (currently 'conversion' and 'infra'):

    wins   = BOTH + BUSINESS_SUCCESS
    losses = NEITHER
    neutral = TECH_SUCCESS + NOISE + ignored

    raw    = (wins - losses) / max(total, 1)        in [-1, 1]
    weight = clamp(0.5 + raw * 0.5, 0.0, 1.0)      in [0, 1]

Interpretation:
    weight = 1.0   pure wins → strong reinforcement
    weight = 0.5   equal wins/losses OR no data → neutral
    weight = 0.0   pure losses → strong discouragement

Sample-weighted dampening: with fewer than _MIN_SAMPLES measured proposals
in a domain, weight is pulled toward 0.5 (neutral) proportionally. This
prevents a single-shot win or loss from swinging the policy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal
from app.services.evolution_business_outcomes import (
    classify_business_domain,
)
from app.services.evolution_business_outcomes import combined_outcome_label

log = logging.getLogger("evolution_reinforcement")

_MIN_SAMPLES = 5          # below this, weight is dampened toward 0.5
_LOOKBACK_DAYS = 180      # history window
_NEUTRAL = 0.5

# Domain-kill thresholds: a domain is RETIRED when we have enough evidence
# (n>=10 measured proposals) that it does not move revenue (<15% success).
# Un-retired automatically when refreshed data crosses back above 0.25.
_KILL_MIN_SAMPLES = 10
_KILL_MAX_SUCCESS_RATE = 0.15
_UNKILL_MIN_SUCCESS_RATE = 0.25

# Exploration floor: when the top domain dominates (>=50% of wins), at
# least this fraction of NEW bets must explore outside the top domain.
_EXPLORATION_FLOOR = 0.20
_DOMINANCE_THRESHOLD = 0.50


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def compute_reinforcement_weights(db: Session, days: int = _LOOKBACK_DAYS) -> dict[str, dict]:
    """
    Return per-domain reinforcement state.

    Returns:
      {
        "conversion": {
          "weight": 0.82, "wins": 7, "losses": 1, "neutral": 3, "total": 11,
          "success_rate": 0.875, "dampened": False,
        },
        "infra": {...},
      }
    """
    cutoff = _now() - timedelta(days=days)
    # ISOLATION GATE: Only real_merchant outcomes may influence reinforcement
    # weights. Pre-merchant/test/sandbox outcomes are excluded to prevent
    # synthetic evidence from becoming product truth.
    rows = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.business_measured_at >= cutoff,
            EvolutionProposal.business_outcome.isnot(None),
            EvolutionProposal.evidence_source == "real_merchant",
        )
        .all()
    )

    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        domain = classify_business_domain(r)
        b = buckets.setdefault(domain, {"wins": 0, "losses": 0, "neutral": 0, "total": 0})
        label = combined_outcome_label(r.outcome_status, r.business_outcome)
        if label in ("BOTH", "BUSINESS_SUCCESS"):
            b["wins"] += 1
        elif label == "NEITHER":
            b["losses"] += 1
        else:
            b["neutral"] += 1
        b["total"] += 1

    # Ensure the canonical domains are always present (even if zero history)
    for d in ("conversion", "infra"):
        buckets.setdefault(d, {"wins": 0, "losses": 0, "neutral": 0, "total": 0})

    result: dict[str, dict] = {}
    for domain, b in buckets.items():
        total = b["total"]
        if total == 0:
            raw = 0.0
        else:
            raw = (b["wins"] - b["losses"]) / total
        weight = max(0.0, min(1.0, _NEUTRAL + raw * _NEUTRAL))

        # Sample-size dampening: pull toward 0.5 when few samples
        dampened = False
        if total < _MIN_SAMPLES:
            sample_weight = total / _MIN_SAMPLES if _MIN_SAMPLES > 0 else 0.0
            weight = _NEUTRAL + (weight - _NEUTRAL) * sample_weight
            dampened = True

        denom = b["wins"] + b["losses"]
        success_rate = round(b["wins"] / denom, 3) if denom > 0 else 0.0

        result[domain] = {
            "weight": round(weight, 3),
            "wins": b["wins"],
            "losses": b["losses"],
            "neutral": b["neutral"],
            "total": total,
            "success_rate": success_rate,
            "dampened": dampened,
        }
    return result


def get_retired_domains(weights: dict[str, dict]) -> list[dict]:
    """
    Compute which domains should be RETIRED (do-not-propose) based on
    historical outcomes. A domain is retired when we have enough evidence
    (>= _KILL_MIN_SAMPLES measured proposals, counting wins+losses) AND
    the success rate is below _KILL_MAX_SUCCESS_RATE.

    Returns a list of {domain, success_rate, total, wins, losses, reason}
    dicts, ready to be injected into the Monthly Opus prompt as a
    "DO NOT PROPOSE" section.

    Retirement is reversible: if new measurements push success_rate above
    _UNKILL_MIN_SUCCESS_RATE, the domain drops off this list automatically.
    """
    retired: list[dict] = []
    for domain, s in weights.items():
        wins = s.get("wins", 0)
        losses = s.get("losses", 0)
        measured = wins + losses
        # Support external weight dicts that use total_attempts instead of wins/losses
        total_attempts = s.get("total_attempts", 0)
        if measured == 0 and total_attempts > 0:
            # Infer from success_rate + total_attempts
            rate = s.get("success_rate", 0.0)
            measured = total_attempts
        else:
            rate = None  # compute below
        if measured < _KILL_MIN_SAMPLES:
            continue
        if rate is None:
            rate = wins / measured if measured > 0 else 0.0
        if rate < _KILL_MAX_SUCCESS_RATE:
            retired.append({
                "domain": domain,
                "success_rate": round(rate, 3),
                "total": measured,
                "wins": wins,
                "losses": losses,
                "reason": (
                    f"retired — only {wins}/{measured} business wins "
                    f"({rate*100:.0f}%) across {measured} measured proposals"
                ),
            })
    return retired


def exploration_required(weights: dict[str, dict]) -> tuple[bool, str | None]:
    """
    Decide whether the next cycle MUST include at least one exploration bet.

    Returns (required: bool, dominant_domain: str | None).

    Exploration is required when ONE domain holds >= _DOMINANCE_THRESHOLD
    share of all measured wins. If no single domain dominates, exploration
    is still welcome but not forced.
    """
    total_wins = sum(s.get("wins", 0) for s in weights.values())
    if total_wins < 4:
        # Not enough wins anywhere to claim dominance.
        return False, None
    for domain, s in weights.items():
        wins = s.get("wins", 0)
        if wins / total_wins >= _DOMINANCE_THRESHOLD:
            return True, domain
    return False, None


def reinforcement_multiplier(domain: str, weights: dict[str, dict]) -> float:
    """
    Translate a domain's weight into a priority multiplier in [0.5, 1.5].

        weight = 0.5 (neutral / no data) → multiplier = 1.0
        weight = 1.0 (all wins)           → multiplier = 1.5
        weight = 0.0 (all losses)         → multiplier = 0.5
    """
    w = weights.get(domain, {}).get("weight", _NEUTRAL)
    return round(0.5 + w, 3)


def format_for_opus_prompt(weights: dict[str, dict]) -> str:
    """
    Render the reinforcement table as a compact prompt block.

    Sorted descending by weight so the highest-performing domains appear
    first, reinforcing Opus's attention on proven directions.
    """
    ordered = sorted(weights.items(), key=lambda kv: -kv[1].get("weight", kv[1].get("success_rate", 0.5)))
    lines = ["Reinforcement weights (higher = repeat this, lower = avoid):"]
    for domain, s in ordered:
        w = s.get("weight", s.get("success_rate", 0.5))
        dampened = s.get("dampened", False)
        flag = " (DAMPENED — few samples)" if dampened else ""
        wins = s.get("wins", 0)
        losses = s.get("losses", 0)
        neutral = s.get("neutral", 0)
        total = s.get("total", s.get("total_attempts", 0))
        lines.append(
            f"  {domain}: weight={w:.2f}{flag} "
            f"(wins={wins} losses={losses} neutral={neutral} n={total})"
        )
    return "\n".join(lines)
