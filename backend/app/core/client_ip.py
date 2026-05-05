"""
Single source of truth for extracting the real client IP from a request.

Precedence (highest authority first):
  1. CF-Connecting-IP — Cloudflare sets this to the true client IP.
     Cannot be chained or appended; if we are behind Cloudflare it is
     authoritative.
  2. X-Forwarded-For first hop — Traefik appends the client IP at the
     LEFT of the chain. When behind Cloudflare, CF prepends its own
     value; without CF, Traefik's first hop is the client.
  3. request.client.host — direct socket peer. Only meaningful when no
     reverse proxy or CDN is in front (dev / loopback).

Why a single helper exists
--------------------------
Before this module, 7 sites read the client IP independently — 4 with
ad-hoc XFF fallback, 3 with bare `request.client.host`. Under
Cloudflare, the bare-socket reads collapse every client onto the CF POP
IP — rate-limit becomes a global cap, audit logs lose attribution,
visitor tracking flattens to one identity per POP. This helper makes
the precedence uniform and makes the Cloudflare flip a configuration
event rather than a code-rewrite event.

Spoof posture
-------------
`CF-Connecting-IP` is trustworthy ONLY when origin requests are gated
to come from Cloudflare (Authenticated Origin Pulls or origin-IP
whitelist on Traefik). Until that gate is in place, an attacker
hitting api.hedgesparkhq.com directly can spoof the header. The
runbook in `screenshots/CLOUDFLARE_SETUP.txt` documents the TIER_2
origin-lock as a same-day-as-NS-flip step. The helper never elevates
trust beyond what the upstream layer guarantees — it just unifies the
read site.

Return shape
------------
A tuple `(ip: str, source: Literal["cf","xff","client","unknown"])`.
The `source` tag is preserved for audit traceability — when an
incident shows "all rate-limit hits from CF POPs", the audit row
shows `source="cf"` and we know the spoof gate is doing its job.
A convenience `extract_client_ip()` returns just the IP string for
sites that don't need the source tag.
"""
from __future__ import annotations

from typing import Literal, Tuple

IPSource = Literal["cf", "xff", "client", "unknown"]

_MAX_LEN = 64  # IPv6 max is 39; 64 covers truncation safely


def extract_client_ip_with_source(request) -> Tuple[str, IPSource]:
    """Return (ip, source). Source is the layer that supplied the value.

    Empty/whitespace headers are skipped — fall through to the next
    layer. The returned IP is length-capped at 64 chars; storage call
    sites historically truncated, so preserving that contract.
    """
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf[:_MAX_LEN], "cf"

    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first[:_MAX_LEN], "xff"

    client = getattr(request, "client", None)
    if client is not None:
        host = getattr(client, "host", None)
        if host:
            return str(host)[:_MAX_LEN], "client"

    return "unknown", "unknown"


def extract_client_ip(request) -> str:
    """Return the IP only. Default for sites that don't audit the source."""
    ip, _ = extract_client_ip_with_source(request)
    return ip
