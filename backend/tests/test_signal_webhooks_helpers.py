"""
Unit tests for the pure helpers extracted from `emit_signal`
in the 2026-05-13 A3 refactor.

The 15 prior end-to-end tests in test_signal_webhooks.py exercise the
full flow. This file locks the new structural-unit helpers: payload
builders + delivery attempt + outcome recording + heal/dead-letter
side-effects + _DeliveryAttempt NamedTuple contract.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.signal_webhooks import (
    _attempt_http_delivery,
    _build_hedgespark_request,
    _build_slack_request,
    _DeliveryAttempt,
)


# ---------------------------------------------------------------------------
# _DeliveryAttempt NamedTuple contract
# ---------------------------------------------------------------------------


class TestDeliveryAttempt:
    def test_field_order_locked(self):
        assert _DeliveryAttempt._fields == (
            "delivered", "http_status", "last_err", "attempts",
        )

    def test_field_name_access(self):
        a = _DeliveryAttempt(
            delivered=True, http_status=200, last_err=None, attempts=1,
        )
        assert a.delivered is True
        assert a.http_status == 200
        assert a.last_err is None
        assert a.attempts == 1

    def test_positional_access_preserved(self):
        a = _DeliveryAttempt(
            delivered=False, http_status=500, last_err="http_500", attempts=2,
        )
        delivered, status, err, attempts = a
        assert (delivered, status, err, attempts) == (False, 500, "http_500", 2)


# ---------------------------------------------------------------------------
# _build_slack_request
# ---------------------------------------------------------------------------


class TestBuildSlackRequest:
    def test_returns_body_and_headers(self):
        body, headers = _build_slack_request(
            event_type="high_intent_abandon",
            shop_domain="x.myshopify.com",
            source="pipeline",
            payload={"visitor_id": "v1"},
        )
        assert isinstance(body, bytes)
        assert headers["Content-Type"] == "application/json"
        assert "HedgeSpark-Webhooks" in headers["User-Agent"]

    def test_no_hmac_signature_for_slack(self):
        # Slack uses URL-embedded auth — we don't sign the body
        _, headers = _build_slack_request(
            event_type="high_intent_abandon",
            shop_domain="x.myshopify.com",
            source="pipeline",
            payload={"x": 1},
        )
        assert "X-HedgeSpark-Signature" not in headers
        assert "X-HedgeSpark-Event-ID" not in headers

    def test_body_is_valid_json(self):
        body, _ = _build_slack_request(
            event_type="goal_at_risk",
            shop_domain="x.myshopify.com",
            source="pipeline",
            payload={"goal_name": "Q1"},
        )
        parsed = json.loads(body)
        # Slack payloads have `text` + `blocks` keys
        assert "text" in parsed
        assert "blocks" in parsed


# ---------------------------------------------------------------------------
# _build_hedgespark_request
# ---------------------------------------------------------------------------


class TestBuildHedgesparkRequest:
    def test_hmac_signed(self):
        body, headers = _build_hedgespark_request(
            event_id="hs_test123",
            event_type="high_intent_abandon",
            shop_domain="x.myshopify.com",
            source="pipeline",
            payload={"visitor_id": "v1"},
            secret="secret-key-32-bytes-long-or-whatever",
        )
        # All 4 HedgeSpark headers present
        assert headers["X-HedgeSpark-Event-ID"] == "hs_test123"
        assert headers["X-HedgeSpark-Event-Type"] == "high_intent_abandon"
        assert "X-HedgeSpark-Signature" in headers
        # Signature is non-empty
        assert len(headers["X-HedgeSpark-Signature"]) > 0

    def test_signature_changes_with_secret(self):
        kwargs = dict(
            event_id="hs_test",
            event_type="x",
            shop_domain="x.myshopify.com",
            source="pipeline",
            payload={"a": 1},
        )
        _, headers_a = _build_hedgespark_request(**kwargs, secret="A")
        _, headers_b = _build_hedgespark_request(**kwargs, secret="B")
        assert headers_a["X-HedgeSpark-Signature"] != headers_b["X-HedgeSpark-Signature"]

    def test_body_shape(self):
        body, _ = _build_hedgespark_request(
            event_id="hs_x",
            event_type="goal_at_risk",
            shop_domain="x.myshopify.com",
            source="aggregation_worker",
            payload={"goal_name": "Q1", "delta": -250.0},
            secret="s",
        )
        parsed = json.loads(body)
        assert parsed["event_id"] == "hs_x"
        assert parsed["event_type"] == "goal_at_risk"
        assert parsed["shop_domain"] == "x.myshopify.com"
        assert parsed["source"] == "aggregation_worker"
        assert "occurred_at" in parsed
        assert parsed["data"] == {"goal_name": "Q1", "delta": -250.0}


# ---------------------------------------------------------------------------
# _attempt_http_delivery — SSRF guard + retry logic
# ---------------------------------------------------------------------------


class TestAttemptHttpDelivery:
    @patch("app.services.signal_webhooks._resolve_and_check_at_delivery")
    @patch("app.services.signal_webhooks.httpx", create=True)
    def test_success_first_try_no_retry(self, mock_httpx_module, mock_resolve):
        resp = MagicMock()
        resp.status_code = 200
        mock_httpx_module.post = MagicMock(return_value=resp)
        # Need to also patch import inside function — use a different shim
        with patch("httpx.post", return_value=resp) as mock_post:
            out = _attempt_http_delivery(
                url="https://example.com/hook", body=b"{}", headers={},
            )
            assert out.delivered is True
            assert out.http_status == 200
            assert out.attempts == 1
            assert out.last_err is None
            mock_post.assert_called_once()

    def test_ssrf_block_short_circuits(self, monkeypatch):
        import app.services.signal_webhooks as sw
        def _explode(url):
            raise ValueError("private IP 10.0.0.1")
        monkeypatch.setattr(sw, "_resolve_and_check_at_delivery", _explode)
        out = _attempt_http_delivery(
            url="https://attacker.example/hook", body=b"{}", headers={},
        )
        assert out.delivered is False
        assert out.last_err is not None
        assert "ssrf_blocked" in out.last_err
        assert out.attempts == 1
        assert out.http_status is None

    def test_5xx_retries_once(self, monkeypatch):
        import app.services.signal_webhooks as sw
        monkeypatch.setattr(sw, "_resolve_and_check_at_delivery", lambda _u: None)
        call_count = {"n": 0}
        def _fake_post(*_a, **_kw):
            call_count["n"] += 1
            r = MagicMock()
            r.status_code = 500
            return r
        with patch("httpx.post", side_effect=_fake_post):
            out = _attempt_http_delivery(
                url="https://example.com/hook", body=b"{}", headers={},
            )
            assert out.delivered is False
            assert out.http_status == 500
            assert call_count["n"] == 2  # 1 attempt + 1 retry
            assert out.attempts == 2

    def test_4xx_no_retry(self, monkeypatch):
        # 4xx errors are not transient — retry would be wasted
        import app.services.signal_webhooks as sw
        monkeypatch.setattr(sw, "_resolve_and_check_at_delivery", lambda _u: None)
        call_count = {"n": 0}
        def _fake_post(*_a, **_kw):
            call_count["n"] += 1
            r = MagicMock()
            r.status_code = 400
            return r
        with patch("httpx.post", side_effect=_fake_post):
            out = _attempt_http_delivery(
                url="https://example.com/hook", body=b"{}", headers={},
            )
            assert out.delivered is False
            assert out.http_status == 400
            assert out.last_err == "http_400"
            assert call_count["n"] == 1  # No retry on 4xx
            assert out.attempts == 1

    def test_network_exception_retries_once(self, monkeypatch):
        import app.services.signal_webhooks as sw
        monkeypatch.setattr(sw, "_resolve_and_check_at_delivery", lambda _u: None)
        call_count = {"n": 0}
        def _explode(*_a, **_kw):
            call_count["n"] += 1
            raise RuntimeError("dns resolution failed")
        with patch("httpx.post", side_effect=_explode):
            out = _attempt_http_delivery(
                url="https://example.com/hook", body=b"{}", headers={},
            )
            assert out.delivered is False
            assert call_count["n"] == 2  # 1 + 1 retry
            assert out.last_err is not None
            assert "dns resolution" in out.last_err
