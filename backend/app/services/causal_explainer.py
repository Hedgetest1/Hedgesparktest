"""
causal_explainer.py — Phase Ω killer #2.

The "why" engine. When a metric drops, walks the merchant's knowledge
graph + atomic anomaly signals + vertical-specific causal hypotheses
to surface the most likely causal chain in plain language.

Generic dashboards say "revenue down 12%". We say:

    "Revenue is down 12% over the last 24h vs the 7-day baseline.
     Likely cause (confidence 71%): paid efficiency collapse on Meta —
     ROAS fell 38% in the same window while Meta spend held steady.
     Recommended next step: pause campaign 'Spring Promo' and rotate
     creatives. If the drop persists 24h after action, escalate to
     ad-creative-fatigue review."

Composition
-----------
* Inputs: anomaly_fusion alerts, knowledge_graph entities,
  vertical_prompt_pack causal hypotheses.
* Output: ranked list of `CausalHypothesis` with score, evidence,
  next action, narrative.

Determinism
-----------
Pure scoring — no LLM. The vertical prompt pack provides the *priors*
(which causes are likely in this vertical), and observed signals
become *evidence* that boosts or suppresses each hypothesis. This is
a Bayesian-style update without any LLM cost.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.services.anomaly_fusion import fuse
from app.services.vertical_classifier import get_vertical
from app.services.vertical_prompt_pack import causal_hypotheses_for, get_profile

log = logging.getLogger("causal_explainer")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Hypothesis catalog — maps causal label → (signals that support it,
# narrative template, recommended action). The catalog is the unique
# bridge between the vertical prior and the observed signals.
# ---------------------------------------------------------------------------


@dataclass
class HypothesisDef:
    label: str
    supporting_signals: tuple[str, ...]   # atomic signal names that BOOST this hypothesis
    suppressing_signals: tuple[str, ...]  # signals that LOWER it
    narrative_template: str
    recommended_action: str
    base_prior: float = 0.10              # default weight if vertical prior absent


_CATALOG: dict[str, HypothesisDef] = {
    "ad_creative_fatigue": HypothesisDef(
        label="ad_creative_fatigue",
        supporting_signals=("ad_roas_collapse_7d",),
        suppressing_signals=("refund_spike_48h",),
        narrative_template="Ad ROAS fell {ad_roas_collapse_7d_delta_pct}% over the last 7 days while spend held — typical creative fatigue.",
        recommended_action="Rotate the top 3 worst-CTR creatives and run a 48h holdout test.",
    ),
    "ad_cpm_spike": HypothesisDef(
        label="ad_cpm_spike",
        supporting_signals=("ad_roas_collapse_7d",),
        suppressing_signals=(),
        narrative_template="Paid efficiency collapsed — likely auction-side CPM spike from a competitor or seasonal demand.",
        recommended_action="Cap budgets on the worst-ROAS campaign for 24h and reassess.",
    ),
    "competitor_promo": HypothesisDef(
        label="competitor_promo",
        supporting_signals=("revenue_drop_24h", "repeat_rate_drop_30d"),
        suppressing_signals=("refund_spike_48h",),
        narrative_template="Revenue and repeat rate dropped together — consistent with a competitor running an aggressive promo.",
        recommended_action="Run a 24h flash incentive on the affected category, holdout 30%.",
    ),
    "subscription_churn": HypothesisDef(
        label="subscription_churn",
        supporting_signals=("repeat_rate_drop_30d",),
        suppressing_signals=(),
        narrative_template="Repeat customers are buying less — typical of subscription churn or retention failure.",
        recommended_action="Trigger the win-back email sequence and check the cancellation reason mix.",
    ),
    "size_curve_imbalance": HypothesisDef(
        label="size_curve_imbalance",
        supporting_signals=("revenue_drop_24h", "refund_spike_48h"),
        suppressing_signals=(),
        narrative_template="Refunds spiked alongside revenue drop — likely size/fit mismatches in the active collection.",
        recommended_action="Pull the top-3 refunded SKUs and audit their size curve coverage.",
    ),
    "ingredient_supply_constraint": HypothesisDef(
        label="ingredient_supply_constraint",
        supporting_signals=("revenue_drop_24h",),
        suppressing_signals=("ad_roas_collapse_7d",),
        narrative_template="Revenue is dropping without ad efficiency loss — possible supply or stock constraint hiding inventory.",
        recommended_action="Audit inventory levels of the top-selling 5 SKUs.",
    ),
    "weather_shift": HypothesisDef(
        label="weather_shift",
        supporting_signals=("revenue_drop_24h",),
        suppressing_signals=("refund_spike_48h",),
        narrative_template="Revenue drop without quality signals — for weather-sensitive verticals this is often atmospheric.",
        recommended_action="Re-check in 48h. If persistent, rotate creative angle to indoor / season-shift framing.",
    ),
    "shipping_delay_perception": HypothesisDef(
        label="shipping_delay_perception",
        supporting_signals=("repeat_rate_drop_30d",),
        suppressing_signals=(),
        narrative_template="Repeat rate softening suggests post-purchase friction — usually shipping or delivery sentiment.",
        recommended_action="Audit shipping carrier on-time rate and add a 'delivered fast' badge if eligible.",
    ),
    "seasonal_demand_shift": HypothesisDef(
        label="seasonal_demand_shift",
        supporting_signals=("revenue_drop_24h",),
        suppressing_signals=(),
        narrative_template="Revenue softening without correlated risk signals — likely seasonal cycle.",
        recommended_action="Compare against same week last year if data exists, otherwise re-check in 48h.",
    ),
    "system_distress": HypothesisDef(
        label="system_distress",
        supporting_signals=("anomaly_volume_24h",),
        suppressing_signals=(),
        narrative_template="System anomaly volume is unusually high — operational issues may be the proximate cause.",
        recommended_action="Triage open ops alerts; suspend non-critical pipelines.",
    ),
}


@dataclass
class CausalHypothesis:
    label: str
    confidence: float          # 0..1
    score: float               # raw before normalization
    prior: float
    evidence: list[str]
    suppressors: list[str]
    narrative: str
    recommended_action: str
    rank: int = 0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "score": round(self.score, 3),
            "prior": round(self.prior, 3),
            "evidence": self.evidence,
            "suppressors": self.suppressors,
            "narrative": self.narrative,
            "recommended_action": self.recommended_action,
            "rank": self.rank,
        }


def _format_narrative(template: str, signals_by_name: dict[str, dict]) -> str:
    """
    Substitute {<signal>_<field>} placeholders. Falls back to the raw
    template when a placeholder can't be resolved.
    """
    out = template
    for sig_name, sig in signals_by_name.items():
        for k, v in sig.items():
            placeholder = "{" + f"{sig_name}_{k}" + "}"
            if placeholder in out:
                out = out.replace(placeholder, str(v))
    return out


def explain(db: Session, shop_domain: str) -> dict:
    """
    Return a ranked list of causal hypotheses for the merchant's current
    state. Built from anomaly fusion + vertical priors + hypothesis catalog.
    """
    fusion = fuse(db, shop_domain)
    signals = fusion.get("atomic_signals") or []
    signals_by_name = {s["name"]: s for s in signals}

    if not signals:
        return {
            "shop_domain": shop_domain,
            "hypotheses": [],
            "narrative": "No active anomalies detected — system reads as healthy.",
            "fusion_alerts": fusion.get("alerts", []),
            "generated_at": _now().isoformat(),
        }

    vertical = get_vertical(db, shop_domain)
    profile = get_profile(vertical)
    vertical_priors = causal_hypotheses_for(vertical)

    # Convert priors to weights — top-of-list = highest weight
    prior_weights: dict[str, float] = {}
    if vertical_priors:
        n = len(vertical_priors)
        for i, label in enumerate(vertical_priors):
            prior_weights[label] = round(1.0 - (i / (n + 1)), 3)

    hypotheses: list[CausalHypothesis] = []
    for label, defn in _CATALOG.items():
        prior = prior_weights.get(label, defn.base_prior)
        evidence = [s for s in defn.supporting_signals if s in signals_by_name]
        suppressors = [s for s in defn.suppressing_signals if s in signals_by_name]
        if not evidence:
            continue
        # Score: sum of supporting signal severities, scaled by prior, minus suppressors
        evidence_strength = sum(
            float(signals_by_name[s].get("severity", 0)) for s in evidence
        )
        suppressor_strength = sum(
            float(signals_by_name[s].get("severity", 0)) for s in suppressors
        )
        score = prior * evidence_strength - 0.4 * suppressor_strength
        if score <= 0:
            continue
        hypotheses.append(CausalHypothesis(
            label=label,
            score=score,
            prior=prior,
            confidence=0.0,
            evidence=evidence,
            suppressors=suppressors,
            narrative=_format_narrative(defn.narrative_template, signals_by_name),
            recommended_action=defn.recommended_action,
        ))

    # Normalize confidence — softmax-lite over the scores
    if hypotheses:
        total = sum(h.score for h in hypotheses)
        for h in hypotheses:
            h.confidence = h.score / total if total > 0 else 0.0
        hypotheses.sort(key=lambda h: h.score, reverse=True)
        for i, h in enumerate(hypotheses):
            h.rank = i + 1

    top = hypotheses[0] if hypotheses else None
    headline = (
        f"Top hypothesis ({int(top.confidence * 100)}% confidence): {top.label}. {top.narrative}"
        if top else "Signals are present but no catalogued cause matched. Review raw signals."
    )

    return {
        "shop_domain": shop_domain,
        "vertical": vertical,
        "vertical_display": profile.display_name,
        "hypotheses": [h.to_dict() for h in hypotheses],
        "narrative": headline,
        "next_action": top.recommended_action if top else None,
        "fusion_alerts": fusion.get("alerts", []),
        "raw_signals": signals,
        "generated_at": _now().isoformat(),
    }
