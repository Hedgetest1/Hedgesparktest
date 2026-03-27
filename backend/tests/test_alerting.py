"""Tests for operational alerting (alerting.py + OpsAlert model)."""
from sqlalchemy import text

from app.services.alerting import write_alert, get_unresolved_alerts, resolve_alert
from tests.conftest import SHOP_A


def test_write_alert_persists(db):
    """write_alert creates a row with all fields."""
    alert = write_alert(
        db,
        severity="warning",
        source="test_runner",
        alert_type="test_alert",
        summary="Test alert summary",
        shop_domain=SHOP_A,
        detail={"key": "value"},
    )
    assert alert.id is not None
    assert alert.severity == "warning"
    assert alert.resolved is False

    row = db.execute(text(
        "SELECT severity, alert_type, resolved FROM ops_alerts WHERE id = :id"
    ), {"id": alert.id}).fetchone()
    assert row is not None
    assert row[0] == "warning"


def test_get_unresolved_alerts(db):
    """get_unresolved_alerts returns only unresolved alerts."""
    write_alert(db, severity="info", source="t", alert_type="a", summary="open")
    write_alert(db, severity="critical", source="t", alert_type="b", summary="also open")
    db.flush()

    alerts = get_unresolved_alerts(db)
    assert len(alerts) >= 2
    assert all(not a.resolved for a in alerts)


def test_get_unresolved_filtered_by_severity(db):
    """Severity filter works."""
    write_alert(db, severity="info", source="t", alert_type="a", summary="info")
    write_alert(db, severity="critical", source="t", alert_type="b", summary="crit")
    db.flush()

    crits = get_unresolved_alerts(db, severity="critical")
    assert all(a.severity == "critical" for a in crits)


def test_resolve_alert_marks_resolved(db):
    """resolve_alert sets resolved=True and resolved_at."""
    alert = write_alert(db, severity="warning", source="t", alert_type="a", summary="fix me")
    db.flush()
    assert alert.resolved is False

    resolve_alert(db, alert.id)
    db.flush()

    row = db.execute(text(
        "SELECT resolved, resolved_at FROM ops_alerts WHERE id = :id"
    ), {"id": alert.id}).fetchone()
    assert row[0] is True
    assert row[1] is not None
