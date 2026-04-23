"""
shopify_client.py — Rate-limited, retry-aware Shopify Admin API client.

Replaces bare httpx.get/post calls throughout the codebase with a
centralized client that enforces:

1. **Rate limiting**: Shopify allows 40 requests per app per store in a
   rolling window.  We enforce a conservative 2 req/s global limit.
2. **Retry with exponential backoff**: Retries on 429, 5xx, and transient
   network errors.  Max 3 retries with jitter.
3. **429 handling**: Reads Retry-After header from Shopify and sleeps
   accordingly.  Falls back to exponential backoff if header missing.
4. **Timeout**: 10s per request, prevents hung connections.
5. **Logging**: Structured logging for every retry and failure.

Usage:
    from app.core.shopify_client import shopify_request

    resp = shopify_request(
        "GET",
        shop_domain="example.myshopify.com",
        path="products.json",
        token="shpat_...",
        params={"limit": 10},
    )
    if resp is not None:
        products = resp.json()["products"]
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

SHOPIFY_API_VERSION = "2024-01"
_REQUEST_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Global rate limiter — token bucket (2 req/s, burst of 4)
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float = 2.0, burst: int = 4):
        self._rate = rate          # tokens per second
        self._burst = burst        # max tokens
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """Block until a token is available or timeout expires."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


# Legacy in-process bucket — kept as a Redis-unavailable fallback. Single-
# worker safe; in multi-worker deployments the Redis rate limiter below
# is authoritative.
_bucket = _TokenBucket(rate=2.0, burst=4)


