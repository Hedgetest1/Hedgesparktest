"""Pin the suicide-prevention rule in worker_watchdog.

Born 2026-05-04 evening after 131-restart suicide loop in
wishspark-agent-worker. Root cause: watchdog runs INSIDE agent_worker
and was attempting `pm2 restart wishspark-agent-worker` when its own
last_run_at appeared stale (because the previous cycle was killed
mid-run, never updated). The subprocess.run call hung waiting for
pm2 to return, while pm2 was sending SIGTERM to the same process.
KeyboardInterrupt → crash → PM2 respawn → identical scenario, loop.

Per `feedback_bugs_dont_inherit.md` 3-layer doctrine, this test
pins the SELF_HOST_PM2_NAME-skip behaviour so a future regression
(removing the skip, renaming the constant, or adding a new
restart path) re-surfaces this bug class instead of shipping
silently.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from app.services import worker_watchdog


def test_self_host_constant_is_agent_worker():
    """The watchdog's host process is documented and discoverable."""
    assert worker_watchdog.SELF_HOST_PM2_NAME == "wishspark-agent-worker"


def test_run_watchdog_skips_self_host_even_when_stale():
    """Even if agent_worker.last_run_at appears arbitrarily stale, the
    watchdog must NOT call pm2_restart against itself."""
    # Build a fake DB session that returns a 24h-stale agent_worker row
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_24h_ago = now - timedelta(hours=24)

    class FakeDB:
        def execute(self, *args, **kwargs):
            class _R:
                def fetchall(self):
                    # Match the WORKER_THRESHOLDS key shape
                    return [("agent_worker", stale_24h_ago)]
            return _R()

    # Patch _pm2_restart so we can assert it never fires for the host
    with patch.object(worker_watchdog, "_pm2_restart") as mock_restart:
        report = worker_watchdog.run_watchdog(FakeDB())

    assert mock_restart.call_count == 0, (
        "watchdog attempted pm2_restart against self — would trigger "
        "suicide loop. Verify SELF_HOST_PM2_NAME skip in run_watchdog()."
    )
    # Also verify report semantics: agent_worker counted as checked
    # but NOT counted as stale (since we skip BEFORE the staleness
    # check) — keeps watchdog's "stale" tally honest about workers
    # it can actually act on.
    assert report["checked"] >= 1
    assert report["stale"] == 0


def test_run_watchdog_does_act_on_other_stale_workers():
    """Sanity check: the suicide-prevention rule doesn't accidentally
    skip OTHER stale workers."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_24h_ago = now - timedelta(hours=24)

    class FakeDB:
        def execute(self, *args, **kwargs):
            class _R:
                def fetchall(self):
                    # gdpr_worker stale → should restart
                    return [("gdpr_worker", stale_24h_ago)]
            return _R()

    with patch.object(worker_watchdog, "_pm2_restart") as mock_restart, \
         patch.object(worker_watchdog, "_on_cooldown", return_value=False), \
         patch.object(worker_watchdog, "_set_cooldown"):
        mock_restart.return_value = True
        report = worker_watchdog.run_watchdog(FakeDB())

    assert mock_restart.call_count == 1
    assert mock_restart.call_args[0][0] == "wishspark-gdpr-worker"
    assert report["restarted"] == 1
