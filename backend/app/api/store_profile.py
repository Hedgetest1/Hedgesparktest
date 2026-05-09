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
        "measurement_health": row[8] or "healthy",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


def _read_latest_vertical_prior(db: Session, shop: str) -> dict | None:
    """Pull the most-recent sip_snapshot for this shop and extract its
    vertical_prior block. Snapshots are weekly, so the value can be up
    to 7 days stale — acceptable for a moat-disclosure surface (the
    underlying baselines themselves only change with vertical_prompt_pack
    edits, which are deploys, not data drifts). Returns None when no
    snapshot exists or the block is missing (e.g. snapshots predating
    Sprint 2 #4 ship)."""
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
        # Defensive shape coerce — pre-existing snapshots may have
        # partial shapes if the schema evolves.
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
    except Exception as exc:
        log.warning("store_profile: vertical_prior read failed for %s: %s", shop, exc)
        return None


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
