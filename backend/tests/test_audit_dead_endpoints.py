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
