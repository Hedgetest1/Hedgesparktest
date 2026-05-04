#!/usr/bin/env python
"""
load_test_harness.py — synthetic merchant load simulator (item 8).

Pre-creates N test merchants, forges an authenticated session for each,
issues concurrent dashboard requests via httpx.AsyncClient, aggregates
latency + DB query count (via the X-Query-Count header from the runtime
N+1 detector wired in app/core/query_count_monitor.py) per route, and
reports pass/fail vs configurable thresholds.

The output validates the analytical round-trip projections from the
2026-05-04 N+1 sweep: if a refactored bulk operation regresses to N+1
under load, this harness surfaces it via X-Query-Count distribution.

Safety
------
Test merchants use the prefix `_loadtest_` (configurable via env
LOAD_TEST_SHOP_PREFIX). The harness REFUSES to run against a database
that already has merchants matching the prefix unless --force is set
(prevents accidental cross-run pollution). Cleanup runs in a finally
block to avoid orphan test merchants on Ctrl-C.

Usage
-----
    # Smoke (5 merchants, 3 requests each, default route):
    ./venv/bin/python scripts/load_test_harness.py --merchants 5 --requests 3

    # Realistic (100 merchants concurrent, 10 requests each, Pro dashboard):
    ./venv/bin/python scripts/load_test_harness.py \\
        --merchants 100 --requests 10 --route /dashboard/overview/pro

    # Aggressive ramp (find ceiling):
    for n in 50 100 250 500; do
        ./venv/bin/python scripts/load_test_harness.py --merchants $n
    done

Thresholds (overridable)
------------------------
    --max-p95-ms 500     # request p95 must be < this
    --max-error-pct 1.0  # error rate (5xx/4xx) must be < this %
    --max-query-count 30 # X-Query-Count p95 must be < this (= soft threshold)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# Ensure imports work when run as `./venv/bin/python scripts/load_test_harness.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env BEFORE app.core.merchant_session reads MERCHANT_SESSION_SECRET
# at module import time. (app.core.database also calls load_dotenv but
# import-order races make explicit early loading safer.)
from dotenv import load_dotenv  # noqa: E402
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))

import httpx
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.merchant_session import SESSION_COOKIE_NAME, create_session_token
from app.core.token_crypto import encrypt_token

_SHOP_PREFIX = os.getenv("LOAD_TEST_SHOP_PREFIX", "_loadtest_")
_BASE_URL = os.getenv("LOAD_TEST_BASE_URL", "http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# Setup / teardown — synthetic merchants
# ---------------------------------------------------------------------------

def _shop_for(idx: int) -> str:
    return f"{_SHOP_PREFIX}{idx:05d}.myshopify.com"


def setup_merchants(n: int, *, force: bool = False) -> list[str]:
    """Create N test merchants. Returns list of shop_domains.
    Refuses to run if existing _loadtest_ merchants found (unless force)."""
    db = SessionLocal()
    try:
        existing = db.execute(
            text("SELECT COUNT(*) FROM merchants WHERE shop_domain LIKE :p"),
            {"p": f"{_SHOP_PREFIX}%"},
        ).scalar() or 0
        if existing > 0 and not force:
            raise RuntimeError(
                f"refusing to run — {existing} existing merchants matching "
                f"prefix '{_SHOP_PREFIX}' (cleanup from prior run not "
                f"completed?). Run with --force to override."
            )
        if existing > 0 and force:
            db.execute(
                text("DELETE FROM merchants WHERE shop_domain LIKE :p"),
                {"p": f"{_SHOP_PREFIX}%"},
            )
            db.commit()

        shops: list[str] = []
        # Bulk INSERT via execute_many for speed
        rows = []
        encrypted = encrypt_token("shpat_loadtest")
        for i in range(n):
            shop = _shop_for(i)
            rows.append({
                "shop": shop,
                "tok": encrypted,
            })
            shops.append(shop)

        db.execute(text("""
            INSERT INTO merchants
                (shop_domain, access_token, plan, billing_active,
                 install_status, session_version)
            VALUES
                (:shop, :tok, 'pro', true, 'active', 0)
        """), rows)
        db.commit()
        return shops
    finally:
        db.close()


def cleanup_merchants() -> int:
    """Delete every merchant with the load-test prefix. Returns count."""
    db = SessionLocal()
    try:
        result = db.execute(
            text("DELETE FROM merchants WHERE shop_domain LIKE :p"),
            {"p": f"{_SHOP_PREFIX}%"},
        )
        db.commit()
        return result.rowcount
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Concurrent request driver
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    shop: str
    route: str
    status: int
    latency_ms: float
    query_count: int
    error: str | None = None


@dataclass
class HarnessReport:
    merchants: int
    requests_per_merchant: int
    route: str
    duration_s: float
    total_requests: int
    successes: int
    errors: int
    error_pct: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    latency_ms_max: float
    requests_per_sec: float
    query_count_p50: int
    query_count_p95: int
    query_count_max: int
    error_samples: list[str] = field(default_factory=list)


async def issue_request(
    client: httpx.AsyncClient, shop: str, route: str, token: str,
) -> RequestResult:
    """Single request with X-Query-Count header capture."""
    cookies = {SESSION_COOKIE_NAME: token}
    t0 = time.perf_counter()
    try:
        resp = await client.get(route, cookies=cookies, timeout=30.0)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        qcount = int(resp.headers.get("X-Query-Count", 0) or 0)
        return RequestResult(
            shop=shop, route=route,
            status=resp.status_code,
            latency_ms=latency_ms,
            query_count=qcount,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return RequestResult(
            shop=shop, route=route, status=0,
            latency_ms=latency_ms, query_count=0,
            error=f"{type(exc).__name__}: {str(exc)[:120]}",
        )


async def run_merchant(
    client: httpx.AsyncClient, shop: str, token: str,
    route: str, k: int,
) -> list[RequestResult]:
    """Issue K sequential requests for one merchant (simulates the
    merchant browsing the dashboard)."""
    results: list[RequestResult] = []
    for _ in range(k):
        r = await issue_request(client, shop, route, token)
        results.append(r)
    return results


async def run_harness(
    shops: list[str], tokens: dict[str, str], route: str,
    requests_per_merchant: int, base_url: str,
) -> HarnessReport:
    """Top-level driver: asyncio.gather over all merchants concurrently."""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=base_url) as client:
        tasks = [
            run_merchant(client, s, tokens[s], route, requests_per_merchant)
            for s in shops
        ]
        merchant_results = await asyncio.gather(*tasks)
    duration_s = time.perf_counter() - t0

    flat: list[RequestResult] = [r for m in merchant_results for r in m]
    successes = [r for r in flat if r.error is None and 200 <= r.status < 400]
    errors = [r for r in flat if r.error is not None or r.status >= 400]
    latencies = sorted([r.latency_ms for r in successes]) or [0.0]
    qcounts = sorted([r.query_count for r in successes]) or [0]

    def _pct(xs, p):
        if not xs:
            return 0
        idx = int(len(xs) * p / 100)
        idx = min(idx, len(xs) - 1)
        return xs[idx]

    error_samples = [
        f"shop={r.shop} status={r.status} err={r.error}"
        for r in errors[:5]
    ]

    return HarnessReport(
        merchants=len(shops),
        requests_per_merchant=requests_per_merchant,
        route=route,
        duration_s=round(duration_s, 3),
        total_requests=len(flat),
        successes=len(successes),
        errors=len(errors),
        error_pct=round(100.0 * len(errors) / max(len(flat), 1), 2),
        latency_ms_p50=round(_pct(latencies, 50), 1),
        latency_ms_p95=round(_pct(latencies, 95), 1),
        latency_ms_p99=round(_pct(latencies, 99), 1),
        latency_ms_max=round(max(latencies), 1),
        requests_per_sec=round(len(flat) / max(duration_s, 0.001), 1),
        query_count_p50=int(_pct(qcounts, 50)),
        query_count_p95=int(_pct(qcounts, 95)),
        query_count_max=int(max(qcounts) if qcounts else 0),
        error_samples=error_samples,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(r: HarnessReport, *, max_p95_ms: float, max_error_pct: float,
                 max_query_count: int) -> bool:
    """Pretty-print the report. Returns True if all thresholds passed."""
    pass_p95 = r.latency_ms_p95 <= max_p95_ms
    pass_err = r.error_pct <= max_error_pct
    pass_qc = r.query_count_p95 <= max_query_count
    all_pass = pass_p95 and pass_err and pass_qc

    print()
    print("=" * 70)
    print(f"  Load Test Report — {r.route}")
    print("=" * 70)
    print(f"  Merchants:            {r.merchants}")
    print(f"  Requests / merchant:  {r.requests_per_merchant}")
    print(f"  Total requests:       {r.total_requests}")
    print(f"  Duration:             {r.duration_s}s")
    print(f"  Throughput:           {r.requests_per_sec} req/s")
    print()
    print(f"  Latency  p50: {r.latency_ms_p50:>8.1f} ms")
    print(f"           p95: {r.latency_ms_p95:>8.1f} ms  "
          f"({'PASS' if pass_p95 else 'FAIL'} ≤ {max_p95_ms} ms)")
    print(f"           p99: {r.latency_ms_p99:>8.1f} ms")
    print(f"           max: {r.latency_ms_max:>8.1f} ms")
    print()
    print(f"  Query    p50: {r.query_count_p50:>8d}")
    print(f"  count    p95: {r.query_count_p95:>8d}  "
          f"({'PASS' if pass_qc else 'FAIL'} ≤ {max_query_count})")
    print(f"           max: {r.query_count_max:>8d}")
    print()
    print(f"  Errors:               {r.errors} / {r.total_requests}  "
          f"= {r.error_pct}%  ({'PASS' if pass_err else 'FAIL'} ≤ {max_error_pct}%)")
    if r.error_samples:
        print(f"  Error samples:")
        for s in r.error_samples:
            print(f"    {s}")
    print("=" * 70)
    print(f"  OVERALL: {'PASS ✅' if all_pass else 'FAIL ❌'}")
    print("=" * 70)
    print()
    return all_pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merchants", type=int, default=10,
                    help="Number of synthetic merchants (default 10)")
    ap.add_argument("--requests", type=int, default=5,
                    help="Sequential requests per merchant (default 5)")
    ap.add_argument("--route", default="/dashboard/overview",
                    help="Endpoint to hit (default /dashboard/overview)")
    ap.add_argument("--base-url", default=_BASE_URL,
                    help=f"Backend URL (default {_BASE_URL})")
    ap.add_argument("--max-p95-ms", type=float, default=500.0,
                    help="Max acceptable p95 latency (default 500ms)")
    ap.add_argument("--max-error-pct", type=float, default=1.0,
                    help="Max acceptable error rate %% (default 1.0)")
    ap.add_argument("--max-query-count", type=int, default=30,
                    help="Max acceptable p95 X-Query-Count (default 30)")
    ap.add_argument("--force", action="store_true",
                    help="Wipe pre-existing _loadtest_ merchants and proceed")
    ap.add_argument("--keep", action="store_true",
                    help="Skip cleanup at end (for debugging)")
    args = ap.parse_args()

    print(f"[setup] creating {args.merchants} synthetic merchants "
          f"(prefix='{_SHOP_PREFIX}')...")
    shops = setup_merchants(args.merchants, force=args.force)

    print(f"[setup] forging {args.merchants} session tokens...")
    tokens: dict[str, str] = {}
    for s in shops:
        tok = create_session_token(s)
        if tok is None:
            raise RuntimeError(
                f"create_session_token returned None for {s} — "
                f"MERCHANT_SESSION_SECRET not configured?"
            )
        tokens[s] = tok

    try:
        print(f"[run] hitting {args.route} ({args.requests} req × "
              f"{args.merchants} merchants = {args.requests * args.merchants} "
              f"total) concurrently against {args.base_url} ...")
        report = asyncio.run(
            run_harness(shops, tokens, args.route,
                        args.requests, args.base_url),
        )
        passed = print_report(
            report,
            max_p95_ms=args.max_p95_ms,
            max_error_pct=args.max_error_pct,
            max_query_count=args.max_query_count,
        )
        return 0 if passed else 1
    finally:
        if args.keep:
            print(f"[teardown] --keep set; leaving {len(shops)} test "
                  f"merchants in place")
        else:
            print(f"[teardown] cleaning up test merchants...")
            n = cleanup_merchants()
            print(f"[teardown] deleted {n} merchants")


if __name__ == "__main__":
    sys.exit(main())
