"""
vertical_prompt_pack.py — Phase Ω moat #1.b.

Vertical-specific tuning for narratives, nudge copy, baselines, and
causal hypotheses. Used by:

  * narrative / digest formatters (vertical-aware copy)
  * nudge_composer (vertical-aware variants)
  * benchmarks_vertical (vertical-aware baselines)
  * causal_explainer (vertical-aware hypotheses ranking)

Pure data — no I/O, no LLM. Adding a vertical = add a row.

Why this matters
----------------
Generic SaaS sends the same nudge copy to every shop. We tailor by
vertical. A beauty brand getting "scarcity: only 2 left" reads natural;
an electronics shop reading the same text feels generic. The cumulative
effect of 12 small tunings × every customer touchpoint = a moat
competitors can only match by hand-curating thousands of templates.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerticalProfile:
    vertical: str
    display_name: str
    # CVR baselines (industry medians, used as fallbacks when peer data is thin)
    cvr_baseline_pct: float
    aov_baseline_eur: float
    # Nudge copy fragments — vertical-aware substitutions
    scarcity_phrasing: str       # how scarcity is expressed
    social_proof_phrasing: str   # how social proof is expressed
    return_visitor_phrasing: str
    high_intent_phrasing: str
    # Causal hypotheses — ordered list of likely root causes for revenue drops,
    # ranked by typical incidence in the vertical
    causal_hypotheses: tuple[str, ...]
    # Seasonal flags — months where this vertical typically peaks
    peak_months: tuple[int, ...] = field(default=())
    # Narrative tone hint
    tone: str = "neutral"


_PROFILES: dict[str, VerticalProfile] = {
    "beauty": VerticalProfile(
        vertical="beauty",
        display_name="Beauty & Cosmetics",
        cvr_baseline_pct=3.2,
        aov_baseline_eur=42.0,
        scarcity_phrasing="Only {n} left in stock — restock takes 3 weeks.",
        social_proof_phrasing="{n} customers loved this in the last 7 days.",
        return_visitor_phrasing="Welcome back — your last visit you looked at this.",
        high_intent_phrasing="You spent {sec}s exploring this — here is a 10% reason to choose now.",
        causal_hypotheses=(
            "ingredient_trend_shift", "influencer_volatility", "seasonal_skincare_cycle",
            "shipping_delay_perception", "ad_creative_fatigue", "competitor_launch",
        ),
        peak_months=(11, 12, 5),
        tone="warm",
    ),
    "fashion": VerticalProfile(
        vertical="fashion",
        display_name="Fashion & Apparel",
        cvr_baseline_pct=2.4,
        aov_baseline_eur=68.0,
        scarcity_phrasing="Only {n} left in your size — popular this week.",
        social_proof_phrasing="{n} people bought this in the last 24 hours.",
        return_visitor_phrasing="Still thinking about it? Your size is still available.",
        high_intent_phrasing="Free returns — try it risk-free.",
        causal_hypotheses=(
            "size_curve_imbalance", "seasonal_collection_transition", "return_rate_spike",
            "ad_creative_fatigue", "competitor_promo", "weather_shift",
        ),
        peak_months=(11, 12, 3, 9),
        tone="confident",
    ),
    "electronics": VerticalProfile(
        vertical="electronics",
        display_name="Electronics & Gadgets",
        cvr_baseline_pct=1.8,
        aov_baseline_eur=145.0,
        scarcity_phrasing="Only {n} units left — next shipment in 4 weeks.",
        social_proof_phrasing="Verified buyers rate this 4.7/5 ({n} reviews).",
        return_visitor_phrasing="Welcome back — the price has held since your last visit.",
        high_intent_phrasing="2-year warranty included. Free shipping over €50.",
        causal_hypotheses=(
            "competitor_price_drop", "new_model_announcement", "supply_constraint",
            "shipping_delay_perception", "warranty_concern", "ad_cpm_spike",
        ),
        peak_months=(11, 12, 1),
        tone="precise",
    ),
    "food_beverage": VerticalProfile(
        vertical="food_beverage",
        display_name="Food & Beverage",
        cvr_baseline_pct=4.1,
        aov_baseline_eur=38.0,
        scarcity_phrasing="Limited harvest — {n} left from this batch.",
        social_proof_phrasing="{n} reorders in the last week — a customer favourite.",
        return_visitor_phrasing="Welcome back — your last order was {days} days ago.",
        high_intent_phrasing="Free shipping on orders over €40.",
        causal_hypotheses=(
            "seasonal_taste_shift", "shipping_perishability", "subscription_churn",
            "supply_origin_change", "competitor_promo",
        ),
        peak_months=(11, 12),
        tone="warm",
    ),
    "home_garden": VerticalProfile(
        vertical="home_garden",
        display_name="Home & Garden",
        cvr_baseline_pct=2.1,
        aov_baseline_eur=85.0,
        scarcity_phrasing="Only {n} in stock — handmade, restock in 2 weeks.",
        social_proof_phrasing="{n} homes added this in the last month.",
        return_visitor_phrasing="Still picturing it in your space? Free returns within 30 days.",
        high_intent_phrasing="Free assembly guide and full year warranty.",
        causal_hypotheses=(
            "seasonal_decor_cycle", "shipping_oversize_friction", "return_rate_spike",
            "ad_cpm_spike", "competitor_promo",
        ),
        peak_months=(3, 4, 10, 11),
        tone="warm",
    ),
    "jewelry": VerticalProfile(
        vertical="jewelry",
        display_name="Jewelry & Accessories",
        cvr_baseline_pct=1.4,
        aov_baseline_eur=180.0,
        scarcity_phrasing="One-of-a-kind — only {n} piece available.",
        social_proof_phrasing="Featured by {n} customers in the last month.",
        return_visitor_phrasing="Welcome back — this piece is still yours to claim.",
        high_intent_phrasing="Lifetime guarantee. Free engraving on all orders.",
        causal_hypotheses=(
            "gold_price_spike", "gifting_season_window", "luxury_tax_change",
            "trust_signal_gap", "competitor_promo",
        ),
        peak_months=(2, 5, 11, 12),
        tone="elegant",
    ),
    "supplements_wellness": VerticalProfile(
        vertical="supplements_wellness",
        display_name="Supplements & Wellness",
        cvr_baseline_pct=3.5,
        aov_baseline_eur=55.0,
        scarcity_phrasing="Batch sells out monthly — {n} left this batch.",
        social_proof_phrasing="{n} customers reordered last month.",
        return_visitor_phrasing="Welcome back — subscribe and save 15%.",
        high_intent_phrasing="Lab-tested. Money-back guarantee within 60 days.",
        causal_hypotheses=(
            "compliance_label_change", "subscription_churn", "ingredient_supply_constraint",
            "regulatory_alert", "competitor_promo",
        ),
        peak_months=(1, 9),
        tone="trustworthy",
    ),
    "pets": VerticalProfile(
        vertical="pets",
        display_name="Pet Care",
        cvr_baseline_pct=4.5,
        aov_baseline_eur=44.0,
        scarcity_phrasing="Top brand — only {n} bags left in stock.",
        social_proof_phrasing="{n} happy pets bought this in the last week.",
        return_visitor_phrasing="Welcome back — running low? Subscribe and save.",
        high_intent_phrasing="Free shipping on orders over €30.",
        causal_hypotheses=(
            "subscription_churn", "ingredient_recall", "shipping_perishability",
            "competitor_promo",
        ),
        peak_months=(),
        tone="warm",
    ),
    "sports_outdoor": VerticalProfile(
        vertical="sports_outdoor",
        display_name="Sports & Outdoor",
        cvr_baseline_pct=2.0,
        aov_baseline_eur=92.0,
        scarcity_phrasing="Limited gear — only {n} left in popular sizes.",
        social_proof_phrasing="Trained by {n} athletes in the last month.",
        return_visitor_phrasing="Welcome back — the season is just starting.",
        high_intent_phrasing="2-year warranty. Free shipping on orders over €60.",
        causal_hypotheses=(
            "weather_shift", "seasonal_sport_cycle", "size_curve_imbalance",
            "competitor_promo", "ad_cpm_spike",
        ),
        peak_months=(3, 4, 5, 9, 10),
        tone="energetic",
    ),
    "kids_baby": VerticalProfile(
        vertical="kids_baby",
        display_name="Kids & Baby",
        cvr_baseline_pct=3.0,
        aov_baseline_eur=58.0,
        scarcity_phrasing="Top seller — only {n} left for your size.",
        social_proof_phrasing="{n} parents chose this last week.",
        return_visitor_phrasing="Still your size? Restocks happen monthly.",
        high_intent_phrasing="Hypoallergenic. Free returns within 60 days.",
        causal_hypotheses=(
            "size_curve_imbalance", "safety_recall", "seasonal_growth_cycle",
            "competitor_promo",
        ),
        peak_months=(11, 12),
        tone="warm",
    ),
    "books_media": VerticalProfile(
        vertical="books_media",
        display_name="Books & Media",
        cvr_baseline_pct=2.8,
        aov_baseline_eur=28.0,
        scarcity_phrasing="Limited print run — only {n} copies left.",
        social_proof_phrasing="{n} readers bought this in the last week.",
        return_visitor_phrasing="Welcome back — this one is still available.",
        high_intent_phrasing="Free shipping over €25.",
        causal_hypotheses=(
            "release_cycle", "review_volatility", "ad_creative_fatigue",
            "competitor_promo",
        ),
        peak_months=(11, 12),
        tone="thoughtful",
    ),
    "other": VerticalProfile(
        vertical="other",
        display_name="General",
        cvr_baseline_pct=2.3,
        aov_baseline_eur=60.0,
        scarcity_phrasing="Only {n} left in stock.",
        social_proof_phrasing="{n} customers bought this recently.",
        return_visitor_phrasing="Welcome back — this is still available.",
        high_intent_phrasing="Free shipping on qualifying orders.",
        causal_hypotheses=(
            "ad_cpm_spike", "competitor_promo", "shipping_delay_perception",
            "seasonal_demand_shift",
        ),
        tone="neutral",
    ),
}


def get_profile(vertical: str) -> VerticalProfile:
    """Return the profile for a vertical, falling back to 'other'."""
    return _PROFILES.get(vertical) or _PROFILES["other"]


def all_profiles() -> dict[str, VerticalProfile]:
    """Return the full profile registry (read-only use)."""
    return dict(_PROFILES)


def baseline_cvr_pct(vertical: str) -> float:
    return get_profile(vertical).cvr_baseline_pct


def baseline_aov_eur(vertical: str) -> float:
    return get_profile(vertical).aov_baseline_eur


def causal_hypotheses_for(vertical: str) -> tuple[str, ...]:
    return get_profile(vertical).causal_hypotheses


def is_peak_month(vertical: str, month: int) -> bool:
    return month in get_profile(vertical).peak_months
