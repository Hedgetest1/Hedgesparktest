"""
learning_isolation.py — Source-of-truth labeling and isolation for the learning pipeline.

Core principle: PRE-MERCHANT DATA MAY TRAIN THE MACHINE TO RUN.
               IT MUST NOT TRAIN THE MACHINE WHAT TO BELIEVE.

Two learning classes:

  A. TECHNICAL LEARNING — pipeline reliability, patch formatting, apply flow,
     validation quality, execution stability. ALL evidence sources may contribute.

  B. PRODUCT LEARNING — bugfix confidence in production, merchant-facing
     prioritization, reinforcement weights, strategic proposal quality,
     long-term autonomous reasoning. ONLY real_merchant evidence may contribute.

Evidence source classification:
  - pre_merchant:   No real merchants yet; system running on internal/dev data.
  - internal_test:  CI/test harness data. Never production.
  - sandbox:        Developer sandbox / staging environment.
  - real_merchant:  A real Shopify merchant with real store data.

Public interface:
    classify_evidence_source(db) -> str
    is_product_learning_eligible(source: str) -> bool
    filter_product_lessons(query, model_class) -> query
    get_evidence_source_for_candidate(db, candidate) -> str
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

log = logging.getLogger("learning_isolation")

# Valid evidence source labels
EVIDENCE_SOURCES = frozenset({"pre_merchant", "internal_test", "sandbox", "real_merchant"})

# Only this source may influence product learning
_PRODUCT_ELIGIBLE_SOURCES = frozenset({"real_merchant"})

# These sources may influence technical learning (all of them)
_TECHNICAL_ELIGIBLE_SOURCES = EVIDENCE_SOURCES

# Known test/dev shop domains that are never real merchants
_DEV_SHOP_DOMAINS = frozenset({
    "legacy.myshopify.com",
    "test.myshopify.com",
    "dev.myshopify.com",
    "sandbox.myshopify.com",
    "example.myshopify.com",
})

# Synthetic merchant shop_domain prefix — all simulation merchants use this.
# Any shop_domain starting with this prefix is permanently classified as sandbox.
SYNTHETIC_SHOP_PREFIX = "sim-"
SYNTHETIC_SHOP_SUFFIX = ".synthetic.hedgespark.test"

# Environment override: set HEDGESPARK_EVIDENCE_SOURCE=real_merchant when first
# real merchant is onboarded. Until then, defaults to pre_merchant.
_ENV_OVERRIDE_KEY = "HEDGESPARK_EVIDENCE_SOURCE"


def classify_evidence_source(db: Session | None = None) -> str:
    """
    Determine the current evidence source classification.

    Priority:
      1. ENV override (HEDGESPARK_EVIDENCE_SOURCE) — operator-controlled
      2. DB heuristic: if any non-blocklisted, active merchant with a real
         access_token exists → real_merchant
      3. Default: pre_merchant

    This function is called at evidence generation time (lesson creation,
    outcome measurement, reinforcement computation) to label artifacts.
    """
    # 1. Explicit operator override
    env_source = os.environ.get(_ENV_OVERRIDE_KEY, "").strip().lower()
    if env_source in EVIDENCE_SOURCES:
        return env_source

    # 2. DB heuristic — check for real (non-synthetic) merchants
    if db is not None:
        try:
            from app.models.merchant import Merchant
            from app.services.onboarding import _ONBOARDING_BLOCKLIST
            # Operator/dev tenant exclusion (founder direttiva 2026-05-06):
            # the dev tenant must not be classified as "real_merchant"
            # for the learning-isolation gate.
            from app.core.operator_blocklist import operator_dev_shops

            real_merchant = (
                db.query(Merchant.id)
                .filter(
                    Merchant.install_status == "active",
                    Merchant.access_token.isnot(None),
                    Merchant.is_synthetic == False,  # noqa: E712
                    Merchant.shop_domain.notin_(_ONBOARDING_BLOCKLIST | _DEV_SHOP_DOMAINS),
                    ~Merchant.shop_domain.in_(operator_dev_shops()),
                )
                .first()
            )
            if real_merchant is not None:
                return "real_merchant"
        except Exception as exc:
            log.warning("learning_isolation: DB heuristic failed (non-fatal): %s", exc)

    # 3. Default: pre-merchant
    return "pre_merchant"


def is_product_learning_eligible(evidence_source: str | None) -> bool:
    """
    Returns True only if the evidence source is eligible to influence
    product learning (confidence boosts, reinforcement weights, strategic
    memory, promotion to regression_warning, etc.).

    Pre-merchant, internal_test, and sandbox evidence is NEVER eligible.
    """
    return (evidence_source or "pre_merchant") in _PRODUCT_ELIGIBLE_SOURCES


def is_technical_learning_eligible(evidence_source: str | None) -> bool:
    """
    Returns True if the evidence source may contribute to technical learning
    (patch formatting, failure taxonomy, pipeline reliability).

    All sources are eligible for technical learning.
    """
    return True


def filter_product_lessons(query, model_class):
    """
    Apply a SQLAlchemy filter to restrict a query to only product-eligible
    lessons (evidence_source = 'real_merchant').

    Usage:
        query = db.query(SystemLesson).filter(...)
        query = filter_product_lessons(query, SystemLesson)
    """
    return query.filter(
        model_class.evidence_source == "real_merchant",
    )


def filter_product_outcomes(query, model_class):
    """
    Apply a SQLAlchemy filter to restrict outcome queries to only
    product-eligible evidence.
    """
    return query.filter(
        model_class.evidence_source == "real_merchant",
    )


def get_evidence_source_for_candidate(db: Session, candidate) -> str:
    """
    Determine the evidence source for a specific bugfix candidate.

    Checks:
      1. If candidate already has evidence_source set, use it.
      2. If candidate is linked to an alert with a shop_domain, check if
         that shop is a real merchant.
      3. Fall back to classify_evidence_source(db).
    """
    # Already labeled — but only skip lookup if it's a definitive label
    # (not the default "pre_merchant" which may just be the server default)
    existing = getattr(candidate, "evidence_source", None)
    if existing and existing in EVIDENCE_SOURCES and existing != "pre_merchant":
        return existing

    # Check if the alert source references a real shop
    if candidate.source_ref and candidate.source_type == "ops_alert":
        try:
            from sqlalchemy import text as sql_text
            row = db.execute(
                sql_text("SELECT shop_domain FROM ops_alerts WHERE id = :id"),
                {"id": int(candidate.source_ref.split("_", 1)[1])},
            ).fetchone()
            if row and row[0]:
                shop = row[0]
                # Synthetic shop domains are always sandbox
                if is_synthetic_shop(shop):
                    return "sandbox"
                if shop in _DEV_SHOP_DOMAINS:
                    return "internal_test"
                from app.services.onboarding import _ONBOARDING_BLOCKLIST
                if shop in _ONBOARDING_BLOCKLIST:
                    return "internal_test"
                # Real shop domain → check if active non-synthetic merchant
                from app.models.merchant import Merchant
                merchant = (
                    db.query(Merchant.id, Merchant.is_synthetic)
                    .filter(
                        Merchant.shop_domain == shop,
                        Merchant.install_status == "active",
                        Merchant.access_token.isnot(None),
                    )
                    .first()
                )
                if merchant is not None:
                    if merchant.is_synthetic:
                        return "sandbox"
                    return "real_merchant"
        except Exception as exc:
            log.warning("learning_isolation: get_evidence_source_for_candidate failed: %s", exc)

    # Fall back to global classification
    return classify_evidence_source(db)


def label_candidate(db: Session, candidate) -> None:
    """Set evidence_source on a candidate if not already set."""
    if not getattr(candidate, "evidence_source", None):
        candidate.evidence_source = get_evidence_source_for_candidate(db, candidate)


def label_lesson(db: Session, lesson, candidate=None) -> None:
    """
    Set evidence_source on a lesson. Inherits from the candidate that
    generated it, or falls back to global classification.
    """
    if candidate and getattr(candidate, "evidence_source", None):
        lesson.evidence_source = candidate.evidence_source
    else:
        lesson.evidence_source = classify_evidence_source(db)


def label_proposal(db: Session, proposal) -> None:
    """Set evidence_source on an evolution proposal."""
    if not getattr(proposal, "evidence_source", None):
        proposal.evidence_source = classify_evidence_source(db)


def label_fingerprint(db: Session, fingerprint, candidate=None) -> None:
    """Set evidence_source on a patch fingerprint, inheriting from candidate."""
    if candidate and getattr(candidate, "evidence_source", None):
        fingerprint.evidence_source = candidate.evidence_source
    else:
        fingerprint.evidence_source = classify_evidence_source(db)


def is_synthetic_shop(shop_domain: str) -> bool:
    """
    Check if a shop_domain belongs to a synthetic merchant.

    Uses naming convention (fast, no DB hit):
      - Starts with SYNTHETIC_SHOP_PREFIX ("sim-")
      - Ends with SYNTHETIC_SHOP_SUFFIX (".synthetic.hedgespark.test")

    For ambiguous cases, use is_synthetic_merchant(db, shop_domain) instead.
    """
    if not shop_domain:
        return False
    return (
        shop_domain.startswith(SYNTHETIC_SHOP_PREFIX)
        or shop_domain.endswith(SYNTHETIC_SHOP_SUFFIX)
    )


def is_synthetic_merchant(db: Session, shop_domain: str) -> bool:
    """
    Authoritative check: is this merchant synthetic?

    Checks both naming convention AND the DB flag.
    """
    if is_synthetic_shop(shop_domain):
        return True
    try:
        from app.models.merchant import Merchant
        merchant = (
            db.query(Merchant.is_synthetic)
            .filter(Merchant.shop_domain == shop_domain)
            .first()
        )
        if merchant is not None:
            return merchant.is_synthetic
    except Exception as exc:
        log.warning("learning_isolation: is_synthetic_merchant failed: %s", exc)
    return False
