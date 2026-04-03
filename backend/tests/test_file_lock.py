"""
Tests for app.core.file_lock — distributed file-level locking.
"""
import pytest

from app.core.file_lock import (
    try_lock_files,
    release_file_locks,
    _clear_all_locks,
    _check_lock,
)


@pytest.fixture(autouse=True)
def clean_locks():
    """Clear all locks before each test."""
    _clear_all_locks()
    yield
    _clear_all_locks()


class TestFileLock:
    """Tests for the file lock mechanism."""

    def test_acquire_single_file(self):
        result = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result.acquired
        assert "app/services/foo.py" in result.locked_files

    def test_acquire_multiple_files(self):
        result = try_lock_files(
            ["app/services/foo.py", "app/services/bar.py"],
            "agent_a",
        )
        assert result.acquired
        assert len(result.locked_files) == 2

    def test_conflict_different_owner(self):
        result1 = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result1.acquired

        result2 = try_lock_files(["app/services/foo.py"], "agent_b")
        assert not result2.acquired
        assert len(result2.conflicts) == 1
        assert result2.conflicts[0]["held_by"] == "agent_a"

    def test_same_owner_reacquire(self):
        """Same owner can re-lock the same file."""
        result1 = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result1.acquired

        result2 = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result2.acquired

    def test_release_and_reacquire(self):
        result1 = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result1.acquired

        release_file_locks(["app/services/foo.py"], "agent_a")

        result2 = try_lock_files(["app/services/foo.py"], "agent_b")
        assert result2.acquired

    def test_release_wrong_owner_no_effect(self):
        result1 = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result1.acquired

        # agent_b tries to release agent_a's lock — should fail silently
        release_file_locks(["app/services/foo.py"], "agent_b")

        # Lock should still be held by agent_a
        assert _check_lock("app/services/foo.py") == "agent_a"

    def test_all_or_nothing_on_conflict(self):
        """If any file is locked, no files get locked."""
        try_lock_files(["app/services/bar.py"], "agent_a")

        result = try_lock_files(
            ["app/services/foo.py", "app/services/bar.py"],
            "agent_b",
        )
        assert not result.acquired
        # foo.py should NOT be locked by agent_b
        assert _check_lock("app/services/foo.py") is None

    def test_empty_files(self):
        result = try_lock_files([], "agent_a")
        assert result.acquired

    def test_deduplication(self):
        result = try_lock_files(
            ["app/services/foo.py", "app/services/foo.py"],
            "agent_a",
        )
        assert result.acquired
        # Should deduplicate
        assert len(result.locked_files) == 1

    def test_path_normalization(self):
        """Different path formats for the same file should conflict."""
        result1 = try_lock_files(["app/services/foo.py"], "agent_a")
        assert result1.acquired

        result2 = try_lock_files(
            ["/opt/wishspark/backend/app/services/foo.py"],
            "agent_b",
        )
        assert not result2.acquired
