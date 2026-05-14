"""
Contract tests for the 3-layer env-file-perm defense
(audit_env_file_perms.py + env_bootstrap.load_env startup hook +
invariant_monitor._check_env_file_perms).

Born 2026-05-14 after external-CTO audit flagged
`/opt/wishspark/backend/.env` mode 644 (world-readable). The fix
spans three independent enforcement points; each is locked here.

Failure of any test = a layer was silently removed or weakened →
the class regression flagged the original sprint is reopening.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_AUDIT_SCRIPT = _BACKEND_DIR / "scripts" / "audit_env_file_perms.py"


# ---------------------------------------------------------------------------
# Layer 1 — static auditor (preflight)
# ---------------------------------------------------------------------------

def _run_auditor_with_env_files(*env_files: Path) -> subprocess.CompletedProcess:
    """Run the auditor with its _ENV_FILES patched to point at temp files.
    Returns the completed process so callers can assert on exit + stdout.
    """
    # Build a small wrapper script that monkey-patches the auditor's
    # _ENV_FILES tuple and calls main().
    wrapper = (
        f"import sys\n"
        f"sys.path.insert(0, {str(_BACKEND_DIR)!r})\n"
        f"import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location("
        f"  'audit_env_file_perms', {str(_AUDIT_SCRIPT)!r})\n"
        f"mod = importlib.util.module_from_spec(spec)\n"
        f"spec.loader.exec_module(mod)\n"
        f"from pathlib import Path as _P\n"
        f"mod._ENV_FILES = tuple(_P(p) for p in {[str(p) for p in env_files]!r})\n"
        f"sys.exit(mod.main())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        check=False,
    )


def test_auditor_passes_when_env_file_is_600(tmp_path):
    """A 0o600 env file is the canonical clean state."""
    f = tmp_path / "x.env"
    f.write_text("KEY=value")
    f.chmod(0o600)
    result = _run_auditor_with_env_files(f)
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_auditor_passes_when_env_file_is_400(tmp_path):
    """0o400 (read-only owner) is stricter than 600, also valid."""
    f = tmp_path / "x.env"
    f.write_text("KEY=value")
    f.chmod(0o400)
    result = _run_auditor_with_env_files(f)
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_auditor_fails_when_env_file_is_644(tmp_path):
    """The exact bug we're preventing — world-readable env file."""
    f = tmp_path / "x.env"
    f.write_text("SECRET=live_key")
    f.chmod(0o644)
    result = _run_auditor_with_env_files(f)
    assert result.returncode == 1
    assert "VIOLATIONS" in result.stdout
    assert "0o644" in result.stdout
    assert "chmod 600" in result.stdout


def test_auditor_fails_when_env_file_is_660(tmp_path):
    """Any group-readable bit is a violation, not just world."""
    f = tmp_path / "x.env"
    f.write_text("KEY=value")
    f.chmod(0o660)
    result = _run_auditor_with_env_files(f)
    assert result.returncode == 1
    assert "0o660" in result.stdout


def test_auditor_skips_missing_files(tmp_path):
    """A dev box without one .env should not trigger a drift alarm —
    env_bootstrap will fail-loud elsewhere if vars are actually needed."""
    nonexistent = tmp_path / "absent.env"
    result = _run_auditor_with_env_files(nonexistent)
    assert result.returncode == 0
    assert "clean" in result.stdout


# ---------------------------------------------------------------------------
# Layer 2 — env_bootstrap startup hook
# ---------------------------------------------------------------------------

def _run_bootstrap_audit_in_subprocess(env_file: Path) -> subprocess.CompletedProcess:
    """Drive env_bootstrap._audit_env_file_perms in a fresh subprocess
    so we can capture the CRITICAL log on stderr without fighting
    pytest's logging plugin (which silently absorbs records under
    this codebase's conftest)."""
    code = (
        f"import sys, logging\n"
        f"sys.path.insert(0, '/opt/wishspark/backend')\n"
        f"logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,\n"
        f"  format='%(levelname)s:%(name)s:%(message)s')\n"
        f"from app.core import env_bootstrap\n"
        f"from pathlib import Path\n"
        f"env_bootstrap._audit_env_file_perms(Path({str(env_file)!r}))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=False,
    )


def test_bootstrap_logs_critical_on_drift(tmp_path):
    """Layer 2: env_bootstrap._audit_env_file_perms must log CRITICAL
    when the .env file is group/world-readable. Non-blocking (no
    raise) — we don't brick production boot — but the message must
    surface."""
    f = tmp_path / "x.env"
    f.write_text("KEY=value")
    f.chmod(0o644)
    result = _run_bootstrap_audit_in_subprocess(f)
    assert result.returncode == 0, f"bootstrap raised: {result.stderr}"
    assert "CRITICAL:wishspark.env_bootstrap" in result.stderr, result.stderr
    assert "0o644" in result.stderr
    assert "group/world-readable" in result.stderr


def test_bootstrap_silent_when_perms_clean(tmp_path):
    """Clean perms → no CRITICAL log noise."""
    f = tmp_path / "x.env"
    f.write_text("KEY=value")
    f.chmod(0o600)
    result = _run_bootstrap_audit_in_subprocess(f)
    assert result.returncode == 0
    assert "CRITICAL:wishspark.env_bootstrap" not in result.stderr


def test_bootstrap_silent_when_file_missing(tmp_path):
    """Missing file → no log; dotenv loader handles absence elsewhere."""
    nonexistent = tmp_path / "absent.env"
    result = _run_bootstrap_audit_in_subprocess(nonexistent)
    assert result.returncode == 0
    assert "CRITICAL" not in result.stderr




# ---------------------------------------------------------------------------
# Layer 3 — invariant_monitor periodic check
# ---------------------------------------------------------------------------

def test_invariant_check_writes_alert_and_heals(db, tmp_path):
    """Layer-3 contract: _check_env_file_perms writes CRITICAL on drift
    and auto-resolves on the next clean cycle.

    Exercises the REAL backend/.env file (briefly flipping perms);
    skips if we cannot mutate it (e.g., locked-down CI sandbox)."""
    from app.services import invariant_monitor as im
    from sqlalchemy import text

    real_env = Path("/opt/wishspark/backend/.env")
    if not real_env.exists() or not os.access(real_env, os.W_OK):
        pytest.skip("cannot mutate real .env perms in this environment")

    original_mode = stat.S_IMODE(real_env.stat().st_mode)
    try:
        # RED: drift → alert written
        real_env.chmod(0o644)
        summary = {"checked": 0, "failed": 0, "alerts_written": 0}
        im._check_env_file_perms(db, summary)
        db.commit()
        assert summary["failed"] == 1
        assert summary["alerts_written"] == 1
        row = db.execute(text(
            "SELECT severity, resolved FROM ops_alerts "
            "WHERE source='invariant:env_perm_drift' "
            "ORDER BY created_at DESC LIMIT 1"
        )).first()
        assert row.severity == "critical"
        assert row.resolved is False

        # GREEN: heal → resolved
        real_env.chmod(0o600)
        summary2 = {"checked": 0, "failed": 0, "alerts_written": 0}
        im._check_env_file_perms(db, summary2)
        db.commit()
        assert summary2["failed"] == 0
        row2 = db.execute(text(
            "SELECT resolved FROM ops_alerts "
            "WHERE source='invariant:env_perm_drift' "
            "ORDER BY created_at DESC LIMIT 1"
        )).first()
        assert row2.resolved is True
    finally:
        real_env.chmod(original_mode)
