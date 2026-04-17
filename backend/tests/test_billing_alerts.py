"""
Tests for billing.py ops_alert emissions on Shopify Billing API failures.

Pre-fix: `_create_charge` / `_fetch_charge` / `_activate_charge` logged
at log.error and returned None/False on Shopify outages. Operators saw
nothing on the ops dashboard; they found out from merchant support
tickets. This test suite locks in the observability contract:

  1. HTTP non-2xx (503/500/etc.) → ops_alert severity=warning
  2. httpx raises                → ops_alert with exception_type populated
  3. 404 on fetch_charge        → NO alert (expected when charge expired)
  4. activate_charge failures   → severity=critical (merchant already paid)
  5. alerting crash is non-fatal → billing flow never blocked

We patch httpx.AsyncClient at the call-site level so no real network
traffic is issued. Alert writes go through the real write_alert path
so we observe the ops_alerts row being created.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.api.billing import (
    _activate_charge,
    _create_charge,
    _fetch_charge,
    _write_billing_alert,
)
from app.core.database import SessionLocal


SHOP = "billing-alert-test.myshopify.com"
TOKEN = "test_token_xyz"
CHARGE_ID = "999999"


@pytest.fixture(autouse=True)
def _cleanup():
    """Purge any ops_alert rows this test might leave behind."""
    s = SessionLocal()
    try:
        s.execute(
            text("DELETE FROM ops_alerts WHERE shop_domain = :s OR source = 'billing'"),
            {"s": SHOP},
        )
        s.commit()
    finally:
        s.close()
    yield
    s = SessionLocal()
    try:
        s.execute(
            text("DELETE FROM ops_alerts WHERE shop_domain = :s OR source = 'billing'"),
            {"s": SHOP},
        )
        s.commit()
    finally:
        s.close()


def _count_billing_alerts(alert_type: str = "billing_api_failure") -> list[dict]:
    """Fetch billing alerts and parse the `detail` JSON column into a dict."""
    s = SessionLocal()
    try:
        rows = s.execute(
            text(
                "SELECT severity, alert_type, source, summary, detail, shop_domain "
                "FROM ops_alerts "
                "WHERE alert_type = :t AND source = 'billing' "
                "ORDER BY id DESC"
            ),
            {"t": alert_type},
        ).fetchall()
        results = []
        for r in rows:
            row = dict(r._mapping)
            # detail is persisted as JSON text — parse so tests can index it
            raw = row.get("detail")
            if isinstance(raw, str):
                try:
                    row["detail"] = json.loads(raw)
                except json.JSONDecodeError:
                    row["detail"] = {}
            elif raw is None:
                row["detail"] = {}
            results.append(row)
        return results
    finally:
        s.close()


def _mock_httpx_response(status_code: int, body: str = "{}"):
    """Build a MagicMock that quacks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.json = MagicMock(return_value={"error": "test"} if status_code >= 400 else {})
    return resp


def _patch_httpx_client(return_value=None, side_effect=None):
    """Patch httpx.AsyncClient inside billing.py with an async mock."""
    mock_client_instance = MagicMock()
    async_ctx = AsyncMock()
    async_ctx.__aenter__.return_value = mock_client_instance
    async_ctx.__aexit__.return_value = None

    if side_effect is not None:
        mock_client_instance.post = AsyncMock(side_effect=side_effect)
        mock_client_instance.get = AsyncMock(side_effect=side_effect)
    else:
        mock_client_instance.post = AsyncMock(return_value=return_value)
        mock_client_instance.get = AsyncMock(return_value=return_value)

    return patch("app.api.billing.httpx.AsyncClient", return_value=async_ctx)


# ---------------------------------------------------------------------------
# HTTP non-2xx path
# ---------------------------------------------------------------------------


def test_create_charge_503_writes_warning_alert():
    """Shopify returns 503 → warning-level ops_alert surfaces to operators."""
    with _patch_httpx_client(return_value=_mock_httpx_response(503, "Service Unavailable")):
        result = asyncio.run(_create_charge(SHOP, TOKEN))

    assert result is None
    alerts = _count_billing_alerts()
    assert len(alerts) == 1, f"expected 1 alert, got {len(alerts)}"
    alert = alerts[0]
    assert alert["severity"] == "warning"
    assert alert["source"] == "billing"
    assert "create_charge" in alert["summary"]
    assert "status=503" in alert["summary"]
    assert alert["shop_domain"] == SHOP
    assert alert["detail"]["operation"] == "create_charge"
    assert alert["detail"]["http_status"] == 503


