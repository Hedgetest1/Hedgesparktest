"""
Tests for spark_voice.py — Spark-as-narrator primitives.

Pure-function unit tests; no db fixture needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.spark_voice import (
    EVENT_DOT_COLORS,
    JARGON_TOKENS,
    PRICING_FORBIDDEN_PHRASES,
    format_memory_sentence,
    greet_by_hour,
    greet_night_shift,
    opening_verdict,
    relative_label,
    top_leak_detail,
)


# ---------------------------------------------------------------------------
# Greetings
# ---------------------------------------------------------------------------


def test_greet_by_hour_morning():
    assert greet_by_hour(6, "Acme") == "Good morning, Acme."
    assert greet_by_hour(11, "Acme") == "Good morning, Acme."


def test_greet_by_hour_afternoon():
    assert greet_by_hour(12, "Acme") == "Hi, Acme."
    assert greet_by_hour(17, "Acme") == "Hi, Acme."


def test_greet_by_hour_evening_and_night():
    assert greet_by_hour(18, "Acme") == "Evening, Acme."
    assert greet_by_hour(23, "Acme") == "Evening, Acme."
    assert greet_by_hour(3, "Acme") == "Evening, Acme."
    assert greet_by_hour(5, "Acme") == "Evening, Acme."


def test_greet_night_shift():
    assert greet_night_shift("Acme") == "Overnight update, Acme."


# ---------------------------------------------------------------------------
# Opening verdict
# ---------------------------------------------------------------------------


def test_opening_verdict_leaking_basic():
    out = opening_verdict(total_at_risk_eur=340.4, count_places=3)
    assert out == "This morning I noticed €340 leaking in 3 places."


def test_opening_verdict_leaking_rounds_to_thousands():
    out = opening_verdict(total_at_risk_eur=1234.7, count_places=5)
    assert "€1,235" in out
    assert "5 places" in out


def test_opening_verdict_steady():
    out = opening_verdict(
        total_at_risk_eur=100, count_places=0, prevented_eur=50
    )
    assert out == "Steady morning — €100 at risk, €50 prevented."


def test_opening_verdict_clean():
    out = opening_verdict(total_at_risk_eur=0, count_places=0)
    assert out == "Clean morning — nothing leaking right now."


def test_opening_verdict_zero_with_count_rendered_as_clean():
    # total rounds to zero + count >= 1 → treated as clean
    out = opening_verdict(total_at_risk_eur=0.4, count_places=2)
    assert out == "Clean morning — nothing leaking right now."


def test_opening_verdict_different_currency():
    out = opening_verdict(
        total_at_risk_eur=1234, count_places=2, currency_symbol="$"
    )
    assert "$1,234" in out


# ---------------------------------------------------------------------------
# Top-leak detail
# ---------------------------------------------------------------------------


def test_top_leak_detail_with_all_data():
    out = top_leak_detail(top_product="Silk Pillowcase", views=68, carts=0)
    assert out == "The biggest is Silk Pillowcase — 68 views, 0 carts."


def test_top_leak_detail_missing_product_returns_none():
    assert top_leak_detail(top_product=None, views=10, carts=2) is None
    assert top_leak_detail(top_product="", views=10, carts=2) is None


def test_top_leak_detail_missing_views_returns_none():
    assert top_leak_detail(top_product="X", views=None, carts=0) is None


def test_top_leak_detail_missing_carts_returns_none():
    assert top_leak_detail(top_product="X", views=10, carts=None) is None


# ---------------------------------------------------------------------------
# Memory sentence templates
# ---------------------------------------------------------------------------


def test_format_memory_sentence_abandoned_detected():
    out = format_memory_sentence(
        "abandoned_detected", {"product": "Silk Pillowcase"}
    )
    assert out == "I noticed Silk Pillowcase lost intent."


def test_format_memory_sentence_prevention_success():
    out = format_memory_sentence(
        "prevention_success",
        {"product": "Cotton Throw", "change": "photo swap"},
    )
    assert out == "Cotton Throw recovered — your photo swap worked."


def test_format_memory_sentence_brief_summary():
    out = format_memory_sentence(
        "brief_summary",
        {
            "day": "Monday",
            "currency": "€",
            "amount": "40",
            "signal_type": "abandoned carts",
        },
    )
    assert out == "Monday brief: you saved €40 on abandoned carts."


def test_format_memory_sentence_cohort_milestone():
    out = format_memory_sentence(
        "cohort_milestone", {"period": "month", "metric": "repeat-rate 34%"}
    )
    assert out == "Your best month so far — repeat-rate 34%."


def test_format_memory_sentence_unusual_pattern():
    out = format_memory_sentence(
        "unusual_pattern", {"source": "Instagram"}
    )
    assert out == "I started watching a new visitor pattern from Instagram."


def test_format_memory_sentence_target_hit():
    out = format_memory_sentence(
        "target_hit", {"weekday": "Monday", "day": "Saturday"}
    )
    assert out == "You hit your Monday target by Saturday."


def test_format_memory_sentence_target_missed():
    out = format_memory_sentence(
        "target_missed",
        {"weekday": "Monday", "currency": "€", "amount": "230"},
    )
    assert out == "Your Monday target fell short by €230."


def test_format_memory_sentence_unknown_type_returns_empty():
    assert format_memory_sentence("nonexistent_type", {}) == ""


def test_format_memory_sentence_missing_context_key_uses_sentinel():
    # Missing keys resolve to "[missing]" — never raise KeyError
    out = format_memory_sentence("abandoned_detected", {})
    assert "[missing]" in out


def test_event_dot_colors_cover_all_template_event_types():
    expected = {
        "abandoned_detected",
        "prevention_success",
        "brief_summary",
        "cohort_milestone",
        "unusual_pattern",
        "target_hit",
        "target_missed",
    }
    assert expected == set(EVENT_DOT_COLORS.keys())


# ---------------------------------------------------------------------------
# Relative time labels
# ---------------------------------------------------------------------------


def test_relative_label_hours_ago():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    evt = now - timedelta(hours=2)
    assert relative_label(now, evt) == "2h ago"


def test_relative_label_minutes_ago_collapses_to_just_now():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    evt = now - timedelta(minutes=30)
    assert relative_label(now, evt) == "just now"


def test_relative_label_negative_delta_is_just_now():
    # Event timestamp in the future (clock skew) → just now, not a negative
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    evt = now + timedelta(seconds=30)
    assert relative_label(now, evt) == "just now"


def test_relative_label_yesterday():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    evt = now - timedelta(days=1, hours=2)
    assert relative_label(now, evt) == "yesterday"


def test_relative_label_multi_days():
    now = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    evt = now - timedelta(days=3)
    assert relative_label(now, evt) == "3 days"


def test_relative_label_accepts_naive_datetimes():
    # Callers that forget tzinfo should still work (assumes UTC)
    now = datetime(2026, 4, 21, 12, 0, 0)
    evt = datetime(2026, 4, 21, 10, 0, 0)
    assert relative_label(now, evt) == "2h ago"


# ---------------------------------------------------------------------------
# Audit-script constants
# ---------------------------------------------------------------------------


def test_jargon_tokens_nonempty_and_contain_common_shopify_terms():
    assert len(JARGON_TOKENS) >= 10
    for term in ("CVR", "LTV", "holdout", "cohort"):
        assert term in JARGON_TOKENS


def test_pricing_forbidden_phrases_match_claudemd_section3():
    # Canonical forbidden phrases per CLAUDE.md §3 pricing anti-canon
    for phrase in ("free forever", "no credit card", "try free", "$0 forever"):
        assert phrase in PRICING_FORBIDDEN_PHRASES
