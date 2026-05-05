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
Two-stage trust gate:
  1. **Env gate** (`CLOUDFLARE_FRONTED`) — deploy-time switch. Pre-flip
     the helper ignores `CF-Connecting-IP` entirely.
  2. **Source-IP gate** (TIER_1 origin-lock, 2026-05-05) — even with
     the env gate open, the helper trusts `CF-Connecting-IP` only when
     the request's socket peer is in published Cloudflare IP ranges
     (see `cf_ip_ranges.py`). An attacker bypassing CF cannot spoof
     the header from a non-CF source — the helper falls through to
     XFF / socket peer.

This second gate handles the residual risk that the env gate alone
cannot: post-flip, an attacker reaching the origin directly (via
direct-IP DNS, leaked origin IP, etc.) cannot impersonate a real CF
proxy because their socket peer is not in CF ranges.

Future hardening (TIER_2, deferred): Cloudflare Authenticated Origin
Pulls (mTLS) OR Traefik IP whitelist on `/docker/traefik/dynamic/
wishspark.yml`. Both block at the proxy layer rather than the app
layer. App-layer gate is sufficient for current scale + threat model;
TIER_2 mTLS is "defense in depth" for the future.

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

# CF source-IP gate counters. Per-worker, in-memory — surfaced via
# /ops/cf-ranges. Spike in `_cf_spoof_count` means an attacker (or a
# misconfigured upstream) is sending CF-Connecting-IP from a non-CF
# socket peer; the gate ignored it.
_cf_spoof_count = 0  # multi-worker: accept-degrade — per-worker counter, trend over total fine
_cf_trust_count = 0  # multi-worker: accept-degrade — per-worker counter, trend over total fine
_cf_counter_lock = Lock()  # multi-worker: thread-only — per-worker counter lock


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


def _bump_cf_counter(trusted: bool) -> None:
    global _cf_spoof_count, _cf_trust_count
    with _cf_counter_lock:
        if trusted:
            _cf_trust_count += 1
        else:
            _cf_spoof_count += 1


def get_cf_gate_counters() -> dict:
    """Return per-worker counters for /ops diagnostic.

    Multi-worker note: each worker has its own counter; the ops endpoint
    gives a single-worker view. Spike trend is what matters, not the
    absolute total across workers.
    """
    with _cf_counter_lock:
        return {"trusted": _cf_trust_count, "ignored_non_cf_source": _cf_spoof_count}


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

    Two-stage CF gate:
      1. `CLOUDFLARE_FRONTED` env must be true (deploy-time switch).
      2. The socket peer must be in a published Cloudflare IP range
         (see `app/core/cf_ip_ranges.py`).
    Both must hold to trust `CF-Connecting-IP`. If only (1) holds and
    (2) fails, the header is ignored and the spoof counter is bumped —
    this is the post-flip origin-lock at the app layer.
    """
    if CLOUDFLARE_FRONTED:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf:
            # Source-IP verification: trust CF-Connecting-IP only if the
            # socket peer is actually a Cloudflare server. Defends against
            # spoofed CF headers from origin-bypassing attackers.
            from app.core.cf_ip_ranges import is_from_cloudflare
            client = getattr(request, "client", None)
            socket_peer = getattr(client, "host", None) if client else None
            if socket_peer and is_from_cloudflare(socket_peer):
                _bump_cf_counter(trusted=True)
                return cf[:_MAX_LEN], "cf"
            # Header present but socket peer not in CF ranges → spoof
            # attempt or misconfigured upstream. Ignore the header,
            # bump counter, fall through to XFF/socket.
            _bump_cf_counter(trusted=False)

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
