"""Test adversarial_reviewer — Sprint B of CTO-brain pipeline upgrade.

Pins:
  * Feature flag off by default
  * 3 lenses (internal / investor / competitor) run per candidate
  * Each lens produces its own persisted AdversarialReviewFinding row
  * Severity clamped 0-10
  * Budget block + PII block return no findings but no exception
  * Truncated response dropped (stop_reason = max_tokens)
  * Parse failure tolerated (finding skipped, other lenses continue)
  * Findings sorted severity-desc on return
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.adversarial_review_finding import AdversarialReviewFinding
from app.models.bugfix_candidate import BugFixCandidate
from app.services import adversarial_reviewer


@pytest.fixture
def enable_reviewer(monkeypatch):
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    yield


def _make_candidate(db, title="test fix", patch_risk_tier=1):
    c = BugFixCandidate(
        status="approved",
        source_type="ops_alert",
        source_ref="probe:adv",
        title=title,
        summary="test candidate for adversarial review",
        patch_diff="--- a/x.py\n+++ b/x.py\n@@\n-    x = 1\n+    x = 2\n",
        patch_risk_tier=patch_risk_tier,
    )
    db.add(c)
    db.flush()
    return c


def _mock_haiku_response(severity, concern, remediation, stop_reason="end_turn"):
    body_text = json.dumps({
        "severity": severity,
        "concern": concern,
        "remediation": remediation,
    })
    return {
        "content": [{"text": body_text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 600, "output_tokens": 200},
    }


def _fake_resp(status, json_body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    return resp


def test_is_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("ADVERSARIAL_REVIEWER_ENABLED", raising=False)
    assert adversarial_reviewer.is_enabled() is False


def test_is_enabled_on_via_env(monkeypatch):
    monkeypatch.setenv("ADVERSARIAL_REVIEWER_ENABLED", "1")
    assert adversarial_reviewer.is_enabled() is True


def test_review_no_op_when_disabled(db, monkeypatch):
    monkeypatch.delenv("ADVERSARIAL_REVIEWER_ENABLED", raising=False)
    candidate = _make_candidate(db)
    out = adversarial_reviewer.review_with_3_lenses(db, candidate)
    assert out == []


def test_review_runs_all_3_lenses(db, enable_reviewer):
    candidate = _make_candidate(db)
    with patch("app.services.adversarial_reviewer.httpx.post") as post, \
         patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.assert_clean"):
        post.return_value = _fake_resp(200, _mock_haiku_response(
            severity=5, concern="test concern", remediation="test fix"))
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)

    assert len(findings) == 3
    lenses = {f.lens for f in findings}
    assert lenses == {"internal", "investor", "competitor"}
    for f in findings:
        assert f.severity == 5
        assert f.concern == "test concern"
        assert f.llm_provider == "anthropic"
        assert f.llm_model == adversarial_reviewer.HAIKU_MODEL
        assert f.bugfix_candidate_id == candidate.id


def test_review_returns_sorted_severity_desc(db, enable_reviewer):
    candidate = _make_candidate(db)
    severities = iter([3, 8, 5])

    def _post_side(url, headers, json, timeout):
        sev = next(severities)
        return _fake_resp(200, _mock_haiku_response(sev, "c", "r"))

    with patch("app.services.adversarial_reviewer.httpx.post", side_effect=_post_side), \
         patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.assert_clean"):
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)

    assert [f.severity for f in findings] == [8, 5, 3]


def test_review_clamps_severity_to_0_10(db, enable_reviewer):
    candidate = _make_candidate(db)
    with patch("app.services.adversarial_reviewer.httpx.post") as post, \
         patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.assert_clean"):
        post.return_value = _fake_resp(200, _mock_haiku_response(
            severity=999, concern="high", remediation="x"))
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)

    for f in findings:
        assert f.severity == 10


def test_review_budget_blocked_produces_no_findings(db, enable_reviewer):
    candidate = _make_candidate(db)
    with patch("app.services.adversarial_reviewer.check_budget", return_value=(False, "budget_exhausted")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.assert_clean"):
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)
    assert findings == []


def test_review_truncated_response_dropped(db, enable_reviewer):
    candidate = _make_candidate(db)
    with patch("app.services.adversarial_reviewer.httpx.post") as post, \
         patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.assert_clean"):
        post.return_value = _fake_resp(200, _mock_haiku_response(
            severity=7, concern="truncated", remediation="x",
            stop_reason="max_tokens"))
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)
    assert findings == []


def test_review_parse_failure_skips_lens_others_continue(db, enable_reviewer):
    """If one lens returns unparseable JSON, other lenses still persist."""
    candidate = _make_candidate(db)
    call_count = {"n": 0}

    def _post_side(url, headers, json, timeout):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # 2nd lens: return non-JSON garbage
            return _fake_resp(200, {
                "content": [{"text": "not parseable at all"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            })
        return _fake_resp(200, _mock_haiku_response(5, "ok", "fix"))

    with patch("app.services.adversarial_reviewer.httpx.post", side_effect=_post_side), \
         patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.assert_clean"):
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)

    # 2 parseable findings (lenses 1 and 3)
    assert len(findings) == 2


def test_review_http_429_records_backoff_no_finding(db, enable_reviewer):
    candidate = _make_candidate(db)
    with patch("app.services.adversarial_reviewer.httpx.post") as post, \
         patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False), \
         patch("app.services.adversarial_reviewer.record_429") as rec_429, \
         patch("app.services.adversarial_reviewer.assert_clean"):
        post.return_value = _fake_resp(429, {})
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)

    assert findings == []
    # record_429 called at least once (once per lens that hit the 429)
    assert rec_429.called


def test_review_pii_block_returns_no_finding(db, enable_reviewer):
    from app.core.llm_pii_guard import LLMPayloadViolation
    candidate = _make_candidate(db)

    with patch(
        "app.services.adversarial_reviewer.assert_clean",
        side_effect=LLMPayloadViolation("email detected"),
    ), patch("app.services.adversarial_reviewer.check_budget", return_value=(True, "ok")), \
         patch("app.services.adversarial_reviewer.is_provider_backed_off", return_value=False):
        findings = adversarial_reviewer.review_with_3_lenses(db, candidate)

    assert findings == []


def test_parse_response_extracts_embedded_json():
    text = 'Here is my analysis:\n{"severity": 7, "concern": "x", "remediation": "y"}\nDone.'
    out = adversarial_reviewer._parse_response(text)
    assert out == {"severity": 7, "concern": "x", "remediation": "y"}


def test_parse_response_none_when_invalid():
    assert adversarial_reviewer._parse_response("") is None
    assert adversarial_reviewer._parse_response("no json here") is None
    # Malformed JSON with severity keyword still fails to parse
    assert adversarial_reviewer._parse_response('{"severity": not a number}') is None
