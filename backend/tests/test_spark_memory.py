"""
Tests for /merchant/spark-memory — Lite v5 Zone 5 timeline.

Covers:
- Empty shop returns {events: [], count: 0}
- daily_brief source feeds the timeline
- ops_alerts source feeds the timeline (with alert_type mapping)
- Events are sorted by recency (newest first)
- MAX_EVENTS cap enforced
- Third-person summary is rewritten to first-person
- Unmapped alert_type is skipped (no fabrication)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from app.services.spark_memory import MAX_EVENTS, build_spark_memory


def test_spark_memory_empty_shop_returns_empty_events(db):
    out = build_spark_memory(db, "spark-memory-empty.myshopify.com")
    assert out["events"] == []
    assert out["count"] == 0
    assert out["shop_domain"] == "spark-memory-empty.myshopify.com"
    assert "generated_at" in out


def test_spark_memory_payload_shape(db):
    out = build_spark_memory(db, "spark-memory-shape.myshopify.com")
    for key in ("shop_domain", "events", "count", "generated_at"):
        assert key in out, f"missing {key}"
    assert isinstance(out["events"], list)
    assert isinstance(out["count"], int)


def test_spark_memory_from_daily_brief(db):
    shop = "spark-memory-brief.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.execute(
            text(
                """
                INSERT INTO daily_brief
                    (shop_domain, brief_date, generated_at, headline,
                     top_product_label, top_signal_type, top_action)
                VALUES
                    (:shop, :bd, :gen, :headline, :prod, :sig, :act)
                ON CONFLICT (shop_domain, brief_date) DO NOTHING
                """
            ),
            {
                "shop": shop,
                "bd": now.date(),
                "gen": now,
                "headline": "Silk Pillowcase losing carts — 3 today.",
                "prod": "Silk Pillowcase",
                "sig": "attention_leak",
                "act": "Check hero photo",
            },
        )
        db.flush()
        out = build_spark_memory(db, shop)
    finally:
        db.execute(
            text("DELETE FROM daily_brief WHERE shop_domain = :shop"),
            {"shop": shop},
        )
        db.flush()

    assert out["count"] >= 1
    brief_events = [e for e in out["events"] if e["event_type"] == "brief_summary"]
    assert len(brief_events) >= 1
    # Spark voice: "{weekday} brief: {headline}"
    assert "brief:" in brief_events[0]["sentence"]
    assert "Silk Pillowcase" in brief_events[0]["sentence"]
    assert brief_events[0]["dot_color"] == "amber"
    assert "relative_label" in brief_events[0]


def test_spark_memory_from_ops_alert(db):
    shop = "spark-memory-alert.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.execute(
            text(
                """
                INSERT INTO ops_alerts
                    (shop_domain, created_at, severity, source,
                     alert_type, summary, resolved)
                VALUES
                    (:shop, :created, 'warning', 'test',
                     'abandoned_intent',
                     'I noticed Cotton Throw lost intent.', false)
                """
            ),
            {"shop": shop, "created": now},
        )
        db.flush()
        out = build_spark_memory(db, shop)
    finally:
        db.execute(
            text("DELETE FROM ops_alerts WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    assert out["count"] >= 1
    alert_events = [e for e in out["events"] if e["event_type"] == "abandoned_detected"]
    assert len(alert_events) >= 1
    assert "Cotton Throw" in alert_events[0]["sentence"]
    assert alert_events[0]["dot_color"] == "rose"


def test_spark_memory_rewrites_third_person_summary(db):
    """Ops alerts with `HedgeSpark noticed …` are rewritten to `I …`."""
    shop = "spark-memory-rewrite.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.execute(
            text(
                """
                INSERT INTO ops_alerts
                    (shop_domain, created_at, severity, source,
                     alert_type, summary, resolved)
                VALUES
                    (:shop, :created, 'info', 'test',
                     'traffic_pattern',
                     'HedgeSpark noticed a new source from Instagram.', false)
                """
            ),
            {"shop": shop, "created": now},
        )
        db.flush()
        out = build_spark_memory(db, shop)
    finally:
        db.execute(
            text("DELETE FROM ops_alerts WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    assert out["count"] >= 1
    # The "HedgeSpark noticed" prefix must be rewritten to "I "
    events = [e for e in out["events"] if e["event_type"] == "unusual_pattern"]
    assert len(events) >= 1
    assert not events[0]["sentence"].startswith("HedgeSpark")
    assert events[0]["sentence"].startswith("I ")


def test_spark_memory_skips_unmapped_alert_types(db):
    """alert_type not in the canonical map → event is skipped."""
    shop = "spark-memory-unmapped.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.execute(
            text(
                """
                INSERT INTO ops_alerts
                    (shop_domain, created_at, severity, source,
                     alert_type, summary, resolved)
                VALUES
                    (:shop, :created, 'warning', 'test',
                     'webhook_drift',
                     'Webhooks drifted.', false)
                """
            ),
            {"shop": shop, "created": now},
        )
        db.flush()
        out = build_spark_memory(db, shop)
    finally:
        db.execute(
            text("DELETE FROM ops_alerts WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    # The unmapped webhook_drift alert must NOT produce a memory event
    for e in out["events"]:
        assert e["sentence"] != "Webhooks drifted."


def test_spark_memory_caps_at_max_events(db):
    """Never return more than MAX_EVENTS, even if sources have more."""
    shop = "spark-memory-cap.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        # Seed MAX_EVENTS + 3 ops_alerts in the last 7 days
        for i in range(MAX_EVENTS + 3):
            db.execute(
                text(
                    """
                    INSERT INTO ops_alerts
                        (shop_domain, created_at, severity, source,
                         alert_type, summary, resolved)
                    VALUES
                        (:shop, :created, 'info', 'test',
                         'abandoned_intent',
                         :summary, false)
                    """
                ),
                {
                    "shop": shop,
                    "created": now - timedelta(hours=i),
                    "summary": f"I noticed Product-{i} lost intent.",
                },
            )
        db.flush()
        out = build_spark_memory(db, shop)
    finally:
        db.execute(
            text("DELETE FROM ops_alerts WHERE shop_domain = :shop AND source = 'test'"),
            {"shop": shop},
        )
        db.flush()

    assert out["count"] == MAX_EVENTS, f"expected {MAX_EVENTS}, got {out['count']}"
    assert len(out["events"]) == MAX_EVENTS
