"""
feature_flags_admin.py — Ops-only admin surface for feature flags.

Guarded by X-API-Key === OPS_API_KEY (same gate as other /ops endpoints).

  GET  /ops/flags                — list all registered flags + live state
  GET  /ops/flags/{name}         — single flag inspection
  POST /ops/flags/{name}         — update flag state (partial)
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.feature_flags import get_flag_state, list_flags, set_flag, REGISTRY

router = APIRouter(tags=["feature_flags"], include_in_schema=False)


def _require_ops_key(x_api_key: str | None) -> None:
    expected = os.environ.get("OPS_API_KEY")
    if not expected:
        raise HTTPException(500, "OPS_API_KEY not configured on server")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "invalid ops api key")


@router.get("/ops/flags")
def list_all_flags(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    return {"flags": list_flags()}


@router.get("/ops/flags/{name}")
def get_flag(name: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    if name not in REGISTRY:
        raise HTTPException(404, "flag not registered")
    return get_flag_state(name)


class FlagPatch(BaseModel):
    enabled: bool | None = None
    percentage: int | None = Field(default=None, ge=0, le=100)
    allowlist: list[str] | None = Field(default=None, max_length=500)
    killswitch: bool | None = None
    ring: int | None = Field(default=None, ge=0, le=3)


@router.post("/ops/flags/{name}")
def patch_flag(
    name: str,
    patch: FlagPatch,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _require_ops_key(x_api_key)
    if name not in REGISTRY:
        raise HTTPException(404, "flag not registered")
    ok = set_flag(
        name,
        enabled=patch.enabled,
        percentage=patch.percentage,
        allowlist=patch.allowlist,
        killswitch=patch.killswitch,
        ring=patch.ring,
    )
    if not ok:
        raise HTTPException(503, "flag store unreachable")
    return get_flag_state(name)
