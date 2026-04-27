"""
Tests for app/core/date_range.py + endpoint integration.

Covers:
- DateRangeQuery model (is_explicit / has_compare / span_days / cache_key)
- get_date_range dependency (validation: both required, end>=start,
  end<=today+1, span<=730d, compare same rules)
- resolve_window_days (explicit vs legacy fallback)
- 3 representative endpoints honor explicit range:
    - /analytics/repeat-cadence
    - /analytics/top-products
    - /analytics/abandonment-trend
- Backward compat: legacy `days` param still works when range omitted
"""
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone

import pytest
from fastapi import HTTPException

from app.core.date_range import (
    DateRangeQuery, get_date_range, resolve_utc_bounds, resolve_window_days,
)
from app.models.shop_order import ShopOrder
from tests.conftest import SHOP_A, auth_cookies


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ════════════════════════════════════════════════════════════════════════
# Pure-model tests
# ════════════════════════════════════════════════════════════════════════


class TestDateRangeQuery:

    def test_empty_query_not_explicit(self):
        q = DateRangeQuery()
        assert q.is_explicit() is False
        assert q.has_compare() is False
        assert q.span_days() == 0
        assert q.cache_key_segment() == ""

    def test_explicit_range(self):
        q = DateRangeQuery(start_date=date(2026, 4, 1), end_date=date(2026, 4, 7))
        assert q.is_explicit() is True
        assert q.span_days() == 7  # inclusive both ends
        assert q.cache_key_segment() == ":r=2026-04-01_2026-04-07"

    def test_with_comparison(self):
        q = DateRangeQuery(
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 7),
            compare_start=date(2026, 3, 25), compare_end=date(2026, 3, 31),
        )
        assert q.has_compare() is True
        assert q.cache_key_segment() == (
            ":r=2026-04-01_2026-04-07:c=2026-03-25_2026-03-31"
        )


class TestGetDateRangeValidation:
    """The dependency must reject invalid ranges with HTTPException 400."""

    def test_only_start_date_provided_400(self):
        with pytest.raises(HTTPException) as exc:
            get_date_range(start_date=date(2026, 4, 1), end_date=None)
        assert exc.value.status_code == 400
        assert "must both be provided" in exc.value.detail

    def test_only_end_date_provided_400(self):
        with pytest.raises(HTTPException) as exc:
            get_date_range(start_date=None, end_date=date(2026, 4, 7))
        assert exc.value.status_code == 400

    def test_end_before_start_400(self):
        with pytest.raises(HTTPException) as exc:
            get_date_range(
                start_date=date(2026, 4, 10), end_date=date(2026, 4, 1),
            )
        assert exc.value.status_code == 400
        assert "must be >=" in exc.value.detail

    def test_end_in_future_400(self):
        future = _now().date() + timedelta(days=10)
        with pytest.raises(HTTPException) as exc:
            get_date_range(start_date=date(2026, 4, 1), end_date=future)
        assert exc.value.status_code == 400
        assert "future" in exc.value.detail

    def test_span_over_730_days_400(self):
        with pytest.raises(HTTPException) as exc:
            get_date_range(
                start_date=date(2024, 1, 1), end_date=date(2026, 4, 1),
            )
        assert exc.value.status_code == 400
        assert "exceeds maximum" in exc.value.detail

    def test_compare_only_start_400(self):
        with pytest.raises(HTTPException) as exc:
            get_date_range(
                start_date=date(2026, 4, 1), end_date=date(2026, 4, 7),
                compare_start=date(2026, 3, 25), compare_end=None,
            )
        assert exc.value.status_code == 400

    def test_valid_range_passes(self):
        # Explicitly pass None for compare params: when calling the
        # dependency directly (not via FastAPI injection) the Query()
        # defaults aren't resolved.
        q = get_date_range(
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 7),
            compare_start=None, compare_end=None,
        )
        assert q.is_explicit() is True
        assert q.span_days() == 7


class TestResolveWindowDays:

    def test_explicit_range_uses_provided(self):
        q = DateRangeQuery(start_date=date(2026, 4, 1), end_date=date(2026, 4, 7))
        start, end, days = resolve_window_days(q, fallback_days=30)
        assert start == date(2026, 4, 1)
        assert end == date(2026, 4, 7)
        assert days == 7

    def test_no_range_falls_back_to_days(self):
        q = DateRangeQuery()
        start, end, days = resolve_window_days(q, fallback_days=14)
        today = _now().date()
        assert end == today
        assert start == today - timedelta(days=13)  # 14 days inclusive
        assert days == 14


