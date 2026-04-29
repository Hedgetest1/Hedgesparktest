"""Tests for Visitor Intent Classification — Phase 1.6.

Covers the new /analytics/visitor-intent-classification endpoint and
its HOT/WARM/COLD threshold logic.
"""
from __future__ import annotations

import time

from sqlalchemy import text

from app.api.visitor_scores import HOT_THRESHOLD, WARM_THRESHOLD, _VI_CACHE_PREFIX
from tests.conftest import SHOP_A, SHOP_B


def _reset_shop_state(db, *shops: str) -> None:
    """Delete events for the given shop(s) AND evict the Redis cache
    entry for each. Phase 1.9.3 added a Redis cache in front of
    /analytics/visitor-intent-classification. Redis lives outside the
    SAVEPOINT the test suite uses, so without explicit eviction a
    stale cached aggregate would leak across tests. Every test that
    seeds events calls this as the very first step."""
    import hashlib
    for shop in shops:
        db.execute(
            text("DELETE FROM events WHERE shop_domain = :shop"),
            {"shop": shop},
        )
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            for shop in shops:
                key = f"{_VI_CACHE_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"
                rc.delete(key)
    except Exception:
        pass  # redis missing in test env is fine; SQL still hits


def _insert_visitor_event(
    db,
    *,
    shop: str,
    visitor_id: str,
    event_type: str = "page_view",
    url: str = "/products/test",
    dwell: int = 0,
    scroll: int = 0,
):
    """Seed a single event row. Used to construct visitors that fall
    into known tiers. timestamp in ms since epoch."""
    db.execute(
        text("""
            INSERT INTO events (shop_domain, visitor_id, event_type, url,
                                timestamp, dwell_seconds, max_scroll_depth)
            VALUES (:shop, :visitor, :etype, :url, :ts, :dwell, :scroll)
        """),
        {
            "shop": shop,
            "visitor": visitor_id,
            "etype": event_type,
            "url": url,
            "ts": int(time.time() * 1000),
            "dwell": dwell,
            "scroll": scroll,
        },
    )


def test_thresholds_are_consistent_pair():
    """HOT > WARM is a non-negotiable invariant. If this ever flips,
    the classification SQL silently mis-categorizes every visitor."""
    assert HOT_THRESHOLD > WARM_THRESHOLD
    assert WARM_THRESHOLD > 0


def test_classification_requires_authentication(client):
    """No session cookie = 401, not data leak."""
    r = client.get("/analytics/visitor-intent-classification")
    assert r.status_code == 401


def test_classification_empty_shop_returns_zeros(client, merchant_a, auth_a, db):
    """Fresh shop with no events yet returns zero counts across all
    tiers — not a 500, not null. Uses the standard auth helpers."""
    # Ensure events table is clean for this shop (tests share the
    # prod DB under SAVEPOINT per feedback_test_hermeticity_prod_db.md)
    _reset_shop_state(db, SHOP_A)
    r = client.get(
        "/analytics/visitor-intent-classification",
        cookies=auth_a,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_visitors"] == 0
    assert body["hot_visitors"] == 0
    assert body["warm_visitors"] == 0
    assert body["cold_visitors"] == 0
    assert body["hot_threshold"] == HOT_THRESHOLD
    assert body["warm_threshold"] == WARM_THRESHOLD


def test_classification_boundary_exactly_hot_threshold(
    client, merchant_a, auth_a, db
):
    """Score == HOT_THRESHOLD is classified as WARM (inclusive of the
    warm upper bound, exclusive of hot lower bound). The SQL uses
    `> hot_threshold` for hot → boundary is warm. Lock this contract."""
    _reset_shop_state(db, SHOP_A)
    # Score: 0*0.6 + 100*0.3 + 2*10 = 50.0 exactly
    # `> 50` is false, `> 20 AND <= 50` is true → warm
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="v-boundary-exact",
        event_type="page_view", dwell=0, scroll=100,
    )
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="v-boundary-exact",
        event_type="click", dwell=0, scroll=100,
    )
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="v-boundary-exact",
        event_type="click", dwell=0, scroll=100,
    )
    r = client.get(
        "/analytics/visitor-intent-classification",
        cookies=auth_a,
    )
    body = r.json()
    assert body["total_visitors"] == 1
    assert body["warm_visitors"] == 1, (
        f"exact threshold score=50 should be warm, got {body}"
    )
    assert body["hot_visitors"] == 0


def test_classification_clear_hot_visitor(
    client, merchant_a, auth_a, db
):
    """A visitor with deep engagement + clicks is classified HOT."""
    _reset_shop_state(db, SHOP_A)
    # Score: 60*0.6 + 80*0.3 + 3*10 = 36 + 24 + 30 = 90 (> 50)
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="v-hot-1",
        event_type="page_view", dwell=60, scroll=80,
    )
    for _ in range(3):
        _insert_visitor_event(
            db, shop=SHOP_A, visitor_id="v-hot-1",
            event_type="click", dwell=60, scroll=80,
        )
    r = client.get(
        "/analytics/visitor-intent-classification",
        cookies=auth_a,
    )
    body = r.json()
    assert body["hot_visitors"] == 1, f"expected hot, got {body}"
    assert body["warm_visitors"] == 0
    assert body["cold_visitors"] == 0


