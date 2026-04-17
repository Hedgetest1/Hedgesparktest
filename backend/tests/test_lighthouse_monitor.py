"""
Tests for lighthouse_monitor — A3 coverage.

Covers:
  - Schedule window gate (outside window = no-op)
  - Daily dedup gate (already ran = no-op)
  - Regression detection against synthetic history
  - No-regression path (healthy run, no alerts)
  - Subprocess failure path emits lighthouse_run_failed

No real Lighthouse subprocess is invoked — we stub _run_lighthouse_subprocess
so tests run in <1s and don't need a dashboard on localhost.
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
def _clean_lighthouse_redis():
    """Per-test isolation — clear Redis keys the monitor writes."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cursor = 0
            while True:
                cursor, keys = rc.scan(cursor=cursor, match="hs:lighthouse:*", count=200)
                if keys:
                    rc.delete(*keys)
                if cursor == 0:
                    break
    except Exception:
        pass  # SILENT-OK: test hygiene
    yield


def _stub_lh_result(routes_metrics: list[tuple[str, dict]]) -> dict:
    """Build a fake Lighthouse JSON result for the given (route, metrics) pairs."""
    return {
        "base_url": "http://127.0.0.1:3000",
        "generated_at": "2026-04-17T12:00:00Z",
        "budgets": {"performance": 85, "accessibility": 95, "best_practices": 85, "seo": 90},
        "routes": [
            {"route": r, "scores": {}, "metrics": m}
            for r, m in routes_metrics
        ],
        "failures": [],
    }


class TestScheduleGate:
    def test_outside_window_noop(self, db):
        """Force-call at a mocked time outside 02-04 UTC → skipped."""
        from app.services.lighthouse_monitor import run_nightly_check
        fake_noon = datetime(2026, 4, 17, 12, 0, 0)
        with patch("app.services.lighthouse_monitor.datetime") as mdt:
            mdt.now.return_value = fake_noon.replace(tzinfo=timezone.utc)
            mdt.side_effect = lambda *a, **k: datetime(*a, **k)
            # Also keep datetime constructor working via side_effect
            result = run_nightly_check(db)
        assert result == {"ran": False, "reason": "outside_window"}

    def test_force_bypasses_window(self, db):
        """force=True should bypass both gates."""
        from app.services.lighthouse_monitor import run_nightly_check
        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=_stub_lh_result([("/app", {"lcp_ms": 1200.0, "cls": 0.05, "tbt_ms": 150.0})]),
        ):
            result = run_nightly_check(db, force=True)
        assert result["ran"] is True
        assert result["status"] == "ok"

    def test_daily_gate_blocks_second_run(self, db):
        """After a successful run, second call same day returns already_ran."""
        from app.services.lighthouse_monitor import run_nightly_check
        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=_stub_lh_result([("/app", {"lcp_ms": 1200.0, "cls": 0.05, "tbt_ms": 150.0})]),
        ):
            first = run_nightly_check(db, force=True)
            assert first["ran"] is True
            # Second call (no force) at the same time should be gated by
            # the daily flag we just set.
            with patch("app.services.lighthouse_monitor._in_schedule_window", return_value=True):
                second = run_nightly_check(db)
        assert second == {"ran": False, "reason": "already_ran"}


class TestRegressionDetection:
    def test_no_regression_when_metrics_stable(self, db):
        """Seed 7 days of stable history → today's similar metrics fire no alert."""
        from app.services.lighthouse_monitor import (
            run_nightly_check, _append_history,
        )
        from app.core.redis_client import _client
        rc = _client()
        # Seed history with 7 stable samples (LCP 1200ms)
        for i in range(7):
            _append_history(rc, "/app", {"lcp_ms": 1200.0, "tbt_ms": 150.0, "cls": 0.05}, f"2026-04-{10+i}T03:00")

        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=_stub_lh_result([("/app", {"lcp_ms": 1250.0, "tbt_ms": 150.0, "cls": 0.05})]),
        ):
            result = run_nightly_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] == 0
        assert _count_alerts(db, "lighthouse_regression") == 0

    def test_lcp_regression_fires_alert(self, db):
        """7 days of LCP≈1200ms + today 2000ms (+67%, +800ms) → alert."""
        from app.services.lighthouse_monitor import (
            run_nightly_check, _append_history,
        )
        from app.core.redis_client import _client
        rc = _client()
        for i in range(7):
            _append_history(rc, "/app", {"lcp_ms": 1200.0, "tbt_ms": 150.0, "cls": 0.05}, f"2026-04-{10+i}T03:00")

        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=_stub_lh_result([("/app", {"lcp_ms": 2000.0, "tbt_ms": 150.0, "cls": 0.05})]),
        ):
            result = run_nightly_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] >= 1
        assert _count_alerts(db, "lighthouse_regression") >= 1

    def test_sparse_history_no_alert(self, db):
        """< 3 historical samples → detector skips — baseline too noisy."""
        from app.services.lighthouse_monitor import (
            run_nightly_check, _append_history,
        )
        from app.core.redis_client import _client
        rc = _client()
        # Only 2 samples — below _compute_baseline minimum
        _append_history(rc, "/app", {"lcp_ms": 1200.0}, "2026-04-15T03:00")
        _append_history(rc, "/app", {"lcp_ms": 1300.0}, "2026-04-16T03:00")

        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=_stub_lh_result([("/app", {"lcp_ms": 5000.0, "tbt_ms": 500.0, "cls": 0.15})]),
        ):
            result = run_nightly_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] == 0

    def test_cls_regression_fires_on_absolute_delta(self, db):
        """CLS regression is abs-only (0-1 unitless)."""
        from app.services.lighthouse_monitor import (
            run_nightly_check, _append_history,
        )
        from app.core.redis_client import _client
        rc = _client()
        for i in range(5):
            _append_history(rc, "/app", {"lcp_ms": 1200.0, "tbt_ms": 150.0, "cls": 0.01}, f"2026-04-{10+i}T03:00")

        # CLS jumps from 0.01 → 0.10 (+0.09 > 0.05 threshold)
        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=_stub_lh_result([("/app", {"lcp_ms": 1250.0, "tbt_ms": 150.0, "cls": 0.10})]),
        ):
            result = run_nightly_check(db, force=True)
        assert result["ran"] is True
        assert result["alerts_fired"] >= 1


class TestFailurePath:
    def test_subprocess_failure_emits_alert(self, db):
        """Lighthouse subprocess returns None → lighthouse_run_failed fires."""
        from app.services.lighthouse_monitor import run_nightly_check
        with patch(
            "app.services.lighthouse_monitor._run_lighthouse_subprocess",
            return_value=None,
        ):
            result = run_nightly_check(db, force=True)
        assert result["ran"] is True
        assert result["status"] == "subprocess_failed"
        assert _count_alerts(db, "lighthouse_run_failed") >= 1