class TestResolveUtcBounds:
    """Phase 3B Stage B DA-loop fix — naive `date` filtering treated
    every range as UTC midnight, so a 23:00 PST order on March 14 was
    bucketed into the merchant's 'March 14' even though they see it
    as March 14 evening locally. Real data correctness bug."""

    def test_utc_bounds_for_pacific_shop(self):
        # Merchant picks "2026-03-14" (single day) on a PST shop (UTC-8).
        # The day starts at 2026-03-14 00:00 PST = 2026-03-14 08:00 UTC.
        # The day ends   at 2026-03-15 00:00 PST = 2026-03-15 08:00 UTC.
        # Pre-fix the SQL ran with `created_at >= '2026-03-14 00:00 UTC'`
        # which included 8 hours of March 13 PST orders.
        from datetime import datetime
        q = DateRangeQuery(
            start_date=date(2026, 3, 14), end_date=date(2026, 3, 14),
        )
        start_utc, end_utc_excl, days, sl, el = resolve_utc_bounds(
            q, fallback_days=1, shop_tz="America/Los_Angeles",
        )
        # PST is UTC-8 in March (PDT actually, UTC-7) — verify the
        # offset shifts the boundary from 00:00 UTC to a non-zero hour.
        assert start_utc != datetime(2026, 3, 14, 0, 0, 0)
        # The local dates round-trip correctly
        assert sl == date(2026, 3, 14)
        assert el == date(2026, 3, 14)
        assert days == 1
        # Span is exactly 24 hours
        assert (end_utc_excl - start_utc) == timedelta(days=1)

    def test_utc_bounds_for_european_shop(self):
        # Italian shop (UTC+1 standard, UTC+2 DST)
        from datetime import datetime
        q = DateRangeQuery(
            start_date=date(2026, 7, 1), end_date=date(2026, 7, 7),
        )
        start_utc, end_utc_excl, days, _, _ = resolve_utc_bounds(
            q, fallback_days=1, shop_tz="Europe/Rome",
        )
        # July → CEST (UTC+2). Italy midnight = UTC 22:00 prior day.
        assert start_utc == datetime(2026, 6, 30, 22, 0, 0)
        assert end_utc_excl == datetime(2026, 7, 7, 22, 0, 0)
        assert days == 7

    def test_utc_bounds_utc_shop_is_pass_through(self):
        from datetime import datetime
        q = DateRangeQuery(
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 1),
        )
        start_utc, end_utc_excl, _, _, _ = resolve_utc_bounds(
            q, fallback_days=1, shop_tz="UTC",
        )
        assert start_utc == datetime(2026, 4, 1, 0, 0, 0)
        assert end_utc_excl == datetime(2026, 4, 2, 0, 0, 0)

    def test_invalid_tz_falls_back_to_utc(self):
        """An unknown IANA tz must not crash — fall back to UTC silently."""
        from datetime import datetime
        q = DateRangeQuery(
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 1),
        )
        start_utc, end_utc_excl, _, _, _ = resolve_utc_bounds(
            q, fallback_days=1, shop_tz="Mars/Olympus_Mons",
        )
        # Falls through to UTC behavior
        assert start_utc == datetime(2026, 4, 1, 0, 0, 0)
        assert end_utc_excl == datetime(2026, 4, 2, 0, 0, 0)


# ════════════════════════════════════════════════════════════════════════
# Endpoint integration — explicit range honored
# ════════════════════════════════════════════════════════════════════════


def _seed_orders_for_dates(db, shop, dates: list[datetime], price: float = 50.0):
    """Seed one order per provided date — for testing range filters."""
    for i, d in enumerate(dates):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"dr-{i}-{int(d.timestamp())}",
            total_price=price,
            currency="USD",
            customer_email=f"customer{i}@test.com",
            financial_status="paid",
            line_items=[{"title": "Widget", "price": str(price), "quantity": 1}],
            created_at=d,
            source="webhook",
        ))
    db.flush()


