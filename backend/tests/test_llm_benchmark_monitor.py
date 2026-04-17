"""
Tests for llm_benchmark_monitor — A5 weekly drift check.

We stub the pytest subprocess so tests run in milliseconds. Redis state
is cleaned per-test to avoid cross-test leakage of the weekly gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text


def _count_alerts(db, alert_type: str) -> int:
    return int(db.execute(
        text("SELECT COUNT(*) FROM ops_alerts WHERE alert_type = :t"),
        {"t": alert_type},
    ).scalar() or 0)


@pytest.fixture(autouse=True)
def _clean_llm_bench_redis():
    """Clear Redis keys the monitor writes so each test starts fresh."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match="hs:llm_bench:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass  # SILENT-OK: test hygiene
    yield


class TestScheduleGate:
    def test_outside_window_returns_noop(self, db):
        """Non-Sunday invocation → skipped via weekday check."""
        from app.services.llm_benchmark_monitor import run_weekly_check
        # 2026-04-15 was a Wednesday — weekday 2, not Sunday (6)
        fake_wednesday = datetime(2026, 4, 15, 5, 0, 0)
        with patch("app.services.llm_benchmark_monitor.datetime") as mdt:
            mdt.now.return_value = fake_wednesday.replace(tzinfo=timezone.utc)
            mdt.side_effect = lambda *a, **k: datetime(*a, **k)
            result = run_weekly_check(db)
        assert result == {"ran": False, "reason": "outside_window"}

    def test_force_bypasses_window(self, db):
        """force=True ignores both gates."""
        from app.services.llm_benchmark_monitor import run_weekly_check
        with patch(
            "app.services.llm_benchmark_monitor._run_benchmark_subprocess",
            return_value={"passed": 17, "failed": 0, "errored": 0, "total": 17, "exit_code": 0},
        ):
            result = run_weekly_check(db, force=True)
        assert result["ran"] is True
        assert result["status"] == "ok"
        assert result["passed"] == 17


class TestRegressionDetection:
    def test_clean_run_no_alert(self, db):
        """17 passed / 0 failed → no alert."""
        from app.services.llm_benchmark_monitor import run_weekly_check
        with patch(
            "app.services.llm_benchmark_monitor._run_benchmark_subprocess",
            return_value={"passed": 17, "failed": 0, "errored": 0, "total": 17, "exit_code": 0},
        ):
            result = run_weekly_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] == 0
        assert _count_alerts(db, "llm_benchmark_regression") == 0

    def test_fail_count_triggers_alert(self, db):
        """Even 1 failed test fires the regression alert."""
        from app.services.llm_benchmark_monitor import run_weekly_check
        with patch(
            "app.services.llm_benchmark_monitor._run_benchmark_subprocess",
            return_value={"passed": 16, "failed": 1, "errored": 0, "total": 17, "exit_code": 1},
        ):
            result = run_weekly_check(db, force=True)
        assert result["alerts_fired"] == 1
        assert _count_alerts(db, "llm_benchmark_regression") == 1

    def test_pass_count_drop_vs_baseline_triggers_alert(self, db):
        """After an earlier run with 17 passed, a later run with 15 passed
        and 0 failed still fires regression because baseline = 17."""
        from app.services.llm_benchmark_monitor import run_weekly_check
        # First run: 17 passed (establishes baseline in history)
        with patch(
            "app.services.llm_benchmark_monitor._run_benchmark_subprocess",
            return_value={"passed": 17, "failed": 0, "errored": 0, "total": 17, "exit_code": 0},
        ):
            run_weekly_check(db, force=True)
        # Clear weekly gate so the second force can proceed (weekly gate
        # is INSIDE the force-bypass path anyway but let's be explicit).
        from app.core.redis_client import _client
        rc = _client()
        cursor = 0
        while True:
            cursor, keys = rc.scan(cursor=cursor, match="hs:llm_bench:last_run:*", count=50)
            if keys:
                rc.delete(*keys)
            if cursor == 0:
                break

        # Second run: 15 passed (drop of 2 vs baseline) — regression fires
        with patch(
            "app.services.llm_benchmark_monitor._run_benchmark_subprocess",
            return_value={"passed": 15, "failed": 0, "errored": 0, "total": 15, "exit_code": 0},
        ):
            result = run_weekly_check(db, force=True)
        assert result["regression"] is True
        assert result["alerts_fired"] == 1
        assert _count_alerts(db, "llm_benchmark_regression") == 1


class TestFailurePath:
    def test_subprocess_failure_emits_alert(self, db):
        """_run_benchmark_subprocess returns None → llm_benchmark_run_failed."""
        from app.services.llm_benchmark_monitor import run_weekly_check
        with patch(
            "app.services.llm_benchmark_monitor._run_benchmark_subprocess",
            return_value=None,
        ):
            result = run_weekly_check(db, force=True)
        assert result["status"] == "subprocess_failed"
        assert _count_alerts(db, "llm_benchmark_run_failed") >= 1


class TestPytestOutputParser:
    def test_parse_standard_summary(self):
        from app.services.llm_benchmark_monitor import _parse_pytest_summary
        out = "tests/test_llm_propose_bench.py ...\n\n============================== 17 passed in 0.52s =============================="
        r = _parse_pytest_summary(out, "")
        assert r["passed"] == 17
        assert r["failed"] == 0

    def test_parse_fail_and_pass(self):
        from app.services.llm_benchmark_monitor import _parse_pytest_summary
        out = "========== 3 failed, 14 passed in 0.52s =========="
        r = _parse_pytest_summary(out, "")
        assert r["passed"] == 14
        assert r["failed"] == 3
