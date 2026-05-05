"""
client_ip_echo.py — Ops smoke endpoint for Cloudflare CDN flip verification.

GET /ops/client-ip-echo — returns the resolved client IP + source layer
(cf / xff / client / unknown) plus the CLOUDFLARE_FRONTED env-gate
state. One curl-line for founder post-flip verification.

Ops-only auth (X-API-Key === OPS_API_KEY) — same gate as
/ops/auth/posture and the other /ops endpoints. Never exposed to
merchants. Returns no PII beyond the IP itself, which the request
already exposes to the server.

Use case
--------
After flipping NS records to Cloudflare, the founder runs:

    curl -s https://api.hedgesparkhq.com/ops/client-ip-echo \\
         -H "X-API-Key: $OPS_API_KEY"

Expected after flip (and after `CLOUDFLARE_FRONTED=true` in .env +
backend restart):

    {"ip": "<real_client_ip>", "source": "cf",
     "cloudflare_fronted": true, ...}

If `source` shows "xff" or "client" while CF is supposedly active,
the CLOUDFLARE_FRONTED env flag is wrong OR Cloudflare is not actually
in front (DNS still pointing at origin).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException, Request

from app.core import cf_ip_ranges as cf_ip_ranges_mod
from app.core import client_ip as client_ip_mod
from app.core.client_ip import extract_client_ip_with_source, get_cf_gate_counters

router = APIRouter(tags=["ops"])


def _require_ops_key(x_api_key: str | None) -> None:
    expected = os.environ.get("OPS_API_KEY")
    if not expected:
        raise HTTPException(500, "OPS_API_KEY not configured")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "invalid ops api key")


@router.get("/ops/client-ip-echo")
def echo_client_ip(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict:
    """Return the resolved client IP + source + env-gate state.

    Reports the headers the helper consults so the founder can confirm
    Cloudflare is actually setting them (and not just appearing to be
    via DNS-only mode).
    """
    _require_ops_key(x_api_key)

    ip, source = extract_client_ip_with_source(request)

    # Surface the raw header values the helper would see (presence only,
    # not contents — except CF-Connecting-IP which is the IP itself, no PII).
    # client-ip: ok — diagnostic surface, not extraction; intentional reads
    cf_header_present = bool((request.headers.get("cf-connecting-ip") or "").strip())
    # client-ip: ok — diagnostic surface, not extraction; intentional reads
    xff_header_present = bool((request.headers.get("x-forwarded-for") or "").strip())
    cf_ray = request.headers.get("cf-ray") or None  # CF identification

    # Source-IP gate diagnostic — show whether the socket peer is in CF
    # ranges. If True + CF-Connecting-IP present + env on, the helper
    # trusted the header. If False, the helper ignored it.
    socket_peer = getattr(getattr(request, "client", None), "host", None)
    socket_peer_is_cf = (
        cf_ip_ranges_mod.is_from_cloudflare(socket_peer) if socket_peer else False
    )

    return {
        "ip": ip,
        "source": source,
        "cloudflare_fronted": client_ip_mod.CLOUDFLARE_FRONTED,
        "cf_connecting_ip_header_present": cf_header_present,
        "x_forwarded_for_header_present": xff_header_present,
        "cf_ray": cf_ray,
        "socket_peer": socket_peer,
        "socket_peer_is_cf_range": socket_peer_is_cf,
        "cf_gate_counters": get_cf_gate_counters(),
        "interpretation": _interpret(
            source, client_ip_mod.CLOUDFLARE_FRONTED, cf_header_present, cf_ray,
            socket_peer_is_cf,
        ),
    }


@router.get("/ops/cf-ranges")
def cf_ranges_state(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict:
    """Inspect the Cloudflare IP-range cache.

    Returns the count of loaded networks, last-refresh source/timestamp,
    and bundled-snapshot fallback metrics. Use to verify the source-IP
    gate has a sane list.
    """
    _require_ops_key(x_api_key)
    return cf_ip_ranges_mod.get_state()


@router.post("/ops/cf-ranges/refresh")
def cf_ranges_refresh(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict:
    """Force-refresh the CF IP-range cache from cloudflare.com.

    Falls back to the bundled snapshot on network failure (degrade-open).
    Returns the result of the refresh attempt.
    """
    _require_ops_key(x_api_key)
    return cf_ip_ranges_mod.refresh_from_cloudflare()


def _interpret(
    source: str,
    cloudflare_fronted: bool,
    cf_header: bool,
    cf_ray: str | None,
    socket_peer_is_cf: bool,
) -> str:
    """Plain-English diagnostic for the founder."""
    if not cloudflare_fronted and cf_header:
        return (
            "CLOUDFLARE_FRONTED is FALSE but Cloudflare-style headers are "
            "present. Either CF is not yet active (and someone is spoofing "
            "the header — gate is correctly ignoring it), OR you flipped CF "
            "but didn't update the env var. Set CLOUDFLARE_FRONTED=true "
            "in .env and restart wishspark-backend after verifying cf-ray "
            "below is non-null."
        )
    if cloudflare_fronted and not cf_header:
        return (
            "CLOUDFLARE_FRONTED is TRUE but no CF-Connecting-IP header was "
            "received. Cloudflare may not actually be in front of this "
            "request (DNS-only mode? bypass via direct IP?). Verify cf-ray "
            "is non-null; if null, traffic is reaching origin without "
            "passing through Cloudflare."
        )
    if cloudflare_fronted and cf_header and not socket_peer_is_cf:
        return (
            "⚠️ CLOUDFLARE_FRONTED is TRUE and CF-Connecting-IP is present, "
            "but the socket peer is NOT in published Cloudflare ranges. The "
            "source-IP gate IGNORED the header (origin-lock at app layer). "
            "This is either a spoof attempt (someone hit origin directly "
            "with a fake CF header) or a misconfigured upstream. The helper "
            "fell through to XFF / socket peer."
        )
    if cloudflare_fronted and cf_header and cf_ray and source == "cf":
        return (
            "✅ Cloudflare is in front, source-IP gate verified the socket "
            "peer is a CF range, and the helper is using CF-Connecting-IP."
        )
    if not cloudflare_fronted and not cf_header and source in {"xff", "client"}:
        return "✅ Pre-Cloudflare mode active — helper using XFF or socket peer as expected."
    return (
        f"source={source}, cf_fronted={cloudflare_fronted}, cf_ray={cf_ray}, "
        f"socket_peer_is_cf={socket_peer_is_cf}"
    )
