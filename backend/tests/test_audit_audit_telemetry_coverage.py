"""Test the audit_audit_telemetry_coverage regression pin.

The pin must fail when a WIRED audit loses its `_audit_telemetry_shim`
import, and pass when every wired audit still imports the shim.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_audit_telemetry_coverage.py")


def _load_module():
    name = "audit_audit_telemetry_coverage_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_imports_shim_detects_from_import(tmp_path):
    mod = _load_module()
    p = tmp_path / "audit_x.py"
    p.write_text(
        "def main():\n"
        "    from _audit_telemetry_shim import emit\n"
        "    emit('x', 0)\n"
    )
    assert mod._imports_shim(p) is True


def test_imports_shim_detects_plain_import(tmp_path):
    mod = _load_module()
    p = tmp_path / "audit_y.py"
    p.write_text(
        "import _audit_telemetry_shim\n"
        "def main():\n"
        "    _audit_telemetry_shim.emit('y', 0)\n"
    )
    assert mod._imports_shim(p) is True


def test_imports_shim_returns_false_when_missing(tmp_path):
    mod = _load_module()
    p = tmp_path / "audit_z.py"
    p.write_text(
        "def main():\n"
        "    return 0\n"
    )
    assert mod._imports_shim(p) is False


def test_imports_shim_not_fooled_by_string_literal(tmp_path):
    """String-literal mention of the shim name in docstrings/comments
    must NOT count as an import — AST-based check ignores non-import
    nodes."""
    mod = _load_module()
    p = tmp_path / "audit_q.py"
    p.write_text(
        '"""This audit would use _audit_telemetry_shim if it were wired."""\n'
        "def main():\n"
        "    print('_audit_telemetry_shim')\n"
        "    return 0\n"
    )
    assert mod._imports_shim(p) is False


def test_live_tree_all_wired_audits_pass():
    """Sanity on the current repo: every audit in WIRED_AUDITS must
    already import the shim, otherwise the pin has a baseline bug."""
    mod = _load_module()
    rc = mod.main([])
    assert rc == 0


def test_main_fails_when_wired_entry_missing_import(monkeypatch, tmp_path):
    """If we monkeypatch WIRED_AUDITS to point at a throwaway file that
    doesn't import the shim, main() must exit 1 and name the file."""
    mod = _load_module()

    bogus = tmp_path / "audit_bogus.py"
    bogus.write_text("def main():\n    return 0\n")

    monkeypatch.setattr(mod, "WIRED_AUDITS", {"audit_bogus.py"})
    monkeypatch.setattr(mod, "SCRIPTS_DIR", tmp_path)

    rc = mod.main([])
    assert rc == 1
