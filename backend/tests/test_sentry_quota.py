"""Tests for app/services/sentry_quota.

Pin the C7 /ops/sentry-budget contract shipped 2026-04-24:
  * Graceful "unconfigured" payload when SENTRY_AUTH_TOKEN / SENTRY_ORG
    are unset. Never raises.
  * Successful API parse normalizes into a predictable quotas list.
  * HTTP failure returns a structured payload with raw_error, doesn't
    raise.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch


def test_sentry_quota_unconfigured_returns_reason():
    from app.services import sentry_quota
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENTRY_AUTH_TOKEN", None)
        os.environ.pop("SENTRY_ORG", None)
        out = sentry_quota.get_quota_summary()
    assert out["configured"] is False
    assert "SENTRY_AUTH_TOKEN" in out["reason"]
    assert out["quotas"] == []
    assert out["raw_error"] is None


def test_sentry_quota_http_error_returns_structured_payload():
    from app.services import sentry_quota

    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.text = "forbidden"

    class _FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **kw): return fake_resp

    with patch.dict(os.environ, {
        "SENTRY_AUTH_TOKEN": "tok", "SENTRY_ORG": "hedgespark", "SENTRY_PROJECT": "backend",
    }):
        with patch.object(sentry_quota.httpx, "Client", return_value=_FakeClient()):
            # Clear cache first
            with patch.object(sentry_quota, "_cached_fetch", return_value=None):
                with patch.object(sentry_quota, "_cache_store", return_value=None):
                    out = sentry_quota.get_quota_summary()
    assert out["configured"] is True
    assert "HTTP 403" in (out["raw_error"] or "")
    assert out["quotas"] == []


def test_sentry_quota_parses_stats_v2_groups():
    from app.services import sentry_quota

    fake_body = {
        "intervals": ["2026-04-01", "2026-04-30"],
        "groups": [
            {"by": {"category": "errors", "outcome": "accepted"}, "totals": {"sum(quantity)": 123}},
            {"by": {"category": "errors", "outcome": "filtered"}, "totals": {"sum(quantity)": 4}},
            {"by": {"category": "transactions", "outcome": "accepted"}, "totals": {"sum(quantity)": 567}},
        ],
    }
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = fake_body

    class _FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **kw): return fake_resp

    with patch.dict(os.environ, {
        "SENTRY_AUTH_TOKEN": "tok", "SENTRY_ORG": "hedgespark",
    }):
        with patch.object(sentry_quota.httpx, "Client", return_value=_FakeClient()):
            with patch.object(sentry_quota, "_cached_fetch", return_value=None):
                with patch.object(sentry_quota, "_cache_store", return_value=None):
                    out = sentry_quota.get_quota_summary()
    assert out["configured"] is True
    assert out["raw_error"] is None
    cats = {q["category"]: q for q in out["quotas"]}
    assert cats["errors"]["accepted"] == 123
    assert cats["errors"]["filtered"] == 4
    assert cats["errors"]["total"] == 127
    assert cats["transactions"]["accepted"] == 567
    assert out["period"] == {"start": "2026-04-01", "end": "2026-04-30"}
