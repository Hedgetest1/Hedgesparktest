"""
feature_usage_api.py — Ops dashboard for shipped-vs-used telemetry.

  GET /ops/features         — all features + counters (ops-gated)
  GET /ops/features/dormant — dormant features only (ops-gated)
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from app.core.feature_usage import all_stats, dormant_features

router = APIRouter(tags=["feature_usage"])


def _require_ops_key(x_api_key: str | None) -> None:
    expected = os.environ.get("OPS_API_KEY")
    if not expected:
        raise HTTPException(500, "OPS_API_KEY not configured")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "invalid ops api key")


@router.get("/ops/features")
def list_features(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    stats = all_stats()
    return {
        "total": len(stats),
        "dormant": sum(1 for s in stats if s["dormant"]),
        "active": sum(1 for s in stats if not s["dormant"]),
        "features": stats,
    }


@router.get("/ops/features/dormant")
def list_dormant(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    return {"dormant": dormant_features()}
