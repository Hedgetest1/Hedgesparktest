"""Tests for the 2026-04-18 on-alert responder framework.

The service ships as FRAMEWORK ONLY — `ON_ALERT_RESPONDER_ENABLED=0`
default. The poll + context-packet logic runs; LLM calls are stubbed
until founder approves money-spend scope.

These tests lock the framework-mode contract:
- Default OFF → run() returns early with mode='framework'
- Enabled → builds context packets but llm_calls_made stays at 0
- Context packet contains alert + related_alerts_48h + commits + worker_errors
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def test_is_enabled_default_off():
    """Absent env var, the responder is disabled."""
    from app.services import on_alert_responder
    # Clear any test-run leakage
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ON_ALERT_RESPONDER_ENABLED", None)
        assert on_alert_responder.is_enabled() is False


def test_is_enabled_respects_env_flag():
    """Flipping the env flag turns it on."""
    from app.services import on_alert_responder
    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}):
        assert on_alert_responder.is_enabled() is True
    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "0"}):
        assert on_alert_responder.is_enabled() is False
    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "yes"}):
        # Only the literal "1" enables — anything else is OFF.
        assert on_alert_responder.is_enabled() is False


def test_run_framework_mode_returns_early_when_disabled():
    """Disabled path must NOT touch the DB. Verified by refusing any
    DB call when mode=framework."""
    from app.services import on_alert_responder
    db = MagicMock()
    db.execute.side_effect = AssertionError("DB should not be queried when disabled")
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ON_ALERT_RESPONDER_ENABLED", None)
        report = on_alert_responder.run(db)
    assert report["enabled"] is False
    assert report["mode"] == "framework"
    assert report["alerts_found"] == 0
    assert report["llm_calls_made"] == 0
    db.execute.assert_not_called()


def test_run_live_mode_no_alerts():
    """Enabled + zero critical unresolved alerts → no contexts built,
    no LLM calls."""
    from app.services import on_alert_responder
    db = MagicMock()
    # Return empty alert list
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    db.execute.return_value = result

    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}):
        report = on_alert_responder.run(db)
    assert report["enabled"] is True
    assert report["mode"] == "live"
    assert report["alerts_found"] == 0
    assert report["contexts_built"] == 0
    assert report["llm_calls_made"] == 0  # still 0 — framework stage


def test_build_context_packet_includes_alert_and_related():
    """Context packet must surface the alert itself + related_alerts_48h
    slot so the future LLM call has grounding material."""
    from app.services import on_alert_responder
    db = MagicMock()

    # First DB call (related alerts) returns []
    empty_result = MagicMock()
    empty_result.mappings.return_value.all.return_value = []
    db.execute.return_value = empty_result

    alert = {
        "id": 42,
        "created_at": "2026-04-18T09:00:00",
        "severity": "critical",
        "alert_type": "pipeline_stalled",
        "shop_domain": None,
        "summary": "Pipeline stalled 7d",
        "detail": "5 proposals, 0 applied",
    }
    packet = on_alert_responder.build_context_packet(db, alert)
    assert packet["alert"] == alert
    assert "related_alerts_48h" in packet or "related_alerts_48h_error" in packet


def test_run_live_mode_with_alerts_builds_contexts_without_llm():
    """Enabled + alerts → builds contexts but llm_calls_made stays at 0
    (this is the framework-mode signature). When LLM wiring lands, this
    test will need to be updated to reflect llm_calls_made == N."""
    from app.services import on_alert_responder

    alert_row = {
        "id": 1, "created_at": "2026-04-18T09:00", "severity": "critical",
        "alert_type": "heartbeat_failed", "shop_domain": None,
        "summary": "probe failed", "detail": "...",
    }

    # The run() function calls _find_untrimmed_criticals, then for each
    # alert calls build_context_packet which does additional queries.
    # Stub both via patch.
    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}), \
         patch.object(on_alert_responder, "_find_untrimmed_criticals", return_value=[alert_row]), \
         patch.object(on_alert_responder, "build_context_packet", return_value={"alert": alert_row, "dummy": "ctx"}):
        db = MagicMock()
        report = on_alert_responder.run(db)

    assert report["alerts_found"] == 1
    assert report["contexts_built"] == 1
    assert report["llm_calls_made"] == 0, \
        "llm_calls_made must stay at 0 until founder approves LLM spend"
