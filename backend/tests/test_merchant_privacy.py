"""Tests for Art. 16 (rectify) + Art. 21 (object) merchant endpoints."""
from __future__ import annotations

import uuid

from app.core.deps import require_merchant_session
from app.main import app as fastapi_app
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.services.merchant_privacy import (
    _hash_email,
    is_merchant_opted_out,
    set_opt_out,
)


def _make_merchant(db) -> str:
    shop = f"privacy-{uuid.uuid4().hex[:8]}.myshopify.com"
    db.add(Merchant(
        shop_domain=shop,
        access_token="enc:fake",
        plan="lite",
        install_status="active",
        contact_email="old@example.com",
    ))
    db.flush()
    return shop


def _with_override(shop: str, fn):
    fastapi_app.dependency_overrides[require_merchant_session] = lambda: shop
    try:
        return fn()
    finally:
        fastapi_app.dependency_overrides.pop(require_merchant_session, None)


# ---------- opt-out flag primitives ----------

def test_opt_out_roundtrip():
    shop = f"flag-{uuid.uuid4().hex[:8]}.myshopify.com"
    assert is_merchant_opted_out(shop) is False
    set_opt_out(shop, True)
    assert is_merchant_opted_out(shop) is True
    set_opt_out(shop, False)
    assert is_merchant_opted_out(shop) is False


def test_is_merchant_opted_out_none_safe():
    assert is_merchant_opted_out(None) is False
    assert is_merchant_opted_out("") is False


# ---------- GET /merchant/privacy/preferences ----------

def test_get_preferences_returns_current_flag(client, db):
    shop = _make_merchant(db)
    set_opt_out(shop, True)
    try:
        resp = _with_override(shop, lambda: client.get(
            "/merchant/privacy/preferences",
        ))
    finally:
        set_opt_out(shop, False)
    assert resp.status_code == 200
    body = resp.json()
    assert body["shop_domain"] == shop
    assert body["opt_out_automated_targeting"] is True


# ---------- PATCH /merchant/me ----------

def test_rectify_updates_contact_email(client, db):
    shop = _make_merchant(db)
    new_email = f"new-{uuid.uuid4().hex[:6]}@example.com"

    resp = _with_override(shop, lambda: client.patch(
        "/merchant/me",
        json={"contact_email": new_email},
    ))
    assert resp.status_code == 200
    body = resp.json()
    assert "contact_email" in body["changed"]

    updated = db.query(Merchant).filter(
        Merchant.shop_domain == shop,
    ).first()
    assert updated.contact_email == new_email


def test_rectify_rejects_invalid_email(client, db):
    shop = _make_merchant(db)
    resp = _with_override(shop, lambda: client.patch(
        "/merchant/me",
        json={"contact_email": "not-a-valid-email"},
    ))
    assert resp.status_code == 422  # pydantic validation


def test_rectify_writes_audit_log(client, db):
    shop = _make_merchant(db)
    before = db.query(AuditLog).filter(
        AuditLog.action_type == "gdpr_rectify",
        AuditLog.target_id == shop,
    ).count()
    new_email = f"audit-{uuid.uuid4().hex[:6]}@example.com"
    _with_override(shop, lambda: client.patch(
        "/merchant/me",
        json={"contact_email": new_email},
    ))
    after = db.query(AuditLog).filter(
        AuditLog.action_type == "gdpr_rectify",
        AuditLog.target_id == shop,
    ).count()
    assert after == before + 1


# ---------- POST /merchant/object (Art. 21) ----------

def test_object_sets_opt_out_flag(client, db):
    shop = _make_merchant(db)
    assert is_merchant_opted_out(shop) is False
    try:
        resp = _with_override(shop, lambda: client.post(
            "/merchant/object",
            json={"reason": "no automated targeting please"},
        ))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "opted_out"
        assert is_merchant_opted_out(shop) is True
    finally:
        set_opt_out(shop, False)


def test_object_writes_audit_log(client, db):
    shop = _make_merchant(db)
    try:
        _with_override(shop, lambda: client.post(
            "/merchant/object",
            json={"reason": "test"},
        ))
        logs = db.query(AuditLog).filter(
            AuditLog.action_type == "gdpr_object",
            AuditLog.target_id == shop,
        ).all()
        assert len(logs) == 1
        assert logs[0].actor_type == "merchant"
    finally:
        set_opt_out(shop, False)


def test_unobject_clears_flag(client, db):
    shop = _make_merchant(db)
    set_opt_out(shop, True)
    try:
        resp = _with_override(shop, lambda: client.post("/merchant/unobject", json={}))
        assert resp.status_code == 200
        assert resp.json()["status"] == "opted_in"
        assert is_merchant_opted_out(shop) is False
    finally:
        set_opt_out(shop, False)


# ---------- Session gating ----------

def test_rectify_requires_session(client):
    resp = client.patch("/merchant/me", json={"contact_email": "a@b.c"})
    assert resp.status_code in (401, 403)


def test_object_requires_session(client):
    resp = client.post("/merchant/object", json={})
    assert resp.status_code in (401, 403)
