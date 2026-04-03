"""
Tests for app.core.pre_apply_guard — unified enforcement gate.
"""
import pytest

from app.core.pre_apply_guard import guard_pre_apply, release_guard
from app.core.file_lock import _clear_all_locks
from app.core.tier_check import TIER_0, TIER_1, TIER_2


@pytest.fixture(autouse=True)
def clean_locks():
    """Clear all locks before each test."""
    _clear_all_locks()
    yield
    _clear_all_locks()


class TestGuardPreApply:
    """Tests for the unified pre-apply guard."""

    def test_allowed_tier0(self):
        result = guard_pre_apply(
            files=["app/services/revenue_metrics.py"],
            patch_diff="+    return total",
            owner="bugfix_pipeline",
        )
        assert result.allowed
        assert result.tier == TIER_0
        assert not result.blocked
        assert result.files_locked
        # Clean up
        release_guard(["app/services/revenue_metrics.py"], "bugfix_pipeline")

    def test_blocked_tier2(self):
        result = guard_pre_apply(
            files=["app/core/token_crypto.py"],
            patch_diff="+    key = new_key",
            owner="bugfix_pipeline",
        )
        assert not result.allowed
        assert result.blocked
        assert result.tier == TIER_2
        assert result.block_reason is not None

    def test_approval_required_tier1(self):
        result = guard_pre_apply(
            files=["app/services/orchestrator.py"],
            owner="bugfix_pipeline",
        )
        assert not result.allowed
        assert not result.blocked
        assert result.tier == TIER_1
        # Still acquires locks even for TIER_1
        assert result.files_locked
        release_guard(["app/services/orchestrator.py"], "bugfix_pipeline")

    def test_dangerous_diff_blocks(self):
        result = guard_pre_apply(
            files=["app/services/something.py"],
            patch_diff="+    result = eval(user_input)",
            owner="bugfix_pipeline",
        )
        assert result.blocked
        assert result.tier == TIER_2

    def test_file_lock_conflict_blocks(self):
        # Agent A locks a file
        first = guard_pre_apply(
            files=["app/services/foo.py"],
            owner="agent_a",
        )
        assert first.allowed

        # Agent B tries to modify the same file
        second = guard_pre_apply(
            files=["app/services/foo.py"],
            owner="agent_b",
        )
        assert not second.allowed
        assert second.blocked
        assert "conflict" in second.block_reason.lower()

        release_guard(["app/services/foo.py"], "agent_a")

    def test_frontend_build_flag(self):
        result = guard_pre_apply(
            files=["dashboard/src/App.tsx"],
            owner="test",
        )
        assert result.requires_frontend_build
        release_guard(["dashboard/src/App.tsx"], "test")

    def test_tracker_bump_flag(self):
        result = guard_pre_apply(
            files=["tracker/spark-tracker.js"],
            owner="test",
        )
        assert result.requires_tracker_bump
        release_guard(["tracker/spark-tracker.js"], "test")

    def test_empty_files_allowed(self):
        result = guard_pre_apply(files=[], owner="test")
        assert result.allowed
        assert result.tier == TIER_0

    def test_release_guard_frees_locks(self):
        guard_pre_apply(
            files=["app/services/foo.py"],
            owner="agent_a",
        )
        release_guard(["app/services/foo.py"], "agent_a")

        # Now agent_b can lock it
        result = guard_pre_apply(
            files=["app/services/foo.py"],
            owner="agent_b",
        )
        assert result.allowed
        release_guard(["app/services/foo.py"], "agent_b")


class TestGuardIntegrationWithTierCheck:
    """Verify guard correctly delegates to tier_check."""

    def test_mixed_tier_files(self):
        result = guard_pre_apply(
            files=["app/services/foo.py", "app/core/token_crypto.py"],
            owner="test",
        )
        assert result.tier == TIER_2
        assert result.blocked

    def test_blast_radius_escalation(self):
        files = [f"app/services/svc_{i}.py" for i in range(6)]
        result = guard_pre_apply(files=files, owner="test")
        assert result.tier == TIER_1
        assert not result.allowed
        assert not result.blocked  # TIER_1 is not blocked, just needs approval
        release_guard(files, "test")
