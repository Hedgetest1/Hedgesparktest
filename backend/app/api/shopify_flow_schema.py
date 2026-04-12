"""
shopify_flow_schema.py — Shopify Flow Connector trigger schema.

Public, unauthenticated, static endpoint that exposes HedgeSpark's
outbound signals in the Shopify Flow Connector schema format. A
merchant who installs our Flow Connector points Shopify at
`GET /shopify-flow/schema` and the triggers appear inside the Flow
editor without any additional setup.

Docs: https://shopify.dev/docs/api/flow

Why separate from signal_webhooks:
- signal_webhooks delivers runtime payloads (per shop, with HMAC)
- this endpoint is a static metadata document (per app, no auth)

Every trigger defined here must map 1:1 to an event in
`app.services.signal_webhooks.SIGNAL_EVENTS`. The test suite enforces
that alignment so neither side can drift.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.signal_webhooks import SIGNAL_EVENTS

router = APIRouter(tags=["shopify-flow"])


# Human metadata for each event — the labels Shopify Flow shows in the
# editor. Keys MUST be a subset of SIGNAL_EVENTS.
_FLOW_TRIGGER_METADATA: dict[str, dict[str, str]] = {
    "high_intent_abandon": {
        "title": "High-intent visitor abandoned",
        "description": (
            "Fires when HedgeSpark detects a visitor with a high behavioral "
            "intent score who left without completing checkout."
        ),
    },
    "goal_at_risk": {
        "title": "Monthly goal at risk",
        "description": (
            "Fires when a declared revenue/CVR/AOV goal is forecast to miss "
            "by end-of-period."
        ),
    },
    "semantic_drift": {
        "title": "Silent data drift detected",
        "description": (
            "Fires when the data integrity probe catches a meaningful KPI "
            "drift that merchants typically miss."
        ),
    },
    "refund_spike": {
        "title": "Refund spike on a product",
        "description": (
            "Fires when refund rate on a product deviates from its baseline."
        ),
    },
    "below_benchmark": {
        "title": "Dropped below peer median",
        "description": (
            "Fires when a core KPI drops below the anonymized peer median "
            "for shops in the same category and revenue band."
        ),
    },
    "nudge_holdout_win": {
        "title": "Nudge proven effective (holdout)",
        "description": (
            "Fires when a nudge passes the quasi-experimental holdout test "
            "with a significant positive lift."
        ),
    },
}

# Payload schema shared by all triggers — matches signal_webhooks emit body.
_COMMON_PROPERTIES: dict[str, dict] = {
    "event_id": {"type": "string", "description": "Unique event identifier."},
    "event_type": {"type": "string", "description": "Signal type, e.g. high_intent_abandon."},
    "shop_domain": {"type": "string", "description": "Merchant shop domain."},
    "source": {"type": "string", "description": "Pipeline component that emitted the signal."},
    "occurred_at": {"type": "string", "format": "date-time"},
    "data": {
        "type": "object",
        "description": "Event-specific payload fields.",
        "additionalProperties": True,
    },
}


def _build_trigger(event_type: str, meta: dict[str, str]) -> dict:
    return {
        "name": event_type,
        "title": meta["title"],
        "description": meta["description"],
        "schema": {
            "type": "object",
            "properties": _COMMON_PROPERTIES,
            "required": ["event_id", "event_type", "shop_domain", "occurred_at"],
        },
    }


@router.get("/shopify-flow/schema")
def flow_schema() -> dict:
    """Static schema document consumed by Shopify Flow Connector."""
    triggers = [
        _build_trigger(ev, meta)
        for ev, meta in _FLOW_TRIGGER_METADATA.items()
        if ev in SIGNAL_EVENTS
    ]
    return {
        "version": "1.0",
        "app": "HedgeSpark",
        "description": (
            "HedgeSpark loss-prevention signals exposed as Shopify Flow "
            "triggers. Route them to any Flow action (tag customer, send "
            "email via a campaign tool, create a task, etc.)."
        ),
        "triggers": triggers,
    }