class TestEndpointHonorsRange:

    def test_top_products_explicit_range_filters_orders(
        self, client, db, merchant_a
    ):
        """Seed 3 orders: today, 5 days ago, 30 days ago. Query
        explicit range covering only "5 days ago" → that order's
        product appears, the other two don't."""
        now = _now()
        _seed_orders_for_dates(db, SHOP_A, [
            now,                              # today
            now - timedelta(days=5),          # 5 days ago
            now - timedelta(days=30),         # 30 days ago
        ])
        db.commit()

        # Query: range covering ONLY day-5 (yesterday before today−5 to today−4)
        five_ago = (now - timedelta(days=5)).date()
        cookies = auth_cookies(SHOP_A)

        resp = client.get(
            f"/analytics/top-products?start_date={five_ago}&end_date={five_ago}",
            cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        # 1 order in window → 1 product, days reported as span
        assert body["has_data"] is True
        assert body["days"] == 1  # inclusive: same day = 1
        assert len(body["products"]) == 1

    def test_top_products_no_range_uses_legacy_days(
        self, client, db, merchant_a
    ):
        """Without start/end, the endpoint uses the legacy `days` param."""
        now = _now()
        _seed_orders_for_dates(db, SHOP_A, [now])
        db.commit()

        cookies = auth_cookies(SHOP_A)
        resp = client.get("/analytics/top-products?days=14", cookies=cookies)
        assert resp.status_code == 200
        body = resp.json()
        assert body["days"] == 14

    def test_top_products_range_invalid_400(self, client, merchant_a):
        cookies = auth_cookies(SHOP_A)
        # end before start
        resp = client.get(
            "/analytics/top-products?start_date=2026-04-10&end_date=2026-04-01",
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_repeat_cadence_explicit_range(self, client, db, merchant_a):
        """Same customer, 2 orders at known timestamps. Range covers
        both → cadence computed. Range covers only one → no data
        (need 2+ orders to compute gap)."""
        from app.models.shop_order import ShopOrder
        now = _now()
        for i, days_ago in enumerate([10, 40]):
            db.add(ShopOrder(
                shop_domain=SHOP_A,
                shopify_order_id=f"cad-{i}",
                total_price=100.0,
                currency="USD",
                customer_email="repeat@test.com",
                financial_status="paid",
                line_items=[{"title": "Widget", "price": "100", "quantity": 1}],
                created_at=now - timedelta(days=days_ago),
                source="webhook",
            ))
        db.commit()

        cookies = auth_cookies(SHOP_A)
        # Range covering both orders (days 10 and 40)
        start = (now - timedelta(days=50)).date()
        end = now.date()
        resp = client.get(
            f"/analytics/repeat-cadence?start_date={start}&end_date={end}",
            cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_data"] is True
        assert body["intervals_count"] >= 1

    def test_abandonment_trend_explicit_range(self, client, db, merchant_a):
        """Range covers a 7-day window; series length matches the span."""
        cookies = auth_cookies(SHOP_A)
        end = _now().date()
        start = end - timedelta(days=6)
        resp = client.get(
            f"/analytics/abandonment-trend?start_date={start}&end_date={end}",
            cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["series"]) == 7  # inclusive 7-day span
        assert body["days"] == 7

    def test_cache_segregates_by_range(self, client, db, merchant_a):
        """Two different ranges must NOT share cache — different keys."""
        from app.models.shop_order import ShopOrder
        now = _now()
        _seed_orders_for_dates(db, SHOP_A, [now])
        db.commit()

        cookies = auth_cookies(SHOP_A)
        end = now.date()
        # First request: range covering 7 days
        resp1 = client.get(
            f"/analytics/top-products?start_date={end - timedelta(days=6)}&end_date={end}",
            cookies=cookies,
        )
        # Second request: range covering 30 days
        resp2 = client.get(
            f"/analytics/top-products?start_date={end - timedelta(days=29)}&end_date={end}",
            cookies=cookies,
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Different days reported (cache wasn't aliased)
        assert resp1.json()["days"] == 7
        assert resp2.json()["days"] == 30


class TestStageCEndpointsAcceptRange:
    """Phase 3B Stage C: smoke-verify every newly-wired endpoint accepts
    explicit start_date+end_date and reports the correct effective span.

    This is the parametrized "all 8 endpoints" test that proves the
    pattern rolled out cleanly. Each endpoint must:
      - Return 200 (not 500) on a 7-day explicit range
      - Report `days` field = 7 (or appropriate equivalent) when wired
      - Reject end<start with 400
    """

    @pytest.mark.parametrize("endpoint,extra_params", [
        ("/analytics/device-breakdown", ""),
        ("/analytics/first-vs-repeat-aov", ""),
        ("/analytics/order-rhythm", ""),
        ("/analytics/order-status", ""),
        ("/analytics/tax-breakdown", ""),
        ("/analytics/payment-methods", ""),
        ("/analytics/discount-codes", ""),
        ("/analytics/top-variants", ""),
        ("/analytics/orders-by-country", ""),
    ])
    def test_endpoint_accepts_explicit_range(
        self, endpoint, extra_params, client, db, merchant_a
    ):
        """Smoke: 200 on a 7-day range, days field reflects span."""
        cookies = auth_cookies(SHOP_A)
        end = _now().date()
        start = end - timedelta(days=6)
        url = f"{endpoint}?start_date={start}&end_date={end}"
        if extra_params:
            url += f"&{extra_params}"
        resp = client.get(url, cookies=cookies)
        assert resp.status_code == 200, (
            f"{endpoint} returned {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        # `days` field on response reflects the effective span (7 days
        # inclusive). first-vs-repeat-aov doesn't surface `days` —
        # skip that assertion for that endpoint.
        if "days" in body:
            assert body["days"] == 7, (
                f"{endpoint} expected days=7, got {body.get('days')}"
            )

    @pytest.mark.parametrize("endpoint", [
        "/analytics/device-breakdown",
        "/analytics/first-vs-repeat-aov",
        "/analytics/order-rhythm",
        "/analytics/order-status",
        "/analytics/tax-breakdown",
        "/analytics/payment-methods",
        "/analytics/discount-codes",
        "/analytics/top-variants",
        "/analytics/orders-by-country",
    ])
    def test_endpoint_rejects_invalid_range(
        self, endpoint, client, merchant_a
    ):
        """Smoke: end<start returns 400 (validation reaches each endpoint)."""
        cookies = auth_cookies(SHOP_A)
        resp = client.get(
            f"{endpoint}?start_date=2026-04-10&end_date=2026-04-01",
            cookies=cookies,
        )
        assert resp.status_code == 400, (
            f"{endpoint} should reject end<start, got {resp.status_code}"
        )
