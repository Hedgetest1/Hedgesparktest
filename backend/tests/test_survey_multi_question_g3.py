"""Multi-question survey config — G3 Lite parity gap close (2026-04-29).

KnoCommerce Free, Zigpoll Free, Fairing $15 ship multi-question post-
purchase surveys at $0-60. Backend extension: `merchants.survey_questions`
JSONB column + PUT /merchant/survey/config accepts an array.

Coverage:
  * GET /survey/config legacy single-question payload unchanged when
    survey_questions is NULL (backward compat)
  * PUT /merchant/survey/config accepts a 3-question array
  * GET /survey/config returns the array as `questions` + version=2
  * Duplicate question_key rejected
  * choice-type questions require >=2 options
  * Setting empty array (or null) resets to legacy mode
  * Tenant isolation
"""
from __future__ import annotations

from app.models.merchant import Merchant
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


def _put_config(client, cookies, payload):
    return client.put(
        "/merchant/survey/config",
        cookies=cookies,
        json=payload,
        headers={"Content-Type": "application/json"},
    )


def test_legacy_single_question_unchanged_when_array_null(client, merchant_a, auth_a, db):
    """GET /survey/config with survey_questions=NULL returns the legacy
    payload (version=1, no `questions` field populated)."""
    merchant_a.survey_questions = None
    db.flush()
    r = client.get(f"/survey/config?shop={SHOP_A}")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1
    assert body["question"]  # still has legacy single question
    assert body.get("questions") in (None, [])


def test_put_multi_question_array_accepted(client, merchant_a, auth_a):
    """PUT /merchant/survey/config with a 3-question array."""
    payload = {
        "survey_questions": [
            {
                "question_key": "how_heard",
                "question": "How did you hear about us?",
                "type": "single_choice",
                "options": [
                    {"label": "Instagram", "value": "instagram"},
                    {"label": "Friend", "value": "friend"},
                ],
                "allow_other": True,
                "position": 0,
            },
            {
                "question_key": "first_time",
                "question": "Is this your first order?",
                "type": "single_choice",
                "options": [
                    {"label": "Yes", "value": "yes"},
                    {"label": "No", "value": "no"},
                ],
                "allow_other": False,
                "position": 1,
            },
            {
                "question_key": "feedback",
                "question": "Anything we could improve?",
                "type": "text",
                "options": [],
                "allow_other": False,
                "position": 2,
            },
        ],
    }
    r = _put_config(client, auth_a, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["config"]["questions"] is not None
    assert len(body["config"]["questions"]) == 3


def test_get_config_returns_array_after_put(client, merchant_a, auth_a):
    """GET /survey/config after multi-question PUT returns version=2 +
    full questions array."""
    payload = {
        "survey_questions": [
            {
                "question_key": "primary",
                "question": "Which channel?",
                "type": "single_choice",
                "options": [
                    {"label": "A", "value": "a"},
                    {"label": "B", "value": "b"},
                ],
                "allow_other": False,
                "position": 0,
            },
        ],
    }
    _put_config(client, auth_a, payload)
    r = client.get(f"/survey/config?shop={SHOP_A}")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2
    assert isinstance(body["questions"], list)
    assert body["questions"][0]["question_key"] == "primary"


def test_duplicate_question_key_rejected(client, merchant_a, auth_a):
    payload = {
        "survey_questions": [
            {
                "question_key": "dup",
                "question": "Q1",
                "type": "single_choice",
                "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
                "allow_other": False,
                "position": 0,
            },
            {
                "question_key": "dup",
                "question": "Q2",
                "type": "single_choice",
                "options": [{"label": "Y", "value": "y"}, {"label": "N", "value": "n"}],
                "allow_other": False,
                "position": 1,
            },
        ],
    }
    r = _put_config(client, auth_a, payload)
    assert r.status_code == 400
    assert "duplicate" in r.text.lower()


def test_choice_question_requires_options(client, merchant_a, auth_a):
    """type=single_choice with empty options is rejected."""
    payload = {
        "survey_questions": [
            {
                "question_key": "bad",
                "question": "Q?",
                "type": "single_choice",
                "options": [],
                "allow_other": False,
                "position": 0,
            },
        ],
    }
    r = _put_config(client, auth_a, payload)
    assert r.status_code == 400


def test_text_question_does_not_require_options(client, merchant_a, auth_a):
    """type=text with no options is fine — text answers don't need choices."""
    payload = {
        "survey_questions": [
            {
                "question_key": "feedback",
                "question": "Anything to share?",
                "type": "text",
                "options": [],
                "allow_other": False,
                "position": 0,
            },
        ],
    }
    r = _put_config(client, auth_a, payload)
    assert r.status_code == 200


def test_empty_array_resets_to_legacy(client, merchant_a, auth_a, db):
    """Setting survey_questions to [] reverts to legacy single-question."""
    merchant_a.survey_questions = [{"question_key": "x", "question": "Q?", "type": "text", "options": [], "allow_other": False, "position": 0}]
    db.flush()
    payload = {"survey_questions": []}
    r = _put_config(client, auth_a, payload)
    assert r.status_code == 200
    db.refresh(merchant_a)
    assert merchant_a.survey_questions is None


def test_tenant_isolation_survey_questions(client, merchant_a, merchant_b, auth_a, auth_b, db):
    """PUT on shop A doesn't affect shop B's config."""
    payload = {
        "survey_questions": [
            {
                "question_key": "a_only",
                "question": "Shop A question",
                "type": "text",
                "options": [],
                "allow_other": False,
                "position": 0,
            },
        ],
    }
    _put_config(client, auth_a, payload)
    db.refresh(merchant_b)
    assert merchant_b.survey_questions is None
