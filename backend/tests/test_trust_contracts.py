"""Tests for Delegated Autonomy / Trust Contracts — THE killer feature.

Covers:
- Contract creation + single-active invariant
- Quota enforcement (daily + weekly)
- Confidence threshold gate
- Discount floor / ceiling bounds
- Holdout requirement
- Scope filtering (all vs specific products)
- Panic stop (revoke-all)
- Record execution + quota increment
- Audit log trail
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta

from app.services.trust_contract import (
    create_contract,
    can_execute,
    record_execution,
    panic_stop,
    revoke_contract,
    update_contract,
    get_active_contract,
    list_contracts,
)
from app.models.trust_contract import TrustContract, TrustExecutionLog


SHOP = "test-trust-suite.myshopify.com"


@pytest.fixture(autouse=True)
def _cleanup(db):
    """Keep the test namespace isolated across runs."""
    db.query(TrustExecutionLog).filter(TrustExecutionLog.shop_domain == SHOP).delete()
    db.query(TrustContract).filter(TrustContract.shop_domain == SHOP).delete()
    db.flush()
    # Clear Redis quota counters if Redis is available
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            for k in rc.keys(f"hs:trust_quota:{SHOP}:*"):
                rc.delete(k)
    except Exception:
        pass
    yield
    db.query(TrustExecutionLog).filter(TrustExecutionLog.shop_domain == SHOP).delete()
    db.query(TrustContract).filter(TrustContract.shop_domain == SHOP).delete()
    db.flush()


class TestContractCreation:
    def test_create_basic_contract(self, db):
        c = create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            max_per_day=3, max_per_week=10,
            discount_floor_pct=-5.0, discount_ceiling_pct=0.0,
            confidence_threshold=0.80,
        )
        assert c.id is not None
        assert c.status == "active"
        assert c.shop_domain == SHOP

    def test_single_active_invariant(self, db):
        """Creating a second contract for the same (shop, action) revokes the first."""
        c1 = create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST", max_per_day=3,
        )
        c2 = create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST", max_per_day=5,
        )
        db.refresh(c1)
        assert c1.status == "revoked"
        assert c1.revoked_reason == "superseded"
        assert c2.status == "active"
        # Only c2 is visible via get_active_contract
        active = get_active_contract(db, SHOP, "PRICE_TEST")
        assert active.id == c2.id

    def test_create_validates_action_type(self, db):
        with pytest.raises(ValueError, match="action_type not allowed"):
            create_contract(db, shop_domain=SHOP, action_type="CRO_FIX")

    def test_create_validates_confidence_range(self, db):
        with pytest.raises(ValueError, match="confidence_threshold"):
            create_contract(
                db, shop_domain=SHOP, action_type="PRICE_TEST",
                confidence_threshold=1.5,
            )

    def test_create_validates_discount_range(self, db):
        with pytest.raises(ValueError, match="discount_floor"):
            create_contract(
                db, shop_domain=SHOP, action_type="PRICE_TEST",
                discount_floor_pct=5.0, discount_ceiling_pct=-5.0,
            )

    def test_create_validates_quota_ordering(self, db):
        with pytest.raises(ValueError, match="max_per_day cannot exceed"):
            create_contract(
                db, shop_domain=SHOP, action_type="PRICE_TEST",
                max_per_day=10, max_per_week=5,
            )


class TestCanExecuteGates:
    def test_no_contract_denies(self, db):
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=True,
        )
        assert not r.allowed
        assert r.reason == "no_active_contract"

    def test_confidence_below_threshold(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence_threshold=0.80,
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.70, discount_pct=-3.0, has_holdout=True,
        )
        assert not r.allowed
        assert "confidence_below_threshold" in r.reason

    def test_discount_below_floor(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            discount_floor_pct=-5.0, discount_ceiling_pct=0.0,
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-10.0, has_holdout=True,
        )
        assert not r.allowed
        assert "discount_below_floor" in r.reason

    def test_discount_above_ceiling(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            discount_floor_pct=-5.0, discount_ceiling_pct=2.0,
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=5.0, has_holdout=True,
        )
        assert not r.allowed
        assert "discount_above_ceiling" in r.reason

    def test_holdout_required(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            require_holdout=True,
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=False,
        )
        assert not r.allowed
        assert r.reason == "holdout_required"

    def test_holdout_not_required_when_disabled(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            require_holdout=False,
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=False,
        )
        assert r.allowed

    def test_all_gates_pass(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            max_per_day=5, max_per_week=20,
            discount_floor_pct=-10.0, discount_ceiling_pct=0.0,
            confidence_threshold=0.80, require_holdout=True,
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.85, discount_pct=-5.0, has_holdout=True,
        )
        assert r.allowed
        assert r.reason == "allowed"
        assert r.remaining_today == 4
        assert r.remaining_week == 19


class TestScopeFilters:
    def test_all_scope_matches_any_url(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            scope_type="all",
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=True,
            target_url="/products/anything",
        )
        assert r.allowed

    def test_products_scope_in(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            scope_type="products",
            scope_values=["/products/allowed-one", "/products/allowed-two"],
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=True,
            target_url="/products/allowed-one",
        )
        assert r.allowed

    def test_products_scope_out(self, db):
        create_contract(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            scope_type="products",
            scope_values=["/products/allowed-one"],
        )
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=True,
            target_url="/products/not-allowed",
        )
        assert not r.allowed
        assert r.reason == "target_out_of_scope"


class TestPanicStop:
    def test_panic_revokes_all_active(self, db):
        c1 = create_contract(db, shop_domain=SHOP, action_type="PRICE_TEST")
        c2 = create_contract(db, shop_domain=SHOP, action_type="FLASH_INCENTIVE")
        n = panic_stop(db, SHOP, reason="test_panic")
        assert n == 2
        db.refresh(c1)
        db.refresh(c2)
        assert c1.status == "revoked"
        assert c1.revoked_reason == "test_panic"
        assert c2.status == "revoked"

    def test_panic_on_empty_is_noop(self, db):
        n = panic_stop(db, SHOP, reason="test")
        assert n == 0

    def test_post_panic_denies_execution(self, db):
        create_contract(db, shop_domain=SHOP, action_type="PRICE_TEST")
        panic_stop(db, SHOP)
        r = can_execute(
            db, shop_domain=SHOP, action_type="PRICE_TEST",
            confidence=0.9, discount_pct=-3.0, has_holdout=True,
        )
        assert not r.allowed
        assert r.reason == "no_active_contract"


class TestRecordExecution:
    def test_record_creates_log_row(self, db):
        c = create_contract(db, shop_domain=SHOP, action_type="PRICE_TEST")
        row = record_execution(
            db,
            contract=c,
            target_url="/products/test",
            confidence=0.85,
            discount_pct=-4.0,
            holdout_pct=20,
            params={"variant": "10%_off"},
        )
        assert row.id is not None
        assert row.shop_domain == SHOP
        assert row.contract_id == c.id
        assert row.outcome == "pending"
        assert json.loads(row.params_json) == {"variant": "10%_off"}


class TestUpdateContract:
    def test_patch_fields(self, db):
        c = create_contract(db, shop_domain=SHOP, action_type="PRICE_TEST", max_per_day=3)
        u = update_contract(db, c.id, max_per_day=5, note="tightened")
        assert u.max_per_day == 5
        assert u.note == "tightened"

    def test_patch_rejects_unknown_field(self, db):
        c = create_contract(db, shop_domain=SHOP, action_type="PRICE_TEST")
        u = update_contract(db, c.id, malicious_field="x")
        # Unknown fields silently dropped, contract still updated for updated_at
        assert u is not None
        assert not hasattr(u, "malicious_field") or getattr(u, "malicious_field", None) is None


class TestListing:
    def test_list_returns_all_statuses(self, db):
        c1 = create_contract(db, shop_domain=SHOP, action_type="PRICE_TEST")
        c2 = create_contract(db, shop_domain=SHOP, action_type="FLASH_INCENTIVE")
        revoke_contract(db, c1.id, reason="test")
        rows = list_contracts(db, SHOP)
        assert len(rows) == 2
        statuses = {r.status for r in rows}
        assert "active" in statuses
        assert "revoked" in statuses
