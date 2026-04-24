"""Test audit_backend_frontend_coverage — the preventer that catches
the "fantasma backend endpoint" bug class.

Pins:
  * AST router-prefix resolution works for files with multiple
    APIRouter declarations (cohorts.py: /pro + /analytics).
  * Multi-line `@router.get(...)` decorators are detected with correct
    line numbers.
  * `# ui-exempt: <reason>` tag parsed and honored.
  * Empty / too-short exempt reason → flagged invalid.
  * Consumer detection excludes api-types.ts.
  * Consumer detection handles parameterized paths via template-literal
    split matching.
  * Strict mode exits 1 on uncovered, 0 on fully covered/exempt.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_backend_frontend_coverage.py")


def _load():
    name = "audit_backend_frontend_coverage_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_router_prefix_resolution_multiple_routers(tmp_path):
    """cohorts.py-style file with two APIRouter vars must resolve each
    router's routes under its own prefix."""
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro/cohorts")\n'
        'lite_router = APIRouter(prefix="/analytics/cohorts")\n'
        '\n'
        '@router.get("/summary")\n'
        'def pro_summary(): pass\n'
        '\n'
        '@lite_router.get("/summary")\n'
        'def lite_summary(): pass\n'
    )
    # Point the audit at a dir containing just this file
    mod.BACKEND_API = tmp_path
    index = mod._extract_decorator_index(tmp_path)
    assert ("GET", "/pro/cohorts/summary") in index
    assert ("GET", "/analytics/cohorts/summary") in index
    assert index[("GET", "/pro/cohorts/summary")]["line"] == 5
    assert index[("GET", "/analytics/cohorts/summary")]["line"] == 8


def test_multi_line_decorator_detected_with_correct_line(tmp_path):
    """proof_report.py-style multi-line `@router.get(\\n ...\\n)` must
    be detected at the line the `@` sits on."""
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro/proof-report")\n'
        '\n'
        '\n'
        '@router.get(\n'
        '    "",\n'
        '    response_model=X,\n'
        ')\n'
        'def h(): pass\n'
    )
    mod.BACKEND_API = tmp_path
    index = mod._extract_decorator_index(tmp_path)
    assert ("GET", "/pro/proof-report") in index
    assert index[("GET", "/pro/proof-report")]["line"] == 5


def test_ui_exempt_tag_parsed(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/internal")  # ui-exempt: external-consumer\n'
        'def h(): pass\n'
    )
    mod.BACKEND_API = tmp_path
    index = mod._extract_decorator_index(tmp_path)
    entry = index[("GET", "/pro/internal")]
    assert entry["exempt"] == "external-consumer"


def test_ui_exempt_missing_tag_is_none(tmp_path):
    mod = _load()
    f = tmp_path / "x.py"
    f.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/foo")\n'
        'def h(): pass\n'
    )
    mod.BACKEND_API = tmp_path
    index = mod._extract_decorator_index(tmp_path)
    assert index[("GET", "/pro/foo")]["exempt"] is None


def test_consumer_detection_excludes_api_types_ts(tmp_path):
    """Presence in api-types.ts does NOT count as a consumer."""
    mod = _load()
    # Create a fake dashboard tree
    dash = tmp_path / "dashboard" / "src"
    dash.mkdir(parents=True)
    (dash / "api-types.ts").write_text('"/pro/foo": { "get": {} }')
    (dash / "real_consumer.tsx").write_text('const x = "/pro/other";')
    # Patch the DASHBOARD_SRC module constant
    mod.DASHBOARD_SRC = dash
    files = mod._dashboard_files()
    names = {f.name for f in files}
    assert "api-types.ts" not in names
    assert "real_consumer.tsx" in names


def test_has_consumer_matches_literal_path(tmp_path):
    mod = _load()
    f = tmp_path / "consumer.tsx"
    f.write_text('fetch("/pro/foo")')
    assert mod._has_consumer("/pro/foo", [f]) is True


