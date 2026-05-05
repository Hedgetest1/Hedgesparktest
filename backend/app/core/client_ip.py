"""
Single source of truth for extracting the real client IP from a request.

Precedence (highest authority first):
  1. CF-Connecting-IP — Cloudflare sets this to the true client IP.
     **Read only when `CLOUDFLARE_FRONTED=true`** (see env-gate below).
     Cannot be chained or appended; when CF is in front it is
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

CLOUDFLARE_FRONTED env gate
---------------------------
Pre-CDN-flip, `api.hedgesparkhq.com` is exposed directly to Internet.
Anyone can send `CF-Connecting-IP: <victim>` and — without the gate —
the helper would trust that header. Same family of issue as
first-hop XFF spoofing, but the gate makes the deploy state explicit:
the helper trusts the CF header ONLY when the env flag is truthy
("1", "true", "yes", case-insensitive).

Default: `CLOUDFLARE_FRONTED=false`. Founder flips to `true` in
`.env` AFTER:
  1. NS records propagated to Cloudflare
  2. Verification curl shows `cf-ray:` header on api responses
  3. Backend restarted (PM2 restart wishspark-backend)

Until then, the helper behaves identically to pre-Cloudflare:
XFF first-hop → request.client.host. No regression vs pre-commit.

Read at MODULE LOAD time. Restart the worker after .env change for
the new value to take effect — operationally the same as any env
change.

Spoof posture
-------------
The env gate is defense-in-depth, NOT a security panacea. Even
post-flip, an attacker bypassing CF and hitting origin directly can
spoof CF-Connecting-IP. The full mitigation is origin-lock at TIER_2
(Cloudflare Authenticated Origin Pulls OR Traefik IP whitelist for
CF source ranges). Documented in
`screenshots/CLOUDFLARE_SETUP.txt` Part E. The helper never elevates
trust beyond what the upstream layer guarantees — it just unifies
the read site and gates the CF-header trust at deploy time.

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

import logging
import os
from threading import Lock
from typing import Literal, Tuple

_log = logging.getLogger("wishspark.client_ip")

IPSource = Literal["cf", "xff", "client", "unknown"]

_MAX_LEN = 64  # IPv6 max is 39; 64 covers truncation safely

# Worker-local "unknown" rate-limit. The helper returns "unknown" only
# when CF-Connecting-IP is absent (or gated), XFF is absent, AND the
# socket peer is also absent. Under Traefik-fronted production this
# should NEVER fire — it's a "proxy is misconfigured" smoke alarm.
# Log once per worker process (4 workers = up to 4 lines per restart),
# enough to fire the signal without flooding logs.
_unknown_warned = False  # multi-worker: thread-only — per-worker by design
_unknown_warn_lock = Lock()  # multi-worker: thread-only — per-process lock


def _warn_unknown_once() -> None:
    global _unknown_warned
    with _unknown_warn_lock:
        if _unknown_warned:
            return
        _unknown_warned = True
    _log.warning(
        "client_ip: returned 'unknown' — proxy headers missing AND socket "
        "peer absent. Indicates Traefik mis-config or test harness without "
        "client. This worker process logs once per lifetime."
    )


def _read_cloudflare_fronted() -> bool:
    """Resolve the env-gate. Module-load by default; tests can monkeypatch
    and call this to refresh."""
    return os.getenv("CLOUDFLARE_FRONTED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


# Module-load read. Workers re-read on restart.
CLOUDFLARE_FRONTED: bool = _read_cloudflare_fronted()


def extract_client_ip_with_source(request) -> Tuple[str, IPSource]:
    """Return (ip, source). Source is the layer that supplied the value.

    Empty/whitespace headers are skipped — fall through to the next
    layer. The returned IP is length-capped at 64 chars; storage call
    sites historically truncated, so preserving that contract.

    `CF-Connecting-IP` is read only when `CLOUDFLARE_FRONTED=true`.
    Pre-flip, the gate ensures we don't accept the spoofable CF header
    just because somebody sent it.
    """
    if CLOUDFLARE_FRONTED:
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

    _warn_unknown_once()
    return "unknown", "unknown"


def extract_client_ip(request) -> str:
    """Return the IP only. Default for sites that don't audit the source."""
    ip, _ = extract_client_ip_with_source(request)
    return ip
