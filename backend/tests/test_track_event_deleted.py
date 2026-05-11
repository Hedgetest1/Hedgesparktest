"""Sprint A audit C2 — POST /track-event was deleted 2026-05-11 because
it was anonymous, unrate-limited, accepted arbitrary shop_domain, and
had ZERO real consumers (tracker/ + dashboard/src/ grep clean except
for auto-generated api-types.ts).

This regression test asserts the endpoint stays deleted. If a future
PR re-introduces it (intentionally or via merge accident), the test
fails — forcing reviewer attention back to the audit rationale.
"""
from __future__ import annotations


def test_track_event_endpoint_returns_404(client):
    """POST /track-event MUST return 404 (route not registered).
    Sprint A C2 deletion regression."""
    response = client.post("/track-event", json={
        "shop_domain": "anyshop.myshopify.com",
        "visitor_id": "vis_x",
        "event_type": "page_view",
        "page_url": "https://example.com",
    })
    assert response.status_code == 404


def test_app_api_events_module_deleted():
    """The app.api.events module MUST NOT exist. Catches re-introduction
    at import time even if main.py forgot to wire it."""
    import importlib
    try:
        importlib.import_module("app.api.events")
        raised = False
    except ModuleNotFoundError:
        raised = True
    assert raised, (
        "app.api.events was deleted Sprint A C2 — do not re-introduce "
        "without the full validation chain (consent gate + known-shop "
        "+ rate-limit + visitor plausibility) per app/api/track.py."
    )


def test_app_schemas_event_module_deleted():
    """The schemas/event.py was deleted along with the legacy endpoint
    (only consumer was app.api.events)."""
    import importlib
    try:
        importlib.import_module("app.schemas.event")
        raised = False
    except ModuleNotFoundError:
        raised = True
    assert raised, "app.schemas.event was deleted Sprint A C2."
