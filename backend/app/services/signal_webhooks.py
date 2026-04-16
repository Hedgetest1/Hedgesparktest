"""
signal_webhooks.py — HedgeSpark Signals → outbound webhook delivery.

The killer integration. Instead of building a Shopify Flow connector
(which requires Shopify App Store approval), we expose HedgeSpark
signals as standard outbound webhooks. Merchants configure a URL in
their settings and route it to:

  * Shopify Flow (via the Generic Webhook trigger)
  * Zapier / Make / n8n / Pipedream
  * Their own internal automations
  * Slack / Discord via Webhook bridges
  * Anything that speaks HTTPS POST

Why this is the moat
--------------------
Triple Whale, Peel, Varos do not expose their signals as outbound
webhooks in a usable way. Merchants get locked into the vendor UI and
can't trigger their own automations on top of the vendor's insights.
HedgeSpark becomes the NERVOUS SYSTEM of the merchant's shop — every
other tool hooks into it.

Signals emitted
---------------
  * high_intent_abandon       → a visitor with high behavioral index left
  * goal_at_risk              → a declared goal is projected to miss
  * semantic_drift            → data integrity probe caught a KPI drift
  * refund_spike              → refund rate anomaly in a product
  * below_benchmark           → merchant dropped below peer p50

Delivery guarantees
-------------------
  * HMAC-SHA256 signature in the X-HedgeSpark-Signature header
  * Signature secret rotation-safe (merchant secret stored in Redis)
  * Idempotent delivery key (X-HedgeSpark-Event-ID) so consumers can dedup
  * Single retry on 5xx, 30s timeout
  * Dead-letter after 2 attempts — writes ops_alert for operator visibility

Storage
-------
  * Webhook configurations: Redis `hs:webhooks:{shop}` (JSON array)
  * Delivery attempts: Redis `hs:webhook_delivery:{event_id}` (TTL 24h)
  * Merchant HMAC secret: Redis `hs:webhook_secret:{shop}` (long TTL)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("signal_webhooks")

_REDIS_KEY_WEBHOOKS = "hs:webhooks:v1"
_REDIS_KEY_SECRET = "hs:webhook_secret:v1"
_REDIS_KEY_DELIVERY = "hs:webhook_delivery:v1"

_CONFIG_TTL_SECONDS = 3 * 365 * 24 * 3600
_DELIVERY_TTL_SECONDS = 24 * 3600

_MAX_WEBHOOKS_PER_SHOP = 5

# Supported signal events — allow-list, not free-form
SIGNAL_EVENTS: frozenset[str] = frozenset({
    "high_intent_abandon",
    "goal_at_risk",
    "semantic_drift",
    "refund_spike",
    "below_benchmark",
    "nudge_holdout_win",
    "test_ping",  # merchant-testable
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("signal_webhooks: _redis failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Webhook configuration storage
# ---------------------------------------------------------------------------


@dataclass
class WebhookConfig:
    id: str
    url: str
    events: list[str]  # subset of SIGNAL_EVENTS
    active: bool
    created_at: str
    last_delivery_at: str | None = None
    last_delivery_status: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "events": self.events,
            "active": self.active,
            "created_at": self.created_at,
            "last_delivery_at": self.last_delivery_at,
            "last_delivery_status": self.last_delivery_status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WebhookConfig":
        return cls(
            id=d["id"],
            url=d["url"],
            events=list(d.get("events") or []),
            active=bool(d.get("active", True)),
            created_at=d.get("created_at", ""),
            last_delivery_at=d.get("last_delivery_at"),
            last_delivery_status=d.get("last_delivery_status"),
        )


def _key_webhooks(shop: str) -> str:
    return f"{_REDIS_KEY_WEBHOOKS}:{shop}"


def _key_secret(shop: str) -> str:
    return f"{_REDIS_KEY_SECRET}:{shop}"


def _key_delivery(event_id: str) -> str:
    return f"{_REDIS_KEY_DELIVERY}:{event_id}"


# --- Per-endpoint circuit breaker ---
# After N consecutive failures, open the circuit and stop delivering for
# `_CIRCUIT_OPEN_TTL_S`. Prevents a single broken endpoint from flooding
# the alert pipeline.
_CIRCUIT_FAIL_THRESHOLD = 5       # consecutive failures to trip
_CIRCUIT_OPEN_TTL_S = 1800        # 30-minute cooldown once tripped
_CIRCUIT_FAIL_COUNTER_TTL_S = 3600 # reset counter after 1h of no failures


def _circuit_fail_key(webhook_id: str) -> str:
    return f"hs:webhook_circuit:fails:{webhook_id}"


def _circuit_open_key(webhook_id: str) -> str:
    return f"hs:webhook_circuit:open:{webhook_id}"


def _is_webhook_circuit_open(webhook_id: str) -> bool:
    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.circuit_check")
        return False
    try:
        return bool(rc.exists(_circuit_open_key(webhook_id)))
    except Exception as exc:
        log.warning("signal_webhooks: _is_webhook_circuit_open failed: %s", exc)
        return False


def _record_webhook_failure(webhook_id: str) -> int:
    """Increment failure counter. Trip the circuit if threshold reached.
    Returns the current failure count (post-increment)."""
    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.record_failure")
        return 0
    try:
        key = _circuit_fail_key(webhook_id)
        count = rc.incr(key)
        rc.expire(key, _CIRCUIT_FAIL_COUNTER_TTL_S)
        if count >= _CIRCUIT_FAIL_THRESHOLD:
            rc.setex(_circuit_open_key(webhook_id), _CIRCUIT_OPEN_TTL_S, "1")
            rc.delete(key)  # reset counter — next check restarts fresh
        return int(count)
    except Exception as exc:
        log.warning("signal_webhooks: _record_webhook_failure failed: %s", exc)
        return 0


def _record_webhook_success(webhook_id: str) -> None:
    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.record_success")
        return
    try:
        rc.delete(_circuit_fail_key(webhook_id))
    except Exception as exc:
        log.warning("signal_webhooks: _record_webhook_success failed: %s", exc)


def list_webhooks(shop_domain: str) -> list[WebhookConfig]:
    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.list")
        return []
    try:
        raw = rc.get(_key_webhooks(shop_domain))
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        rc.expire(_key_webhooks(shop_domain), _CONFIG_TTL_SECONDS)
        return [WebhookConfig.from_dict(d) for d in data if isinstance(d, dict)]
    except Exception as exc:
        log.debug("signal_webhooks: list failed: %s", exc)
        return []


def get_or_create_secret(shop_domain: str) -> str:
    """
    Return the shop's HMAC secret, creating one if it doesn't exist.
    Merchants get this on webhook-create and should store it server-side
    to verify incoming signatures.
    """
    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.secret")
        return ""
    try:
        existing = rc.get(_key_secret(shop_domain))
        if existing:
            if isinstance(existing, bytes):
                existing = existing.decode()
            rc.expire(_key_secret(shop_domain), _CONFIG_TTL_SECONDS)
            return existing
        new_secret = secrets.token_urlsafe(32)
        rc.setex(_key_secret(shop_domain), _CONFIG_TTL_SECONDS, new_secret)
        return new_secret
    except Exception as exc:
        log.warning("signal_webhooks: get_or_create_secret failed: %s", exc)
        return ""


def _validate_webhook_url(url: str) -> None:
    """Block SSRF — prevent webhooks to internal/cloud metadata services."""
    if not url.startswith("https://"):
        raise ValueError("webhook URL must use https://")
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("webhook URL has no hostname")
    # Block loopback, link-local, cloud metadata, and RFC 1918 private ranges
    _BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "metadata.google.internal"}
    if hostname in _BLOCKED_HOSTS:
        raise ValueError("webhook URL must not point to internal services")
    # Block IP ranges: 10.x, 172.16-31.x, 192.168.x, 169.254.x (link-local/metadata)
    import ipaddress
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("webhook URL must not point to private/internal IP addresses")
    except ValueError as exc:
        if "must not point" in str(exc):
            raise
        # Not an IP address — hostname is fine, continue


def create_webhook(shop_domain: str, *, url: str, events: list[str]) -> WebhookConfig | None:
    _validate_webhook_url(url)
    bad_events = [e for e in events if e not in SIGNAL_EVENTS]
    if bad_events:
        raise ValueError(f"unknown events: {bad_events}. Valid: {sorted(SIGNAL_EVENTS)}")
    if not events:
        raise ValueError("at least one event must be specified")

    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.create")
        return None

    existing = list_webhooks(shop_domain)
    if len(existing) >= _MAX_WEBHOOKS_PER_SHOP:
        raise ValueError(f"max {_MAX_WEBHOOKS_PER_SHOP} webhooks per shop")

    wh = WebhookConfig(
        id=str(uuid.uuid4())[:12],
        url=url,
        events=events,
        active=True,
        created_at=_now_iso(),
    )

    try:
        existing.append(wh)
        rc.setex(
            _key_webhooks(shop_domain),
            _CONFIG_TTL_SECONDS,
            json.dumps([w.to_dict() for w in existing]),
        )
        # Ensure the shop has a signing secret
        get_or_create_secret(shop_domain)
        return wh
    except Exception as exc:
        log.warning("signal_webhooks: create failed: %s", exc)
        return None


def delete_webhook(shop_domain: str, webhook_id: str) -> bool:
    rc = _redis()
    if rc is None:
        record_silent_return("signal_webhooks.delete")
        return False
    try:
        existing = list_webhooks(shop_domain)
        kept = [w for w in existing if w.id != webhook_id]
        if len(kept) == len(existing):
            return False
        if kept:
            rc.setex(
                _key_webhooks(shop_domain),
                _CONFIG_TTL_SECONDS,
                json.dumps([w.to_dict() for w in kept]),
            )
        else:
            rc.delete(_key_webhooks(shop_domain))
        return True
    except Exception as exc:
        log.warning("signal_webhooks: delete_webhook failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Signature + delivery
# ---------------------------------------------------------------------------


def _sign_payload(secret: str, body: bytes) -> str:
    """Return the hex-encoded HMAC-SHA256 signature."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, provided_signature: str) -> bool:
    """Constant-time comparison for signature verification."""
    expected = _sign_payload(secret, body)
    return hmac.compare_digest(expected, provided_signature)


