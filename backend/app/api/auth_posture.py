"""
auth_posture.py — Ops endpoint for auth hardening inspection.

  GET /ops/auth/posture — secret posture + session anomaly settings
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from app.core.auth_hardening import auth_posture

router = APIRouter(tags=["auth_posture"])


def _require_ops_key(x_api_key: str | None) -> None:
    expected = os.environ.get("OPS_API_KEY")
    if not expected:
        raise HTTPException(500, "OPS_API_KEY not configured")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "invalid ops api key")


@router.get("/ops/auth/posture")
def get_posture(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    _require_ops_key(x_api_key)
    posture = auth_posture()
    # Redact the actual secret values — only status is returned
    return posture
