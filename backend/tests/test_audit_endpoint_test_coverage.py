"""Test audit_endpoint_test_coverage — the preventer that surfaces
routes without any test-file path reference.

Pins:
  * Runtime route enumeration + AST decorator-index attachment
  * Implicit framework-auto skip for /docs, /openapi, /redoc, /
  * `# test-exempt: <reason>` tag parsed with allowlist validation
  * Parameterized path matching via prefix+suffix split
  * Strict mode exits 1 on uncovered-without-exempt OR invalid reason
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_endpoint_test_coverage.py")


def _load():
    name = "audit_endpoint_test_coverage_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_implicit_framework_skip():
    mod = _load()
    assert mod._is_implicit_framework("/") is True
    assert mod._is_implicit_framework("/favicon.ico") is True
    assert mod._is_implicit_framework("/docs") is True
    assert mod._is_implicit_framework("/docs/oauth2-redirect") is True
    assert mod._is_implicit_framework("/openapi.json") is True
    assert mod._is_implicit_framework("/redoc") is True
    assert mod._is_implicit_framework("/pro/foo") is False
    assert mod._is_implicit_framework("/merchant/bar") is False


def test_decorator_index_extracts_exempt_tag(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/webhook-receiver")  # test-exempt: webhook-receiver\n'
        'def h(): pass\n'
    )
    mod.BACKEND_API = tmp_path
    mod.BACKEND_ROOT = tmp_path
    index = mod._extract_decorator_index(tmp_path)
    entry = index[("GET", "/pro/webhook-receiver")]
    assert entry["exempt"] == "webhook-receiver"


def test_valid_reasons_set_is_non_empty():
    mod = _load()
    assert "framework-auto" in mod._VALID_TEST_EXEMPT_REASONS
    assert "oauth-callback" in mod._VALID_TEST_EXEMPT_REASONS
    assert "webhook-receiver" in mod._VALID_TEST_EXEMPT_REASONS
    assert "sse-stream" in mod._VALID_TEST_EXEMPT_REASONS
    assert "deprecated" in mod._VALID_TEST_EXEMPT_REASONS
    # Tight allowlist — don't accept arbitrary junk
    assert "tested-via-service" not in mod._VALID_TEST_EXEMPT_REASONS


def test_has_test_reference_literal_match(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text("def test_y(client):\n    client.get('/pro/foo')\n")
    assert mod._has_test_reference("/pro/foo", [f]) is True


def test_has_test_reference_parameterized_via_prefix_suffix(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        "def test_y(client):\n"
        "    resp = client.get('/pro/goals/revenue/progress')\n"
    )
    # Template path `/pro/goals/{metric}/progress` — prefix
    # `/pro/goals`, suffix `/progress` — both present in same file.
    assert mod._has_test_reference("/pro/goals/{metric}/progress", [f]) is True


def test_has_test_reference_returns_false_when_absent(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text("def test_y(): pass\n")
    assert mod._has_test_reference("/pro/nowhere", [f]) is False


def test_live_tree_survey_exits_zero():
    mod = _load()
    rc = mod.main([])
    assert rc == 0


def test_live_tree_strict_exits_one_until_gap_closed():
    """Baseline-pin: strict mode fails because 198 routes are
    uncovered. When this test starts returning 0, either the gap
    is closed OR the audit broke — update the pin to lock new state."""
    mod = _load()
    rc = mod.main(["--strict"])
    assert rc == 1


def test_json_mode_emits_parseable_payload(capsys):
    mod = _load()
    rc = mod.main(["--json"])
    out = capsys.readouterr().out
    # First JSON doc starts at the first `{`; log lines may precede
    start = out.index("{")
    import json
    data = json.loads(out[start:])
    assert "total_routes" in data
    assert "covered" in data
    assert "uncovered_list" in data
    assert isinstance(data["uncovered_list"], list)
    assert rc in (0, 1)
