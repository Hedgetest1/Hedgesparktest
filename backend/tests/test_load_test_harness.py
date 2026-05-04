"""Test harness aggregation helpers (load_test_harness.py).

The harness itself is a CLI script run manually + in CI. These tests
cover the pure helper functions (percentile math, report shape) so a
regression doesn't silently produce wrong load-test numbers.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path("/opt/wishspark/backend/scripts/load_test_harness.py")


def _load_module():
    import sys as _sys
    spec = importlib.util.spec_from_file_location("load_test_harness", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module so dataclass type
    # resolution can find the module's __dict__ (Python dataclass
    # internals walk sys.modules.get(cls.__module__).__dict__).
    _sys.modules["load_test_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_shop_for_uses_loadtest_prefix():
    mod = _load_module()
    s = mod._shop_for(7)
    assert s.startswith("_loadtest_")
    assert s.endswith(".myshopify.com")
    # Padded to 5 digits for sortability
    assert "00007" in s


def test_request_result_dataclass_shape():
    mod = _load_module()
    r = mod.RequestResult(
        shop="x.myshopify.com", route="/test", status=200,
        latency_ms=150.5, query_count=12, error=None,
    )
    assert r.shop == "x.myshopify.com"
    assert r.status == 200
    assert r.latency_ms == 150.5
    assert r.query_count == 12
    assert r.error is None


def test_print_report_pass_when_all_thresholds_met(capsys):
    mod = _load_module()
    rep = mod.HarnessReport(
        merchants=10, requests_per_merchant=5, route="/x",
        duration_s=1.0, total_requests=50, successes=50, errors=0,
        error_pct=0.0,
        latency_ms_p50=100.0, latency_ms_p95=200.0,
        latency_ms_p99=300.0, latency_ms_max=400.0,
        requests_per_sec=50.0,
        query_count_p50=5, query_count_p95=10, query_count_max=15,
    )
    passed = mod.print_report(
        rep, max_p95_ms=500.0, max_error_pct=1.0, max_query_count=30,
    )
    out = capsys.readouterr().out
    assert passed is True
    assert "OVERALL: PASS" in out
    assert "/x" in out


def test_print_report_fails_on_high_p95():
    mod = _load_module()
    rep = mod.HarnessReport(
        merchants=10, requests_per_merchant=5, route="/x",
        duration_s=1.0, total_requests=50, successes=50, errors=0,
        error_pct=0.0,
        latency_ms_p50=100.0, latency_ms_p95=2000.0,  # over budget
        latency_ms_p99=300.0, latency_ms_max=400.0,
        requests_per_sec=50.0,
        query_count_p50=5, query_count_p95=10, query_count_max=15,
    )
    passed = mod.print_report(
        rep, max_p95_ms=500.0, max_error_pct=1.0, max_query_count=30,
    )
    assert passed is False


def test_print_report_fails_on_high_query_count():
    mod = _load_module()
    rep = mod.HarnessReport(
        merchants=10, requests_per_merchant=5, route="/x",
        duration_s=1.0, total_requests=50, successes=50, errors=0,
        error_pct=0.0,
        latency_ms_p50=100.0, latency_ms_p95=200.0,
        latency_ms_p99=300.0, latency_ms_max=400.0,
        requests_per_sec=50.0,
        query_count_p50=20, query_count_p95=80, query_count_max=120,
    )
    passed = mod.print_report(
        rep, max_p95_ms=500.0, max_error_pct=1.0, max_query_count=30,
    )
    assert passed is False


def test_print_report_fails_on_error_rate():
    mod = _load_module()
    rep = mod.HarnessReport(
        merchants=10, requests_per_merchant=5, route="/x",
        duration_s=1.0, total_requests=50, successes=40, errors=10,
        error_pct=20.0,
        latency_ms_p50=100.0, latency_ms_p95=200.0,
        latency_ms_p99=300.0, latency_ms_max=400.0,
        requests_per_sec=50.0,
        query_count_p50=5, query_count_p95=10, query_count_max=15,
    )
    passed = mod.print_report(
        rep, max_p95_ms=500.0, max_error_pct=1.0, max_query_count=30,
    )
    assert passed is False
