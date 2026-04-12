"""Consent gating on /track — GDPR Art. 6 (lawful basis), Art. 7 (consent)."""
from __future__ import annotations

import uuid

from app.api.track import TrackPayload, _consent_allows_ingestion


def _payload(**overrides):
    defaults = dict(
        shop_domain="consent-test.myshopify.com",
        visitor_id=f"v_{uuid.uuid4().hex[:8]}",
        event_type="page_view",
    )
    defaults.update(overrides)
    return TrackPayload(**defaults)


def test_consent_given_allows_ingestion():
    assert _consent_allows_ingestion(_payload(gdpr_consent_given=True)) is True


def test_consent_denied_rejects_ingestion():
    assert _consent_allows_ingestion(_payload(gdpr_consent_given=False)) is False


def test_consent_unknown_is_allowed_for_backwards_compat():
    """Legacy tracker without the field must still work — the strict
    tightening will happen once the tracker ships consent support."""
    assert _consent_allows_ingestion(_payload(gdpr_consent_given=None)) is True


def test_sec_gpc_header_denies_ingestion():
    """Global Privacy Control signal = denial, per CCPA/CPRA."""
    from types import SimpleNamespace
    fake_request = SimpleNamespace(headers={"sec-gpc": "1"})
    assert _consent_allows_ingestion(_payload(), fake_request) is False


def test_dnt_header_denies_ingestion():
    """Legacy Do Not Track header is honored as well."""
    from types import SimpleNamespace
    fake_request = SimpleNamespace(headers={"dnt": "1"})
    assert _consent_allows_ingestion(_payload(), fake_request) is False


def test_explicit_consent_overrides_sec_gpc():
    """If the merchant has shipped a consent banner and the visitor
    explicitly accepted, honor it even though Sec-GPC is set."""
    from types import SimpleNamespace
    fake_request = SimpleNamespace(headers={"sec-gpc": "1"})
    assert _consent_allows_ingestion(
        _payload(gdpr_consent_given=True), fake_request,
    ) is True


def test_strict_mode_env_var(monkeypatch):
    monkeypatch.setenv("TRACK_CONSENT_STRICT", "1")
    from types import SimpleNamespace
    fake_request = SimpleNamespace(headers={})
    # None = missing field; strict mode rejects
    assert _consent_allows_ingestion(_payload(), fake_request) is False


def test_track_endpoint_drops_events_with_sec_gpc(client, db):
    from app.models.event import Event
    from app.models.merchant import Merchant

    shop = f"gpc-{uuid.uuid4().hex[:8]}.myshopify.com"
    db.add(Merchant(
        shop_domain=shop,
        access_token="enc:fake",
        plan="lite",
        install_status="active",
    ))
    db.flush()

    before = db.query(Event).filter(Event.shop_domain == shop).count()
    resp = client.post(
        "/track",
        json={
            "shop_domain": shop,
            "visitor_id": f"v_{uuid.uuid4().hex[:8]}",
            "event_type": "page_view",
            "page_url": "/",
        },
        headers={"Sec-GPC": "1"},
    )
    assert resp.status_code == 200
    assert resp.json().get("reason") == "consent_denied"
    after = db.query(Event).filter(Event.shop_domain == shop).count()
    assert after == before


def test_track_endpoint_drops_denied_events(client, db):
    """End-to-end: POST /track with explicit consent denial returns a
    200 but does NOT persist an event row."""
    from app.models.event import Event
    from app.models.merchant import Merchant

    shop = f"consent-{uuid.uuid4().hex[:8]}.myshopify.com"
    db.add(Merchant(
        shop_domain=shop,
        access_token="enc:fake",
        plan="lite",
        install_status="active",
    ))
    db.flush()

    before = db.query(Event).filter(Event.shop_domain == shop).count()
    resp = client.post("/track", json={
        "shop_domain": shop,
        "visitor_id": f"v_{uuid.uuid4().hex[:8]}",
        "event_type": "page_view",
        "page_url": "/",
        "gdpr_consent_given": False,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("reason") == "consent_denied"
    after = db.query(Event).filter(Event.shop_domain == shop).count()
    assert after == before
