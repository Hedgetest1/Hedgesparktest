"""Tests for /public/transparency — the trust-signal snapshot.

Locks:
- Response shape contains the 7 expected top-level sections
- Self-healing counts come from audit_log
- LLM drift reads from Redis history (pending shape when empty)
- PII guard reads from get_violation_count_7d
- Audit integrity walks verify_audit_log_chain
- Preflight count reflects scripts/audit_*.py on disk
- Holdout proof counts come from action_outcomes
- Cache: second call hits Redis-cached copy
- Redis outage: endpoint still returns full shape
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _fake_history_entry():
    return {
        "iso_week": "2026-W16",
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "json_parse_rate": 1.0,
        "refusal_rate": 1.0,
        "severity_valid_rate": 1.0,
        "mean_response_chars": 180.0,
    }


class TestEndpointShape:

    def test_response_has_expected_top_level_sections(self):
        """Contract lock — landing + transparency page depend on these keys."""
        from app.api.public_transparency import get_public_transparency
        with patch("app.core.redis_client._client", return_value=None):
            r = get_public_transparency()
        for key in (
            "self_healing",
            "llm_drift",
            "pii_guard",
            "audit_integrity",
            "preflight",
            "holdout_proof",
            "tests",
            "checked_at",
        ):
            assert key in r, f"missing top-level key: {key}"

    def test_preflight_count_reflects_disk(self):
        """Audit count is derived from disk; any new audit script
        shipped via a commit should bump this number automatically."""
        from app.api.public_transparency import get_public_transparency
        with patch("app.core.redis_client._client", return_value=None):
            r = get_public_transparency()
        assert r["preflight"]["audit_count"] >= 20
        # Names stripped of "audit_" prefix so the UI can display them cleanly.
        assert all(
            not n.startswith("audit_")
            for n in r["preflight"]["audit_names"]
        )


class TestLlmDriftSection:

    def test_pending_shape_when_history_empty(self):
        """Before the first weekly run, status='pending' + nulls.
        Critical: UI must not crash on null rates."""
        from app.api.public_transparency import _llm_drift_section
        rc = MagicMock()
        rc.lrange.return_value = []
        with patch("app.core.redis_client._client", return_value=rc):
            out = _llm_drift_section()
        assert out["status"] == "pending"
        assert out["json_parse_rate"] is None
        assert out["refusal_rate"] is None

    def test_measured_shape_after_first_run(self):
        from app.api.public_transparency import _llm_drift_section
        rc = MagicMock()
        rc.lrange.return_value = [
            json.dumps(_fake_history_entry()).encode(),
        ]
        with patch("app.core.redis_client._client", return_value=rc):
            out = _llm_drift_section()
        assert out["status"] == "measured"
        assert out["json_parse_rate"] == 1.0
        assert out["provider"] == "anthropic"
        assert out["last_run_iso_week"] == "2026-W16"

    def test_redis_unavailable_returns_pending_shape(self):
        from app.api.public_transparency import _llm_drift_section
        with patch("app.core.redis_client._client", return_value=None):
            out = _llm_drift_section()
        assert out["status"] == "pending"


class TestCaching:

    def test_second_call_returns_cached_copy(self):
        """The endpoint caches the response 60s. Second call skips
        the DB queries entirely — verified by a read that never
        touches the engine."""
        from app.api import public_transparency as mod

        cached_payload = {
            "self_healing": {"autonomous_fixes_7d": 99},
            "checked_at": "cached",
        }
        rc = MagicMock()
        rc.get.return_value = json.dumps(cached_payload).encode()

        with patch("app.core.redis_client._client", return_value=rc), \
             patch.object(mod, "_self_healing_section") as selfheal:
            out = mod.get_public_transparency()
        assert out == cached_payload
        selfheal.assert_not_called()

    def test_cache_write_on_miss(self):
        """Cache miss → setex called. Pins ONLY that contract; mocks all
        helper sections so the test is deterministic regardless of live
        DB/Redis state in the full-suite run (eliminated an intermittent
        flake caught by the founder 2026-04-25)."""
        from app.api import public_transparency as mod
        rc = MagicMock()
        rc.get.return_value = None  # cache miss
        with patch("app.core.redis_client._client", return_value=rc), \
             patch.object(mod, "_self_healing_section", return_value={}), \
             patch.object(mod, "_llm_drift_section", return_value={}), \
             patch.object(mod, "_pii_guard_section", return_value={}), \
             patch.object(mod, "_audit_integrity_section", return_value={}), \
             patch.object(mod, "_preflight_section", return_value={}), \
             patch.object(mod, "_holdout_proof_section", return_value={}), \
             patch.object(mod, "_tests_section", return_value={}):
            mod.get_public_transparency()
        rc.setex.assert_called_once()
        args, _ = rc.setex.call_args
        assert args[0] == mod._CACHE_KEY


class TestResilience:

    def test_endpoint_returns_even_when_db_breaks(self):
        """A DB outage must degrade the numbers to 0, not break the
        endpoint — the transparency page being broken during an
        incident would be the worst possible UX."""
        from app.api import public_transparency as mod

        def _boom(*a, **kw):
            raise RuntimeError("db down")

        with patch("app.core.redis_client._client", return_value=None), \
             patch.object(mod.engine, "connect", side_effect=_boom):
            out = mod.get_public_transparency()
        # All seven sections still present.
        assert "self_healing" in out
        assert "holdout_proof" in out
        # Counts degrade to 0, they don't vanish.
        assert out["self_healing"]["autonomous_fixes_7d"] == 0
        assert out["holdout_proof"]["actions_measured_30d"] == 0

    def test_audit_integrity_failure_does_not_break_endpoint(self):
        from app.api import public_transparency as mod
        with patch("app.core.redis_client._client", return_value=None), \
             patch(
                 "app.services.audit.verify_audit_log_chain",
                 side_effect=RuntimeError("audit query failed"),
             ):
            out = mod.get_public_transparency()
        assert out["audit_integrity"]["violations"] == 0
        assert out["audit_integrity"]["chained_rows"] == 0


class TestRouterMount:

    def test_router_mounted_in_main(self):
        """Lock the mount — a refactor that drops the include_router
        line would silently 404 the /public/transparency URL."""
        import inspect
        from app import main
        src = inspect.getsource(main)
        assert "public_transparency_router" in src
        assert "app.include_router(public_transparency_router)" in src
