"""Test audit_dead_endpoints respects `deprecated=True` / tags=["deprecated"]
(LOW-02 fix).

Deprecated routes are EXPECTED to be orphan — they shouldn't pollute
the survey as false positives. This test exercises `_collect_routes`
against a mocked FastAPI-like app object.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_dead_endpoints.py")


class _FakeRoute:
    def __init__(self, path, methods, deprecated=False, tags=None):
        self.path = path
        self.methods = set(methods)
        self.deprecated = deprecated
        self.tags = tags or []


class _FakeApp:
    def __init__(self, routes):
        self.routes = routes


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_dead_endpoints", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_with_fake_routes(routes):
    """Temporarily replace `app.main.app.routes` with the fake list and
    call `_collect_routes()` — then restore. `_collect_routes` imports
    `app.main` lazily inside the function, so the swap must be alive
    during the call."""
    import app.main as real_main
    saved = real_main.app
    real_main.app = _FakeApp(routes)
    try:
        mod = _load_module()
        return mod._collect_routes()
    finally:
        real_main.app = saved


def test_deprecated_flag_skipped():
    routes = [
        _FakeRoute("/live", ["GET"]),
        _FakeRoute("/old", ["GET"], deprecated=True),
    ]
    out = _collect_with_fake_routes(routes)
    assert ("GET", "/live") in out
    assert ("GET", "/old") not in out


def test_deprecated_tag_skipped_case_insensitive():
    routes = [
        _FakeRoute("/live", ["GET"]),
        _FakeRoute("/old-a", ["GET"], tags=["deprecated"]),
        _FakeRoute("/old-b", ["GET"], tags=["Deprecated", "v1"]),
        _FakeRoute("/old-c", ["GET"], tags=["DEPRECATED"]),
    ]
    out = _collect_with_fake_routes(routes)
    paths = [p for _, p in out]
    assert "/live" in paths
    assert "/old-a" not in paths
    assert "/old-b" not in paths
    assert "/old-c" not in paths


def test_non_deprecated_tag_not_filtered():
    routes = [
        _FakeRoute("/billing", ["POST"], tags=["billing", "v2"]),
    ]
    out = _collect_with_fake_routes(routes)
    assert ("POST", "/billing") in out


def test_head_and_options_methods_still_filtered():
    routes = [
        _FakeRoute("/x", ["GET", "HEAD", "OPTIONS"]),
    ]
    out = _collect_with_fake_routes(routes)
    assert ("GET", "/x") in out
    assert ("HEAD", "/x") not in out
    assert ("OPTIONS", "/x") not in out


def test_strip_comments_removes_line_and_block():
    """Sibling-fix DA closure: audit_dead_endpoints also strips
    TS/JS comments before substring match, same as the backend-
    frontend coverage audit."""
    mod = _load_module()
    src = (
        "// fetch('/pro/commented');\n"
        "const keep = '/pro/live';\n"
        "/* block fetch('/pro/blocked') */\n"
    )
    cleaned = mod._strip_comments(src)
    assert "/pro/commented" not in cleaned
    assert "/pro/blocked" not in cleaned
    assert "/pro/live" in cleaned


def test_file_contains_skips_commented_fetch_in_ts(tmp_path):
    """A path only referenced inside a `//` comment in a .tsx file
    must NOT count as a consumer."""
    mod = _load_module()
    f = tmp_path / "c.tsx"
    f.write_text("// fetch('/pro/ghost');\n")
    assert mod._file_contains([f], "/pro/ghost") is False


def test_file_contains_preserves_hash_in_python_tests(tmp_path):
    """Python test files use `#` for comments and MUST NOT be
    comment-stripped — stripping would drop real test data."""
    mod = _load_module()
    f = tmp_path / "test_x.py"
    # Path embedded in a Python fixture string — the `#` is NOT a TS
    # comment, and in fact this file has no TS comments at all.
    f.write_text(
        "TEST_PATH = '/pro/real-endpoint'\n"
        "def test(): assert TEST_PATH == '/pro/real-endpoint'\n"
    )
    # Real consumer in Python should still count
    assert mod._file_contains([f], "/pro/real-endpoint") is True
