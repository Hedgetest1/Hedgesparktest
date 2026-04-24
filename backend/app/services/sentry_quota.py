"""Thin client for Sentry's quota-usage API.

Closes the D11 "no quota visibility" gap flagged in the 2026-04-24
Sentry hardening audit. Triggered by the real incident that morning:
founder received a "100% cron monitors consumed" email, and we had
no operator-facing way to check remaining budget on any other
quota-bearing surface (errors, transactions, replays, profiles,
attachments). This module adds the polling path.

Used by:
  * /ops/sentry-budget endpoint (operator dashboard tile)

Read-only. Never mutates Sentry state. Fails soft when auth unset —
returns a structured "unconfigured" payload rather than raising, so
the operator dashboard always renders something.

Cached in Redis for 5min (hs:sentry:quota:v1) to avoid hammering the
Sentry API and to survive brief network blips without spamming alerts.

Tier: TIER_0.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("sentry_quota")

_SENTRY_API = "https://sentry.io/api/0"
_CACHE_KEY = "hs:sentry:quota:v1"
_CACHE_TTL = 300  # 5 min
_REQUEST_TIMEOUT_S = 10.0


def _cached_fetch() -> dict[str, Any] | None:
    try:
        from app.core.redis_client import _client
        raw = _client().get(_CACHE_KEY)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _cache_store(payload: dict[str, Any]) -> None:
    try:
        from app.core.redis_client import _client
        _client().set(_CACHE_KEY, json.dumps(payload), ex=_CACHE_TTL)
    except Exception:
        pass  # SILENT-EXCEPT-OK: cache write is best-effort


def get_quota_summary(refresh: bool = False) -> dict[str, Any]:
    """Return a normalized summary of current-period Sentry quota usage.

    Shape:
      {
        "configured": bool,
        "cached": bool,
        "reason": str | None,                 # when configured=False
        "org": str | None,
        "project": str | None,
        "period": {"start": iso, "end": iso} | None,
        "quotas": [
            {"category": "errors", "accepted": N, "total": N},
            {"category": "transactions", ...},
            ...
        ] | [],
        "raw_error": str | None,              # on API failure
      }
    """
    token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    org = os.getenv("SENTRY_ORG", "").strip()
    project = os.getenv("SENTRY_PROJECT", "").strip()

    if not token or not org:
        return {
            "configured": False,
            "cached": False,
            "reason": "SENTRY_AUTH_TOKEN + SENTRY_ORG must be set in backend/.env",
            "org": org or None,
            "project": project or None,
            "period": None,
            "quotas": [],
            "raw_error": None,
        }

    if not refresh:
        cached = _cached_fetch()
        if cached is not None:
            cached["cached"] = True
            return cached

    url = f"{_SENTRY_API}/organizations/{org}/stats_v2/"
    params = {
        "field": "sum(quantity)",
        "interval": "1d",
        "statsPeriod": "30d",
        "groupBy": ["category", "outcome"],
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            payload: dict[str, Any] = {
                "configured": True,
                "cached": False,
                "reason": None,
                "org": org,
                "project": project or None,
                "period": None,
                "quotas": [],
                "raw_error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
            _cache_store(payload)
            return payload
        body = resp.json() or {}
    except Exception as exc:
        log.warning("sentry_quota: fetch failed: %s", exc)
        payload = {
            "configured": True,
            "cached": False,
            "reason": None,
            "org": org,
            "project": project or None,
            "period": None,
            "quotas": [],
            "raw_error": f"{type(exc).__name__}: {exc}",
        }
        return payload

    quotas: list[dict[str, Any]] = []
    groups = body.get("groups", []) or []
    tally: dict[str, dict[str, int]] = {}
    for g in groups:
        by = g.get("by", {}) or {}
        cat = by.get("category") or "unknown"
        outcome = by.get("outcome") or "unknown"
        totals = g.get("totals", {}) or {}
        qty = int(totals.get("sum(quantity)", 0) or 0)
        slot = tally.setdefault(cat, {"accepted": 0, "total": 0, "filtered": 0, "rate_limited": 0})
        slot["total"] += qty
        if outcome == "accepted":
            slot["accepted"] += qty
        elif outcome == "filtered":
            slot["filtered"] += qty
        elif outcome == "rate_limited":
            slot["rate_limited"] += qty
    for cat, vals in sorted(tally.items()):
        quotas.append({"category": cat, **vals})

    intervals = body.get("intervals") or []
    period = None
    if intervals:
        period = {"start": intervals[0], "end": intervals[-1]}

    payload = {
        "configured": True,
        "cached": False,
        "reason": None,
        "org": org,
        "project": project or None,
        "period": period,
        "quotas": quotas,
        "raw_error": None,
    }
    _cache_store(payload)
    return payload
