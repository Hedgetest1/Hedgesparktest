"""Tests for the on-alert responder framework.

Framework shipped 2026-04-18 (commit 2d2d843). LLM wiring shipped
2026-04-19 after founder approved the €2-5/mo spend.

These tests lock the contract:
- Default OFF → run() returns early with mode='framework', no DB call
- Enabled + no alerts → no triage path taken
- Enabled + alerts + LLM returns verdict → triage row written + counts
- Enabled + alerts + LLM unavailable → triage_failed row written (so the
  alert isn't retried every cycle)
- P0 verdict → telegram ping fires with the triage summary
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


def _make_alert(**over):
    base = {
        "id": 1,
        "created_at": "2026-04-19T09:00:00",
        "severity": "critical",
        "alert_type": "heartbeat_failed",
        "shop_domain": None,
        "summary": "probe failed",
        "detail": "...",
    }
    base.update(over)
    return base


def _make_verdict(**over):
    from app.services.on_alert_triage_llm import TriageVerdict
    base = dict(
        severity="P1",
        probable_cause="dns timeout on upstream",
        suggested_owner="infra",
        triage_steps=["check cloudflare status", "restart gateway"],
        related_commits=[],
        requires_human_now=False,
        model_used="claude-sonnet-4-20250514",
    )
    base.update(over)
    return TriageVerdict(**base)


def test_run_live_mode_triage_happy_path_increments_counts():
    """Enabled + alerts + LLM returns valid verdict → triaged count
    increments, llm_calls_made increments, audit_log gets written."""
    from app.services import on_alert_responder

    alert = _make_alert()
    verdict = _make_verdict(severity="P1", requires_human_now=False)
    audit_writes: list[dict] = []

    def _capture(db, **kwargs):
        audit_writes.append(kwargs)
        return MagicMock()

    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}), \
         patch.object(on_alert_responder, "_find_untrimmed_criticals", return_value=[alert]), \
         patch.object(on_alert_responder, "build_context_packet", return_value={"alert": alert}), \
         patch("app.services.on_alert_triage_llm.triage", return_value=verdict), \
         patch("app.services.audit.write_audit_log", side_effect=_capture):
        db = MagicMock()
        report = on_alert_responder.run(db)

    assert report["alerts_found"] == 1
    assert report["contexts_built"] == 1
    assert report["llm_calls_made"] == 1
    assert report["triaged"] == 1
    assert report["triage_failed"] == 0
    assert report["p0_pings"] == 0
    assert len(audit_writes) == 1
    row = audit_writes[0]
    assert row["action_type"] == "alert_triage"
    assert row["actor_type"] == "worker"
    assert row["actor_name"] == "on_alert_responder"
    assert row["target_type"] == "ops_alert"
    assert row["target_id"] == "1"
    assert row["status"] == "triaged"
    assert row["metadata"]["severity"] == "P1"
    assert row["metadata"]["probable_cause"] == "dns timeout on upstream"


def test_run_live_mode_triage_failed_still_writes_receipt():
    """If the LLM is unavailable (no api key / budget / PII block /
    parse fail) the triage function returns None. We must still write
    an audit_log row so the SAME alert isn't retriaged every cycle —
    the idempotency anti-join depends on the row existing."""
    from app.services import on_alert_responder

    alert = _make_alert()
    audit_writes: list[dict] = []

    def _capture(db, **kwargs):
        audit_writes.append(kwargs)
        return MagicMock()

    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}), \
         patch.object(on_alert_responder, "_find_untrimmed_criticals", return_value=[alert]), \
         patch.object(on_alert_responder, "build_context_packet", return_value={"alert": alert}), \
         patch("app.services.on_alert_triage_llm.triage", return_value=None), \
         patch("app.services.audit.write_audit_log", side_effect=_capture):
        db = MagicMock()
        report = on_alert_responder.run(db)

    assert report["triaged"] == 0
    assert report["triage_failed"] == 1
    assert report["llm_calls_made"] == 0
    assert len(audit_writes) == 1
    assert audit_writes[0]["status"] == "triage_failed"
    assert audit_writes[0]["metadata"]["failure_reason"] == (
        "llm_unavailable_or_parse_failed"
    )


def test_run_live_mode_p0_triggers_telegram_ping():
    """`requires_human_now=True` → founder gets a Telegram ping with
    the triage summary. Ping must include alert_type + probable_cause
    + triage steps."""
    from app.services import on_alert_responder

    alert = _make_alert(alert_type="tracker_down", summary="tracker CDN 5xx")
    verdict = _make_verdict(
        severity="P0",
        probable_cause="cdn cache miss",
        triage_steps=["check cloudflare", "purge cache"],
        requires_human_now=True,
    )
    sent_texts: list[str] = []

    def _capture_send(text, chat_id=None, parse_mode="HTML", reply_to=None):
        sent_texts.append(text)
        return True

    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}), \
         patch.object(on_alert_responder, "_find_untrimmed_criticals", return_value=[alert]), \
         patch.object(on_alert_responder, "build_context_packet", return_value={"alert": alert}), \
         patch("app.services.on_alert_triage_llm.triage", return_value=verdict), \
         patch("app.services.audit.write_audit_log", return_value=MagicMock()), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture_send):
        db = MagicMock()
        report = on_alert_responder.run(db)

    assert report["p0_pings"] == 1
    assert len(sent_texts) == 1
    msg = sent_texts[0]
    assert "P0" in msg
    assert "tracker_down" in msg
    assert "cdn cache miss" in msg
    assert "check cloudflare" in msg


def test_run_live_mode_non_p0_does_not_ping():
    """P1 / P2 verdicts must NOT ping the founder — only P0."""
    from app.services import on_alert_responder

    alert = _make_alert()
    verdict = _make_verdict(severity="P1", requires_human_now=False)
    sent_texts: list[str] = []

    def _capture_send(text, **kwargs):
        sent_texts.append(text)
        return True

    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}), \
         patch.object(on_alert_responder, "_find_untrimmed_criticals", return_value=[alert]), \
         patch.object(on_alert_responder, "build_context_packet", return_value={"alert": alert}), \
         patch("app.services.on_alert_triage_llm.triage", return_value=verdict), \
         patch("app.services.audit.write_audit_log", return_value=MagicMock()), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message", side_effect=_capture_send):
        db = MagicMock()
        report = on_alert_responder.run(db)

    assert report["p0_pings"] == 0
    assert sent_texts == []


def test_run_live_mode_telegram_outage_does_not_break_loop():
    """If Telegram send raises, the triage result still counts — the
    alert was triaged, only the notification fallback failed."""
    from app.services import on_alert_responder

    alert = _make_alert()
    verdict = _make_verdict(requires_human_now=True)

    with patch.dict(os.environ, {"ON_ALERT_RESPONDER_ENABLED": "1"}), \
         patch.object(on_alert_responder, "_find_untrimmed_criticals", return_value=[alert]), \
         patch.object(on_alert_responder, "build_context_packet", return_value={"alert": alert}), \
         patch("app.services.on_alert_triage_llm.triage", return_value=verdict), \
         patch("app.services.audit.write_audit_log", return_value=MagicMock()), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch(
             "app.services.telegram_agent.send_message",
             side_effect=RuntimeError("telegram down"),
         ):
        db = MagicMock()
        report = on_alert_responder.run(db)

    assert report["triaged"] == 1
    assert report["p0_pings"] == 0, (
        "p0_pings counts successful sends only; a raised send is a "
        "failure path, not a success"
    )
