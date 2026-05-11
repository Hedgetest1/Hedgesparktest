"""Sprint A audit C4 preventer — tests for audit_subprocess_arg_origin.

Born 2026-05-11 Sprint A: Agent flagged C4 (orchestrator subprocess
surface) but on investigation ALL existing sites were already safe
(hardcoded args OR explicit allowlist). The structural value is the
PREVENTER: scripts/audit_subprocess_arg_origin.py enforces the pattern
on every future commit so a new contributor can't introduce an unsafe
subprocess call without an explicit allowlist annotation.

This test file pins the preventer's contract.
"""
from __future__ import annotations

import subprocess


def test_preventer_runs_clean_on_current_codebase():
    """The preventer MUST exit 0 today — all 7 existing variable-arg
    subprocess sites carry `# subprocess-allowlist:` annotations."""
    result = subprocess.run(
        ["./venv/bin/python", "scripts/audit_subprocess_arg_origin.py"],
        cwd="/opt/wishspark/backend",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"audit_subprocess_arg_origin must pass on current codebase. "
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "all subprocess calls safe" in result.stdout


def test_preventer_detects_unannotated_variable_arg(tmp_path):
    """Synthetic file with subprocess.run(target_var) without annotation
    → preventer must FAIL. Catches regression of the parser logic."""
    import textwrap
    test_module = tmp_path / "fake_service.py"
    test_module.write_text(textwrap.dedent("""
        import subprocess
        def restart(target):
            subprocess.run(["pm2", "restart", target], timeout=10)
    """))

    # Run the audit's _audit_file against the synthetic module directly
    import sys
    sys.path.insert(0, "/opt/wishspark/backend/scripts")
    from audit_subprocess_arg_origin import _audit_file
    findings = _audit_file(test_module)
    assert len(findings) == 1
    assert "subprocess.run" in findings[0][1]


def test_preventer_accepts_annotated_variable_arg(tmp_path):
    """Synthetic file with subprocess.run(target_var) PLUS the
    `# subprocess-allowlist:` annotation → preventer must PASS."""
    import textwrap
    test_module = tmp_path / "fake_service.py"
    test_module.write_text(textwrap.dedent("""
        import subprocess
        _ALLOWED = {"a", "b"}
        def restart(target):
            if target not in _ALLOWED:
                return
            # subprocess-allowlist: target validated against _ALLOWED above
            subprocess.run(["pm2", "restart", target], timeout=10)
    """))

    import sys
    sys.path.insert(0, "/opt/wishspark/backend/scripts")
    from audit_subprocess_arg_origin import _audit_file
    findings = _audit_file(test_module)
    assert findings == [], f"annotated site should pass, got: {findings}"


def test_preventer_accepts_all_literal_args(tmp_path):
    """Synthetic file with subprocess.run(["pm2", "restart", "wishspark-dashboard"])
    — all string literals → preventer must PASS without annotation."""
    import textwrap
    test_module = tmp_path / "fake_service.py"
    test_module.write_text(textwrap.dedent("""
        import subprocess
        def restart():
            subprocess.run(["pm2", "restart", "wishspark-dashboard"], timeout=10)
    """))

    import sys
    sys.path.insert(0, "/opt/wishspark/backend/scripts")
    from audit_subprocess_arg_origin import _audit_file
    findings = _audit_file(test_module)
    assert findings == [], f"all-literal site should pass, got: {findings}"
