"""Locks the 2026-04-18 B1 residue closure:
_run_daily_digest must write an audit_log row for every state transition
(sent / silenced_quiet / send_failed) so daily-brief hit-rate is
queryable in SQL.

Before the fix: only a log() line went to stdout, no DB row, no way to
measure silence-rate or failure-rate without reading logs.

After the fix: every transition emits write_audit_log() with
action_type='daily_digest_decision' and status∈{sent, silenced_quiet,
send_failed}.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


_FIXED_DATE_STR = "2026-04-18"


def _fixed_rome_9am():
    """Return a Rome-9am datetime. We only care that `.hour >= 8`.
    Paired with _FIXED_DATE_STR so the MockDB `last_date` and the
    function-internal `today = _today_rome()` agree regardless of
    what actual wall-clock day the test runs on (otherwise MockDB
    would embed today's real date while `_today_rome()` sees the
    mocked 2026-04-18, making the 'already sent' branch miss)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 4, 18, 9, 0, 0, tzinfo=ZoneInfo("Europe/Rome"))


class _MockDB:
    """Minimal DB stub — enough to support the wrapper path."""
    def __init__(self, last_date=None):
        self._last_date = last_date
        self.committed = False

    def execute(self, *args, **kwargs):
        result = MagicMock()
        result.fetchone.return_value = (self._last_date,) if self._last_date else None
        return result

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def audit_writes():
    """Captures every write_audit_log call made during the test."""
    calls: list[dict] = []

    def _capture(db, **kwargs):
        calls.append(kwargs)
        return MagicMock()

    with patch("app.services.audit.write_audit_log", side_effect=_capture):
        yield calls


def test_silenced_quiet_writes_audit_log(audit_writes):
    """When is_digest_quiet=True, an audit_log row with status=silenced_quiet
    must be written before worker_state update."""
    from app.workers import agent_worker

    db = _MockDB(last_date=None)
    with patch.object(agent_worker, "SessionLocal", return_value=db), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.is_digest_quiet", return_value=True), \
         patch("app.services.telegram_agent.send_daily_digest", return_value=False), \
         patch("app.workers.agent_worker.datetime") as mock_dt:
        mock_dt.now.return_value = _fixed_rome_9am()
        agent_worker._run_daily_digest()

    assert len(audit_writes) == 1, \
        f"expected 1 audit write for silenced_quiet, got {len(audit_writes)}"
    call = audit_writes[0]
    assert call["action_type"] == "daily_digest_decision"
    assert call["status"] == "silenced_quiet"
    assert call["actor_type"] == "worker"
    assert call["actor_name"] == "agent_worker"


def test_sent_writes_audit_log(audit_writes):
    """When send_daily_digest returns True, audit_log row status=sent."""
    from app.workers import agent_worker

    db = _MockDB(last_date=None)
    with patch.object(agent_worker, "SessionLocal", return_value=db), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.is_digest_quiet", return_value=False), \
         patch("app.services.telegram_agent.send_daily_digest", return_value=True), \
         patch("app.workers.agent_worker.datetime") as mock_dt:
        mock_dt.now.return_value = _fixed_rome_9am()
        agent_worker._run_daily_digest()

    sent_rows = [c for c in audit_writes if c.get("status") == "sent"]
    assert len(sent_rows) == 1
    assert sent_rows[0]["action_type"] == "daily_digest_decision"


def test_send_failed_writes_audit_log(audit_writes):
    """When send_daily_digest returns False, audit_log row status=send_failed."""
    from app.workers import agent_worker

    db = _MockDB(last_date=None)
    with patch.object(agent_worker, "SessionLocal", return_value=db), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.is_digest_quiet", return_value=False), \
         patch("app.services.telegram_agent.send_daily_digest", return_value=False), \
         patch("app.workers.agent_worker.datetime") as mock_dt:
        mock_dt.now.return_value = _fixed_rome_9am()
        agent_worker._run_daily_digest()

    failed_rows = [c for c in audit_writes if c.get("status") == "send_failed"]
    assert len(failed_rows) == 1


def test_already_sent_today_does_not_write_audit_log(audit_writes):
    """When last_digest_date is already today, no audit row is written
    (we already recorded the decision on the first cycle of the day)."""
    from app.workers import agent_worker

    db = _MockDB(last_date=_FIXED_DATE_STR)
    with patch.object(agent_worker, "SessionLocal", return_value=db), \
         patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.workers.agent_worker.datetime") as mock_dt:
        mock_dt.now.return_value = _fixed_rome_9am()
        agent_worker._run_daily_digest()

    assert audit_writes == [], \
        f"expected 0 audit writes when already decided, got {audit_writes}"


def test_pre_08_rome_does_not_write_audit_log(audit_writes):
    """The 08:00 Rome gate runs BEFORE any audit write — no noise rows
    between 00:00 and 08:00 Rome (~32 cycles per night)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.workers import agent_worker

    pre_08 = datetime(2026, 4, 18, 7, 30, 0, tzinfo=ZoneInfo("Europe/Rome"))
    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.workers.agent_worker.datetime") as mock_dt:
        mock_dt.now.return_value = pre_08
        agent_worker._run_daily_digest()

    assert audit_writes == []
