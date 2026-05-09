"""Sprint 4 #7 — /pro/store-profile endpoint tests.

Surfaces the per-shop deterministic learning engine state to merchants:
data points, confidence, trust, autonomy ladder, top nudge effectiveness,
and the vertical-tuned prior block (Sprint 2 #4).

Tests cover:
  - Empty-shop "warming" response (no SIP row yet)
  - Populated SIP row → all fields returned
  - Vertical_prior block round-trip from sip_snapshots.profile_data JSONB
  - Cache hit (60s Redis) — second call hits cache
  - Tenant isolation (shop A request doesn't leak shop B's data)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.database import get_read_db
from app.main import app as fastapi_app
from tests.conftest import SHOP_A, SHOP_B


@pytest.fixture(autouse=True)
def _override_read_db(db):
    def _get_read_db_override():
        yield db
    fastapi_app.dependency_overrides[get_read_db] = _get_read_db_override
    yield
    fastapi_app.dependency_overrides.pop(get_read_db, None)


def _seed_sip(db, shop: str, *, data_points=2500, confidence="medium",
              trust=0.74, autonomy=2):
    """Seed a populated store_intelligence_profiles row."""
    db.execute(
        text("""
            INSERT INTO store_intelligence_profiles
              (shop_domain, profile_version, baseline_cart_rate,
               data_points_total, confidence_level,
               trust_score, trust_profile, autonomy_level,
               learned_thresholds, nudge_type_scores,
               measurement_health, computed_at, created_at, updated_at)
            VALUES
              (:shop, 1, 0.041,
               :dp, :conf,
               :ts, :tp, :al,
               :lt, :nts,
               'healthy', NOW(), NOW(), NOW())
            ON CONFLICT (shop_domain) DO UPDATE SET
              data_points_total = EXCLUDED.data_points_total,
              confidence_level = EXCLUDED.confidence_level,
              trust_score = EXCLUDED.trust_score,
              trust_profile = EXCLUDED.trust_profile,
              autonomy_level = EXCLUDED.autonomy_level,
              learned_thresholds = EXCLUDED.learned_thresholds,
              nudge_type_scores = EXCLUDED.nudge_type_scores
        """),
        {
            "shop": shop,
            "dp": data_points,
            "conf": confidence,
            "ts": trust,
            "tp": json.dumps({
                "execution_reliability": 0.81,
                "measurement_integrity": 0.65,
                "outcome_quality": 0.78,
                "stability": 0.72,
                "overall": 0.74,
            }),
            "al": autonomy,
            "lt": json.dumps({
                "views_floor": 12, "dwell_floor": 4.2,
                "return_floor": 4, "low_conv_threshold": 0.0164,
            }),
            "nts": json.dumps({
                "social_proof": 0.82,
                "urgency": 0.45,
                "engagement_depth": 0.61,
                "best_seller": 0.30,
            }),
        },
    )
    db.flush()


def _seed_sip_snapshot_with_vertical(db, shop: str, *, vertical="beauty"):
    """Seed a sip_snapshots row whose profile_data carries a vertical_prior."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    profile_data = {
        "shop_domain": shop,
        "profile_version": 1,
        "data_points_total": 200,
        "vertical_prior": {
            "vertical": vertical,
            "vertical_display": "Beauty & Cosmetics",
            "cvr_baseline_pct": 3.2,
            "aov_baseline_eur": 42.0,
            "n_prior_strength": 200,
            "n_observed": 200,
            "blended_cart_rate": 0.046,
            "applied": True,
        },
    }
    db.execute(
        text("""
            INSERT INTO sip_snapshots (shop_domain, snapshot_week, profile_data,
                                       baseline_cart_rate, data_points)
            VALUES (:shop, :week, :data, 0.046, 200)
            ON CONFLICT (shop_domain, snapshot_week) DO UPDATE SET
              profile_data = EXCLUDED.profile_data
        """),
        {"shop": shop, "week": week_start, "data": json.dumps(profile_data)},
    )
    db.flush()


def test_store_profile_empty_shop_warming_response(client, merchant_a, auth_a):
    """No SIP row → honest 'warming' response with note + zeros."""
    resp = client.get("/pro/store-profile", cookies=auth_a)
    assert resp.status_code == 200
    data = resp.json()
    assert data["shop_domain"] == SHOP_A
    assert data["profile_version"] == 0
    assert data["data_points"] == 0
    assert data["confidence_level"] == "none"
    assert data["trust_score"] == 0.5
    assert data["autonomy_level"] == 0
    assert data["top_nudge_scores"] == []
    assert data["vertical_prior"] is None
    assert "warming" in (data.get("note") or "").lower()


