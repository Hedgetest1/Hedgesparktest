"""Locks the LLM-aware downgrade in _assess_pipeline_liveness.

Pre-fix (2026-05-05): pending candidates + 0 proposals/applied → critical.
This produced 4-per-hour Telegram spam (672 in 7 days observed) when LLM
provider was credit-exhausted, because the system flagged "DEAD" but
agent_worker still fired Telegram on every cycle.

Post-fix: distinguish "broken" (calls happen, fail) from "parked" (calls
don't happen at all = external dep blocker = founder action required).
Parked → `degraded` (no Telegram from agent_worker guard); Broken →
`critical` (genuine system fault).

The 3683 _loadtest_*-ghost-shop alerts on 2026-05-04 were a separate
incident; cleanup query in this same commit. No test for that — it's
data hygiene, not a code invariant.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services.system_health_synthesizer import _assess_pipeline_liveness


def _mock_db_with_counts(pending: int, proposals_7d: int, applied_7d: int):
    """Stub a Session whose execute().fetchone() returns the configured counts
    in the order the assessor calls them: candidates, proposals, applied."""
    db = MagicMock()
    db.execute.side_effect = [
        MagicMock(fetchone=lambda: (pending,)),
        MagicMock(fetchone=lambda: (proposals_7d,)),
        MagicMock(fetchone=lambda: (applied_7d,)),
    ]
    return db


def test_liveness_critical_when_calls_happening_but_no_proposals():
    """LLM is being called but no proposals produced → genuine system fault."""
    db = _mock_db_with_counts(pending=10, proposals_7d=0, applied_7d=0)
    now = datetime.now(timezone.utc)
    with patch(
        "app.core.llm_budget.get_usage_summary",
        return_value={"global_calls_today": 25, "monthly_cost_eur": 0.50},
    ):
        result = _assess_pipeline_liveness(db, now)
    assert result.status == "critical"
    assert "system fault" in result.detail
    assert "25 LLM calls today" in result.detail


def test_liveness_degraded_when_no_llm_activity_at_all():
    """LLM not even being called → external blocker, not system fault.
    Downgrade to degraded so agent_worker's guard suppresses Telegram."""
    db = _mock_db_with_counts(pending=10, proposals_7d=0, applied_7d=0)
    now = datetime.now(timezone.utc)
    with patch(
        "app.core.llm_budget.get_usage_summary",
        return_value={"global_calls_today": 0, "monthly_cost_eur": 0.0},
    ):
        result = _assess_pipeline_liveness(db, now)
    assert result.status == "degraded"
    assert "Pipeline parked" in result.detail
    assert "awaiting external" in result.detail


def test_liveness_degraded_when_budget_module_unavailable():
    """If llm_budget import fails (rare), do NOT crash — fall through
    to safe-default critical so the system fault is at least visible."""
    db = _mock_db_with_counts(pending=10, proposals_7d=0, applied_7d=0)
    now = datetime.now(timezone.utc)
    with patch(
        "app.core.llm_budget.get_usage_summary",
        side_effect=Exception("budget module crash"),
    ):
        result = _assess_pipeline_liveness(db, now)
    # Falls through to critical with -1 sentinel values
    assert result.status == "critical"


def test_liveness_healthy_when_no_pending_candidates():
    """Empty queue, no work to do — healthy regardless of LLM state."""
    db = _mock_db_with_counts(pending=0, proposals_7d=0, applied_7d=0)
    now = datetime.now(timezone.utc)
    result = _assess_pipeline_liveness(db, now)
    assert result.status == "healthy"


def test_liveness_degraded_when_proposals_but_zero_applied():
    """Proposals being generated but reviewer rejects everything →
    stalled, not dead. Pre-existing branch unchanged by 2026-05-05 fix."""
    db = _mock_db_with_counts(pending=10, proposals_7d=5, applied_7d=0)
    now = datetime.now(timezone.utc)
    result = _assess_pipeline_liveness(db, now)
    assert result.status == "degraded"
    assert "stalled" in result.detail


def test_liveness_healthy_when_applying():
    """Proposals applied = pipeline working — healthy."""
    db = _mock_db_with_counts(pending=10, proposals_7d=5, applied_7d=3)
    now = datetime.now(timezone.utc)
    result = _assess_pipeline_liveness(db, now)
    assert result.status == "healthy"