def test_classification_clear_cold_visitor(
    client, merchant_a, auth_a, db
):
    """A visitor who barely engaged is classified COLD."""
    _reset_shop_state(db, SHOP_A)
    # Score: 10*0.6 + 20*0.3 + 0 = 6 + 6 = 12 (<= 20)
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="v-cold-1",
        event_type="page_view", dwell=10, scroll=20,
    )
    r = client.get(
        "/analytics/visitor-intent-classification",
        cookies=auth_a,
    )
    body = r.json()
    assert body["cold_visitors"] == 1, f"expected cold, got {body}"
    assert body["hot_visitors"] == 0
    assert body["warm_visitors"] == 0


def test_classification_counts_sum_to_total(
    client, merchant_a, auth_a, db
):
    """Every visitor lands in EXACTLY ONE tier — hot + warm + cold must
    equal total. No double-counting, no visitor vanishes. Brutal
    invariant: validates the SQL partition logic."""
    _reset_shop_state(db, SHOP_A)
    # Seed one visitor per tier
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="vsum-hot",
        event_type="page_view", dwell=60, scroll=80,
    )
    for _ in range(3):
        _insert_visitor_event(
            db, shop=SHOP_A, visitor_id="vsum-hot",
            event_type="click", dwell=60, scroll=80,
        )
    # Warm: dwell 40, scroll 60, 0 clicks → 40*0.6 + 60*0.3 = 24 + 18 = 42
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="vsum-warm",
        event_type="page_view", dwell=40, scroll=60,
    )
    # Cold: dwell 5, scroll 10 → 3 + 3 = 6
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="vsum-cold",
        event_type="page_view", dwell=5, scroll=10,
    )
    r = client.get(
        "/analytics/visitor-intent-classification",
        cookies=auth_a,
    )
    body = r.json()
    assert (
        body["hot_visitors"]
        + body["warm_visitors"]
        + body["cold_visitors"]
        == body["total_visitors"]
    ), f"tiers must partition the visitor set exactly: {body}"
    assert body["total_visitors"] == 3
    assert body["hot_visitors"] == 1
    assert body["warm_visitors"] == 1
    assert body["cold_visitors"] == 1


def test_classification_tenant_isolation(
    client, merchant_a, merchant_b, auth_a, auth_b, db
):
    """Shop B's visitors must not appear in Shop A's counts. Brutal
    tenant-isolation test — a leaky classifier would let one merchant
    see another's traffic.

    Endpoint is Pro-gated since 2026-04-29 (strict $0-70 parity rule).
    Both shops are upgraded to Pro inline so the tenant-iso invariant
    is the thing under test, not the auth gate. Lite-tier 403 has its
    own dedicated test below.
    """
    _reset_shop_state(db, SHOP_A, SHOP_B)
    # Upgrade both shops to Pro so they can read the endpoint
    merchant_a.plan = "pro"
    merchant_a.billing_active = True
    merchant_b.plan = "pro"
    merchant_b.billing_active = True
    db.flush()
    # Plant a hot visitor on Shop A only
    _insert_visitor_event(
        db, shop=SHOP_A, visitor_id="tenant-a-hot",
        event_type="page_view", dwell=60, scroll=80,
    )
    for _ in range(3):
        _insert_visitor_event(
            db, shop=SHOP_A, visitor_id="tenant-a-hot",
            event_type="click", dwell=60, scroll=80,
        )
    # Plant a hot visitor on Shop B — should never reach Shop A's count
    _insert_visitor_event(
        db, shop=SHOP_B, visitor_id="tenant-b-hot",
        event_type="page_view", dwell=60, scroll=80,
    )
    for _ in range(3):
        _insert_visitor_event(
            db, shop=SHOP_B, visitor_id="tenant-b-hot",
            event_type="click", dwell=60, scroll=80,
        )

    ra = client.get(
        "/analytics/visitor-intent-classification", cookies=auth_a,
    )
    rb = client.get(
        "/analytics/visitor-intent-classification", cookies=auth_b,
    )
    assert ra.json()["total_visitors"] == 1, (
        f"Shop A leaked Shop B visitor: {ra.json()}"
    )
    assert rb.json()["total_visitors"] == 1, (
        f"Shop B leaked Shop A visitor: {rb.json()}"
    )


# ════════════════════════════════════════════════════════════════════
# Pro-tier gate tests — strict $0-70 parity rule (2026-04-29)
# ════════════════════════════════════════════════════════════════════


def test_visitor_intent_classification_lite_returns_403(
    client, merchant_b, auth_b, db
):
    """Lite merchants must NOT access /analytics/visitor-intent-classification.
    Per strict $0-70 parity rule: no $0-70 competitor ships per-visitor
    intent classification at any price (Glew $79 minimum)."""
    # merchant_b fixture defaults to plan="lite" billing_active=False —
    # the gate must reject regardless of billing state.
    r = client.get("/analytics/visitor-intent-classification", cookies=auth_b)
    assert r.status_code == 403, (
        f"Lite tier must get 403, got {r.status_code}: {r.text}"
    )


def test_visitor_intent_classification_pro_returns_200(
    client, merchant_a, auth_a, db
):
    """Pro merchants get 200 — merchant_a fixture is plan='pro' billing_active=True."""
    r = client.get("/analytics/visitor-intent-classification", cookies=auth_a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "total_visitors" in body
    assert "hot_visitors" in body
