"""
store_intelligence_profile.py — Per-merchant learned intelligence.

One row per shop_domain.  Written by the SIP computation step inside the
aggregation worker (daily, or after threshold data-point milestones).

Contains:
  - Behavioral baselines (store-specific rolling windows)
  - Learned signal thresholds (adaptive, not global constants)
  - Nudge effectiveness scores (which nudge types work for THIS store)
  - Traffic source quality (conversion propensity per source)
  - Price sensitivity bands (cart rate by price bucket)
  - Temporal patterns (peak traffic and conversion hours)
  - Confidence level (how much data backs the profile)

Read by:
  - opportunity_engine.py  (store-specific thresholds for signal detection)
  - nudge_composer.py      (best nudge type selection per store)
  - digest_formatter.py    (per-store insights in weekly digest)
  - future autonomous loop (risk classification)
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class StoreIntelligenceProfile(Base):
    __tablename__ = "store_intelligence_profiles"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, unique=True, index=True)
    profile_version = Column(Integer, nullable=False, default=1)

    # ── Behavioral baselines (store-wide rolling averages) ──
    baseline_cart_rate = Column(Float, nullable=True)         # 7d cart conversion rate
    baseline_scroll_depth = Column(Float, nullable=True)      # avg scroll % across products
    baseline_dwell_time = Column(Float, nullable=True)        # avg dwell seconds
    baseline_return_rate = Column(Float, nullable=True)       # % visitors who return within 7d
    baseline_views_per_product = Column(Float, nullable=True) # avg 7d views per active product
    baseline_mobile_pct = Column(Float, nullable=True)        # % of views from mobile

    # ── Learned signal thresholds (adaptive per-store) ──
    # These override global constants when SIP confidence is medium or high.
    # Stored as JSONB: {"views_floor": 15, "dwell_floor": 4.2, "return_floor": 4, ...}
    learned_thresholds = Column(JSONB, nullable=True)

    # ── Traffic source quality ──
    # {"instagram": 0.72, "google": 0.58, "direct": 0.41, ...}
    # Score = cart_rate_from_source / overall_cart_rate (1.0 = average)
    traffic_source_quality = Column(JSONB, nullable=True)

    # ── Price sensitivity bands ──
    # [{"range": "0-25", "cart_rate": 0.041, "products": 5}, ...]
    price_sensitivity_bands = Column(JSONB, nullable=True)

    # ── Nudge effectiveness (learned from proof outcomes) ──
    # {"social_proof": 0.82, "urgency": 0.45, "engagement_depth": 0.61, ...}
    # Score: weighted average of measured lift (higher = more effective for this store)
    nudge_type_scores = Column(JSONB, nullable=True)

    # ── Best nudge by signal type (learned mapping) ──
    # {"HIGH_TRAFFIC_NO_CART": "social_proof", "HIGH_RETURN_LOW_CONVERSION": "urgency", ...}
    best_nudge_by_signal = Column(JSONB, nullable=True)

    # ── Temporal patterns ──
    # [{"hour": 14, "day": "mon", "views": 42, "carts": 3}, ...]
    peak_traffic_hours = Column(JSONB, nullable=True)

    # ── Signal history (what signals fire most for this store) ──
    # {"HIGH_TRAFFIC_NO_CART": 12, "DEAD_TRAFFIC": 3, ...}
    signal_frequency_30d = Column(JSONB, nullable=True)

    # ── Confidence & freshness ──
    data_points_total = Column(Integer, nullable=False, default=0)
    confidence_level = Column(String(8), nullable=False, default="low")  # low / medium / high

    # ── Trust (scalar + multi-dimensional profile) ──
    trust_score = Column(Float, nullable=False, default=0.5)
    # {"execution_reliability": 0.5, "measurement_integrity": 0.5,
    #  "outcome_quality": 0.5, "stability": 0.5, "overall": 0.5}
    trust_profile = Column(JSONB, nullable=True)
    autonomous_paused = Column(Boolean, nullable=False, default=False)
    pause_reason = Column(String(256), nullable=True)

    # ── Autonomy level (0–5, earned through outcomes) ──
    # 0=observe, 1=suggest, 2=assisted, 3=semi-auto, 4=full-auto, 5=aggressive
    autonomy_level = Column(Integer, nullable=False, default=0)

    # ── Measurement health ──
    measurement_health = Column(String(16), nullable=False, default="healthy")  # healthy/degraded/broken
    measurement_health_detail = Column(String(512), nullable=True)

    # ── Nudge type cooldowns ──
    nudge_type_cooldowns = Column(JSONB, nullable=True)

    # ── Nudge interaction matrix ──
    # {"social_proof+urgency": {"lift": 0.12, "n": 340}, ...}
    nudge_interaction_matrix = Column(JSONB, nullable=True)

    # ── Autonomous action history counters ──
    total_autonomous_actions = Column(Integer, nullable=False, default=0)
    total_positive_outcomes = Column(Integer, nullable=False, default=0)
    total_rollbacks = Column(Integer, nullable=False, default=0)
    contradiction_count = Column(Integer, nullable=False, default=0)
    last_outcome_at = Column(DateTime, nullable=True)

    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SipSnapshot(Base):
    """Weekly snapshot for drift detection and trend analysis."""
    __tablename__ = "sip_snapshots"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)
    snapshot_week = Column(DateTime, nullable=False)  # ISO week start (Monday)
    profile_data = Column(JSONB, nullable=False)      # full SIP as JSON
    baseline_cart_rate = Column(Float, nullable=True)  # denormalized for fast queries
    data_points = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # One snapshot per shop per week
        {"schema": None},
    )
