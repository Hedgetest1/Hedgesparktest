"""
cf_ip_ranges.py — Cloudflare IP range membership for app-layer origin-lock.

Used by `app/core/client_ip.py` to verify that the socket peer of an
incoming request is actually a Cloudflare server before trusting the
`CF-Connecting-IP` header. Defends against attackers that bypass
Cloudflare and connect directly to the origin while spoofing the
header.

Source of truth: cloudflare.com/ips-v4 + cloudflare.com/ips-v6.
Cloudflare publishes range changes ~3 times per year, so a 7-day cache
is conservative. A bundled snapshot ships in this module so the helper
is functional pre-network-fetch and during outages of cloudflare.com.

Degrade-open posture
--------------------
If the network refresh fails AND the bundled list is somehow empty,
`is_from_cloudflare()` returns False — the upstream caller in
`client_ip.py` will then ignore the CF header. This is degrade-CLOSED
on the trust gate but degrade-OPEN on the request itself: the request
still completes, just attributed to XFF/socket peer instead of the
spoofable CF header. **No request is ever rejected on the basis of CF
membership** — that's intentional, because the bundled list could go
stale and we'd rather under-trust than block legitimate traffic.

API
---
- `is_from_cloudflare(ip: str) -> bool` — hot path; pure CIDR lookup.
- `refresh_from_cloudflare(timeout=5.0) -> dict` — fetch + replace
  module-level cache. Called at startup + periodically (worker, ops).
- `get_state() -> dict` — diagnostic snapshot for ops endpoint.
"""
from __future__ import annotations

import ipaddress
import logging
from threading import Lock
from typing import List, Optional, Union

import httpx

_log = logging.getLogger("wishspark.cf_ip_ranges")
_lock = Lock()  # multi-worker: thread-only — per-worker init lock, no shared state

# Bundled snapshot from https://www.cloudflare.com/ips-v4 and ips-v6 as
# of 2026-05-05. Used as fallback before first network refresh and when
# cloudflare.com is unreachable. Refresh schedule: 7 days.
_BUNDLED_V4: List[str] = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]

_BUNDLED_V6: List[str] = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
]

_CF_IPS_V4_URL = "https://www.cloudflare.com/ips-v4"
_CF_IPS_V6_URL = "https://www.cloudflare.com/ips-v6"

NetworkT = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
_networks: Optional[List[NetworkT]] = None  # multi-worker: per-worker cache, OK
_last_refresh_ts: Optional[float] = None
_last_refresh_source: str = "uninitialized"


def _parse_networks(v4_lines: List[str], v6_lines: List[str]) -> List[NetworkT]:
    out: List[NetworkT] = []
    for cidr in v4_lines:
        s = cidr.strip()
        if not s:
            continue
        try:
            out.append(ipaddress.IPv4Network(s, strict=False))
        except ValueError:
            _log.warning("cf_ip_ranges: invalid v4 cidr %r — skipped", s)
    for cidr in v6_lines:
        s = cidr.strip()
        if not s:
            continue
        try:
            out.append(ipaddress.IPv6Network(s, strict=False))
        except ValueError:
            _log.warning("cf_ip_ranges: invalid v6 cidr %r — skipped", s)
    return out


def _ensure_loaded() -> List[NetworkT]:
    """Initialise from bundled snapshot if cache is empty."""
    global _networks, _last_refresh_source
    if _networks is None:
        with _lock:
            if _networks is None:
                _networks = _parse_networks(_BUNDLED_V4, _BUNDLED_V6)
                _last_refresh_source = "bundled-init"
    return _networks


def is_from_cloudflare(ip: Optional[str]) -> bool:
    """Return True iff `ip` is in a known Cloudflare network range.

    Hot path: called once per request when CF-Connecting-IP is present
    and CLOUDFLARE_FRONTED is on. Pure CIDR lookup, no I/O.
    """
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except (ValueError, AttributeError):
        return False
    nets = _ensure_loaded()
    is_v4 = isinstance(addr, ipaddress.IPv4Address)
    for net in nets:
        if is_v4 and isinstance(net, ipaddress.IPv4Network):
            if addr in net:
                return True
        elif not is_v4 and isinstance(net, ipaddress.IPv6Network):
            if addr in net:
                return True
    return False


def refresh_from_cloudflare(timeout: float = 5.0) -> dict:
    """Fetch the latest CF IP ranges and replace the module cache.

    On success: cache replaced, source="network".
    On failure: cache preserved (or initialised from bundled if empty),
    source="bundled-fallback" with `error` populated.

    Never raises — the helper must always succeed because client_ip.py
    is on the request hot path.
    """
    global _networks, _last_refresh_ts, _last_refresh_source
    import time as _time  # local import to avoid module-load surface
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as cli:
            r4 = cli.get(_CF_IPS_V4_URL)
            r4.raise_for_status()
            r6 = cli.get(_CF_IPS_V6_URL)
            r6.raise_for_status()
        v4 = [ln for ln in r4.text.splitlines() if ln.strip()]
        v6 = [ln for ln in r6.text.splitlines() if ln.strip()]
        nets = _parse_networks(v4, v6)
        if not nets:
            raise RuntimeError("cloudflare.com returned 0 parseable ranges")
        with _lock:
            _networks = nets
            _last_refresh_ts = _time.time()
            _last_refresh_source = "network"
        return {
            "v4_count": sum(1 for n in nets if isinstance(n, ipaddress.IPv4Network)),
            "v6_count": sum(1 for n in nets if isinstance(n, ipaddress.IPv6Network)),
            "source": "network",
            "ts": _last_refresh_ts,
        }
    except Exception as exc:
        _log.warning("cf_ip_ranges: refresh failed (%s) — keeping current cache", exc)
        _ensure_loaded()  # ensure cache is at least bundled
        return {
            "v4_count": sum(1 for n in (_networks or []) if isinstance(n, ipaddress.IPv4Network)),
            "v6_count": sum(1 for n in (_networks or []) if isinstance(n, ipaddress.IPv6Network)),
            "source": _last_refresh_source,
            "error": str(exc),
        }


def get_state() -> dict:
    """Snapshot for /ops endpoint diagnostic."""
    nets = _networks or []
    v4 = sum(1 for n in nets if isinstance(n, ipaddress.IPv4Network))
    v6 = sum(1 for n in nets if isinstance(n, ipaddress.IPv6Network))
    return {
        "loaded": _networks is not None,
        "total": len(nets),
        "v4_count": v4,
        "v6_count": v6,
        "last_refresh_ts": _last_refresh_ts,
        "last_refresh_source": _last_refresh_source,
        "bundled_v4_count": len(_BUNDLED_V4),
        "bundled_v6_count": len(_BUNDLED_V6),
    }