# ---------------------------------------------------------------------------
# Per-shop Redis rate limiter (multi-worker correct)
#
# Shopify's actual limit is 2 req/s per SHOP (not per app). With uvicorn
# running 4 workers + 7 singleton PM2 workers, a per-process _TokenBucket
# lets the fleet issue up to 8 req/s to a single shop, hitting 429 four
# times sooner than intended.
#
# This helper counts per-shop requests in Redis with 1-second windows,
# capped at 2 per window. Approximates Shopify's leaky-bucket rate without
# the complexity of a distributed token bucket.
#
# Redis key: hs:shopify_rl:{shop}:{unix_epoch_seconds}
# TTL: 2 seconds (covers the window + one grace tick to handle clock skew).
#
# Fallback: when Redis is unreachable, falls back to the per-process
# _TokenBucket so single-worker / dev environments still behave correctly.
# ---------------------------------------------------------------------------
def _acquire_shopify_token(shop_domain: str, timeout: float = 30.0) -> bool:
    """Acquire one Shopify API rate-limit token for `shop_domain`.

    Returns True when a token was granted within `timeout` seconds,
    False if the caller should give up and return None to the request
    issuer.
    """
    deadline = time.monotonic() + timeout
    backoff = 0.1
    while time.monotonic() < deadline:
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                window = int(time.time())
                key = f"hs:shopify_rl:{shop_domain}:{window}"
                count = rc.incr(key)
                if count == 1:
                    rc.expire(key, 2)
                if count <= 2:
                    return True
                # Over per-shop cap for this window — back off until next
                time.sleep(min(backoff, max(0.05, deadline - time.monotonic())))
                backoff = min(backoff * 1.5, 0.5)
                continue
        except Exception:
            pass  # SILENT-EXCEPT-OK: Redis optional — falls through to legacy in-process bucket below
        # Redis unreachable — fall back to per-process bucket (safe single-worker,
        # degraded but non-blocking under multi-worker + Redis outage combined).
        return _bucket.acquire(timeout=max(0.1, deadline - time.monotonic()))
    return False

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0     # seconds
_MAX_BACKOFF = 30.0     # seconds
_JITTER_RANGE = 0.5     # ±50% jitter

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _backoff_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Calculate backoff delay with jitter."""
    if retry_after is not None and retry_after > 0:
        return min(retry_after, _MAX_BACKOFF)
    delay = _BASE_BACKOFF * (2 ** attempt)
    delay = min(delay, _MAX_BACKOFF)
    jitter = delay * _JITTER_RANGE * (2 * random.random() - 1)
    return max(0.1, delay + jitter)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def shopify_url(shop_domain: str, path: str) -> str:
    """Build Shopify Admin API URL."""
    return f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/{path}"


def shopify_request(
    method: str,
    shop_domain: str,
    path: str,
    token: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    timeout: float = _REQUEST_TIMEOUT,
    max_retries: int = _MAX_RETRIES,
) -> Optional[httpx.Response]:
    """
    Make a rate-limited, retry-aware Shopify Admin API request.

    Returns the httpx.Response on success, or None on permanent failure.
    Caller is responsible for checking response status and parsing body.

    Rate limiting: blocks until a token is available (max 30s wait).
    Retry: up to max_retries on 429, 5xx, and network errors.
    """
    url = shopify_url(shop_domain, path)
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        # Acquire rate limit token (per-shop, Redis-backed → multi-worker safe)
        if not _acquire_shopify_token(shop_domain, timeout=30.0):
            logger.warning(
                "shopify_client: rate limit timeout shop=%s path=%s",
                shop_domain, path,
            )
            return None

        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout,
            )

            # Success
            if resp.status_code < 400:
                return resp

            # Retryable error
            if resp.status_code in _RETRYABLE_STATUS_CODES:
                retry_after = None
                if resp.status_code == 429:
                    ra_header = resp.headers.get("Retry-After")
                    if ra_header:
                        try:
                            retry_after = float(ra_header)
                        except ValueError:
                            pass

                if attempt < max_retries:
                    delay = _backoff_delay(attempt, retry_after)
                    logger.warning(
                        "shopify_client: %d from %s %s shop=%s — "
                        "retry %d/%d in %.1fs",
                        resp.status_code, method, path, shop_domain,
                        attempt + 1, max_retries, delay,
                    )
                    time.sleep(delay)
                    continue

                # Exhausted retries
                logger.error(
                    "shopify_client: %d from %s %s shop=%s — "
                    "exhausted %d retries",
                    resp.status_code, method, path, shop_domain,
                    max_retries,
                )
                return None

            # Non-retryable client error (400, 401, 403, 404, 422)
            logger.error(
                "shopify_client: %d from %s %s shop=%s (non-retryable): %s",
                resp.status_code, method, path, shop_domain,
                resp.text[:200],
            )
            return resp  # Return so caller can inspect status

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                httpx.PoolTimeout, httpx.ConnectTimeout) as exc:
            if attempt < max_retries:
                delay = _backoff_delay(attempt)
                logger.warning(
                    "shopify_client: network error %s %s shop=%s: %s — "
                    "retry %d/%d in %.1fs",
                    method, path, shop_domain, exc,
                    attempt + 1, max_retries, delay,
                )
                time.sleep(delay)
                continue
            logger.error(
                "shopify_client: network error %s %s shop=%s: %s — "
                "exhausted %d retries",
                method, path, shop_domain, exc, max_retries,
            )
            return None

        except Exception as exc:
            logger.error(
                "shopify_client: unexpected error %s %s shop=%s: %s",
                method, path, shop_domain, exc,
            )
            return None

    return None


async def shopify_request_async(
    method: str,
    shop_domain: str,
    path: str,
    token: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    timeout: float = _REQUEST_TIMEOUT,
    max_retries: int = _MAX_RETRIES,
) -> Optional[httpx.Response]:
    """
    Async version of shopify_request for OAuth callback and async workers.
    Same rate limiting, retry, and error handling semantics.
    """
    import asyncio

    url = shopify_url(shop_domain, path)
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        # Rate limit (per-shop, Redis-backed → multi-worker safe)
        if not _acquire_shopify_token(shop_domain, timeout=30.0):
            logger.warning("shopify_client_async: rate limit timeout shop=%s", shop_domain)
            return None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method, url, headers=headers,
                    params=params, json=json_body,
                )

            if resp.status_code < 400:
                return resp

            if resp.status_code in _RETRYABLE_STATUS_CODES:
                retry_after = None
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        try:
                            retry_after = float(ra)
                        except ValueError:
                            pass
                if attempt < max_retries:
                    delay = _backoff_delay(attempt, retry_after)
                    logger.warning(
                        "shopify_client_async: %d shop=%s — retry %d/%d in %.1fs",
                        resp.status_code, shop_domain, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                return None

            logger.error(
                "shopify_client_async: %d from %s %s shop=%s",
                resp.status_code, method, path, shop_domain,
            )
            return resp

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            if attempt < max_retries:
                delay = _backoff_delay(attempt)
                logger.warning(
                    "shopify_client_async: network error shop=%s: %s — retry %d/%d",
                    shop_domain, exc, attempt + 1, max_retries,
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                "shopify_client_async: network error shop=%s: %s — exhausted retries",
                shop_domain, exc,
            )
            return None

        except Exception as exc:
            logger.error("shopify_client_async: unexpected error shop=%s: %s", shop_domain, exc)
            return None

    return None
