"""Tests for the consent banner script endpoint."""
import pytest


def test_consent_banner_js_served(client):
    """GET /consent-banner.js returns JavaScript content."""
    resp = client.get("/consent-banner.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_consent_banner_contains_hs_set_consent(client):
    """Banner script must call window.hsSetConsent."""
    resp = client.get("/consent-banner.js")
    assert "hsSetConsent" in resp.text


def test_consent_banner_respects_gpc(client):
    """Banner script must check for globalPrivacyControl."""
    resp = client.get("/consent-banner.js")
    assert "globalPrivacyControl" in resp.text


def test_consent_banner_stores_choice(client):
    """Banner script must use localStorage for persistence."""
    resp = client.get("/consent-banner.js")
    assert "localStorage" in resp.text
    assert "hs_consent" in resp.text


def test_consent_banner_cors_header(client):
    """Banner script must have wildcard CORS for storefront embedding."""
    resp = client.get("/consent-banner.js")
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_consent_banner_has_accept_and_decline(client):
    """Banner must have both accept and decline buttons."""
    resp = client.get("/consent-banner.js")
    assert "Accept" in resp.text
    assert "Decline" in resp.text


def test_consent_banner_links_privacy_policy(client):
    """Banner must link to the privacy policy."""
    resp = client.get("/consent-banner.js")
    assert "privacy-policy" in resp.text
