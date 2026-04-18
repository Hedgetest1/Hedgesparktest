"""
Test the audit_empty_path_fields audit — verify it catches the specific
bug class we wrote it for (empty-path return drops a data field that
the happy path emits).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


AUDIT_SCRIPT = "/opt/wishspark/backend/scripts/audit_empty_path_fields.py"
PY = "/opt/wishspark/backend/venv/bin/python"


def _run_audit_on_source(source: str, tmp_path: Path) -> tuple[int, str]:
    """Write `source` to a temp file under app/services/, run audit,
    return (exit_code, stdout)."""
    # The audit scans BACKEND_DIR/app/api + app/services. Place fixture
    # under services subdir (tmp_path will be ignored — we write to a
    # real location but use a distinct filename to avoid collision).
    target = Path("/opt/wishspark/backend/app/services/_test_empty_path_fixture.py")
    target.write_text(source)
    try:
        proc = subprocess.run(
            [PY, AUDIT_SCRIPT, "--strict"],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode, proc.stdout
    finally:
        if target.exists():
            target.unlink()


def test_audit_catches_currency_drop_in_empty_path(tmp_path):
    """Happy path emits `currency`, empty path doesn't → audit flags it."""
    source = '''
def fake_service(db, shop):
    if not shop:
        return {
            "shop_domain": shop,
            "total": 0,
            "count": 0,
            "headline": "empty",
        }
    return {
        "shop_domain": shop,
        "total": 100,
        "count": 5,
        "headline": "ok",
        "currency": "USD",
    }
'''
    rc, stdout = _run_audit_on_source(source, tmp_path)
    assert rc == 1, f"Expected exit 1 (--strict found findings), got {rc}"
    assert "_test_empty_path_fixture.py" in stdout
    assert "currency" in stdout.lower()
    assert "MISSING" in stdout


def test_audit_ignores_distinct_error_shape(tmp_path):
    """Pure error-shape {error: ...} is NOT flagged (distinct shape)."""
    source = '''
def fake_service_b(db, shop):
    if not shop:
        return {"error": "invalid_shop"}
    return {
        "shop_domain": shop,
        "total": 100,
        "count": 5,
        "headline": "ok",
        "currency": "USD",
    }
'''
    rc, stdout = _run_audit_on_source(source, tmp_path)
    # The distinct-shape return (only `{error}`) shares 0 fields with
    # the happy path, so the "near-sibling" filter should exclude it.
    # Audit must NOT flag this specific fixture (other pre-existing
    # findings may still surface — check the fixture specifically).
    fixture_findings = [
        line for line in stdout.split("\n")
        if "_test_empty_path_fixture.py" in line
    ]
    assert len(fixture_findings) == 0, (
        f"distinct-shape error path was flagged: {fixture_findings}"
    )


def test_audit_runs_without_strict_returns_0(tmp_path):
    """Default mode (no --strict) exits 0 even when findings exist."""
    proc = subprocess.run(
        [PY, AUDIT_SCRIPT],
        capture_output=True, text=True, timeout=30,
    )
    # No fixture this time — just run against current repo state.
    assert proc.returncode == 0, (
        f"Default mode should exit 0. stdout head: {proc.stdout[:200]}"
    )
