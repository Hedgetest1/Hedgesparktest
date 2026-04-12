"""Tests for F7 — Monthly ROI self-justification report."""
from __future__ import annotations

from unittest.mock import patch

from app.services.roi_report import (
    _PRO_TIER_COST_EUR,
    _month_key,
    _render_email_html,
    _render_email_text,
    generate_roi_report,
)


def test_month_key_format():
    from datetime import datetime
    now = datetime(2026, 4, 11)
    assert _month_key(now) == "2026-04"


def test_generate_report_has_all_fields(db):
    report = generate_roi_report(db, "roi-shape-shop.myshopify.com")
    assert report.shop_domain == "roi-shape-shop.myshopify.com"
    assert report.cost_eur == _PRO_TIER_COST_EUR
    assert isinstance(report.at_risk_detected_eur, float)
    assert isinstance(report.prevented_eur, float)
    assert isinstance(report.net_roi_eur, float)
    assert report.headline
    assert report.email_body_html.startswith("<!DOCTYPE html>")
    assert "HedgeSpark" in report.email_body_text


def test_generate_report_net_roi_equals_prevented_minus_cost(db):
    report = generate_roi_report(db, "roi-math-shop.myshopify.com")
    expected = report.prevented_eur - _PRO_TIER_COST_EUR
    assert abs(report.net_roi_eur - expected) < 0.01


def test_email_html_contains_headline_and_costs(db):
    report = generate_roi_report(db, "roi-html-shop.myshopify.com")
    html = _render_email_html(report)
    assert report.headline in html
    assert f"€{_PRO_TIER_COST_EUR:.0f}" in html
    assert "Subscription cost" in html


def test_email_text_has_all_line_items(db):
    report = generate_roi_report(db, "roi-text-shop.myshopify.com")
    text = _render_email_text(report)
    assert "Subscription cost" in text
    assert "At-risk detected" in text
    assert "Net ROI" in text


def test_report_headline_positive_roi_shows_green_message(db):
    """When prevented > cost, headline says 'paid for itself'."""
    # Patch get_revenue_at_risk to return a positive-ROI fake
    fake_rars = {
        "total_at_risk_eur": 2000.0,
        "prevented_eur_this_month": 800.0,  # cost = 99, net_roi = 701
        "components": [],
    }
    with patch(
        "app.services.revenue_at_risk.get_revenue_at_risk",
        return_value=fake_rars,
    ):
        report = generate_roi_report(db, "roi-green-shop.myshopify.com")
    assert "paid for itself" in report.headline
    assert report.net_roi_eur > 0


def test_report_headline_zero_prevention_uses_neutral_copy(db):
    """When prevented = 0, headline is loss-framed without overclaiming."""
    fake_rars = {
        "total_at_risk_eur": 1500.0,
        "prevented_eur_this_month": 0.0,
        "components": [],
    }
    with patch(
        "app.services.revenue_at_risk.get_revenue_at_risk",
        return_value=fake_rars,
    ):
        report = generate_roi_report(db, "roi-zero-prevention.myshopify.com")
    assert "paid for itself" not in report.headline
    assert "surfaced" in report.headline or "at risk" in report.headline
