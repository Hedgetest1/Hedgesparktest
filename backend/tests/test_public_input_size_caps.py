"""Regression tests: public/untrusted-input endpoints reject oversized
JSON payloads BEFORE serialization to prevent CPU+memory amplification.

Bug class 2026-05-08 brutal audit: Pydantic `dict[str, Any] | None =
Field(None, max_length=32)` caps the KEY count but does not cap the
VALUE byte size. An attacker can send 32 keys × 1MB-each-value = 32MB
payload. Pydantic accepts it; downstream `json.dumps()` burns CPU and
memory before the size check rejects.

Fix: field_validator computes serialized size at parse time, drops
the field if oversized. Endpoints continue to function (extra=None,
properties=None) but the abuse vector is closed.
"""
from __future__ import annotations

import json


def test_frontend_errors_extra_field_rejects_oversized_payload():
    """FrontendErrorPayload.extra must drop values > 16KB serialized."""
    from app.api.frontend_errors import FrontendErrorPayload

    huge_extra = {f"k{i}": "x" * 2000 for i in range(20)}  # ~40KB total
    p = FrontendErrorPayload(
        component="t",
        error_type="TypeError",
        message="m",
        extra=huge_extra,
    )
    assert p.extra is None, (
        f"oversized extra (~40KB) must be dropped to None to prevent "
        f"CPU/memory amplification, got {len(json.dumps(p.extra)) if p.extra else 0} bytes"
    )


def test_frontend_errors_extra_field_accepts_normal_payload():
    """Normal extra dict (< 16KB) must pass through."""
    from app.api.frontend_errors import FrontendErrorPayload

    normal_extra = {"component_state": "loading", "user_id": "u123", "viewport": "1920x1080"}
    p = FrontendErrorPayload(
        component="t",
        error_type="TypeError",
        message="m",
        extra=normal_extra,
    )
    assert p.extra == normal_extra, "normal extra (<16KB) must pass through"


def test_public_events_properties_rejects_oversized_payload():
    """PublicEventPayload.properties must drop values > 8KB serialized."""
    from app.api.public_events import PublicEventPayload

    huge_props = {f"k{i}": "x" * 1000 for i in range(20)}  # ~20KB total
    p = PublicEventPayload(
        event_name="checkout_started",
        properties=huge_props,
    )
    assert p.properties is None, (
        f"oversized properties (~20KB) must be dropped to None to prevent "
        f"row-bloat in events_metadata"
    )


def test_public_events_properties_accepts_normal_payload():
    """Normal properties dict (< 8KB) must pass through."""
    from app.api.public_events import PublicEventPayload

    normal_props = {"product_id": "p123", "variant_id": "v1", "price": "29.99"}
    p = PublicEventPayload(
        event_name="checkout_started",
        properties=normal_props,
    )
    assert p.properties == normal_props
