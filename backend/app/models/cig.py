"""
cig.py — Commerce Intelligence Graph models.

CigCohort: Aggregated intelligence per merchant cohort. Written by the weekly
CIG computation worker. Contains cross-store nudge effectiveness, baselines,
signal patterns, and optimization playbooks.

CigMerchantMapping: Maps each merchant to their best-matching cohorts with
similarity scores. Used for bootstrap intelligence injection and CIG-informed
decision making.

Anonymization invariant: no raw merchant data, product names, URLs, or
shop_domains appear in CigCohort. Only aggregated, weighted statistics.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class CigCohort(Base):
    """Aggregated intelligence for a group of similar stores."""
    __tablename__ = "cig_cohorts"

    id = Column(Integer, primary_key=True)
    cohort_key = Column(String, nullable=False, unique=True, index=True)

    # ── Cohort definition ──
    # Computed from SIP fingerprints via similarity clustering
    aov_band = Column(String(16), nullable=False)         # "low" (<$25), "mid" ($25-75), "high" (>$75)
    traffic_band = Column(String(16), nullable=False)      # "low" (<50/day), "mid" (50-500), "high" (>500)
    mobile_band = Column(String(16), nullable=False)       # "low" (<40%), "mid" (40-70%), "high" (>70%)

    # ── Aggregated baselines (weighted averages from member SIPs) ──
    avg_cart_rate = Column(Float, nullable=True)
    avg_scroll_depth = Column(Float, nullable=True)
    avg_dwell_time = Column(Float, nullable=True)
    avg_return_rate = Column(Float, nullable=True)
    p25_cart_rate = Column(Float, nullable=True)           # 25th percentile
    p75_cart_rate = Column(Float, nullable=True)           # 75th percentile

    # ── Cross-store nudge effectiveness (JSONB) ──
    # {"social_proof": {"avg_lift": 0.28, "n": 1240, "confidence": "high"}, ...}
    nudge_effectiveness = Column(JSONB, nullable=True)

    # ── Signal frequency distribution (JSONB) ──
    # {"HIGH_TRAFFIC_NO_CART": 0.38, "DEAD_TRAFFIC": 0.22, ...} (proportion of stores)
    signal_distribution = Column(JSONB, nullable=True)

    # ── Price sensitivity (JSONB) ──
    # [{"range": "0-25", "avg_cart_rate": 0.041}, ...]
    price_sensitivity = Column(JSONB, nullable=True)

    # ── Traffic source quality (JSONB) ──
    # {"instagram": 0.72, "google": 0.58, ...}
    traffic_quality = Column(JSONB, nullable=True)

    # ── Optimization playbooks (JSONB) ──
    # [{"signal": "HIGH_TRAFFIC_NO_CART", "best_nudge": "social_proof",
    #   "avg_lift": 0.28, "n": 340}, ...]
    playbooks = Column(JSONB, nullable=True)

    # ── Meta ──
    merchant_count = Column(Integer, nullable=False, default=0)
    total_data_points = Column(Integer, nullable=False, default=0)
    confidence_level = Column(String(8), nullable=False, default="low")

    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CigMerchantMapping(Base):
    """Maps each merchant to their best-matching CIG cohorts."""
    __tablename__ = "cig_merchant_mappings"
    __table_args__ = (
        UniqueConstraint("shop_domain", name="uq_cig_mapping_shop"),
    )

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)

    # Top 3 cohort matches with similarity scores
    primary_cohort_key = Column(String, nullable=True)
    primary_similarity = Column(Float, nullable=True)      # 0-1

    secondary_cohort_key = Column(String, nullable=True)
    secondary_similarity = Column(Float, nullable=True)

    tertiary_cohort_key = Column(String, nullable=True)
    tertiary_similarity = Column(Float, nullable=True)

    # Store fingerprint used for matching (snapshot for audit)
    fingerprint = Column(JSONB, nullable=True)

    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