def test_fetch_charge_500_writes_warning_alert():
    """fetch_charge with 500 surfaces as a warning alert."""
    with _patch_httpx_client(return_value=_mock_httpx_response(500)):
        result = asyncio.run(_fetch_charge(SHOP, TOKEN, CHARGE_ID))

    assert result is None
    alerts = _count_billing_alerts()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "warning"
    assert alerts[0]["detail"]["charge_id"] == CHARGE_ID


def test_fetch_charge_404_does_NOT_alert():
    """404 is expected (charge expired/deleted) — noisy if we alerted."""
    with _patch_httpx_client(return_value=_mock_httpx_response(404)):
        result = asyncio.run(_fetch_charge(SHOP, TOKEN, CHARGE_ID))

    assert result is None
    alerts = _count_billing_alerts()
    assert len(alerts) == 0, "404 is expected and must not alert"


# ---------------------------------------------------------------------------
# httpx exception path
# ---------------------------------------------------------------------------


def test_create_charge_httpx_raise_writes_alert_with_exception_type():
    """Transient network error: httpx raises → alert captures exception_type."""
    import httpx
    err = httpx.ConnectTimeout("simulated connect timeout")
    with _patch_httpx_client(side_effect=err):
        result = asyncio.run(_create_charge(SHOP, TOKEN))

    assert result is None
    alerts = _count_billing_alerts()
    assert len(alerts) == 1
    assert alerts[0]["detail"]["exception_type"] == "ConnectTimeout"
    assert alerts[0]["detail"]["http_status"] is None


# ---------------------------------------------------------------------------
# activate_charge — higher-severity path
# ---------------------------------------------------------------------------


def test_activate_charge_failure_is_critical_not_warning():
    """
    activate_charge failure is higher-impact: merchant already approved
    on Shopify's billing page but we can't flip the local state to
    billing_active=True. Operators must page-respond, not batch-handle.
    """
    with _patch_httpx_client(return_value=_mock_httpx_response(502, "Bad Gateway")):
        result = asyncio.run(_activate_charge(SHOP, TOKEN, CHARGE_ID))

    assert result is False
    alerts = _count_billing_alerts()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical", (
        "activate_charge failures MUST be critical severity — merchant "
        "has already paid on Shopify's side"
    )
    assert alerts[0]["detail"]["operation"] == "activate_charge"


def test_activate_charge_httpx_raise_is_critical():
    """Exception path on activate is also critical severity."""
    import httpx
    with _patch_httpx_client(side_effect=httpx.ReadTimeout("read timeout")):
        result = asyncio.run(_activate_charge(SHOP, TOKEN, CHARGE_ID))

    assert result is False
    alerts = _count_billing_alerts()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"


# ---------------------------------------------------------------------------
# Alerting crash must not propagate
# ---------------------------------------------------------------------------


def test_billing_flow_survives_write_alert_crash():
    """
    Observability is a nice-to-have; billing is load-bearing. If the
    alerting subsystem itself crashes (DB down, sentry misconfigured),
    _create_charge MUST still return None cleanly so the merchant
    sees the retry page, not a 500.
    """
    with patch("app.services.alerting.write_alert", side_effect=RuntimeError("boom")):
        with _patch_httpx_client(return_value=_mock_httpx_response(503)):
            result = asyncio.run(_create_charge(SHOP, TOKEN))

    # The billing flow completes cleanly: None (not an exception).
    assert result is None


# ---------------------------------------------------------------------------
# _write_billing_alert unit behavior
# ---------------------------------------------------------------------------


def test_write_billing_alert_unit_direct():
    """Direct unit test: helper writes a row we can read back."""
    _write_billing_alert(
        operation="create_charge",
        shop=SHOP,
        http_status=429,
        error_body="rate limited",
        severity="warning",
    )
    alerts = _count_billing_alerts()
    assert len(alerts) == 1
    assert alerts[0]["detail"]["http_status"] == 429
    assert "rate limited" in (alerts[0]["detail"].get("error_body") or "")


def test_write_billing_alert_truncates_long_body():
    """Long error bodies are truncated to 200 chars to cap detail size."""
    long_body = "x" * 5000
    _write_billing_alert(
        operation="fetch_charge",
        shop=SHOP,
        http_status=500,
        error_body=long_body,
    )
    alerts = _count_billing_alerts()
    assert len(alerts) == 1
    assert len(alerts[0]["detail"]["error_body"]) == 200