@dataclass
class DeliveryResult:
    webhook_id: str
    event_id: str
    event_type: str
    status: str  # "delivered" | "failed" | "skipped"
    http_status: int | None = None
    attempts: int = 0
    error: str | None = None


_SLACK_HOSTS = frozenset({"hooks.slack.com"})

_SLACK_EVENT_TITLES: dict[str, str] = {
    "high_intent_abandon": ":eyes: High-intent visitor left without buying",
    "goal_at_risk": ":warning: A monthly target is slipping",
    "semantic_drift": ":mag: Silent data drift detected",
    "refund_spike": ":money_with_wings: Refund spike on a product",
    "below_benchmark": ":chart_with_downwards_trend: You dropped below peer median",
    "nudge_holdout_win": ":trophy: A nudge is proven effective",
    "test_ping": ":satellite_antenna: HedgeSpark test ping",
}


def _is_slack_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception as exc:
        log.warning("signal_webhooks: _is_slack_url failed: %s", exc)
        return False
    return host in _SLACK_HOSTS


def _build_slack_payload(
    event_type: str, shop_domain: str, source: str, payload: dict[str, Any],
) -> dict[str, Any]:
    """Slack Block Kit payload for a HedgeSpark signal.

    Slack webhooks don't verify HMAC custom headers, so we send a
    human-readable message instead of raw JSON. Keeps merchants from
    having to build their own Slack bridge.
    """
    title = _SLACK_EVENT_TITLES.get(event_type, f":bell: {event_type}")

    fields: list[dict[str, Any]] = []
    for k, v in list(payload.items())[:8]:
        label = str(k).replace("_", " ").title()
        val = str(v)
        if len(val) > 120:
            val = val[:117] + "..."
        fields.append({"type": "mrkdwn", "text": f"*{label}*\n{val}"})

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Shop:* `{shop_domain}`  ·  *Source:* `{source}`"},
            ],
        },
    ]
    if fields:
        blocks.append({"type": "section", "fields": fields})
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "Sent by HedgeSpark · loss-prevention signals"},
        ],
    })

    return {"text": title, "blocks": blocks}


