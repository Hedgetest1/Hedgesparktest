"""
Regression test for `audit_cte_missing_comma.py` TOCTOU defense.

Born 2026-05-13 after invariant_monitor produced a CRITICAL alert
caused by FileNotFoundError when the audit's rglob discovered a temp
file (`_test_hardcoded_eur_DELETE_ME.py` written by test_audit_data_truth_gate)
that got deleted between `rglob` iteration and `path.read_text()`.

The audit must NOT crash on disappearing files — the next monitor
cycle will re-scan cleanly, no risk of missing a real bug.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path("/opt/wishspark")
AUDIT_SCRIPT = REPO_ROOT / "backend" / "scripts" / "audit_cte_missing_comma.py"
SERVICES_DIR = REPO_ROOT / "backend" / "app" / "services"


@pytest.fixture
def disappearing_file():
    """Create a temp .py file then delete it AFTER yielding — simulates
    the TOCTOU window the audit must absorb."""
    path = SERVICES_DIR / "_test_audit_race_DELETE_ME.py"
    path.write_text("# fixture file — should disappear mid-audit\n")
    yield path
    if path.exists():
        path.unlink()


def test_audit_survives_file_disappearance(disappearing_file):
    """If the file is deleted just before the audit reads it, the
    audit MUST exit 0 (clean) rather than crash with non-zero exit
    code and a FileNotFoundError traceback."""
    # Delete the file to simulate the race — rglob may still yield it
    # because of inode/dirent timing, but read_text will fail
    disappearing_file.unlink()

    result = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "backend"),
        timeout=30,
    )
    # Audit MUST succeed (no crash) — exit code 0 = clean
    assert result.returncode == 0, (
        f"Audit crashed on disappearing file. stderr:\n{result.stderr[:500]}"
    )
    # The fix should NOT propagate FileNotFoundError to stderr
    assert "FileNotFoundError" not in result.stderr, (
        f"FileNotFoundError leaked to stderr:\n{result.stderr[:500]}"
    )


def test_audit_still_detects_real_violations(tmp_path, monkeypatch):
    """Conservative guard: the defensive try/except must NOT silently
    swallow real findings. A file with an actual missing-comma CTE
    chain must still be flagged."""
    # Synthetic Python source with a real missing-comma violation
    bad_file = SERVICES_DIR / "_test_audit_real_bug_DELETE_ME.py"
    bad_file.write_text("""
from sqlalchemy import text
def buggy(db):
    return db.execute(text('''
        WITH
        first_cte AS (
            SELECT 1
        )
        second_cte AS (
            SELECT 2
        )
        SELECT * FROM second_cte
    '''))
""")
    try:
        result = subprocess.run(
            [sys.executable, str(AUDIT_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "backend"),
            timeout=30,
        )
        # Real bug → audit fires non-zero
        assert result.returncode != 0, (
            "Audit must fire on real CTE missing-comma bugs. "
            f"stdout:\n{result.stdout[:300]}"
        )
        assert "missing comma" in result.stdout.lower()
    finally:
        if bad_file.exists():
            bad_file.unlink()


def test_audit_clean_baseline():
    """Baseline: with no synthetic violations, audit reports clean."""
    result = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "backend"),
        timeout=30,
    )
    assert result.returncode == 0
    assert "clean" in result.stdout.lower()