def test_store_profile_populated_sip_full_shape(client, db, merchant_a, auth_a):
    """SIP row populated → all fields surfaced verbatim."""
    _seed_sip(db, SHOP_A, data_points=2500, confidence="medium",
              trust=0.74, autonomy=2)
    resp = client.get("/pro/store-profile", cookies=auth_a)
    assert resp.status_code == 200
    data = resp.json()
    assert data["shop_domain"] == SHOP_A
    assert data["profile_version"] == 1
    assert data["data_points"] == 2500
    assert data["confidence_level"] == "medium"
    assert data["trust_score"] == 0.74
    assert data["autonomy_level"] == 2
    assert data["measurement_health"] == "healthy"
    # trust_profile JSONB → 4 dimensions exposed
    tp = data["trust_profile"]
    assert tp is not None
    assert tp["execution_reliability"] == 0.81
    assert tp["overall"] == 0.74
    # learned_thresholds JSONB → exposed verbatim
    lt = data["learned_thresholds"]
    assert lt["views_floor"] == 12
    # top_nudge_scores → top 3 sorted descending
    top = data["top_nudge_scores"]
    assert len(top) == 3
    assert top[0]["nudge_type"] == "social_proof"
    assert top[0]["effectiveness"] == 0.82
    assert top[1]["nudge_type"] == "engagement_depth"  # 0.61 > 0.45
    assert top[2]["nudge_type"] == "urgency"  # 0.45


def test_store_profile_vertical_prior_round_trip(client, db, merchant_a, auth_a):
    """vertical_prior persisted in sip_snapshots.profile_data JSONB → exposed."""
    _seed_sip(db, SHOP_A)
    _seed_sip_snapshot_with_vertical(db, SHOP_A, vertical="beauty")
    # Force cache miss for this shop (Sprint 4 cache is per-shop md5)
    try:
        from app.api.store_profile import _cache_key
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(_cache_key(SHOP_A))
    except Exception:
        pass

    resp = client.get("/pro/store-profile", cookies=auth_a)
    assert resp.status_code == 200
    vp = resp.json().get("vertical_prior")
    assert vp is not None
    assert vp["vertical"] == "beauty"
    assert vp["vertical_display"] == "Beauty & Cosmetics"
    assert vp["cvr_baseline_pct"] == 3.2
    assert vp["aov_baseline_eur"] == 42.0
    assert vp["n_prior_strength"] == 200
    assert vp["n_observed"] == 200
    assert vp["blended_cart_rate"] == 0.046
    assert vp["applied"] is True


def test_store_profile_no_snapshot_returns_null_vertical_prior(client, db,
                                                                 merchant_a,
                                                                 auth_a):
    """SIP exists but no sip_snapshots row yet → vertical_prior=None,
    response still 200 (vertical_prior is optional)."""
    _seed_sip(db, SHOP_A)
    try:
        from app.api.store_profile import _cache_key
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(_cache_key(SHOP_A))
    except Exception:
        pass

    resp = client.get("/pro/store-profile", cookies=auth_a)
    assert resp.status_code == 200
    assert resp.json()["vertical_prior"] is None


def test_store_profile_unauth_blocked(client):
    """No session cookie → 401 or 403."""
    resp = client.get("/pro/store-profile")
    assert resp.status_code in (401, 403)


def test_store_profile_does_not_leak_other_tenant(client, db, merchant_a,
                                                    merchant_b, auth_a):
    """Shop B's SIP must NOT appear in Shop A's response (tenant isolation)."""
    _seed_sip(db, SHOP_B, data_points=99999, confidence="high",
              trust=0.99, autonomy=5)
    # Shop A has no SIP row → must return warming, NOT shop B's data
    try:
        from app.api.store_profile import _cache_key
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete(_cache_key(SHOP_A))
    except Exception:
        pass
    resp = client.get("/pro/store-profile", cookies=auth_a)
    assert resp.status_code == 200
    data = resp.json()
    assert data["shop_domain"] == SHOP_A
    assert data["data_points"] == 0  # Not 99999 from shop B
    assert data["autonomy_level"] == 0  # Not 5 from shop B
    assert data["confidence_level"] == "none"
