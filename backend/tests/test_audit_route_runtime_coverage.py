"""Test audit_route_runtime_coverage — authoritative runtime signal.

Pins:
  * Missing coverage.json returns 0 (informational, never blocks)
  * Handlers correctly mapped to their file + line range
  * Body-lines-only execution check (decorator/def lines excluded so
    a never-called handler in an imported module still flags)
  * --strict exits 1 when coverage file present + any uncovered
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_route_runtime_coverage.py")


def _load():
    name = "audit_route_runtime_coverage_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_missing_coverage_file_returns_zero(tmp_path):
    """When coverage.json doesn't exist, audit is informational and
    exits 0 (preflight-safe)."""
    mod = _load()
    missing = tmp_path / "absent.json"
    rc = mod.main(["--cov-file", str(missing)])
    assert rc == 0


def test_json_mode_reports_no_coverage_gracefully(tmp_path, capsys):
    mod = _load()
    missing = tmp_path / "absent.json"
    rc = mod.main(["--cov-file", str(missing), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    start = out.index("{")
    data = json.loads(out[start:])
    assert data["error"] == "no_coverage_data"
    assert "handlers_scanned" in data


def test_body_lines_only_heuristic_excludes_def(tmp_path):
    """Construct a synthetic handler + coverage payload where ONLY
    the def line is 'executed' (import side-effect). The audit must
    NOT count that as covered."""
    mod = _load()

    # Write a fake api file
    fake_api = tmp_path / "app" / "api"
    fake_api.mkdir(parents=True)
    fake_file = fake_api / "fake_router.py"
    fake_file.write_text(
        'from fastapi import APIRouter\n'  # line 1
        'router = APIRouter(prefix="/pro")\n'  # line 2
        '\n'  # line 3
        '@router.get("/foo")\n'  # line 4
        'def handler():\n'  # line 5  (def line)
        '    return {}\n'  # line 6  (body)
    )
    # Build coverage.json with ONLY def line executed (lines 5) not body (6)
    cov = {
        "files": {
            "app/api/fake_router.py": {"executed_lines": [1, 2, 4, 5]}
        }
    }
    cov_file = tmp_path / "cov.json"
    cov_file.write_text(json.dumps(cov))

    # Monkey-patch BACKEND_API to tmp
    mod.BACKEND_API = fake_api
    mod.BACKEND_ROOT = tmp_path
    # Patch _load_coverage resolver to point at fake file
    original_load = mod._load_coverage

    def patched_load(path):
        data = json.loads(path.read_text())
        out = {}
        for rel, entry in data["files"].items():
            candidate = str(tmp_path / rel)
            out[candidate] = set(entry["executed_lines"])
        return out

    mod._load_coverage = patched_load

    try:
        rc = mod.main(["--cov-file", str(cov_file), "--json"])
    finally:
        mod._load_coverage = original_load

    # def line at 5 executed but body line 6 NOT → uncovered
    assert rc == 0  # not strict


def test_strict_mode_exits_one_when_uncovered(tmp_path):
    """Body-line-miss in --strict mode → exit 1."""
    mod = _load()

    fake_api = tmp_path / "app" / "api"
    fake_api.mkdir(parents=True)
    f = fake_api / "r.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/foo")\n'
        'def h():\n'
        '    return {}\n'
    )
    cov_file = tmp_path / "cov.json"
    cov_file.write_text(json.dumps({
        "files": {"app/api/r.py": {"executed_lines": [1, 2, 3, 4]}}  # no body line 5
    }))

    mod.BACKEND_API = fake_api
    mod.BACKEND_ROOT = tmp_path

    def patched_load(path):
        data = json.loads(path.read_text())
        out = {}
        for rel, entry in data["files"].items():
            out[str(tmp_path / rel)] = set(entry["executed_lines"])
        return out

    mod._load_coverage = patched_load
    rc = mod.main(["--cov-file", str(cov_file), "--strict"])
    assert rc == 1


def test_covered_when_body_executed(tmp_path):
    mod = _load()

    fake_api = tmp_path / "app" / "api"
    fake_api.mkdir(parents=True)
    f = fake_api / "r.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/foo")\n'
        'def h():\n'
        '    return {}\n'
    )
    cov_file = tmp_path / "cov.json"
    cov_file.write_text(json.dumps({
        "files": {"app/api/r.py": {"executed_lines": [4, 5]}}
    }))

    mod.BACKEND_API = fake_api
    mod.BACKEND_ROOT = tmp_path

    def patched_load(path):
        data = json.loads(path.read_text())
        out = {}
        for rel, entry in data["files"].items():
            out[str(tmp_path / rel)] = set(entry["executed_lines"])
        return out

    mod._load_coverage = patched_load
    rc = mod.main(["--cov-file", str(cov_file), "--strict"])
    # body line 5 in executed_lines → covered → strict passes
    assert rc == 0