def emit_signal(
    shop_domain: str,
    *,
    event_type: str,
    payload: dict[str, Any],
    source: str = "pipeline",
) -> list[DeliveryResult]:
    """
    Fire-and-forget outbound signal delivery.

    Looks up every active webhook registered for this shop whose
    `events` list contains `event_type`, signs the payload, POSTs it,
    and records the outcome. Returns one DeliveryResult per webhook.

    On delivery failure, writes an ops_alert so the self-healing pipeline
    sees the dead-letter and can triage.

    Called by the signal producers: nudge_engine (high_intent_abandon),
    goals (goal_at_risk), data_integrity_probe (semantic_drift), etc.
    """
    if event_type not in SIGNAL_EVENTS:
        log.debug("signal_webhooks: unknown event_type %s, skipping", event_type)
        return []

    webhooks = [w for w in list_webhooks(shop_domain) if w.active and event_type in w.events]
    if not webhooks:
        return []

    secret = get_or_create_secret(shop_domain)
    results: list[DeliveryResult] = []

    for wh in webhooks:
        event_id = f"hs_{uuid.uuid4().hex[:16]}"

        # Circuit breaker — skip delivery if this endpoint is in cooldown
        if _is_webhook_circuit_open(wh.id):
            results.append(DeliveryResult(
                webhook_id=wh.id,
                event_id=event_id,
                event_type=event_type,
                status="skipped",
                error="circuit_open",
            ))
            continue

        # Idempotency — skip if we already attempted this event_id on this
        # webhook. Use atomic SET NX so two concurrent workers can't both
        # see the key as missing and double-deliver. Previously the pair
        # exists() + setex() left a race window.
        rc = _redis()
        if rc is not None:
            dkey = _key_delivery(event_id)
            try:
                claimed = rc.set(dkey, "pending", nx=True, ex=_DELIVERY_TTL_SECONDS)
                if not claimed:
                    results.append(DeliveryResult(
                        webhook_id=wh.id, event_id=event_id,
                        event_type=event_type, status="skipped",
                        error="idempotency_key_exists",
                    ))
                    continue
            except Exception as exc:
                log.warning("signal_webhooks: emit_signal failed: %s", exc)

        is_slack = _is_slack_url(wh.url)
        if is_slack:
            body = json.dumps(
                _build_slack_payload(event_type, shop_domain, source, payload),
                default=str,
            ).encode()
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "HedgeSpark-Webhooks/1.0",
            }
        else:
            body = json.dumps({
                "event_id": event_id,
                "event_type": event_type,
                "shop_domain": shop_domain,
                "source": source,
                "occurred_at": _now_iso(),
                "data": payload,
            }, default=str).encode()
            signature = _sign_payload(secret, body)
            headers = {
                "Content-Type": "application/json",
                "X-HedgeSpark-Event-ID": event_id,
                "X-HedgeSpark-Event-Type": event_type,
                "X-HedgeSpark-Signature": signature,
                "User-Agent": "HedgeSpark-Webhooks/1.0",
            }

        delivered = False
        last_err: str | None = None
        http_status: int | None = None
        attempts = 0

        for attempt in (1, 2):  # single retry on failure
            attempts = attempt
            try:
                import httpx
                resp = httpx.post(wh.url, content=body, headers=headers, timeout=30.0)
                http_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivered = True
                    break
                last_err = f"http_{resp.status_code}"
                if resp.status_code < 500:
                    # 4xx won't be fixed by retry
                    break
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {str(exc)[:200]}"
                continue

        # Record outcome on the webhook config
        try:
            rc2 = _redis()
            if rc2 is not None:
                existing = list_webhooks(shop_domain)
                for w in existing:
                    if w.id == wh.id:
                        w.last_delivery_at = _now_iso()
                        w.last_delivery_status = "delivered" if delivered else "failed"
                rc2.setex(
                    _key_webhooks(shop_domain),
                    _CONFIG_TTL_SECONDS,
                    json.dumps([w.to_dict() for w in existing]),
                )
        except Exception as exc:
            log.warning("signal_webhooks: emit_signal failed: %s", exc)

        # Update circuit breaker based on outcome
        if delivered:
            _record_webhook_success(wh.id)
        else:
            _record_webhook_failure(wh.id)

        results.append(DeliveryResult(
            webhook_id=wh.id,
            event_id=event_id,
            event_type=event_type,
            status="delivered" if delivered else "failed",
            http_status=http_status,
            attempts=attempts,
            error=last_err,
        ))

        # Dead-letter alert
        if not delivered:
            try:
                from app.core.database import SessionLocal
                from app.services.alerting import write_alert
                db = SessionLocal()
                try:
                    write_alert(
                        db,
                        severity="warning",
                        source=f"signal_webhooks:{wh.id}",
                        alert_type="webhook_delivery_failed",
                        summary=(
                            f"Outbound webhook failed for shop {shop_domain}: "
                            f"event={event_type} url={wh.url} err={last_err}"
                        ),
                        shop_domain=shop_domain,
                        detail={
                            "webhook_id": wh.id,
                            "event_type": event_type,
                            "event_id": event_id,
                            "http_status": http_status,
                            "error": last_err,
                            "attempts": attempts,
                        },
                    )
                    db.commit()
                finally:
                    db.close()
            except Exception as exc:
                log.warning("signal_webhooks: emit_signal failed: %s", exc)

    return results
