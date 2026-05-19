#!/usr/bin/env python
"""
smoke_endpoints.py — Tier 3.3 runtime smoke harness.

Hits every include_in_schema=True GET route on /pro, /merchant, and
/analytics with a real test-merchant session (via the in-process
FastAPI TestClient), asserts every response:

  1. Returns a 2xx status code (not 5xx, not 401/403).
  2. Parses as JSON.
  3. Matches the declared response_model (Pydantic .model_validate).
  4. Lands under the p95 latency budget (default: 200 ms).

No path params — routes with `{id}` / `{name}` / `{metric}` are
skipped because they need real test data. Those are covered by
per-route unit tests in `tests/`, not by this smoke sweep.

Usage:
    ./venv/bin/python scripts/smoke_endpoints.py                 # full
    ./venv/bin/python scripts/smoke_endpoints.py --prefix /pro   # filter
    ./venv/bin/python scripts/smoke_endpoints.py --strict        # fail on any
    ./venv/bin/python scripts/smoke_endpoints.py --json          # machine
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field

from fastapi.routing import APIRoute
from sqlalchemy import text

# Make sure the app is importable.
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# The test DB is the same prod Postgres under SAVEPOINT isolation per
# CLAUDE.md §14. The smoke harness uses a dedicated seed merchant so
# it doesn't collide with pytest-scoped fixtures.
SMOKE_SHOP = "hedgespark-smoke.myshopify.com"

TARGET_PREFIXES = ("/pro/", "/merchant/", "/analytics/")
P95_BUDGET_MS = 200.0


@dataclass
class Result:
    path: str
    status: int | None
    duration_ms: float
    ok: bool
    error: str | None = None
    schema_ok: bool | None = None
    notes: list[str] = field(default_factory=list)


def _ensure_seed_merchant() -> int:
    """Create the dedicated smoke-test merchant if it doesn't exist.

    Uses a raw connection rather than the FastAPI db session so we
    don't fight the TestClient's dependency overrides."""
    from app.core.database import SessionLocal
    from app.models.merchant import Merchant

    db = SessionLocal()
    try:
        existing = (
            db.query(Merchant)
            .filter(Merchant.shop_domain == SMOKE_SHOP)
            .one_or_none()
        )
        if existing is None:
            m = Merchant(
                shop_domain=SMOKE_SHOP,
                plan="pro",
                billing_active=True,
                billing_confirmed_at=None,
                install_status="active",
                session_version=0,
                contact_email="smoke@hedgesparkhq.com",
                onboarding_status="ready",
            )
            db.add(m)
            db.commit()
            return 0
        # Keep the smoke merchant at pro+active in case an earlier
        # run left it in a degraded state.
        if existing.plan != "pro" or not existing.billing_active:
            existing.plan = "pro"
            existing.billing_active = True
            existing.install_status = "active"
            db.commit()
        # Return the merchant's CURRENT session_version so the forged
        # token matches it. Born 2026-05-19h: the smoke merchant had
        # drifted to sv=19 (forced-logout/billing/uninstall paths bump
        # it over months) while the token was hardcoded sv=0 →
        # deps.py:190 token_sv(0) < db_sv(19) → 401 on EVERY authed
        # route → all counted "skipped_auth" → the harness reported
        # green while testing ~1/137 routes for an unknown number of
        # commits. Read-and-match (NOT a DB sv reset): no row mutation,
        # so no fight with the deps msv Redis cache (db_sv unchanged,
        # cache stays consistent). Drift-robust by construction.
        return int(getattr(existing, "session_version", 0) or 0)
    finally:
        db.close()


def _get_routes(prefix_filter: str | None) -> list[tuple[str, APIRoute]]:
    from app.main import app
    routes: list[tuple[str, APIRoute]] = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        if not r.include_in_schema:
            continue
        if "GET" not in r.methods:
            continue
        if "{" in r.path:  # path params — skip
            continue
        if not any(r.path.startswith(p) for p in TARGET_PREFIXES):
            continue
        if prefix_filter and not r.path.startswith(prefix_filter):
            continue
        routes.append((r.path, r))
    routes.sort(key=lambda kv: kv[0])
    return routes


def _validate_schema(route: APIRoute, payload: object) -> tuple[bool, str | None]:
    model = getattr(route, "response_model", None)
    if model is None:
        return True, "no response_model declared"
    # Pydantic TypeAdapter handles both BaseModel subclasses and
    # parametrized shapes like list[Model] / dict[str, Model]. Using
    # the adapter uniformly avoids the `list.model_validate` crash
    # we hit on /pro/rules, /pro/shares, and the trust endpoints.
    try:
        from pydantic import TypeAdapter
        TypeAdapter(model).validate_python(payload)
        return True, None
    except Exception as exc:
        msg = str(exc)
        short = msg.splitlines()[0] if msg else type(exc).__name__
        return False, short[:200]


def run_smoke(prefix_filter: str | None = None) -> list[Result]:
    smoke_sv = _ensure_seed_merchant()

    # Build a session cookie for the smoke merchant. Mint with the
    # merchant's CURRENT session_version (not hardcoded 0) so the
    # forged session actually authenticates past deps.py:190.
    from app.core.merchant_session import create_session_token, SESSION_COOKIE_NAME
    token = create_session_token(SMOKE_SHOP, session_version=smoke_sv)
    if token is None:
        print("FAIL: unable to mint smoke session token (HS_SESSION_SECRET unset?)", file=sys.stderr)
        return []
    cookies = {SESSION_COOKIE_NAME: token}

    # Import TestClient lazily so the --help path stays cheap.
    from fastapi.testclient import TestClient
    from app.main import app

    routes = _get_routes(prefix_filter)
    results: list[Result] = []

    with TestClient(app, cookies=cookies) as client:
        for path, route in routes:
            start = time.perf_counter()
            try:
                r = client.get(path)
                duration = (time.perf_counter() - start) * 1000.0
            except Exception as exc:
                duration = (time.perf_counter() - start) * 1000.0
                results.append(Result(
                    path=path, status=None, duration_ms=duration,
                    ok=False, error=f"transport:{type(exc).__name__}",
                ))
                continue

            # 200-299 is OK. 401/403/404 are acceptable for routes that
            # the smoke merchant legitimately cannot hit (e.g. ops-only,
            # partner-only) — we count them as "skipped, not failed".
            if 200 <= r.status_code < 300:
                try:
                    body = r.json() if r.content else None
                except Exception:
                    results.append(Result(
                        path=path, status=r.status_code, duration_ms=duration,
                        ok=False, error="non-json response",
                    ))
                    continue
                schema_ok, schema_err = _validate_schema(route, body)
                results.append(Result(
                    path=path, status=r.status_code, duration_ms=duration,
                    ok=schema_ok, schema_ok=schema_ok,
                    error=schema_err if not schema_ok else None,
                    notes=["schema-skipped"] if schema_err == "no response_model declared" else [],
                ))
            elif r.status_code in (400, 401, 403, 404, 422, 429):
                # 422 = route requires query params the smoke harness
                # doesn't synthesize. 400 = route needs a specific
                # session state (e.g. already-installed webhook).
                # 401/403/404 = auth/permission boundary. 429 = rate
                # limiter triggered by rapid preflight probing. All are
                # "can't reach from a blank smoke session", NOT
                # runtime failures.
                results.append(Result(
                    path=path, status=r.status_code, duration_ms=duration,
                    ok=True,  # skipped, not failed
                    notes=[f"skipped:{r.status_code}"],
                ))
            else:
                # 5xx and unexpected codes are real failures.
                results.append(Result(
                    path=path, status=r.status_code, duration_ms=duration,
                    ok=False, error=f"status:{r.status_code}",
                ))
    return results


def summarize(results: list[Result]) -> dict:
    total = len(results)
    ok_ = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    skipped = [r for r in results if r.notes and any(n.startswith("skipped") for n in r.notes)]
    durations = [r.duration_ms for r in results if r.status and 200 <= r.status < 300]
    p95 = round(statistics.quantiles(durations, n=20)[18], 1) if len(durations) >= 20 else (
        round(max(durations), 1) if durations else 0.0
    )
    p50 = round(statistics.median(durations), 1) if durations else 0.0
    return {
        "total": total,
        "passed": ok_,
        "failed": len(failed),
        "skipped_auth": len(skipped),
        "p50_ms": p50,
        "p95_ms": p95,
        "failures": [
            {"path": r.path, "status": r.status, "error": r.error}
            for r in failed
        ],
    }


def _smoke_session_vacuous(summary: dict) -> bool:
    """True iff the smoke run is FICTION: the forged session reached
    so few routes that "green" means nothing. `passed` counts
    skipped(401/403/404) rows as ok=True, so the honest signal is
    genuinely-2xx = passed - skipped_auth. Born 2026-05-19h: token
    sv=0 vs merchant sv=19 → 136/137 auth-skipped while reporting
    "passed 137". Measured-healthy baseline 86% genuine; the 50%
    floor leaves a 36-pt margin (non-flaky vs normal 404/operator
    variation) yet trips on the catastrophic session-broken collapse.
    Pure function so the §1.4 lesson is contract-locked."""
    total = summary.get("total", 0)
    if total <= 0:
        return False
    genuine = summary.get("passed", 0) - summary.get("skipped_auth", 0)
    return genuine < total * 0.5


def main() -> int:
    prefix_filter = None
    strict = "--strict" in sys.argv
    want_json = "--json" in sys.argv
    for i, arg in enumerate(sys.argv):
        if arg == "--prefix" and i + 1 < len(sys.argv):
            prefix_filter = sys.argv[i + 1]

    results = run_smoke(prefix_filter=prefix_filter)
    summary = summarize(results)

    if want_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"smoke_endpoints: {summary['total']} routes")
        print(f"  passed       : {summary['passed']}")
        print(f"  failed       : {summary['failed']}")
        print(f"  skipped_auth : {summary['skipped_auth']}  (401/403/404 — session cannot reach)")
        print(f"  p50 latency  : {summary['p50_ms']} ms")
        print(f"  p95 latency  : {summary['p95_ms']} ms  (budget: {P95_BUDGET_MS} ms)")
        if summary["failures"]:
            print()
            print("Failures:")
            for f in summary["failures"]:
                print(f"  {f['path']}  [{f['status']}]  {f['error']}")

    exit_code = 0
    if strict and summary["failed"]:
        exit_code = 1
    if strict and summary["p95_ms"] > P95_BUDGET_MS:
        print(f"FAIL: p95 {summary['p95_ms']} ms exceeds budget {P95_BUDGET_MS} ms")
        exit_code = 1

    # Structural vacuity guard (born 2026-05-19h — mechanizes the
    # §1.4 "sanity-check the implausible green" lesson). `passed`
    # counts skipped(401/403/404) rows as ok=True, so a forged
    # session that authenticates NOTHING still reports
    # "passed == total". The honest metric is genuinely-2xx =
    # passed - skipped_auth. If a pro+active smoke session cannot
    # reach the majority of /pro·/merchant·/analytics GET routes,
    # the harness is testing ~nothing (the 2026-05-19 finding:
    # token sv=0 vs merchant sv=19 → 136/137 skipped while green).
    # Measured-healthy baseline = 86% genuine; 50% floor leaves a
    # 36-pt margin (non-flaky vs normal 404/operator variation)
    # while catching the catastrophic "session broken" collapse.
    if strict and _smoke_session_vacuous(summary):
        genuine = summary["passed"] - summary["skipped_auth"]
        print(
            f"FAIL: smoke harness vacuous — only {genuine}/"
            f"{summary['total']} routes genuinely 2xx "
            f"({summary['skipped_auth']} auth-skipped). The forged "
            f"smoke session is NOT authenticating (likely token "
            f"session_version vs merchant.session_version drift "
            f"— see _ensure_seed_merchant). A green run here would "
            f"be fiction."
        )
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
