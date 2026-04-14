"""
slo_api.py — Ops-facing SLO endpoints.

  GET /ops/slo             — full SLO report
  GET /ops/slo/{name}      — single SLO detail
  GET /ops/slo/routes/{route} — arbitrary route stats for debugging
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.slo import CATALOGUE, route_stats, slo_report

router = APIRouter(tags=["slo"], include_in_schema=False)


def _require_ops_key(x_api_key: str | None) -> None:
    expected = os.environ.get("OPS_API_KEY")
    if not expected:
        raise HTTPException(500, "OPS_API_KEY not configured")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "invalid ops api key")


@router.get("/ops/slo")
def get_slo_report(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    report = slo_report()
    summary = {
        "total": len(report),
        "healthy": sum(1 for s in report if s["health"] == "healthy"),
        "warning": sum(1 for s in report if "warning" in s["health"]),
        "breach": sum(1 for s in report if "breach" in s["health"] or s["health"] == "critical_burn"),
        "insufficient_data": sum(1 for s in report if s["health"] == "insufficient_data"),
    }
    return {"summary": summary, "slos": report}


@router.get("/ops/slo/{name}")
def get_slo_detail(name: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    match = next((s for s in CATALOGUE if s.name == name), None)
    if not match:
        raise HTTPException(404, "slo not registered")
    return {
        "slo": {
            "name": match.name,
            "route": match.route,
            "method": match.method,
            "availability_target_pct": match.availability_target_pct,
            "latency_p95_target_ms": match.latency_p95_target_ms,
        },
        "stats_5m": route_stats(match.route, match.method, "5m"),
        "stats_60m": route_stats(match.route, match.method, "60m"),
    }


@router.get("/ops/slo/routes/inspect")
def inspect_route(
    route: str = Query(...),
    method: str = Query(default="GET"),
    window: str = Query(default="5m", pattern="^(5m|60m)$"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _require_ops_key(x_api_key)
    return route_stats(route, method, window)
