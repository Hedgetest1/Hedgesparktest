"""Tests for event_bus (β6) — ClickHouse-shaped internal event store."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.event_bus import (
    emit,
    emit_batch,
    query,
    aggregate_by_source,
    cleanup_old_events,
    _ALLOWED_EVENT_NAMES,
    _now_ms,
)

SHOP = "test-bus.myshopify.com"


@pytest.fixture(autouse=True)
def _cleanup(db):
    db.execute(text("DELETE FROM analytics_events WHERE shop_domain = :s"), {"s": SHOP})
    db.commit()
    yield
    db.execute(text("DELETE FROM analytics_events WHERE shop_domain = :s"), {"s": SHOP})
    db.commit()


class TestProducer:
    def test_emit_allowed_event(self, db):
        ok = emit(
            "page_view",
            SHOP,
            visitor_id="v1",
            source="google",
            props={"url": "/products/x"},
            db=db,
        )
        assert ok is True
        rows = query(db, SHOP, event_name="page_view", limit=10)
        assert len(rows) == 1
        assert rows[0]["source"] == "google"
        assert rows[0]["props"] == {"url": "/products/x"}

    def test_emit_rejects_unknown_event(self, db):
        ok = emit("hacker_event", SHOP, db=db)
        assert ok is False

    def test_emit_batch(self, db):
        now_ms = _now_ms()
        batch = [
            {"event_name": "page_view", "shop_domain": SHOP, "visitor_id": "v1"},
            {"event_name": "add_to_cart", "shop_domain": SHOP, "visitor_id": "v1"},
            {"event_name": "checkout_completed", "shop_domain": SHOP, "visitor_id": "v1", "revenue_eur": 49.99},
            {"event_name": "bogus", "shop_domain": SHOP},  # filtered
            {"event_name": "page_view", "shop_domain": None},  # filtered
        ]
        n = emit_batch(batch, db=db)
        assert n == 3


class TestConsumer:
    def test_query_filter_by_event_name(self, db):
        emit("page_view", SHOP, visitor_id="v1", db=db)
        emit("add_to_cart", SHOP, visitor_id="v1", db=db)
        emit("page_view", SHOP, visitor_id="v2", db=db)

        views = query(db, SHOP, event_name="page_view")
        assert len(views) == 2

        carts = query(db, SHOP, event_name="add_to_cart")
        assert len(carts) == 1

    def test_query_filter_by_time(self, db):
        emit("page_view", SHOP, db=db)
        rows = query(db, SHOP, since_ms=_now_ms() - 10_000)
        assert len(rows) == 1
        rows_future = query(db, SHOP, since_ms=_now_ms() + 60_000)
        assert len(rows_future) == 0

    def test_aggregate_by_source(self, db):
        emit("page_view", SHOP, source="google", visitor_id="a", db=db)
        emit("page_view", SHOP, source="google", visitor_id="b", db=db)
        emit("page_view", SHOP, source="meta", visitor_id="c", db=db)
        emit(
            "checkout_completed", SHOP, source="google", visitor_id="a", revenue_eur=100.0, db=db,
        )

        agg = aggregate_by_source(db, SHOP, window_days=1)
        sources = {s["source"]: s for s in agg["sources"]}
        assert "google" in sources
        assert "meta" in sources
        assert sources["google"]["event_count"] == 3
        assert sources["google"]["visitors"] == 2
        assert sources["google"]["revenue_eur"] == 100.0
        assert sources["meta"]["event_count"] == 1


class TestRetention:
    def test_cleanup_ignores_recent_events(self, db):
        emit("page_view", SHOP, db=db)
        deleted = cleanup_old_events(db)
        # Recent rows must not be touched
        rows = query(db, SHOP)
        assert len(rows) >= 1


class TestAllowlist:
    def test_allowed_events_are_strings(self):
        for e in _ALLOWED_EVENT_NAMES:
            assert isinstance(e, str)
            assert len(e) > 0

    def test_core_events_present(self):
        for core in ("page_view", "add_to_cart", "checkout_completed", "trust_action_executed"):
            assert core in _ALLOWED_EVENT_NAMES
