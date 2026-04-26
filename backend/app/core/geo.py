"""
Lightweight IP geolocation for live visitor mapping.

Strategy
--------
1. Extract client IP from request (handles X-Forwarded-For behind Traefik)
2. Lookup geo via ip-api.com (free, no key needed, 45 req/min)
3. Cache result in Redis for 24h by IP
4. Store per-visitor geo in Redis with 1h TTL for live map

All lookups are best-effort. If geo fails, the visitor just won't
appear on the map — the radar still works fine.
"""

import logging
import threading
import httpx
from .redis_client import cache_get, cache_set

log = logging.getLogger(__name__)

# Reusable httpx client for geo lookups (thread-safe)
_http_client: httpx.Client | None = None

def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=2.0)
    return _http_client

_GEO_IP_CACHE_TTL = 86400      # 24h — IPs don't move
_VISITOR_GEO_TTL = 3600         # 1h — live visitors rotate


def _extract_ip(request) -> str | None:
    """Get real client IP, handling X-Forwarded-For from Traefik."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in chain is the real client
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _lookup_ip_sync(ip: str) -> dict | None:
    """Query ip-api.com for geo data (sync). Returns None on failure."""
    # Skip private/local IPs
    if ip.startswith(("127.", "10.", "192.168.", "172.16.", "172.17.",
                      "172.18.", "172.19.", "172.2", "172.3",
                      "::1", "fe80", "fd")):
        return None

    cache_key = f"hs:geoip:{ip}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached if cached != "__none__" else None

    try:
        client = _get_http_client()
        resp = client.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,countryCode,city,lat,lon"},
        )
        if resp.status_code != 200:
            cache_set(cache_key, "__none__", 300)
            return None

        data = resp.json()
        if data.get("status") != "success":
            cache_set(cache_key, "__none__", 300)
            return None

        geo = {
            "country": data.get("country", ""),
            "country_code": data.get("countryCode", ""),
            "city": data.get("city", ""),
            "lat": data.get("lat", 0),
            "lon": data.get("lon", 0),
        }
        cache_set(cache_key, geo, _GEO_IP_CACHE_TTL)
        return geo

    except Exception:
        cache_set(cache_key, "__none__", 300)
        return None


def _do_capture(request, shop_domain: str, visitor_id: str) -> None:
    """Background thread worker for geo capture."""
    ip = _extract_ip(request)
    if not ip:
        return
    geo = _lookup_ip_sync(ip)
    if not geo:
        return
    cache_set(f"hs:geo:{shop_domain}:{visitor_id}", geo, _VISITOR_GEO_TTL)


def capture_visitor_geo_sync(request, shop_domain: str, visitor_id: str) -> None:
    """
    Fire-and-forget geo capture. Runs lookup in background thread
    so the track response is never delayed by geo.
    """
    t = threading.Thread(
        target=_do_capture,
        args=(request, shop_domain, visitor_id),
        daemon=True,
    )
    t.start()


def get_visitor_geo(shop_domain: str, visitor_id: str) -> dict | None:
    """Retrieve cached geo for a visitor. Used by live visitors endpoint."""
    return cache_get(f"hs:geo:{shop_domain}:{visitor_id}")


# ───────────────────────────────────────────────────────────────────
# Order geo aggregation
# ───────────────────────────────────────────────────────────────────
#
# At purchase time we look up the visitor's cached geo (1h TTL — fresh
# at moment of purchase, since the page-view that fired purchase ALSO
# capture'd geo) and write a per-shop, per-country, per-day counter to
# Redis. The aggregate survives 90 days; the live-visitor cache stays
# 1h-bounded.
#
# Storage shape — single hash per shop, multi-field:
#   HSET hs:order_geo:{shop_domain}
#        "{country_code}:{YYYY-MM-DD}:count"           {N}
#        "{country_code}:{YYYY-MM-DD}:revenue_{CCY}"   {sum}
#
# Reuses the existing visitor_geo path — no schema migration needed.
# Fail-closed and silent: if Redis is down or geo is missing, the
# order still lands in shop_orders; we just lose this analytic.
# ───────────────────────────────────────────────────────────────────

_ORDER_GEO_TTL_S = 90 * 86400  # 90 days


def record_order_geo(
    shop_domain: str, visitor_id: str, revenue: float, currency: str
) -> None:
    """Record an order's country contribution to the per-shop aggregate.

    Best-effort. Called from track._persist_purchase after the order
    has been persisted to shop_orders. Never raises — geo enrichment
    is auxiliary."""
    if not visitor_id or not shop_domain or revenue <= 0:
        return
    geo = get_visitor_geo(shop_domain, visitor_id)
    if not geo:
        return
    cc = (geo.get("country_code") or "").upper()
    if not cc or len(cc) != 2:
        return

    try:
        from .redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("geo.record_order_geo.no_redis")
            return
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        ccy = (currency or "USD").upper()

        key = f"hs:order_geo:{shop_domain}"
        rc.hincrby(key, f"{cc}:{today}:count", 1)
        rc.hincrbyfloat(key, f"{cc}:{today}:revenue_{ccy}", float(revenue))
        rc.expire(key, _ORDER_GEO_TTL_S)
    except Exception as exc:
        log.warning("record_order_geo: %s", exc)
        try:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("geo.record_order_geo")
        except Exception:
            pass
