"""
Unit tests for the pure helpers extracted from
`push_intent_signals_to_klaviyo` in the 2026-05-13 A3 refactor.

The 5 prior end-to-end tests in test_klaviyo_push.py exercise the full
endpoint; this file locks the new structural-unit helpers: payload
builder, profile resolver, eligible-visitor picker, HTTP poster +
per-signal processor.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.klaviyo_export import (
    _build_klaviyo_event_payload,
    _pick_eligible_visitors,
    _post_klaviyo_event,
    _resolve_profile_attrs,
)


# ---------------------------------------------------------------------------
# _resolve_profile_attrs — 3 branches (email / anon-allowed / anon-skip)
# ---------------------------------------------------------------------------


class TestResolveProfileAttrs:
    def test_email_path(self):
        out = _resolve_profile_attrs(
            email="alice@example.com", vid="v_xyz", allow_anon=False,
        )
        assert out is not None
        attrs, label = out
        assert attrs == {"email": "alice@example.com"}
        # Label masked to first 3 chars + ***
        assert label == "ali***"

    def test_anon_skipped_in_production(self):
        # ALLOW_INSECURE_DEV off → anon visitor returns None
        out = _resolve_profile_attrs(
            email=None, vid="v_xyz12345", allow_anon=False,
        )
        assert out is None

    def test_anon_allowed_in_dev(self):
        out = _resolve_profile_attrs(
            email=None, vid="v_xyz12345", allow_anon=True,
        )
        assert out is not None
        attrs, label = out
        # Synthetic profile contains vid[:8]
        assert attrs["email"] == "v_xyz123@anon.hedgespark.local"
        assert attrs["external_id"] == "v_xyz12345"
        assert label == "anon:v_xyz123"

    def test_email_label_masking(self):
        # Confirm we never leak the full email into the label
        _, label = _resolve_profile_attrs(
            email="very-long-email-address@domain.com",
            vid="v_x", allow_anon=False,
        )
        assert "very-long-email" not in label
        assert "domain.com" not in label
        assert label.endswith("***")


# ---------------------------------------------------------------------------
# _pick_eligible_visitors — hot + warm_top (BI >= 0.40)
# ---------------------------------------------------------------------------


class TestPickEligibleVisitors:
    def test_empty_segment_yields_empty(self):
        assert _pick_eligible_visitors({}) == []

    def test_hot_visitors_always_included(self):
        segment = {
            "hot": {"visitors": [
                {"visitor_id": "v1", "behavioral_index": 0.7},
                {"visitor_id": "v2", "behavioral_index": 0.6},
            ]},
            "warm": {"visitors": []},
        }
        out = _pick_eligible_visitors(segment)
        assert len(out) == 2
        assert {v["visitor_id"] for v in out} == {"v1", "v2"}

    def test_warm_above_threshold_included(self):
        segment = {
            "hot": {"visitors": []},
            "warm": {"visitors": [
                {"visitor_id": "w1", "behavioral_index": 0.45},  # above 0.40
                {"visitor_id": "w2", "behavioral_index": 0.39},  # below 0.40
            ]},
        }
        out = _pick_eligible_visitors(segment)
        ids = {v["visitor_id"] for v in out}
        assert "w1" in ids
        assert "w2" not in ids

    def test_threshold_boundary_inclusive(self):
        # >= 0.40 → inclusive
        segment = {
            "hot": {"visitors": []},
            "warm": {"visitors": [
                {"visitor_id": "w_eq", "behavioral_index": 0.40},
                {"visitor_id": "w_just_below", "behavioral_index": 0.3999},
            ]},
        }
        out = _pick_eligible_visitors(segment)
        ids = {v["visitor_id"] for v in out}
        assert "w_eq" in ids
        assert "w_just_below" not in ids

    def test_missing_behavioral_index_excluded(self):
        # Warm visitor without behavioral_index → treated as 0 → excluded
        segment = {
            "hot": {"visitors": []},
            "warm": {"visitors": [{"visitor_id": "w_no_bi"}]},
        }
        out = _pick_eligible_visitors(segment)
        assert {v["visitor_id"] for v in out} == set()


# ---------------------------------------------------------------------------
# _build_klaviyo_event_payload — v3 API shape
# ---------------------------------------------------------------------------


def _visitor(vid="v1", bi=0.7, vc=5, scroll=80, dwell=30):
    return {
        "visitor_id": vid,
        "behavioral_index": bi,
        "visit_count": vc,
        "avg_scroll": scroll,
        "avg_dwell_secs": dwell,
    }


class TestBuildPayload:
    def test_shape(self):
        out = _build_klaviyo_event_payload(
            product_url="/products/wallet", signal_type="HIGH_ENGAGEMENT_NO_ACTION",
            signal_strength=0.85, visitor=_visitor(),
            profile_attrs={"email": "alice@x.com"},
            shop_domain="myshop.myshopify.com",
        )
        assert out["data"]["type"] == "event"
        attrs = out["data"]["attributes"]
        assert attrs["metric"]["data"]["attributes"]["name"] == "HedgeSpark — Intent Detected"
        assert attrs["profile"]["data"]["attributes"] == {"email": "alice@x.com"}

    def test_properties_round_trip(self):
        out = _build_klaviyo_event_payload(
            product_url="/products/wallet", signal_type="HIGH_ENGAGEMENT_NO_ACTION",
            signal_strength=0.823456, visitor=_visitor(bi=0.75, vc=8, scroll=90, dwell=42),
            profile_attrs={"email": "alice@x.com"},
            shop_domain="myshop.myshopify.com",
        )
        props = out["data"]["attributes"]["properties"]
        assert props["product_url"] == "/products/wallet"
        assert props["signal_type"] == "HIGH_ENGAGEMENT_NO_ACTION"
        # signal_strength rounded to 3 dp
        assert props["signal_strength"] == 0.823
        assert props["behavioral_index"] == 0.75
        assert props["visit_count"] == 8
        assert props["avg_scroll_pct"] == 90
        assert props["avg_dwell_secs"] == 42
        assert props["shop_domain"] == "myshop.myshopify.com"
        assert props["source"] == "hedgespark"

    def test_time_iso_with_z_suffix(self):
        out = _build_klaviyo_event_payload(
            product_url="/p/x", signal_type="X", signal_strength=0.5,
            visitor=_visitor(), profile_attrs={"email": "a@x.com"},
            shop_domain="x.myshopify.com",
        )
        time_str = out["data"]["attributes"]["time"]
        assert time_str.endswith("Z")


# ---------------------------------------------------------------------------
# _post_klaviyo_event — 3 outcomes (success / 4xx / network)
# ---------------------------------------------------------------------------


class TestPostKlaviyoEvent:
    @patch("app.services.klaviyo_export.httpx.post")
    def test_success_returns_true(self, mock_post):
        mock_post.return_value.status_code = 202
        mock_post.return_value.raise_for_status = MagicMock()
        out = _post_klaviyo_event(
            headers={"a": "b"}, payload={"data": {}}, profile_label="ali***",
            shop_domain="x.myshopify.com", product_url="/p/x", signal_type="X",
        )
        assert out is True
        mock_post.assert_called_once()

    @patch("app.services.klaviyo_export.httpx.post")
    def test_http_status_error_returns_false(self, mock_post):
        import httpx
        resp = MagicMock()
        resp.status_code = 400
        resp.text = "bad request body"
        err = httpx.HTTPStatusError("400", request=MagicMock(), response=resp)
        mock_post.return_value.raise_for_status.side_effect = err
        mock_post.return_value.status_code = 400
        out = _post_klaviyo_event(
            headers={}, payload={}, profile_label="x***",
            shop_domain="x", product_url="/p/x", signal_type="X",
        )
        assert out is False

    @patch("app.services.klaviyo_export.httpx.post")
    def test_network_error_returns_false(self, mock_post):
        mock_post.side_effect = RuntimeError("dns lookup failed")
        out = _post_klaviyo_event(
            headers={}, payload={}, profile_label="x***",
            shop_domain="x", product_url="/p/x", signal_type="X",
        )
        assert out is False
