"""
klaviyo_events.py — Automatic Klaviyo event forwarding.

When a HedgeSpark signal fires (goal_at_risk, high_intent_abandon,
nudge_recovered, rars_spike, semantic_drift), and the merchant has
Klaviyo connected, this service forwards the event to Klaviyo as a
custom event so the merchant can build flows around our intelligence.

Design
------
- Async/background: the call site never blocks on Klaviyo.
- Per-shop circuit breaker (reuses signal_webhooks pattern): after 5
  consecutive API failures, skip for 30 min.
- Rate-limited: max 60 events/hour/shop, Redis counter with 1h TTL.
- Deterministic event schema — every forwarded event has the same shape
  so merchants can write durable Klaviyo flows.

Event schema (Klaviyo custom events):
    {
        "data": {
            "type": "event",
            "attributes": {
                "properties": { ...hedgespark_payload },
                "metric": {"data": {"type": "metric", "attributes": {"name": "HedgeSpark — {event_name}"}}},
                "profile": {"data": {"type": "profile", "attributes": {"email": customer_email}}},
            }
        }
    }

Public API
----------
    forward_event_async(shop, event_name, email, properties, revenue_eur=None)
        Non-blocking. Spawns a thread that does the Klaviyo API call.

    forward_event_sync(db, shop, event_name, email, properties, revenue_eur=None)
        Blocking. Used internally; also exposed for testing.

    is_shop_connected(db, shop) -> bool
        Quick check before firing.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("klaviyo_events")

KLAVIYO_API_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-07-15"
_REQUEST_TIMEOUT = 10.0

# Circuit breaker + rate limit Redis keys
_CIRCUIT_KEY_PREFIX = "hs:klaviyo_events:circuit"
_RATE_KEY_PREFIX = "hs:klaviyo_events:rate"
_CIRCUIT_FAIL_THRESHOLD = 5
_CIRCUIT_OPEN_TTL_S = 1800  # 30 min
_RATE_LIMIT_PER_HOUR = 60

# Allowlist of HedgeSpark event names that can be forwarded. Anything not
# in this list is refused at the forward_event_sync boundary.
ALLOWED_EVENTS = frozenset({
    "goal_at_risk",
    "high_intent_abandon",
    "nudge_recovered",
    "rars_spike",
    "semantic_drift",
    "churn_risk_escalated",
    "price_test_winner",
    "trust_action_executed",
})


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def is_shop_connected(db: Session, shop_domain: str) -> bool:
    """Cheap check: is Klaviyo connected + verified for this shop?"""
    try:
        from app.models.merchant import Merchant
        m = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
        if m is None:
            return False
        status = getattr(m, "klaviyo_connection_status", None)
        return status == "connected"
    except Exception:
        return False


def _is_circuit_open(shop_domain: str) -> bool:
    rc = _redis()
    if rc is None:
        record_silent_return("klaviyo_events.circuit_check")
        return False
    try:
        return bool(rc.exists(f"{_CIRCUIT_KEY_PREFIX}:{shop_domain}"))
    except Exception:
        return False


def _record_failure(shop_domain: str) -> None:
    rc = _redis()
    if rc is None:
        record_silent_return("klaviyo_events.record_failure")
        return
    try:
        fail_key = f"{_CIRCUIT_KEY_PREFIX}:{shop_domain}:fails"
        count = rc.incr(fail_key)
        rc.expire(fail_key, 3600)
        if count >= _CIRCUIT_FAIL_THRESHOLD:
            rc.setex(f"{_CIRCUIT_KEY_PREFIX}:{shop_domain}", _CIRCUIT_OPEN_TTL_S, "1")
            rc.delete(fail_key)
            # Emit triage-visible alert so the self-healing pipeline picks
            # up chronic Klaviyo failures as a bugfix candidate source.
            try:
                from app.core.database import SessionLocal
                from app.services.alerting import write_alert
                db = SessionLocal()
                try:
                    write_alert(
                        db,
                        severity="warning",
                        source=f"klaviyo_events:{shop_domain}",
                        alert_type="klaviyo_circuit_tripped",
                        summary=(
                            f"Klaviyo integration degraded for {shop_domain} — "
                            f"circuit opened after {_CIRCUIT_FAIL_THRESHOLD} consecutive failures"
                        ),
                        shop_domain=shop_domain,
                        detail={
                            "threshold": _CIRCUIT_FAIL_THRESHOLD,
                            "cooldown_s": _CIRCUIT_OPEN_TTL_S,
                        },
                    )
                    db.commit()
                finally:
                    db.close()
            except Exception:
                pass
    except Exception:
        pass


def _record_success(shop_domain: str) -> None:
    rc = _redis()
    if rc is None:
        record_silent_return("klaviyo_events.record_success")
        return
    try:
        rc.delete(f"{_CIRCUIT_KEY_PREFIX}:{shop_domain}:fails")
    except Exception:
        pass


def _rate_limit_allow(shop_domain: str) -> bool:
    rc = _redis()
    if rc is None:
        # Fail-open on the rate limit ONLY (the circuit breaker still
        # protects Klaviyo). Without Redis we can't count, so we let a
        # bounded amount through.
        record_silent_return("klaviyo_events.rate_limit")
        return True
    try:
        hour_key = f"{_RATE_KEY_PREFIX}:{shop_domain}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        count = rc.incr(hour_key)
        rc.expire(hour_key, 3700)
        return int(count) <= _RATE_LIMIT_PER_HOUR
    except Exception:
        return True


def forward_event_sync(
    db: Session,
    *,
    shop_domain: str,
    event_name: str,
    email: str | None,
    properties: dict[str, Any] | None = None,
    revenue_eur: float | None = None,
) -> tuple[bool, str]:
    """Send a single event to Klaviyo synchronously. Returns (ok, reason)."""
    if event_name not in ALLOWED_EVENTS:
        return False, "event_not_allowed"

    if not email:
        return False, "no_email"

    if not is_shop_connected(db, shop_domain):
        return False, "not_connected"

    if _is_circuit_open(shop_domain):
        return False, "circuit_open"

    if not _rate_limit_allow(shop_domain):
        return False, "rate_limited"

    from app.services.klaviyo_connection import resolve_klaviyo_key
    key = resolve_klaviyo_key(db, shop_domain)
    if not key:
        return False, "no_key"

    # Build the payload
    attrs: dict[str, Any] = {
        "properties": properties or {},
        "time": datetime.now(timezone.utc).isoformat(),
        "metric": {
            "data": {
                "type": "metric",
                "attributes": {"name": f"HedgeSpark — {event_name}"},
            }
        },
        "profile": {
            "data": {
                "type": "profile",
                "attributes": {"email": email},
            }
        },
    }
    if revenue_eur is not None:
        attrs["value"] = round(float(revenue_eur), 2)
        attrs["value_currency"] = "EUR"

    body = {"data": {"type": "event", "attributes": attrs}}

    try:
        resp = httpx.post(
            f"{KLAVIYO_API_BASE}/events",
            headers={
                "Authorization": f"Klaviyo-API-Key {key}",
                "revision": KLAVIYO_REVISION,
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            content=json.dumps(body),
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _record_failure(shop_domain)
        log.warning("klaviyo_events: request failed shop=%s err=%s", shop_domain, exc)
        return False, f"http_error:{type(exc).__name__}"

    if resp.status_code in (200, 201, 202):
        _record_success(shop_domain)
        return True, "ok"

    _record_failure(shop_domain)
    log.warning(
        "klaviyo_events: API error shop=%s status=%d body=%s",
        shop_domain, resp.status_code, (resp.text or "")[:200],
    )
    return False, f"api_{resp.status_code}"


def forward_event_async(
    shop_domain: str,
    event_name: str,
    email: str | None,
    properties: dict[str, Any] | None = None,
    revenue_eur: float | None = None,
) -> None:
    """Fire-and-forget. Spawns a daemon thread with its own DB session."""
    def _run():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            ok, reason = forward_event_sync(
                db,
                shop_domain=shop_domain,
                event_name=event_name,
                email=email,
                properties=properties,
                revenue_eur=revenue_eur,
            )
            if not ok and reason not in ("not_connected", "no_email", "rate_limited"):
                log.info("klaviyo_events: skipped shop=%s reason=%s", shop_domain, reason)
        except Exception as exc:
            log.warning("klaviyo_events: async thread error shop=%s: %s", shop_domain, exc)
        finally:
            db.close()

    t = threading.Thread(target=_run, daemon=True, name=f"kev-{shop_domain[:20]}")
    t.start()
