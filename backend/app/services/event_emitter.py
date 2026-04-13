"""
event_emitter.py — Phase Ω'' centralized outbound event hook.

Wraps `outbound_webhooks.publish_event` with a non-raising signature so
business logic can fire-and-forget without coupling. Every emit is
swallowed on failure — never breaks the calling code path.

Usage
-----
    from app.services.event_emitter import emit
    emit(db, shop_domain, "nudge.fired", {"nudge_id": 42, "...": ...})

Event types are validated against the registered SUPPORTED_EVENTS in
`app.api.outbound_webhooks` so misspellings get rejected at dev time.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger("event_emitter")

# Mirror of SUPPORTED_EVENTS in app.api.outbound_webhooks — kept as a tuple
# here to avoid a circular import. Keep these in sync if either changes.
_KNOWN_EVENTS = frozenset({
    "nudge.fired",
    "nudge.dismissed",
    "rars.spike",
    "goal.at_risk",
    "anomaly.detected",
    "refund.processed",
    "trust_contract.executed",
    "compliance.alert",
})


def emit(db: Session, shop_domain: str | None, event_type: str, payload: dict) -> None:
    """
    Fire-and-forget outbound event publish.

    * Returns immediately on any failure
    * Never raises
    * Skips entirely when shop_domain is None (system-level events)
    * Logs at debug level only — production noise is unwelcome
    """
    if not shop_domain:
        return
    if event_type not in _KNOWN_EVENTS:
        log.debug("event_emitter: unknown event_type %r — skipping", event_type)
        return
    try:
        from app.services.outbound_webhooks import publish_event
        publish_event(db, shop_domain, event_type, payload, deliver_now=True)
    except Exception as exc:
        log.debug("event_emitter: %s/%s failed: %s", shop_domain, event_type, exc)
