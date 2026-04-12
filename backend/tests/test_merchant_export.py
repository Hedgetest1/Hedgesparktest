"""Tests for GDPR Art. 15/20 merchant self-serve export endpoint."""
from __future__ import annotations

import uuid

from app.main import app as fastapi_app
from app.core.deps import require_merchant_session
from app.models.audit_log import AuditLog
from app.models.event import Event
from app.models.merchant import Merchant
from app.models.ops_alert import OpsAlert


def _make_merchant(db) -> str:
    shop = f"export_{uuid.uuid4().hex[:10]}.myshopify.com"
    db.add(Merchant(
        shop_domain=shop,
        access_token="encrypted:fake",
        plan="lite",
        install_status="active",
    ))
    db.flush()
    return shop


def _with_session_override(shop: str, fn):
    fastapi_app.dependency_overrides[require_merchant_session] = lambda: shop
    try:
        return fn()
    finally:
        fastapi_app.dependency_overrides.pop(require_merchant_session, None)


def test_export_requires_session(client):
    """Unauthenticated request must not leak anything."""
    resp = client.get("/merchant/export")
    assert resp.status_code in (401, 403)


def test_export_returns_merchant_scoped_payload(client, db):
    shop = _make_merchant(db)
    db.add(Event(
        visitor_id="vis_1",
        event_type="page_view",
        url="/",
        shop_domain=shop,
        timestamp=1_700_000_000_000,
    ))
    db.add(OpsAlert(
        severity="info",
        source="test",
        alert_type="test_alert",
        shop_domain=shop,
        summary="test",
        resolved=False,
    ))
    db.flush()

    resp = _with_session_override(shop, lambda: client.get("/merchant/export"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["shop_domain"] == shop
    assert body["gdpr_article"].startswith("Art. 15")
    assert "exported_at" in body
    assert "merchant" in body and body["merchant"] is not None
    # Access token MUST be redacted from the artifact
    assert "access_token" not in body["merchant"]
    # Events + ops_alerts must include the seeded rows
    assert len(body["events"]) >= 1
    assert len(body["ops_alerts"]) >= 1
    cd = resp.headers.get("content-disposition", "")
    assert "hedgespark-export" in cd


def test_export_does_not_leak_other_shops(client, db):
    shop_a = _make_merchant(db)
    shop_b = _make_merchant(db)
    db.add(OpsAlert(
        severity="info", source="t", alert_type="a",
        shop_domain=shop_a, summary="A", resolved=False,
    ))
    db.add(OpsAlert(
        severity="info", source="t", alert_type="b",
        shop_domain=shop_b, summary="B", resolved=False,
    ))
    db.flush()

    resp = _with_session_override(shop_a, lambda: client.get("/merchant/export"))
    body = resp.json()
    for a in body["ops_alerts"]:
        assert a["shop_domain"] == shop_a


def test_export_writes_audit_log(client, db):
    shop = _make_merchant(db)
    db.flush()

    before = db.query(AuditLog).filter(
        AuditLog.action_type == "gdpr_self_export",
        AuditLog.target_id == shop,
    ).count()

    _with_session_override(shop, lambda: client.get("/merchant/export"))

    after = db.query(AuditLog).filter(
        AuditLog.action_type == "gdpr_self_export",
        AuditLog.target_id == shop,
    ).count()
    assert after == before + 1