def test_has_consumer_matches_parameterized_via_template_split(tmp_path):
    """Route `/pro/foo/{id}` is "covered" when a file contains both the
    prefix and the suffix (consumer built the URL via template
    literal)."""
    mod = _load()
    f = tmp_path / "consumer.tsx"
    f.write_text('const url = `${API_BASE}/pro/foo/${id}/details`;')
    # Path `/pro/foo/{id}/details` → prefix `/pro/foo`, suffix `/details`
    # — both present in same file → covered.
    assert mod._has_consumer("/pro/foo/{id}/details", [f]) is True


def test_has_consumer_returns_false_when_no_match(tmp_path):
    mod = _load()
    f = tmp_path / "noise.tsx"
    f.write_text('const x = "unrelated";')
    assert mod._has_consumer("/pro/nowhere", [f]) is False


def test_valid_exempt_reasons_accepted(tmp_path, monkeypatch):
    """Every reason in the allowlist must be accepted by main()."""
    mod = _load()
    valid = list(mod._VALID_EXEMPT_REASONS)

    # Generate a single .py file with one exempted route per valid reason
    py = tmp_path / "routes.py"
    lines = ['from fastapi import APIRouter', 'router = APIRouter(prefix="/pro")']
    for i, reason in enumerate(valid):
        lines.append(f'@router.get("/r{i}")  # ui-exempt: {reason}')
        lines.append(f'def r{i}(): pass')
    py.write_text("\n".join(lines))

    monkeypatch.setattr(mod, "BACKEND_API", tmp_path)
    # Dashboard with no consumers — every route must resolve via exempt
    dash = tmp_path / "dashboard_src"
    dash.mkdir()
    monkeypatch.setattr(mod, "DASHBOARD_SRC", dash)

    # Mock app.routes to reflect what we declared
    class _FakeRoute:
        def __init__(self, path, method):
            self.path = path
            self.methods = {method}
    import app.main as real_main
    saved = real_main.app
    class _FakeApp:
        pass
    fa = _FakeApp()
    fa.routes = [_FakeRoute(f"/pro/r{i}", "GET") for i in range(len(valid))]
    real_main.app = fa
    try:
        rc = mod.main(["--strict"])
    finally:
        real_main.app = saved
    # All routes exempted under valid reasons → strict passes
    assert rc == 0


def test_invalid_exempt_reason_flagged(tmp_path, monkeypatch):
    """A reason outside the allowlist is treated as uncovered under
    --strict."""
    mod = _load()
    py = tmp_path / "routes.py"
    py.write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter(prefix="/pro")\n'
        '@router.get("/r1")  # ui-exempt: junk-reason\n'
        'def r1(): pass\n'
    )
    monkeypatch.setattr(mod, "BACKEND_API", tmp_path)
    dash = tmp_path / "dashboard_src"
    dash.mkdir()
    monkeypatch.setattr(mod, "DASHBOARD_SRC", dash)

    class _FakeRoute:
        def __init__(self, path, method):
            self.path = path
            self.methods = {method}
    import app.main as real_main
    saved = real_main.app
    class _FakeApp:
        pass
    fa = _FakeApp()
    fa.routes = [_FakeRoute("/pro/r1", "GET")]
    real_main.app = fa
    try:
        rc = mod.main(["--strict"])
    finally:
        real_main.app = saved
    assert rc == 1


def test_is_merchant_facing():
    mod = _load()
    assert mod._is_merchant_facing("/pro/foo") is True
    assert mod._is_merchant_facing("/merchant/bar") is True
    assert mod._is_merchant_facing("/analytics/x") is True
    assert mod._is_merchant_facing("/webhooks/shopify/order") is False
    assert mod._is_merchant_facing("/ops/llm-budget") is False
    assert mod._is_merchant_facing("/auth/callback") is False
    assert mod._is_merchant_facing("/public/proof") is False


def test_live_tree_survey_exits_zero():
    """Against the real repo, survey mode must exit 0 regardless of
    uncovered count."""
    mod = _load()
    rc = mod.main([])
    assert rc == 0


def test_live_tree_strict_detects_uncovered():
    """Sanity: strict mode against the real tree exits 1 because the
    14 utility/admin + 3 Ads + 3 cohorts/vertical/proof fantasma are
    uncovered. If this test starts returning 0, either all fantasma
    were surfaced OR the audit broke — update the test to lock the
    new green baseline."""
    mod = _load()
    rc = mod.main(["--strict"])
    assert rc == 1
