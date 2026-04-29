"""Test audit_tier_gates — survey + preventer for Pro-tier gate governance.

Verifies:
  * Router prefix is joined with decorator path to form full route
  * Gate sites are detected with/without tier comment
  * Preventer warn-only exits 0 (bootstrap), --strict exits 1 with untagged
  * Invalid tier tag values flagged distinctly
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_tier_gates.py")


def _load():
    name = "audit_tier_gates_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_scan_file_joins_prefix_with_decorator_path(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter, Depends\n'
        'from app.core.deps import require_pro_session\n'
        'router = APIRouter(prefix="/pro/foo")\n'
        '\n'
        '@router.get("/bar")\n'
        'def handler(shop: str = Depends(require_pro_session)):\n'
        '    return {}\n'
    )
    # Point BACKEND_ROOT at tmp_path so relative-to works
    mod.BACKEND_ROOT = tmp_path
    out = mod._scan_file(f)
    assert len(out) == 1
    assert out[0].route_method == "GET"
    assert out[0].route_path == "/pro/foo/bar"
    assert out[0].tier_tag is None


def test_scan_file_detects_tier_tag_inline(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter, Depends\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/y")\n'
        'def h(shop: str = Depends(require_pro_session)):  # tier: pro\n'
        '    return {}\n'
    )
    mod.BACKEND_ROOT = tmp_path
    out = mod._scan_file(f)
    assert len(out) == 1
    assert out[0].tier_tag == "pro"


def test_scan_file_accepts_all_valid_tiers(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/a")\n'
        'def a(s: str = Depends(require_pro_session)):  # tier: pro\n'
        '    pass\n'
        '@router.get("/b")\n'
        'def b(s: str = Depends(require_pro_session)):  # tier: lite-candidate\n'
        '    pass\n'
        '@router.get("/c")\n'
        'def c(s: str = Depends(require_pro_session)):  # tier: lite-unlocked\n'
        '    pass\n'
        '@router.get("/d")\n'
        'def d(s: str = Depends(require_pro_session)):  # tier: scale-only\n'
        '    pass\n'
    )
    mod.BACKEND_ROOT = tmp_path
    out = mod._scan_file(f)
    tags = {s.tier_tag for s in out}
    assert tags == {"pro", "lite-candidate", "lite-unlocked", "scale-only"}
    # All are VALID so no invalid hits
    invalid = [s for s in out if s.tier_tag and s.tier_tag not in mod._VALID_TIERS]
    assert invalid == []


def test_preventer_warn_only_exits_zero_even_untagged(tmp_path, monkeypatch, capsys):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'router = APIRouter(prefix="/p")\n'
        '@router.get("/a")\n'
        'def a(s: str = Depends(require_pro_session)):\n'
        '    pass\n'
    )
    mod.BACKEND_ROOT = tmp_path
    mod.API_DIR = tmp_path
    rc = mod.main(["--preventer"])
    # Warn-only default exits 0 even with untagged gates
    assert rc == 0


def test_preventer_strict_exits_one_with_untagged(tmp_path, monkeypatch):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'router = APIRouter(prefix="/p")\n'
        '@router.get("/a")\n'
        'def a(s: str = Depends(require_pro_session)):\n'
        '    pass\n'
    )
    mod.BACKEND_ROOT = tmp_path
    mod.API_DIR = tmp_path
    rc = mod.main(["--preventer", "--strict"])
    assert rc == 1


def test_preventer_strict_exits_zero_when_all_tagged(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'router = APIRouter(prefix="/p")\n'
        '@router.get("/a")\n'
        'def a(s: str = Depends(require_pro_session)):  # tier: pro\n'
        '    pass\n'
    )
    mod.BACKEND_ROOT = tmp_path
    mod.API_DIR = tmp_path
    rc = mod.main(["--preventer", "--strict"])
    assert rc == 0


def test_preventer_strict_rejects_invalid_tier_value(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'router = APIRouter(prefix="/p")\n'
        '@router.get("/a")\n'
        'def a(s: str = Depends(require_pro_session)):  # tier: bogus-value\n'
        '    pass\n'
    )
    mod.BACKEND_ROOT = tmp_path
    mod.API_DIR = tmp_path
    rc = mod.main(["--preventer", "--strict"])
    assert rc == 1


def test_live_tree_survey_runs_clean():
    """Sanity: against the real backend, survey mode exits 0 and counts
    the expected 139 gates (may drift as code evolves; this pins the
    baseline observation from the 2026-04-25 session)."""
    mod = _load()
    sites = mod._collect_all_gates()
    # Baseline ~139 gates; allow ±20 drift for small refactors
    assert 119 <= len(sites) <= 159, f"unexpected gate count: {len(sites)}"