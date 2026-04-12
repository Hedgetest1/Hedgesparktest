"""Tests for the privacy policy and cookie policy legal pages."""
import pytest


def test_privacy_policy_json(client):
    """GET /legal/privacy returns structured privacy policy."""
    resp = client.get("/legal/privacy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Privacy Policy"
    assert len(data["sections"]) >= 10
    # Every section must have an id and title
    for section in data["sections"]:
        assert "id" in section
        assert "title" in section


def test_cookie_policy_json(client):
    """GET /legal/cookies returns structured cookie policy."""
    resp = client.get("/legal/cookies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Cookie Policy"
    assert len(data["sections"]) >= 4
    # At least one section should have a cookies list
    cookie_sections = [s for s in data["sections"] if "cookies" in s]
    assert len(cookie_sections) >= 1
    # Each cookie entry must have required fields
    for c in cookie_sections[0]["cookies"]:
        assert "name" in c
        assert "purpose" in c
        assert "type" in c
        assert "duration" in c


def test_privacy_policy_html(client):
    """GET /privacy-policy returns HTML content."""
    resp = client.get("/privacy-policy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Privacy Policy" in resp.text
    assert "HedgeSpark" in resp.text


def test_cookie_policy_html(client):
    """GET /cookie-policy returns HTML content."""
    resp = client.get("/cookie-policy")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Cookie Policy" in resp.text


def test_privacy_policy_covers_gdpr_articles(client):
    """Policy must mention key GDPR articles."""
    resp = client.get("/legal/privacy")
    data = resp.json()
    all_text = " ".join(s.get("body", "") for s in data["sections"])
    for keyword in ["Art. 15", "Art. 16", "Art. 17", "Art. 20", "Art. 21", "CCPA", "GPC"]:
        assert keyword in all_text, f"Privacy policy missing reference to {keyword}"


def test_privacy_policy_mentions_sub_processors(client):
    """Policy must list sub-processors."""
    resp = client.get("/legal/privacy")
    data = resp.json()
    all_text = " ".join(s.get("body", "") for s in data["sections"])
    for processor in ["Shopify", "Resend", "Anthropic", "OpenAI", "Sentry"]:
        assert processor in all_text, f"Privacy policy missing sub-processor: {processor}"
