"""Tests for app/services/sentry_api (SENTRY-2 closure).

Pin the bidirectional API contract:
  * extract_issue_id parses the Sentry URL formats we actually receive
  * add_issue_comment + set_issue_status return False (not raise) when
    SENTRY_AUTH_TOKEN is missing
  * Successful 201 / 204 responses return True
  * notify_triage_outcome dispatches the right helper based on
    incident_status (linked → comment, resolved → comment+status,
    ignored → status only, anything else → no-op)
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_extract_issue_id_canonical_url():
    from app.services.sentry_api import extract_issue_id
    assert extract_issue_id("https://sentry.io/organizations/hedgespark/issues/12345/") == "12345"


def test_extract_issue_id_with_event_suffix():
    from app.services.sentry_api import extract_issue_id
    assert extract_issue_id(
        "https://sentry.io/organizations/hedgespark/issues/9876/events/abc/"
    ) == "9876"


def test_extract_issue_id_subdomain_form():
    from app.services.sentry_api import extract_issue_id
    assert extract_issue_id("https://hedgespark.sentry.io/issues/4242/") == "4242"


def test_extract_issue_id_invalid_returns_none():
    from app.services.sentry_api import extract_issue_id
    assert extract_issue_id(None) is None
    assert extract_issue_id("") is None
    assert extract_issue_id("https://example.com/anything") is None


def test_add_issue_comment_unconfigured_returns_false():
    from app.services import sentry_api
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENTRY_AUTH_TOKEN", None)
        assert sentry_api.add_issue_comment("12345", "hi") is False


def test_set_issue_status_invalid_status_returns_false():
    from app.services import sentry_api
    with patch.dict(os.environ, {"SENTRY_AUTH_TOKEN": "tok", "SENTRY_ORG": "hedgespark"}):
        assert sentry_api.set_issue_status("12345", "totally-bogus") is False


def test_add_issue_comment_success_path():
    from app.services import sentry_api

    fake_resp = MagicMock(status_code=201, text="")

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, **kw):
            assert "/issues/12345/comments/" in url
            assert kw["json"]["text"] == "hello"
            assert "Bearer tok" in kw["headers"]["Authorization"]
            return fake_resp

    with patch.dict(os.environ, {"SENTRY_AUTH_TOKEN": "tok", "SENTRY_ORG": "hedgespark"}):
        with patch.object(sentry_api.httpx, "Client", return_value=_C()):
            assert sentry_api.add_issue_comment("12345", "hello") is True


def test_set_issue_status_success_path():
    from app.services import sentry_api

    fake_resp = MagicMock(status_code=200, text="")

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def put(self, url, **kw):
            assert "/issues/12345/" in url
            assert kw["json"]["status"] == "resolved"
            return fake_resp

    with patch.dict(os.environ, {"SENTRY_AUTH_TOKEN": "tok", "SENTRY_ORG": "hedgespark"}):
        with patch.object(sentry_api.httpx, "Client", return_value=_C()):
            assert sentry_api.set_issue_status("12345", "resolved") is True


def test_notify_triage_outcome_linked_posts_comment_only():
    from app.services import sentry_api

    with patch.object(sentry_api, "add_issue_comment", return_value=True) as m_comment, \
         patch.object(sentry_api, "set_issue_status", return_value=True) as m_status:
        out = sentry_api.notify_triage_outcome(
            sentry_issue_url="https://sentry.io/organizations/hedgespark/issues/777/",
            incident_status="linked",
            verdict_summary="Null deref in /pro/scan",
            bugfix_candidate_id=42,
        )
    assert out["issue_id"] == "777"
    assert out["posted"] is True
    assert out["status_set"] is False  # linked = comment only
    assert m_comment.called
    assert not m_status.called
    posted_text = m_comment.call_args[0][1]
    assert "candidate" in posted_text.lower()
    assert "#42" in posted_text
    assert "Null deref" in posted_text


def test_notify_triage_outcome_resolved_posts_comment_and_status():
    from app.services import sentry_api

    with patch.object(sentry_api, "add_issue_comment", return_value=True) as m_comment, \
         patch.object(sentry_api, "set_issue_status", return_value=True) as m_status:
        out = sentry_api.notify_triage_outcome(
            sentry_issue_url="https://sentry.io/organizations/hedgespark/issues/888/",
            incident_status="resolved",
            verdict_summary="Auto-fixed by self-healing pipeline",
        )
    assert out["posted"] is True
    assert out["status_set"] is True
    assert m_comment.called
    assert m_status.called
    assert m_status.call_args[0] == ("888", "resolved")


def test_notify_triage_outcome_no_issue_id_skips():
    from app.services import sentry_api
    out = sentry_api.notify_triage_outcome(
        sentry_issue_url=None,
        incident_status="linked",
    )
    assert out["posted"] is False
    assert out["status_set"] is False
    assert out["skipped_reason"] == "no issue_id in URL"


def test_notify_triage_outcome_non_terminal_skips():
    from app.services import sentry_api
    out = sentry_api.notify_triage_outcome(
        sentry_issue_url="https://sentry.io/organizations/hedgespark/issues/1/",
        incident_status="parsed",
    )
    assert out["posted"] is False
    assert out["status_set"] is False
    assert "non-terminal" in (out["skipped_reason"] or "")
