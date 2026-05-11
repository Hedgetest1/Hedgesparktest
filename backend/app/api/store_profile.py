"""
store_profile.py — GET /pro/store-profile API endpoint.

Sprint 4 #7 of the per-shop deterministic learning engine roadmap
(2026-05-09). Exposes the merchant's Store Intelligence Profile state:
model version, data points, confidence, trust profile, autonomy level,
top learned thresholds, top nudge effectiveness scores, and the
vertical-tuned prior block (Sprint 2 #4) — surfacing the deterministic
moat to merchants on demand.

Backend-only. The dashboard rendering of this surface is founder-domain
(taste / visual / copy decision); this endpoint just provides the data.

Pro-gated via require_pro_session. Cached 60s in Redis to amortize
cross-merchant fan-out (e.g. weekly digest building "intelligence
status" cards reads this once per merchant). Stampede-safe via SETNX
lock pattern shared with proof_engine / knowledge_graph.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger("store_profile_api")

router = APIRouter(tags=["store_profile"])

_CACHE_TTL_SECONDS = 60
_CACHE_KEY_PREFIX = "hs:storeprofile:v1"
_LOCK_KEY_PREFIX = "hs:storeprofile:lock:v1"
_LOCK_TTL_SECONDS = 30
_LOCK_WAIT_BUDGET_SEC = 2.0


class TrustProfile(BaseModel):
    execution_reliability: float
    measurement_integrity: float
    outcome_quality: float
    stability: float
    overall: float


class VerticalPrior(BaseModel):
    vertical: str
    vertical_display: str
    cvr_baseline_pct: float
    aov_baseline_eur: float
    n_prior_strength: int
    n_observed: int
    blended_cart_rate: float | None = None
    applied: bool


class CrossShopPriorItem(BaseModel):
    """Sprint 3 #3 — one (action_kind, metric_kind) aggregate.

    Represents what the shop's vertical has measured collectively for
    this action+metric pair. k>=3 distinct shops by construction
    (enforced at SQL CHECK + aggregator code + audit).
    """
    action_kind: str
    metric_kind: str
    lift_pct_avg: float
    lift_pct_std: float | None = None
    n_shops: int
    n_decisions: int
    p_value: float | None = None
    confidence: str  # high / medium / low
    last_aggregated_at: str | None = None


class StoreProfileResponse(BaseModel):
    shop_domain: str
    profile_version: int
    data_points: int = Field(description="30-day events count powering the SIP")
    confidence_level: str = Field(description="low | medium | high")
    trust_score: float
    trust_profile: TrustProfile | None = None
    autonomy_level: int = Field(description="0=observe, 1=suggest, 2=assisted, 3=semi-auto, 4=full-auto, 5=aggressive")
    learned_thresholds: dict[str, Any] | None = None
    top_nudge_scores: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Top 3 nudge types by effectiveness for THIS shop",
    )
    vertical_prior: VerticalPrior | None = None
    cross_shop_priors: list[CrossShopPriorItem] = Field(
        default_factory=list,
        description="Sprint 3 #3 — aggregated lift measurements from other shops in the same vertical (k>=3, GDPR-aggregate-only).",
    )
    measurement_health: str = "healthy"
    generated_at: str
    note: str | None = None


def _cache_key(shop: str) -> str:
    return f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"


def _lock_key(shop: str) -> str:
    return f"{_LOCK_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"


def _read_cached(shop: str) -> dict | None:
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("store_profile.cache_read_redis_down")
            return None
        cached = rc.get(_cache_key(shop))
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log.warning("store_profile: cache read failed for %s: %s", shop, exc)
    return None


def _write_cached(shop: str, payload: dict) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(_cache_key(shop), _CACHE_TTL_SECONDS, json.dumps(payload, default=str))
    except Exception as exc:
        log.warning("store_profile: cache write failed for %s: %s", shop, exc)


def _acquire_lock(shop: str) -> bool:
    """SETNX-based stampede lock. Returns True if caller holds the lock."""
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("store_profile.lock_acquire_redis_down")
            return True  # no Redis → no stampede protection, proceed
        return bool(rc.set(_lock_key(shop), "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception as exc:
        log.warning("store_profile: lock acquire failed for %s: %s", shop, exc)
        return True  # degrade-open: better to compute twice than block


def _wait_for_cache(shop: str) -> dict | None:
    """Stampede waiter: another caller is computing — poll briefly."""
    deadline = time.monotonic() + _LOCK_WAIT_BUDGET_SEC
    while time.monotonic() < deadline:
        cached = _read_cached(shop)
        if cached is not None:
            return cached
        time.sleep(0.1)
    return None


def _compute_store_profile(db: Session, shop: str) -> dict:
    """Read SIP row + latest sip_snapshot.profile_data → assemble response."""
    row = db.execute(
        text("""
            SELECT
                profile_version, data_points_total, confidence_level,
                trust_score, trust_profile, autonomy_level,
                learned_thresholds, nudge_type_scores, measurement_health
            FROM store_intelligence_profiles
            WHERE shop_domain = :shop
        """),
        {"shop": shop},
    ).fetchone()

    if not row:
        # No SIP yet — empty profile, honest "warming" state
        return {
            "shop_domain": shop,
            "profile_version": 0,
            "data_points": 0,
            "confidence_level": "none",
            "trust_score": 0.5,
            "trust_profile": None,
            "autonomy_level": 0,
            "learned_thresholds": None,
            "top_nudge_scores": [],
            "vertical_prior": None,
            "cross_shop_priors": [],
            "measurement_health": "healthy",
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "note": "Store intelligence profile is warming — first cycle requires ~10 events.",
        }

    # Top-3 nudge scores by effectiveness (descending)
    nudge_scores = row[7] or {}
    if isinstance(nudge_scores, str):
        try:
            nudge_scores = json.loads(nudge_scores)
        except Exception:
            nudge_scores = {}
    top_nudges = sorted(
        ((k, float(v)) for k, v in (nudge_scores or {}).items()),
        key=lambda kv: kv[1],
        reverse=True,
    )[:3]
    top_nudge_scores = [{"nudge_type": k, "effectiveness": round(v, 4)} for k, v in top_nudges]

    # vertical_prior is stored only inside sip_snapshots.profile_data JSONB
    # (no column on store_intelligence_profiles by design — Sprint 2 #4
    # chose JSON-snapshot persistence to avoid TIER_2 schema change).
    vertical_prior = _read_latest_vertical_prior(db, shop)

    # Sprint 3 #3 — cross-shop priors derived from cross_shop_patterns
    # filtered by THIS shop's vertical. Read live (not snapshot-cached)
    # because the aggregate updates every 6h and is cheap to read
    # (single SELECT on (vertical, *) indexed table).
    cross_shop_priors = _read_cross_shop_priors_for_shop(
        db, shop, vertical_prior,
    )

    return {
        "shop_domain": shop,
        "profile_version": int(row[0] or 1),
        "data_points": int(row[1] or 0),
        "confidence_level": row[2] or "low",
        "trust_score": round(float(row[3] or 0.5), 4),
        "trust_profile": row[4],  # JSONB → dict already
        "autonomy_level": int(row[5] or 0),
        "learned_thresholds": row[6],  # JSONB → dict already
        "top_nudge_scores": top_nudge_scores,
        "vertical_prior": vertical_prior,
        "cross_shop_priors": cross_shop_priors,
        "measurement_health": row[8] or "healthy",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


def _read_cross_shop_priors_for_shop(
    db: Session, shop: str, vertical_prior: dict | None,
) -> list[dict]:
    """Read cross_shop_patterns aggregates for this shop's vertical.

    vertical_prior already carries the resolved vertical for this shop
    (Sprint 2 #4 wired the classifier). When it's missing (cold-start
    shop) we have no vertical anchor → return empty list.
    """
    if not vertical_prior or not vertical_prior.get("vertical"):
        return []
    vertical = vertical_prior["vertical"]
    try:
        rows = db.execute(text("""
            SELECT action_kind, metric_kind, lift_pct_avg, lift_pct_std,
                   n_shops, n_decisions, p_value, confidence,
                   last_aggregated_at
            FROM cross_shop_patterns
            WHERE vertical = :v
            ORDER BY
              CASE confidence
                WHEN 'high' THEN 0
                WHEN 'medium' THEN 1
                ELSE 2
              END,
              n_shops DESC
        """), {"v": vertical}).fetchall()
    except Exception as exc:
        log.warning(
            "store_profile: cross_shop_priors read failed for %s: %s",
            shop, exc,
        )
        return []
    return [
        {
            "action_kind": r.action_kind,
            "metric_kind": r.metric_kind,
            "lift_pct_avg": round(float(r.lift_pct_avg), 4),
            "lift_pct_std": round(float(r.lift_pct_std), 4) if r.lift_pct_std is not None else None,
            "n_shops": int(r.n_shops),
            "n_decisions": int(r.n_decisions),
            "p_value": round(float(r.p_value), 4) if r.p_value is not None else None,
            "confidence": r.confidence,
            "last_aggregated_at": r.last_aggregated_at.isoformat() if r.last_aggregated_at else None,
        }
        for r in rows
    ]


def _read_latest_vertical_prior(db: Session, shop: str) -> dict | None:
    """Pull the most-recent sip_snapshot for this shop and extract its
    vertical_prior block. Snapshots are weekly, so the value can be up
    to 7 days stale — acceptable for a moat-disclosure surface (the
    underlying baselines themselves only change with vertical_prompt_pack
    edits, which are deploys, not data drifts).

    Fallback: when no snapshot block exists yet (pre-Sprint-2 snapshots
    predate the vertical_prior field), compute it on-the-fly from the
    same 3 deterministic helpers `compute_sip` uses (vertical_classifier
    + vertical_prompt_pack + vertical_blend). Cheap (~1ms after Redis
    cache hit on the classifier) and keeps the surface honest from
    day-1 of Sprint 4 ship instead of waiting up to 7 days for the
    next snapshot rotation.
    """
    block = _read_vertical_prior_from_snapshot(db, shop)
    if block is not None:
        return block
    # Fallback: on-the-fly compute (no full compute_sip, just the prior).
    return _compute_vertical_prior_on_the_fly(db, shop)


def _read_vertical_prior_from_snapshot(db: Session, shop: str) -> dict | None:
    """Read vertical_prior from latest sip_snapshots.profile_data JSONB."""
    try:
        snap = db.execute(
            text("""
                SELECT profile_data
                FROM sip_snapshots
                WHERE shop_domain = :shop
                ORDER BY snapshot_week DESC
                LIMIT 1
            """),
            {"shop": shop},
        ).fetchone()
        if not snap or not snap[0]:
            return None
        data = snap[0] if isinstance(snap[0], dict) else json.loads(snap[0])
        block = data.get("vertical_prior")
        if not isinstance(block, dict):
            return None
        return _coerce_vertical_prior_block(block)
    except Exception as exc:
        log.warning("store_profile: vertical_prior snapshot read failed for %s: %s", shop, exc)
        return None


def _compute_vertical_prior_on_the_fly(db: Session, shop: str) -> dict | None:
    """Compute the vertical_prior block live, using the same 3 helpers
    sip_engine.compute_sip uses. Used as a fallback when the latest
    sip_snapshot predates Sprint 2 #4. Reads observed cart_rate from
    store_intelligence_profiles (already computed by the worker)."""
    try:
        from app.services.sip_engine import _compute_vertical_prior
        from app.services.vertical_classifier import get_vertical
    except Exception:
        return None
    try:
        sip_row = db.execute(
            text("""
                SELECT baseline_cart_rate, data_points_total, confidence_level
                FROM store_intelligence_profiles
                WHERE shop_domain = :shop
            """),
            {"shop": shop},
        ).fetchone()
        if not sip_row:
            return None
        observed_cart = float(sip_row[0]) if sip_row[0] is not None else None
        data_points = int(sip_row[1] or 0)
        confidence = sip_row[2] or "low"
        vertical = get_vertical(db, shop)
        block = _compute_vertical_prior(
            vertical=vertical,
            observed_cart_rate=observed_cart,
            data_points=data_points,
            confidence=confidence,
        )
        if block is None:
            return None
        return _coerce_vertical_prior_block(block)
    except Exception as exc:
        log.warning("store_profile: vertical_prior on-the-fly compute failed for %s: %s", shop, exc)
        return None


def _coerce_vertical_prior_block(block: dict) -> dict:
    """Defensive shape coerce — snapshots may have partial shapes if the
    block schema evolves; the on-the-fly path returns the canonical shape."""
    return {
        "vertical": str(block.get("vertical") or "other"),
        "vertical_display": str(block.get("vertical_display") or "General"),
        "cvr_baseline_pct": float(block.get("cvr_baseline_pct") or 0.0),
        "aov_baseline_eur": float(block.get("aov_baseline_eur") or 0.0),
        "n_prior_strength": int(block.get("n_prior_strength") or 200),
        "n_observed": int(block.get("n_observed") or 0),
        "blended_cart_rate": (
            float(block["blended_cart_rate"])
            if block.get("blended_cart_rate") is not None
            else None
        ),
        "applied": bool(block.get("applied", False)),
    }


@router.get("/pro/store-profile", response_model=StoreProfileResponse)
def get_store_profile(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Per-shop intelligence state — exposes the deterministic learning
    engine's view of this merchant: data volume, confidence, trust,
    autonomy ladder position, top learned thresholds, nudge effectiveness
    ranking, and the vertical-tuned prior block (Sprint 2 #4).
    """
    cached = _read_cached(shop)
    if cached is not None:
        return cached

    if not _acquire_lock(shop):
        cached = _wait_for_cache(shop)
        if cached is not None:
            return cached
        # Lock held by another caller, wait timed out — compute anyway
        # (degrade-open under contention).

    payload = _compute_store_profile(db, shop)
    _write_cached(shop, payload)
    return payload
