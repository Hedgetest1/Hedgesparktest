"""Tests for /merchant/groups/* — Gap #5 multi-store consolidation
(Lite-flipped 2026-04-29).

Coverage:
  * CRUD (create / list / add member / remove member)
  * Per-currency rollup honesty — never sum mixed currencies
  * Tenant isolation (API + service layer defense in depth)
  * Lite-tier access (was Pro-gated; flipped per $0-60 parity doctrine)
  * Primary-uniqueness (atomic flip at service layer + DB partial index)
  * Max-members cap (DoS guard)
  * Empty-state handling
  * 401 / 403 paths
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.merchant import Merchant
from app.models.merchant_group import MerchantGroup, MerchantGroupMember
from app.models.shop_order import ShopOrder
from app.services.merchant_groups import (
    add_member as add_member_svc,
    create_group as create_group_svc,
    get_group_dashboard,
    list_groups_for_owner,
    list_members,
    remove_member as remove_member_svc,
    _MAX_MEMBERS_PER_GROUP,
)
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ════════════════════════════════════════════════════════════════════
# Fixtures specific to multi-store tests
# ════════════════════════════════════════════════════════════════════


@pytest.fixture()
def merchant_lite(db):
    """A Lite-tier merchant with a resolvable contact_email.

    The Pro-gated endpoint pattern was require_pro_session; Gap #5 flips
    it to require_merchant_session. This fixture proves the gate
    actually accepts a non-Pro plan.
    """
    m = Merchant(
        shop_domain="lite-shop.myshopify.com",
        plan="starter",
        billing_active=True,
        install_status="active",
        session_version=0,
        contact_email="owner@lite-shop.com",
        primary_currency="EUR",
    )
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def merchant_lite_aux(db, merchant_lite):
    """A second store owned by the SAME contact_email — the typical
    multi-store scenario (one founder, two Shopify stores)."""
    m = Merchant(
        shop_domain="lite-shop-us.myshopify.com",
        plan="starter",
        billing_active=True,
        install_status="active",
        session_version=0,
        contact_email="owner@lite-shop.com",  # same owner
        primary_currency="USD",
    )
    db.add(m)
    db.flush()
    return m


def _seed_order(db, *, shop, currency, total, days_ago=0, idx=0):
    db.add(ShopOrder(
        shop_domain=shop,
        shopify_order_id=f"o-mg-{shop}-{idx}",
        total_price=total,
        currency=currency,
        customer_email=f"c{idx}@x.com",
        line_items=[{"price": str(total), "quantity": 1, "title": "x"}],
        created_at=_now_naive() - timedelta(days=days_ago),
        source="webhook",
    ))


# ════════════════════════════════════════════════════════════════════
# CRUD endpoints
# ════════════════════════════════════════════════════════════════════


def test_create_group_lite_tier(client, merchant_lite):
    """Lite plan merchants can create groups (Pro→Lite flip)."""
    cookies = auth_cookies("lite-shop.myshopify.com")
    r = client.post(
        "/merchant/groups",
        cookies=cookies,
        json={"name": "EU+US Brand"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "EU+US Brand"
    assert body["owner_email"] == "owner@lite-shop.com"


def test_list_groups_returns_only_own(client, merchant_a, merchant_lite, db):
    """Tenant isolation: shop A's owner sees only their own groups."""
    # Group owned by merchant_a's email
    g_a = create_group_svc(
        db, name="A's group",
        owner_email="owner@test-shop-a.com",
        base_currency="USD",
    )
    add_member_svc(db, g_a.id, SHOP_A, is_primary=True)

    # Group owned by Lite shop's email
    g_lite = create_group_svc(
        db, name="Lite's group",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    add_member_svc(db, g_lite.id, "lite-shop.myshopify.com", is_primary=True)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/groups", cookies=cookies)
    assert r.status_code == 200
    names = [g["name"] for g in r.json()["groups"]]
    assert "A's group" in names
    assert "Lite's group" not in names


def test_unauthenticated_returns_401(client):
    r = client.post("/merchant/groups", json={"name": "x"})
    assert r.status_code == 401


def test_add_remove_member(client, merchant_a, merchant_b, db):
    g = create_group_svc(
        db, name="A group",
        owner_email="owner@test-shop-a.com",
        base_currency="USD",
    )
    add_member_svc(db, g.id, SHOP_A, is_primary=True)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    # Add merchant_b as a member
    r = client.post(
        f"/merchant/groups/{g.id}/members",
        cookies=cookies,
        json={"shop_domain": SHOP_B, "label": "B"},
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text} (g.id={g.id})"
    assert r.json()["shop_domain"] == SHOP_B

    # Remove
    r = client.delete(f"/merchant/groups/{g.id}/members/{SHOP_B}", cookies=cookies, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Removing again → 404
    r = client.delete(f"/merchant/groups/{g.id}/members/{SHOP_B}", cookies=cookies, headers={"Content-Type": "application/json"})
    assert r.status_code == 404


def test_cannot_modify_other_owners_group(client, merchant_a, merchant_lite, db):
    """Tenant iso at API layer: shop A cannot add members to Lite's group."""
    g = create_group_svc(
        db, name="Lite group",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.post(
        f"/merchant/groups/{g.id}/members",
        cookies=cookies,
        json={"shop_domain": SHOP_A},
    )
    assert r.status_code == 403


# ════════════════════════════════════════════════════════════════════
# Service-layer tenant-iso (defense in depth)
# ════════════════════════════════════════════════════════════════════


def test_service_layer_tenant_iso(merchant_a, merchant_lite, db):
    """get_group_dashboard MUST refuse mismatched requesting_owner_email
    even if the caller bypassed the API layer (defense in depth)."""
    g = create_group_svc(
        db, name="A's group",
        owner_email="owner@test-shop-a.com",
        base_currency="USD",
    )
    add_member_svc(db, g.id, SHOP_A, is_primary=True)
    db.flush()

    # Wrong owner — must be rejected
    result = get_group_dashboard(
        db, g.id, requesting_owner_email="owner@lite-shop.com",
    )
    assert result.get("error") == "forbidden"

    # Right owner — must succeed
    result = get_group_dashboard(
        db, g.id, requesting_owner_email="owner@test-shop-a.com",
    )
    assert "error" not in result


# ════════════════════════════════════════════════════════════════════
# Per-currency rollup — the truth-shaped fix
# ════════════════════════════════════════════════════════════════════


def test_dashboard_empty_members(merchant_lite, db):
    g = create_group_svc(
        db, name="Empty",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    db.flush()
    res = get_group_dashboard(db, g.id, requesting_owner_email="owner@lite-shop.com")
    assert res["shop_count"] == 0
    assert res["by_currency"] == {}
    assert res["headline"] is None
    assert res["is_homogeneous"] is True


def test_dashboard_homogeneous_currency(merchant_lite, db):
    """All shops in same currency → single bucket, headline populated."""
    g = create_group_svc(
        db, name="EU only",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    add_member_svc(db, g.id, "lite-shop.myshopify.com", is_primary=True)
    _seed_order(db, shop="lite-shop.myshopify.com", currency="EUR", total=100.0, idx=1)
    _seed_order(db, shop="lite-shop.myshopify.com", currency="EUR", total=200.0, idx=2)
    db.flush()

    res = get_group_dashboard(db, g.id, requesting_owner_email="owner@lite-shop.com")
    assert res["is_homogeneous"] is True
    assert set(res["by_currency"].keys()) == {"EUR"}
    eur = res["by_currency"]["EUR"]
    assert eur["revenue"] == 300.0
    assert eur["orders"] == 2
    assert res["headline"]["currency"] == "EUR"
    assert res["headline"]["revenue"] == 300.0


def test_dashboard_mixed_currency_no_fake_sum(merchant_lite, merchant_lite_aux, db):
    """The critical fix — mixed currencies must surface separately,
    NEVER summed as if same denomination."""
    g = create_group_svc(
        db, name="EU+US",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    add_member_svc(db, g.id, "lite-shop.myshopify.com", is_primary=True)
    add_member_svc(db, g.id, "lite-shop-us.myshopify.com")
    _seed_order(db, shop="lite-shop.myshopify.com", currency="EUR", total=1000.0, idx=10)
    _seed_order(db, shop="lite-shop-us.myshopify.com", currency="USD", total=800.0, idx=20)
    db.flush()

    res = get_group_dashboard(db, g.id, requesting_owner_email="owner@lite-shop.com")
    assert res["is_homogeneous"] is False
    assert set(res["by_currency"].keys()) == {"EUR", "USD"}
    assert res["by_currency"]["EUR"]["revenue"] == 1000.0
    assert res["by_currency"]["USD"]["revenue"] == 800.0
    # No top-level revenue field — that would be the bug we fixed
    assert "revenue_eur" not in res
    assert "revenue" not in res  # no fake-sum
    # Headline is the dominant currency only
    assert res["headline"]["currency"] in ("EUR", "USD")
    assert res["headline"]["mixed"] is True


# ════════════════════════════════════════════════════════════════════
# Primary-uniqueness — service-layer atomic flip + DB partial index
# ════════════════════════════════════════════════════════════════════


def test_primary_atomic_flip(merchant_lite, merchant_lite_aux, db):
    """Adding a new shop with is_primary=True must demote any prior
    primary in the SAME group, atomically."""
    g = create_group_svc(
        db, name="X",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    add_member_svc(db, g.id, "lite-shop.myshopify.com", is_primary=True)
    add_member_svc(db, g.id, "lite-shop-us.myshopify.com", is_primary=True)
    db.flush()

    members = list_members(db, g.id)
    primaries = [m for m in members if m.is_primary]
    assert len(primaries) == 1
    assert primaries[0].shop_domain == "lite-shop-us.myshopify.com"


def test_max_members_cap(merchant_lite, db):
    """Adding the 51st member raises ValueError — DoS guard."""
    g = create_group_svc(
        db, name="X",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    for i in range(_MAX_MEMBERS_PER_GROUP):
        add_member_svc(db, g.id, f"shop-{i}.myshopify.com", is_primary=(i == 0))
    db.flush()

    with pytest.raises(ValueError, match="max_members_exceeded"):
        add_member_svc(db, g.id, "shop-overflow.myshopify.com")


def test_add_member_idempotent_on_duplicate(merchant_lite, db):
    g = create_group_svc(
        db, name="X",
        owner_email="owner@lite-shop.com",
        base_currency="EUR",
    )
    a = add_member_svc(db, g.id, "shop-a.myshopify.com", label="L1")
    b = add_member_svc(db, g.id, "shop-a.myshopify.com", label="L2")
    db.flush()
    # Same row, label updated
    assert a.id == b.id
    assert b.label == "L2"
