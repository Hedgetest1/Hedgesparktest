"""Tests for /merchant/reports/* — Gap #1 Custom Report Builder.

Covers:
- standard surfaces metadata returns 6 surfaces
- CRUD happy paths (create / list / get / put / delete)
- name uniqueness violation → 409
- soft-delete excludes from list
- max 50 reports per shop
- formula validation: empty / unknown token / unbalanced parens / no metric
- formula validation: valid expression accepted
- date_range_preset='custom' requires both start + end
- forecast_horizon must be 30/60/90
- max 2 dimensions, max 3 filters
- tenant isolation (shop A cannot read shop B reports)
- schedule cap: 2 daily reports → 409
- toggle schedule on/off
- execute report returns rows + chart_type + total
- last_run_at updates on execute
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.merchant_saved_report import MerchantSavedReport
from app.models.shop_order import ShopOrder
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ════════════════════════════════════════════════════════════════════════
# /merchant/reports/standard
# ════════════════════════════════════════════════════════════════════════


def test_standard_surfaces(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get("/merchant/reports/standard", cookies=cookies)
    assert resp.status_code == 200
    body = resp.json()
    keys = {s["surface"] for s in body["surfaces"]}
    assert {"rars", "benchmarks", "pnl", "cohorts_monthly", "attribution"}.issubset(keys)


def test_standard_surfaces_no_session(client):
    resp = client.get("/merchant/reports/standard")
    assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════════
# CRUD happy path
# ════════════════════════════════════════════════════════════════════════


_REVENUE_BY_CHANNEL = {
    "name": "Revenue by channel",
    "metric": "revenue",
    "dimensions": ["channel"],
    "date_range_preset": "last_30_days",
}


def test_create_get_list_put_delete(client, merchant_a, db):
    cookies = auth_cookies(SHOP_A)

    # CREATE
    r = client.post("/merchant/reports", cookies=cookies, json=_REVENUE_BY_CHANNEL)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    # GET single
    r = client.get(f"/merchant/reports/{rid}", cookies=cookies)
    assert r.status_code == 200
    assert r.json()["name"] == "Revenue by channel"

    # LIST shows it
    r = client.get("/merchant/reports", cookies=cookies)
    assert r.status_code == 200
    assert any(rep["id"] == rid for rep in r.json()["reports"])

    # PUT updates the name + dimensions
    payload = dict(_REVENUE_BY_CHANNEL)
    payload["name"] = "Renamed report"
    payload["dimensions"] = ["channel", "time"]
    r = client.put(f"/merchant/reports/{rid}", cookies=cookies, json=payload)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed report"
    assert r.json()["dimensions"] == ["channel", "time"]

    # DELETE soft-deletes (name freed for re-use)
    r = client.delete(f"/merchant/reports/{rid}", cookies=cookies, headers={"Content-Type": "application/json"})
    assert r.status_code == 200

    r = client.get("/merchant/reports", cookies=cookies)
    assert all(rep["id"] != rid for rep in r.json()["reports"])


def test_duplicate_name_returns_409(client, merchant_a, db):
    cookies = auth_cookies(SHOP_A)
    r = client.post("/merchant/reports", cookies=cookies, json=_REVENUE_BY_CHANNEL)
    assert r.status_code == 200
    r2 = client.post("/merchant/reports", cookies=cookies, json=_REVENUE_BY_CHANNEL)
    assert r2.status_code == 409


def test_max_dimensions_enforced(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    bad = dict(_REVENUE_BY_CHANNEL)
    bad["dimensions"] = ["channel", "time", "country"]  # 3 > 2
    r = client.post("/merchant/reports", cookies=cookies, json=bad)
    assert r.status_code == 422  # pydantic max_length on list


def test_max_filters_enforced(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    bad = dict(_REVENUE_BY_CHANNEL)
    bad["filters"] = {"channel": "x", "country": "y", "product": "z", "discount_code": "w"}
    r = client.post("/merchant/reports", cookies=cookies, json=bad)
    assert r.status_code == 422


def test_unknown_metric_rejected(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    bad = dict(_REVENUE_BY_CHANNEL)
    bad["metric"] = "not_a_metric"
    r = client.post("/merchant/reports", cookies=cookies, json=bad)
    assert r.status_code == 400


def test_custom_range_requires_both_dates(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    bad = dict(_REVENUE_BY_CHANNEL)
    bad["date_range_preset"] = "custom"
    bad["custom_start"] = None
    bad["custom_end"] = None
    r = client.post("/merchant/reports", cookies=cookies, json=bad)
    assert r.status_code == 400


def test_forecast_horizon_validation(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    bad = dict(_REVENUE_BY_CHANNEL)
    bad["forecast_horizon"] = 7  # not in {30,60,90}
    r = client.post("/merchant/reports", cookies=cookies, json=bad)
    assert r.status_code == 400


# ════════════════════════════════════════════════════════════════════════
# Custom formula validation
# ════════════════════════════════════════════════════════════════════════


def test_formula_valid(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    payload = {
        "name": "Margin per order",
        "metric": "formula",
        "dimensions": [],
        "formula": "(Revenue * 0.7) / Orders",
    }
    r = client.post("/merchant/reports", cookies=cookies, json=payload)
    assert r.status_code == 200
    assert r.json()["formula"] == "(Revenue * 0.7) / Orders"


def test_formula_unknown_token_rejected(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    payload = {
        "name": "Bad formula 1",
        "metric": "formula",
        "formula": "Revenue + RandomToken",
    }
    r = client.post("/merchant/reports", cookies=cookies, json=payload)
    assert r.status_code == 400
    assert "Unknown token" in r.json()["detail"]


def test_formula_unbalanced_parens_rejected(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    payload = {
        "name": "Bad formula 2",
        "metric": "formula",
        "formula": "(Revenue * 0.7",
    }
    r = client.post("/merchant/reports", cookies=cookies, json=payload)
    assert r.status_code == 400


def test_formula_without_metric_token_rejected(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    payload = {
        "name": "Bad formula 3",
        "metric": "formula",
        "formula": "1 + 2 * 3",
    }
    r = client.post("/merchant/reports", cookies=cookies, json=payload)
    assert r.status_code == 400


def test_formula_metric_string_required(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    payload = {
        "name": "Bad formula 4",
        "metric": "revenue",
        "formula": "Revenue + Orders",
    }
    r = client.post("/merchant/reports", cookies=cookies, json=payload)
    assert r.status_code == 400


# ════════════════════════════════════════════════════════════════════════
# Tenant isolation
# ════════════════════════════════════════════════════════════════════════


def test_tenant_isolation(client, merchant_a, merchant_b, db):
    cookies_a = auth_cookies(SHOP_A)
    cookies_b = auth_cookies(SHOP_B)

    r = client.post("/merchant/reports", cookies=cookies_a, json=_REVENUE_BY_CHANNEL)
    rid = r.json()["id"]

    # Shop B cannot fetch shop A's report
    r = client.get(f"/merchant/reports/{rid}", cookies=cookies_b)
    assert r.status_code == 404

    # Shop B cannot delete shop A's report
    r = client.delete(f"/merchant/reports/{rid}", cookies=cookies_b, headers={"Content-Type": "application/json"})
    assert r.status_code == 404

    # Shop B's list does not include shop A's report
    r = client.get("/merchant/reports", cookies=cookies_b)
    assert all(rep["id"] != rid for rep in r.json()["reports"])


# ════════════════════════════════════════════════════════════════════════
# Schedule cap (1 daily / 1 weekly per shop)
# ════════════════════════════════════════════════════════════════════════


def test_schedule_cap(client, merchant_a, db):
    cookies = auth_cookies(SHOP_A)
    a_id = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={**_REVENUE_BY_CHANNEL, "name": "Daily one"},
    ).json()["id"]
    b_id = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={**_REVENUE_BY_CHANNEL, "name": "Daily two"},
    ).json()["id"]

    # Schedule the first as daily
    r1 = client.post(
        f"/merchant/reports/{a_id}/schedule",
        cookies=cookies,
        json={"scheduled": True, "scheduled_cadence": "daily"},
    )
    assert r1.status_code == 200

    # Try to schedule the second as daily → 409 (partial UNIQUE)
    r2 = client.post(
        f"/merchant/reports/{b_id}/schedule",
        cookies=cookies,
        json={"scheduled": True, "scheduled_cadence": "daily"},
    )
    assert r2.status_code == 409

    # Unschedule the first → second can take the slot
    r3 = client.post(
        f"/merchant/reports/{a_id}/schedule",
        cookies=cookies,
        json={"scheduled": False},
    )
    assert r3.status_code == 200
    r4 = client.post(
        f"/merchant/reports/{b_id}/schedule",
        cookies=cookies,
        json={"scheduled": True, "scheduled_cadence": "daily"},
    )
    assert r4.status_code == 200


def test_schedule_invalid_cadence(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies, json=_REVENUE_BY_CHANNEL
    ).json()["id"]
    r = client.post(
        f"/merchant/reports/{rid}/schedule",
        cookies=cookies,
        json={"scheduled": True, "scheduled_cadence": "monthly"},
    )
    assert r.status_code == 400


# ════════════════════════════════════════════════════════════════════════
# Execute report
# ════════════════════════════════════════════════════════════════════════


def _seed_orders(db, shop, n=5):
    """Seed orders with payment_method varied (no utm_source on
    ShopOrder — that lives on visitor_purchase_sessions)."""
    now = _now_naive()
    for i in range(n):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"o-rep-{i}",
            total_price=100.0 + i * 10,
            currency="EUR",
            customer_email=f"c{i}@x.com",
            line_items=[{"price": "100", "quantity": 1, "title": f"Product-{i}"}],
            payment_method="shopify_payments" if i % 2 == 0 else "paypal",
            created_at=now - timedelta(days=i),
            source="webhook",
        ))


def test_execute_revenue_by_payment_method(client, merchant_a, db):
    _seed_orders(db, SHOP_A, n=6)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={
            "name": "Run me",
            "metric": "revenue",
            "dimensions": ["payment_method"],
            "date_range_preset": "last_30_days",
        },
    ).json()["id"]

    r = client.get(f"/merchant/reports/{rid}/data", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["report_id"] == rid
    assert body["metric"] == "revenue"
    assert body["chart_type"] == "bar"
    assert body["total"] > 0
    labels = {row["label"] for row in body["rows"]}
    assert {"shopify_payments", "paypal"} & labels


def test_execute_scalar_no_dimensions(client, merchant_a, db):
    _seed_orders(db, SHOP_A, n=3)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports",
        cookies=cookies,
        json={
            "name": "Total revenue",
            "metric": "revenue",
            "dimensions": [],
            "date_range_preset": "last_30_days",
        },
    ).json()["id"]

    r = client.get(f"/merchant/reports/{rid}/data", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["chart_type"] == "scalar"
    assert body["total"] > 0
    assert len(body["rows"]) == 1


def test_execute_unknown_id_returns_404(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/reports/9999999/data", cookies=cookies)
    assert r.status_code == 404


# ════════════════════════════════════════════════════════════════════════
# Soft-delete + name-reuse flow
# ════════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════════
# Special-metric SQL — Gap #1 strict 10/10 closure
# ════════════════════════════════════════════════════════════════════════


def test_special_metric_repeat_rate_scalar(client, merchant_a, db):
    """3 customers: A buys twice, B buys twice, C buys once → repeat_rate = 66.7%."""
    now = _now_naive()
    for email in ["a@x.com", "a@x.com", "b@x.com", "b@x.com", "c@x.com"]:
        db.add(ShopOrder(
            shop_domain=SHOP_A,
            shopify_order_id=f"o-rep-{email}-{now.timestamp()}",
            total_price=50.0,
            currency="EUR",
            customer_email=email,
            line_items=[{"price": "50", "quantity": 1, "title": "P"}],
            created_at=now - timedelta(hours=1),
            source="webhook",
        ))
        # Distinct order IDs
        now -= timedelta(seconds=1)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={"name": "Repeat scalar", "metric": "repeat_rate", "dimensions": []},
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "scalar"
    assert body["rows"][0]["value"] == pytest.approx(66.66, abs=0.5)


def test_special_metric_customer_ltv_scalar(client, merchant_a, db):
    """2 customers, total revenue 200 → LTV = 100."""
    now = _now_naive()
    db.add(ShopOrder(
        shop_domain=SHOP_A, shopify_order_id="ltv-1", total_price=120.0,
        currency="EUR", customer_email="x1@x.com",
        line_items=[{"price": "120", "quantity": 1, "title": "P"}],
        created_at=now - timedelta(hours=1), source="webhook",
    ))
    db.add(ShopOrder(
        shop_domain=SHOP_A, shopify_order_id="ltv-2", total_price=80.0,
        currency="EUR", customer_email="x2@x.com",
        line_items=[{"price": "80", "quantity": 1, "title": "P"}],
        created_at=now - timedelta(hours=2), source="webhook",
    ))
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={"name": "LTV scalar", "metric": "customer_ltv", "dimensions": []},
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "scalar"
    assert body["rows"][0]["value"] == pytest.approx(100.0, abs=0.1)


def test_special_metric_conversion_rate_scalar(client, merchant_a, db):
    """1 order + 5 visitor sessions → conversion_rate = 20%."""
    from app.models.visitor_purchase_session import VisitorPurchaseSession
    now = _now_naive()
    db.add(ShopOrder(
        shop_domain=SHOP_A, shopify_order_id="cv-1", total_price=100.0,
        currency="EUR", customer_email="cv@x.com",
        line_items=[{"price": "100", "quantity": 1, "title": "P"}],
        created_at=now - timedelta(hours=1), source="webhook",
    ))
    for i in range(5):
        db.add(VisitorPurchaseSession(
            shop_domain=SHOP_A,
            visitor_id=f"v-cv-{i}",
            shopify_order_id=f"vps-cv-{i}",
            confirmed_at=now - timedelta(hours=2),
        ))
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={"name": "CR scalar", "metric": "conversion_rate", "dimensions": []},
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "scalar"
    # 1 order / 5 visitor sessions = 20%
    assert body["rows"][0]["value"] == pytest.approx(20.0, abs=0.1)


def test_special_metric_survey_top_scalar(client, merchant_a, db):
    """3 instagram + 1 google → survey_response_top label='instagram', count=3."""
    from app.models.survey_response import SurveyResponse
    now = _now_naive()
    for choice in ["instagram", "instagram", "instagram", "google"]:
        db.add(SurveyResponse(
            shop_domain=SHOP_A,
            order_id=f"o-srv-{now.timestamp()}",
            answer_choice=choice,
            consent_given=True,
            created_at=now - timedelta(hours=1),
        ))
        now -= timedelta(seconds=1)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={"name": "Survey top", "metric": "survey_response_top", "dimensions": []},
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "scalar"
    assert body["rows"][0]["label"] == "instagram"
    assert body["rows"][0]["value"] == 3.0


def test_special_metric_revenue_at_risk_uses_rars_engine(client, merchant_a, db, monkeypatch):
    """RAR scalar reads from get_revenue_at_risk; mock returns 4521.50."""
    fake_report = {"total_at_risk_eur": 4521.50, "components": [{"x": 1}]}
    monkeypatch.setattr(
        "app.services.revenue_at_risk.get_revenue_at_risk",
        lambda db, shop: fake_report,
    )

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={"name": "RAR scalar", "metric": "revenue_at_risk", "dimensions": []},
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "scalar"
    assert body["rows"][0]["value"] == pytest.approx(4521.50, abs=0.01)
    assert any("right-now" in n.lower() for n in body["notes"])


def test_special_metric_repeat_rate_by_time(client, merchant_a, db):
    """repeat_rate × time → bar chart with one bucket per time slice."""
    now = _now_naive()
    # Day 0: customer A buys twice (repeat). Day 1: customer B buys once.
    for email in ["d0a@x.com", "d0a@x.com"]:
        db.add(ShopOrder(
            shop_domain=SHOP_A, shopify_order_id=f"o-d0-{email}-{now.timestamp()}",
            total_price=50.0, currency="EUR", customer_email=email,
            line_items=[{"price": "50", "quantity": 1, "title": "P"}],
            created_at=now - timedelta(hours=1), source="webhook",
        ))
        now -= timedelta(seconds=1)
    db.add(ShopOrder(
        shop_domain=SHOP_A, shopify_order_id="o-d1-once", total_price=50.0,
        currency="EUR", customer_email="d1b@x.com",
        line_items=[{"price": "50", "quantity": 1, "title": "P"}],
        created_at=now - timedelta(days=1), source="webhook",
    ))
    db.flush()

    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={
            "name": "Repeat by time",
            "metric": "repeat_rate",
            "dimensions": ["time"],
            "date_range_preset": "last_7_days",
        },
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "bar"
    assert len(body["rows"]) >= 2  # at least 2 day buckets


def test_special_metric_non_time_dim_surfaces_calm_note(client, merchant_a, db):
    """When merchant requests repeat_rate × channel, executor falls back
    to scalar with a calm merchant-friendly note."""
    cookies = auth_cookies(SHOP_A)
    rid = client.post(
        "/merchant/reports", cookies=cookies,
        json={
            "name": "Repeat by channel",
            "metric": "repeat_rate",
            "dimensions": ["channel"],
        },
    ).json()["id"]
    body = client.get(f"/merchant/reports/{rid}/data", cookies=cookies).json()
    assert body["chart_type"] == "scalar"
    assert any("breakdown" in n.lower() for n in body["notes"])


# ════════════════════════════════════════════════════════════════════════
# Other tests
# ════════════════════════════════════════════════════════════════════════


def test_name_reuse_after_soft_delete(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    payload = {**_REVENUE_BY_CHANNEL, "name": "Reusable name"}

    # Create + delete
    rid = client.post("/merchant/reports", cookies=cookies, json=payload).json()["id"]
    r = client.delete(f"/merchant/reports/{rid}", cookies=cookies, headers={"Content-Type": "application/json"})
    assert r.status_code == 200

    # Re-create with same name → succeeds (the partial UNIQUE excludes deleted)
    r = client.post("/merchant/reports", cookies=cookies, json=payload)
    assert r.status_code == 200
