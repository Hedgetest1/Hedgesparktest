"""Tests for H3 — Shopify refund webhook ingestion (F2 v2)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.refund_ingest import (
    _parse_refund_rows,
    aggregate_product_refunds,
    ingest_refund,
    list_recent_refunds,
)


def _shop(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}.myshopify.com"


def test_parse_refund_rows_with_line_items():
    payload = {
        "id": 9001,
        "order_id": 111,
        "created_at": "2026-04-11T10:00:00",
        "note": "damaged",
        "refund_line_items": [
            {
                "line_item": {"product_id": 501, "title": "Mug", "price": "12.00"},
                "quantity": 2,
                "subtotal": "24.00",
            },
            {
                "line_item": {"product_id": 502, "title": "Tee"},
                "quantity": 1,
                "subtotal": "18.50",
            },
        ],
    }
    rows = _parse_refund_rows(payload)
    assert len(rows) == 2
    assert rows[0]["product_id"] == "501"
    assert rows[0]["amount_eur"] == 24.00
    assert rows[0]["quantity"] == 2
    assert rows[0]["reason"] == "damaged"
    assert rows[1]["product_id"] == "502"
    assert rows[1]["amount_eur"] == 18.50


def test_parse_refund_rows_falls_back_to_total():
    payload = {
        "id": 9002, "order_id": 222,
        "total_refund_set": {"shop_money": {"amount": "42.00"}},
    }
    rows = _parse_refund_rows(payload)
    assert len(rows) == 1
    assert rows[0]["amount_eur"] == 42.00
    assert rows[0]["product_id"] == ""


def test_ingest_and_aggregate():
    shop = _shop("refund-ingest")
    added = ingest_refund(shop, {
        "id": 1, "order_id": 111,
        "refund_line_items": [
            {"line_item": {"product_id": 501, "title": "Mug"}, "quantity": 1, "subtotal": "15.00"},
        ],
    })
    if added == 0:
        pytest.skip("redis unavailable")

    ingest_refund(shop, {
        "id": 2, "order_id": 112,
        "refund_line_items": [
            {"line_item": {"product_id": 501, "title": "Mug"}, "quantity": 2, "subtotal": "30.00"},
        ],
    })

    recent = list_recent_refunds(shop)
    assert len(recent) >= 2

    agg = aggregate_product_refunds(shop)
    assert "501" in agg
    assert agg["501"]["refund_count"] == 2
    assert agg["501"]["refund_qty"] == 3
    assert agg["501"]["refund_eur"] == 45.00


def test_ingest_is_idempotent():
    shop = _shop("refund-idem")
    payload = {
        "id": 777, "order_id": 888,
        "refund_line_items": [
            {"line_item": {"product_id": 601, "title": "X"}, "quantity": 1, "subtotal": "10.00"},
        ],
    }
    first = ingest_refund(shop, payload)
    if first == 0:
        pytest.skip("redis unavailable")
    second = ingest_refund(shop, payload)
    assert second == 0  # deduped by (refund_id, product_id)


def test_webhook_endpoint_rejects_missing_hmac(monkeypatch):
    from app.api import webhooks as wh_mod
    monkeypatch.setattr(wh_mod, "_WEBHOOK_SECRET", "testsecret123")
    monkeypatch.setattr(wh_mod, "_ALLOW_INSECURE_DEV", False)

    client = TestClient(app)
    r = client.post(
        "/webhooks/shopify/refunds",
        content=b'{"id":1}',
        headers={"X-Shopify-Shop-Domain": "x.myshopify.com"},
    )
    assert r.status_code == 401


def test_webhook_endpoint_ingests_with_valid_hmac(monkeypatch):
    import base64
    import hashlib
    import hmac as _hmac
    import json

    from app.api import webhooks as wh_mod
    monkeypatch.setattr(wh_mod, "_WEBHOOK_SECRET", "testsecret123")
    monkeypatch.setattr(wh_mod, "_ALLOW_INSECURE_DEV", False)

    shop = _shop("refund-webhook")
    body_obj = {
        "id": 4242, "order_id": 8080,
        "refund_line_items": [
            {"line_item": {"product_id": 701, "title": "Hat"}, "quantity": 1, "subtotal": "20.00"},
        ],
    }
    body = json.dumps(body_obj).encode()
    sig = base64.b64encode(_hmac.new(b"testsecret123", body, hashlib.sha256).digest()).decode()

    client = TestClient(app)
    r = client.post(
        "/webhooks/shopify/refunds",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": sig,
            "X-Shopify-Shop-Domain": shop,
            "X-Shopify-Topic": "refunds/create",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    body_json = r.json()
    assert body_json["ok"] is True
    if body_json.get("stored_rows", 0) == 0:
        pytest.skip("redis unavailable")

    agg = aggregate_product_refunds(shop)
    assert "701" in agg
    assert agg["701"]["refund_eur"] == 20.00


def test_refund_loss_prefers_real_data(monkeypatch):
    from app.services.refund_loss import _merge_real_refund_data

    shop = _shop("refund-merge")
    ingested = ingest_refund(shop, {
        "id": 9, "order_id": 10,
        "refund_line_items": [
            {"line_item": {"product_id": 801, "title": "Widget"}, "quantity": 1, "subtotal": "28.00"},
        ],
    })
    if ingested == 0:
        pytest.skip("redis unavailable")

    proxy_signals = [{
        "product_title": "Widget",
        "product_id": "801",
        "orders_recent_14d": 1,
        "orders_prior_14d": 5,
        "avg_price_recent": 28.0,
        "avg_price_prior": 28.0,
        "revenue_recent_14d": 28.0,
        "revenue_prior_14d": 140.0,
        "loss_eur": 240.0,
        "decline_pct": 80.0,
        "reason": "order_frequency_decline",
    }]
    merged = _merge_real_refund_data(shop, proxy_signals)
    widget = next(s for s in merged if s["product_id"] == "801")
    assert widget["reason"] == "real_shopify_refund"
    assert widget["loss_eur"] == 30.0  # 28 * (30/28)
    assert widget["refund_count_28d"] == 1
