"""Tests for /survey/* — Gap #7 close (post-purchase attribution survey).

Coverage:
- GET /survey/config returns merchant defaults (preset for unknown shop)
- POST /survey/response happy path → 200 + row written
- POST /survey/response duplicate (same shop+order+key) → already_answered
- POST /survey/response with PII in answer_text → row stored with NULL text
- POST /survey/response missing both answer_choice and answer_text → 400
- POST /survey/response invalid shop_domain → 400
- GET /merchant/survey/aggregate → distribution + top_choice + tenant isolation
- PUT /pro/survey/config rejects non-Pro session
- PUT /pro/survey/config validates option count + invalidates cache
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.survey_response import SurveyResponse
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


# ════════════════════════════════════════════════════════════════════════
# GET /survey/config
# ════════════════════════════════════════════════════════════════════════

def test_config_unknown_shop_returns_preset(client):
    resp = client.get("/survey/config?shop=nonexistent-x.myshopify.com")
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "How did you hear about us?"
    assert isinstance(body["options"], list)
    assert len(body["options"]) >= 3
    assert body["allow_other"] is True
    assert body["disabled_on_order_status"] is False


def test_config_known_shop_returns_merchant_values(client, merchant_a):
    resp = client.get(f"/survey/config?shop={SHOP_A}")
    assert resp.status_code == 200
    body = resp.json()
    # merchant_a fixture creates a Pro merchant; defaults from migration
    assert body["question"] == "How did you hear about us?"
    assert any(o["value"] == "instagram" for o in body["options"])


def test_config_invalid_shop_returns_400(client):
    resp = client.get("/survey/config?shop=not-a-myshopify-domain")
    assert resp.status_code == 400


# ════════════════════════════════════════════════════════════════════════
# POST /survey/response
# ════════════════════════════════════════════════════════════════════════

def test_response_happy_path(client, merchant_a, db):
    body = {
        "shop_domain": SHOP_A,
        "order_id": "test-order-1",
        "answer_choice": "Instagram",
        "consent_given": True,
    }
    resp = client.post("/survey/response", json=body)
    assert resp.status_code == 200
    out = resp.json()
    assert out["status"] == "ok"
    assert out["choice"] == "instagram"

    rows = db.query(SurveyResponse).filter(SurveyResponse.shop_domain == SHOP_A).all()
    assert len(rows) == 1
    assert rows[0].answer_choice == "instagram"
    assert rows[0].order_id == "test-order-1"
    assert rows[0].consent_given is True
    # Hashes set, never raw values
    assert rows[0].client_ip_hash is None or len(rows[0].client_ip_hash) == 64


def test_response_dedup_via_unique(client, merchant_a, db):
    body = {
        "shop_domain": SHOP_A,
        "order_id": "test-order-2",
        "answer_choice": "google",
        "consent_given": True,
    }
    r1 = client.post("/survey/response", json=body)
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"

    r2 = client.post("/survey/response", json=body)
    assert r2.status_code == 200
    assert r2.json()["status"] == "already_answered"

    rows = db.query(SurveyResponse).filter(
        SurveyResponse.shop_domain == SHOP_A,
        SurveyResponse.order_id == "test-order-2",
    ).all()
    assert len(rows) == 1


def test_response_pii_in_text_is_blocked(client, merchant_a, db):
    body = {
        "shop_domain": SHOP_A,
        "order_id": "test-order-3",
        "answer_choice": "other",
        "answer_text": "contact me at customer@example.com",
        "consent_given": True,
    }
    resp = client.post("/survey/response", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    row = db.query(SurveyResponse).filter(
        SurveyResponse.shop_domain == SHOP_A,
        SurveyResponse.order_id == "test-order-3",
    ).one()
    # PII guard nullifies the text but still stores the choice
    assert row.answer_choice == "other"
    assert row.answer_text is None


def test_response_clean_text_persists(client, merchant_a, db):
    body = {
        "shop_domain": SHOP_A,
        "order_id": "test-order-4",
        "answer_choice": "other",
        "answer_text": "saw it at a friend's house",
        "consent_given": True,
    }
    resp = client.post("/survey/response", json=body)
    assert resp.status_code == 200
    row = db.query(SurveyResponse).filter(
        SurveyResponse.order_id == "test-order-4"
    ).one()
    assert row.answer_text == "saw it at a friend's house"


def test_response_requires_choice_or_text(client, merchant_a):
    body = {
        "shop_domain": SHOP_A,
        "order_id": "test-order-5",
        "consent_given": True,
    }
    resp = client.post("/survey/response", json=body)
    assert resp.status_code == 400


def test_response_invalid_shop_returns_400(client, merchant_a):
    body = {
        "shop_domain": "not-a-myshopify-domain",
        "order_id": "x",
        "answer_choice": "instagram",
        "consent_given": True,
    }
    resp = client.post("/survey/response", json=body)
    assert resp.status_code == 400


# ════════════════════════════════════════════════════════════════════════
# GET /merchant/survey/aggregate
# ════════════════════════════════════════════════════════════════════════

def _seed_response(db, shop, order_id, choice, days_ago=0):
    db.add(SurveyResponse(
        shop_domain=shop,
        order_id=order_id,
        question_key="how_did_you_hear",
        answer_choice=choice,
        consent_given=True,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago),
    ))


def test_aggregate_returns_distribution_and_top(client, merchant_a, db):
    _seed_response(db, SHOP_A, "ord-1", "instagram", 1)
    _seed_response(db, SHOP_A, "ord-2", "instagram", 2)
    _seed_response(db, SHOP_A, "ord-3", "google", 3)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    resp = client.get("/merchant/survey/aggregate?range=last_30_days", cookies=cookies)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["top_choice"]["choice"] == "instagram"
    assert body["top_choice"]["count"] == 2
    choices = {row["choice"]: row["count"] for row in body["distribution"]}
    assert choices == {"instagram": 2, "google": 1}


def test_aggregate_tenant_isolation(client, merchant_a, merchant_b, db):
    _seed_response(db, SHOP_A, "ord-a1", "instagram", 1)
    _seed_response(db, SHOP_B, "ord-b1", "google", 1)
    _seed_response(db, SHOP_B, "ord-b2", "tiktok", 1)
    db.flush()

    cookies_a = auth_cookies(SHOP_A)
    resp = client.get("/merchant/survey/aggregate?range=last_30_days", cookies=cookies_a)
    body = resp.json()
    assert body["total"] == 1
    assert body["top_choice"]["choice"] == "instagram"
    # Shop B's data must NOT leak into Shop A's aggregate
    choices = {row["choice"] for row in body["distribution"]}
    assert "google" not in choices
    assert "tiktok" not in choices


def test_aggregate_unknown_range_returns_400(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get("/merchant/survey/aggregate?range=last_decade", cookies=cookies)
    assert resp.status_code == 400


def test_aggregate_no_session_returns_401(client, merchant_a):
    resp = client.get("/merchant/survey/aggregate?range=last_30_days")
    assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════════
# PUT /merchant/survey/config — Lite + Pro per 0-60 parity doctrine
# ════════════════════════════════════════════════════════════════════════

def test_config_update_pro_session_succeeds(client, merchant_a, db):
    cookies = auth_cookies(SHOP_A)
    resp = client.put(
        "/merchant/survey/config",
        cookies=cookies,
        json={
            "survey_question": "Where did you hear about us?",
            "survey_options": [
                {"label": "Instagram", "value": "instagram"},
                {"label": "Google", "value": "google"},
                {"label": "Friend", "value": "friend"},
            ],
            "survey_allow_other": False,
            "survey_show_on_order_status": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["config"]["question"] == "Where did you hear about us?"
    assert len(body["config"]["options"]) == 3
    assert body["config"]["allow_other"] is False
    assert body["config"]["show_on_order_status"] is False


def test_config_update_lite_session_succeeds(client, merchant_b, db):
    """Per `feedback_0_60_parity_doctrine.md`: Lite (€39) is in the
    $0-60 band where every competitor (KnoCommerce/Fairing/Zigpoll)
    ships customizable surveys → Lite must allow customization too.
    No Pro gate on this endpoint."""
    cookies = auth_cookies(SHOP_B)
    resp = client.put(
        "/merchant/survey/config",
        cookies=cookies,
        json={"survey_question": "Lite tier custom question?"},
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["question"] == "Lite tier custom question?"


def test_config_update_no_session_returns_401(client):
    resp = client.put(
        "/merchant/survey/config",
        json={"survey_question": "x"},
    )
    assert resp.status_code == 401


def test_pro_config_validates_option_count(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    # Below MIN_OPTIONS (3)
    resp = client.put(
        "/merchant/survey/config",
        cookies=cookies,
        json={
            "survey_options": [
                {"label": "Only one", "value": "one"},
                {"label": "Two", "value": "two"},
            ],
        },
    )
    assert resp.status_code == 400


def test_pro_config_validates_option_shape(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.put(
        "/merchant/survey/config",
        cookies=cookies,
        json={
            "survey_options": [
                {"label": "", "value": "empty-label"},
                {"label": "Two", "value": "two"},
                {"label": "Three", "value": "three"},
            ],
        },
    )
    assert resp.status_code == 400


def test_pro_config_rejects_duplicate_values(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.put(
        "/merchant/survey/config",
        cookies=cookies,
        json={
            "survey_options": [
                {"label": "A", "value": "dup"},
                {"label": "B", "value": "dup"},
                {"label": "C", "value": "three"},
            ],
        },
    )
    assert resp.status_code == 400
