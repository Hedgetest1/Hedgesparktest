"""
Inverse test for scripts/audit_data_truth.py.

An audit script wired into preflight --strict is only valuable if it
actually BITES when a regression is injected. Without this test, a
future refactor could silently weaken the regex / allowlist the world /
hit a parse error and the gate would pass while the real bug ships.

Strategy: create a throwaway .py file under app/ with a known-bad line,
run the script with --strict via subprocess, assert exit 1, then delete
the file. Uses a subprocess so we exercise the real CLI entry point the
preflight hook invokes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
APP_DIR = BACKEND / "app"
AUDIT_SCRIPT = BACKEND / "scripts" / "audit_data_truth.py"


def _run_audit(strict: bool) -> tuple[int, str]:
    """Invoke the audit script via subprocess and return (returncode, combined output)."""
    cmd = [sys.executable, str(AUDIT_SCRIPT)]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout + proc.stderr


def test_audit_strict_passes_on_clean_tree():
    """Baseline: the current tree is clean and --strict exits 0."""
    code, out = _run_audit(strict=True)
    assert code == 0, f"audit_data_truth --strict exited {code} on clean tree:\n{out}"


@pytest.fixture
def injected_bad_file():
    """Drop a throwaway .py file under app/ with a critical regression,
    yield its path, and clean up no matter what.
    """
    path = APP_DIR / "services" / "_test_regression_probe_DELETE_ME.py"
    path.write_text(
        '"""Throwaway fixture file for test_audit_data_truth_gate."""\n'
        'from sqlalchemy import text\n\n'
        'def bad_query(db, shop):\n'
        '    # CRITICAL regression: SUM(total_price) without any currency filter\n'
        '    # anywhere in the query. The audit must catch this.\n'
        '    return db.execute(text("""\n'
        '        SELECT SUM(total_price) AS total_revenue\n'
        '        FROM shop_orders\n'
        '        WHERE shop_domain = :shop\n'
        '          AND created_at >= NOW() - INTERVAL \'30 days\'\n'
        '    """), {"shop": shop}).scalar()\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_strict_bites_on_sum_without_currency(injected_bad_file):
    """
    Lock-in: inject a SUM(total_price) with no currency filter ANYWHERE
    in the surrounding SQL block. `--strict` must exit 1 and name the
    file in the output. If this test ever starts passing under a weaker
    regex, the preflight gate has become theater.
    """
    code, out = _run_audit(strict=True)
    assert code == 1, (
        f"audit_data_truth failed to detect injected regression "
        f"(exit={code}). Output:\n{out}"
    )
    assert str(injected_bad_file.name) in out, (
        f"output does not mention the bad file {injected_bad_file.name}:\n{out}"
    )
    assert "money_aggregation_no_currency" in out, (
        f"output does not classify as money_aggregation_no_currency:\n{out}"
    )


@pytest.fixture
def injected_hardcoded_euro():
    """Drop a file with a hardcoded € symbol outside any allowlisted context."""
    path = APP_DIR / "services" / "_test_hardcoded_eur_DELETE_ME.py"
    path.write_text(
        '"""Throwaway fixture file for test_audit_data_truth_gate."""\n\n'
        'def fake_render(amount):\n'
        '    return f"\u20ac{amount:,.0f}/mo at risk"\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_bites_on_hardcoded_euro_symbol(injected_hardcoded_euro):
    """
    The warning-level 'hardcoded_currency' check must flag an inline €
    outside the allowlisted detection files. --strict ignores warnings
    (only critical findings block the commit), so we run WITHOUT --strict
    and verify the output mentions the finding.
    """
    code, out = _run_audit(strict=False)
    # Non-strict exit code is always 0 (warnings don't block by design)
    assert code == 0
    assert str(injected_hardcoded_euro.name) in out, (
        f"hardcoded_currency finding should be surfaced:\n{out}"
    )
    assert "hardcoded_currency" in out


@pytest.fixture
def injected_frontend_hardcoded_euro(tmp_path):
    """Inject a fake .tsx file under dashboard/src/ with hardcoded €."""
    from pathlib import Path as _Path
    dashboard_src = _Path("/opt/wishspark/dashboard/src")
    if not dashboard_src.exists():
        pytest.skip("dashboard/src not present in this checkout")
    path = dashboard_src / "app" / "_DELETE_ME_probe.tsx"
    path.write_text(
        "// Throwaway fixture file for frontend audit test\n"
        "export function Demo() {\n"
        "  return <span>€42 hardcoded</span>;\n"
        "}\n"
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_flags_frontend_hardcoded_currency(injected_frontend_hardcoded_euro):
    """
    The frontend scan must catch a hardcoded € in dashboard TSX. It
    reports as `warning` severity (non-blocking in strict mode) so
    pre-existing violations don't block commits — but the finding IS
    surfaced so drift is visible.
    """
    code, out = _run_audit(strict=False)
    # Non-strict exit is 0 regardless of warning count.
    assert code == 0
    assert injected_frontend_hardcoded_euro.name in out, (
        "frontend_hardcoded_currency must report injected file:\n"
        + out[:1000]
    )
    assert "frontend_hardcoded_currency" in out


def test_audit_strict_still_ignores_frontend_warnings(injected_frontend_hardcoded_euro):
    """
    --strict currently blocks on CRITICAL only. Frontend violations are
    warnings by design — the full-migration sweep is a follow-up effort;
    making every hardcoded € block commits would freeze the repo until
    the sweep lands.
    """
    code, _ = _run_audit(strict=True)
    # Clean repo → exit 0 even with 116 frontend warnings.
    assert code == 0


def test_audit_script_is_wired_into_preflight():
    """
    Locks in the preflight integration. If a future refactor drops the
    gate step from preflight.sh, this test breaks and flags the
    regression at commit time instead of allowing a drift to ship.
    """
    preflight = (BACKEND / "scripts" / "preflight.sh").read_text()
    assert "audit_data_truth.py" in preflight, (
        "preflight.sh must reference audit_data_truth.py — it is the "
        "currency/timezone/credentials invariant gate"
    )
    assert "--strict" in preflight, "the gate must run in --strict mode"
