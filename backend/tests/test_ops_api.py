"""Tests for operator API (ops.py) — alerts + GDPR export retrieval."""
import json
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.gdpr_request import GdprRequest
from app.models.merchant import Merchant
from app.models.event import Event
from app.services.alerting import write_alert
from tests.conftest import SHOP_A, now_ms

# Operator key from env (set in conftest.py via DASHBOARD_API_KEY or test default)
_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "")

# If DASHBOARD_API_KEY is not set in env, set a test value
if not _OP_KEY:
    _OP_KEY = "test-operator-key-for-ci"
    os.environ["DASHBOARD_API_KEY"] = _OP_KEY


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Alerts API
# ---------------------------------------------------------------------------

def test_dashboard_health_requires_auth(client):
    resp = client.get("/ops/dashboard-health")
    assert resp.status_code == 401


def test_dashboard_health_shape(client, db):
    """GET /ops/dashboard-health returns the full preventer-state shape."""
    from app.services.alerting import write_alert
    write_alert(
        db,
        severity="critical",
        source="dashboard_asset_probe",
        alert_type="dashboard_asset_drift",
        summary="unresolved test drift",
        detail={"failures": ["/: asset X returned HTTP 500"]},
    )
    db.commit()
    resp = client.get("/ops/dashboard-health", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "preventer_enabled" in data
    assert "unresolved_drift_alerts" in data
    assert "unresolved_escalations" in data
    assert "this_hour" in data
    assert data["this_hour"]["limit"] == 3
    assert isinstance(data["this_hour"]["attempts"], int)
    assert any(a.get("summary") == "unresolved test drift"
               for a in data["unresolved_drift_alerts"])


def test_list_alerts_requires_auth(client):
    """No X-API-Key → 401."""
    resp = client.get("/ops/alerts")
    assert resp.status_code == 401


def test_list_alerts_wrong_key(client):
    """Wrong key → 401."""
    resp = client.get("/ops/alerts", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_list_unresolved_alerts(client, db):
    """Operator can list unresolved alerts."""
    write_alert(db, severity="warning", source="test", alert_type="test_type", summary="test alert")
    db.commit()
    resp = client.get("/ops/alerts", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(a["alert_type"] == "test_type" for a in data)


def test_list_recent_alerts(client, db):
    """Operator can list recent alerts including resolved."""
    a = write_alert(db, severity="info", source="test", alert_type="recent_test", summary="recent")
    db.commit()
    resp = client.get("/ops/alerts/recent", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert any(a["alert_type"] == "recent_test" for a in data)


def test_resolve_alert(client, db):
    """Operator can resolve an alert."""
    alert = write_alert(db, severity="warning", source="test", alert_type="resolve_test", summary="fix me")
    db.commit()
    resp = client.post(f"/ops/alerts/{alert.id}/resolve", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


def test_resolve_alert_requires_auth(client, db):
    """Resolve without operator key → 401."""
    alert = write_alert(db, severity="info", source="t", alert_type="a", summary="s")
    db.commit()
    resp = client.post(
        f"/ops/alerts/{alert.id}/resolve",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GDPR Export Retrieval
# ---------------------------------------------------------------------------

def _seed_completed_export(db: Session) -> int:
    """Create a merchant + completed data export, return request id."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    db.flush()

    db.add(Event(shop_domain=SHOP_A, visitor_id="export_v", event_type="product_view",
                 product_url="/products/item", timestamp=now_ms()))
    db.flush()

    export = {"request_id": 1, "shop_domain": SHOP_A, "data": {"orders": [], "events": [{"type": "view"}]}}
    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A,
        customer_id="c_test",
        status="completed",
        result_summary=json.dumps(export),
    )
    db.add(req)
    db.flush()
    return req.id


def test_list_exports_requires_auth(client):
    resp = client.get("/ops/gdpr/exports")
    assert resp.status_code == 401


def test_list_exports(client, db):
    """Operator can list data exports."""
    rid = _seed_completed_export(db)
    db.commit()
    resp = client.get("/ops/gdpr/exports", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(e["id"] == rid for e in data)


def test_list_exports_filter_by_status(client, db):
    """Status filter works."""
    _seed_completed_export(db)
    db.commit()
    resp = client.get("/ops/gdpr/exports?status=completed", headers=_op_headers())
    assert resp.status_code == 200
    assert all(e["status"] == "completed" for e in resp.json())


def test_get_export_detail(client, db):
    """Operator can retrieve full export payload."""
    rid = _seed_completed_export(db)
    db.commit()
    resp = client.get(f"/ops/gdpr/exports/{rid}", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert "export" in data
    assert data["export"]["shop_domain"] == SHOP_A


def test_get_export_requires_auth(client, db):
    rid = _seed_completed_export(db)
    db.commit()
    resp = client.get(f"/ops/gdpr/exports/{rid}")
    assert resp.status_code == 401


def test_get_export_pending_status(client, db):
    """Pending export returns note, not data."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A, customer_id="c2", status="pending",
    )
    db.add(req)
    db.flush()
    db.commit()
    resp = client.get(f"/ops/gdpr/exports/{req.id}", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert "note" in resp.json()
    assert "export" not in resp.json()


def test_get_export_failed_status(client, db):
    """Failed export returns error detail."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A, customer_id="c3", status="failed",
        error_detail="DB connection timeout",
    )
    db.add(req)
    db.flush()
    db.commit()
    resp = client.get(f"/ops/gdpr/exports/{req.id}", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Governance observability
# ---------------------------------------------------------------------------

def test_tier_check_endpoint(client):
    """GET /ops/tier-check classifies files correctly."""
    resp = client.get("/ops/tier-check?files=app/core/token_crypto.py", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == 2
    assert data["label"] == "TIER_2"
    assert data["blocked"] is True


def test_tier_check_safe_file(client):
    """Safe file returns TIER_0."""
    resp = client.get("/ops/tier-check?files=app/services/revenue_metrics.py", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["tier"] == 0
    assert resp.json()["blocked"] is False


def test_tier_check_multiple_files(client):
    """Multiple files — highest tier wins."""
    resp = client.get(
        "/ops/tier-check?files=app/services/foo.py,app/core/token_crypto.py",
        headers=_op_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["tier"] == 2


def test_file_locks_endpoint(client):
    """GET /ops/file-locks returns lock list."""
    resp = client.get("/ops/file-locks", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "active_locks" in data
    assert "count" in data
    assert isinstance(data["active_locks"], list)
