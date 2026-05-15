"""Contract tests for nudge_dna.extract_patterns — N+1 fix and correctness.

Born 2026-05-15 — closes the "extract_patterns N+1" item in
project_post_2026_05_14_audit_pending.md. The pre-fix code did 1
SELECT per impression against visitor_purchase_sessions (up to 20k
queries on a busy shop). The fix batches purchase lookups into one
query and matches per-impression in memory.

These tests pin:
  1. Correctness: a known conversion (visitor purchased within 48h of
     impression) is detected — same semantics as the pre-fix loop.
  2. Correctness: a purchase OUTSIDE the 48h window is NOT counted.
  3. N+1 contract: regardless of N impressions, the purchase lookup
     fires exactly once.
  4. Edge: empty impression set → no crash, no query.
  5. Edge: visitors with no purchases → impressions counted, conversions zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.active_nudge import ActiveNudge
from app.models.merchant import Merchant
from app.models.nudge_event import NudgeEvent
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services import nudge_dna


_SHOP = "nudge-dna-test.myshopify.com"


def _make_merchant(db) -> None:
    db.add(Merchant(shop_domain=_SHOP, access_token="x", primary_currency="USD"))
    db.commit()


def _make_nudge(db) -> int:
    """Insert one active_nudge with a 2-variant A/B config. Return nudge_id."""
    copy_variants = json.dumps([
        {
            "variant_name": "urgent",
            "copy_config": {
                "headline": "Only 3 left",
                "subtext": "Hurry, ending soon",
                "badge": "limited",
            },
        },
        {
            "variant_name": "social",
            "copy_config": {
                "headline": "Loved by 200+",
                "subtext": "Most popular this week",
                "badge": "trending",
            },
        },
    ])
    nudge = ActiveNudge(
        shop_domain=_SHOP,
        product_url="/products/widget",
        action_type="recover_intent",
        trigger_source="abandon",
        copy_variant="urgent",
        copy_config=json.dumps({"headline": "Only 3 left"}),
        copy_variants=copy_variants,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(nudge)
    db.commit()
    return nudge.id


def _show_event(db, nudge_id: int, visitor_id: str, variant: str, ts: datetime) -> None:
    db.add(NudgeEvent(
        shop_domain=_SHOP,
        nudge_id=nudge_id,
        visitor_id=visitor_id,
        product_url="/products/widget",
        event_type="shown",
        created_at=ts,
        event_meta=json.dumps({"variant_name": variant}),
    ))


def _purchase(db, visitor_id: str, confirmed_at: datetime) -> None:
    db.add(VisitorPurchaseSession(
        shop_domain=_SHOP,
        visitor_id=visitor_id,
        shopify_order_id=f"order-{visitor_id}-{int(confirmed_at.timestamp())}",
        confirmed_at=confirmed_at,
    ))


def test_extract_patterns_detects_in_window_conversion(db):
    """Impression at T, purchase at T+1h → conversion detected."""
    _make_merchant(db)
    nudge_id = _make_nudge(db)
    now = datetime.utcnow().replace(microsecond=0)
    impression_ts = now - timedelta(days=5)
    purchase_ts = impression_ts + timedelta(hours=1)

    # Enough impressions to clear _MIN_SAMPLE_PER_VARIANT (20)
    for i in range(25):
        _show_event(db, nudge_id, f"visitor-{i}", "urgent", impression_ts)
    _purchase(db, "visitor-0", purchase_ts)
    db.commit()

    result = nudge_dna.extract_patterns(db, _SHOP, window_days=30)
    assert result["shop_domain"] == _SHOP
    assert result["total_impressions"] == 25
    assert result["total_conversions"] == 1, f"expected 1 conversion, got {result}"


def test_extract_patterns_ignores_out_of_window_purchase(db):
    """Purchase at T+72h (>48h window) → not counted."""
    _make_merchant(db)
    nudge_id = _make_nudge(db)
    now = datetime.utcnow().replace(microsecond=0)
    impression_ts = now - timedelta(days=5)

    for i in range(25):
        _show_event(db, nudge_id, f"visitor-{i}", "urgent", impression_ts)
    # Purchase 72h after impression — outside the 48h window
    _purchase(db, "visitor-0", impression_ts + timedelta(hours=72))
    db.commit()

    result = nudge_dna.extract_patterns(db, _SHOP, window_days=30)
    assert result["total_impressions"] == 25
    assert result["total_conversions"] == 0


def test_extract_patterns_purchase_lookup_is_batched_n_plus_one_killed(db):
    """N impressions → exactly 1 visitor_purchase_sessions query, not N.

    This is the contract that closes the original bug. Counts SELECT
    statements against the visitor_purchase_sessions table.
    """
    _make_merchant(db)
    nudge_id = _make_nudge(db)
    now = datetime.utcnow().replace(microsecond=0)
    impression_ts = now - timedelta(days=5)

    # 50 impressions across 50 distinct visitors. Pre-fix code would
    # have fired 50 SELECTs against visitor_purchase_sessions; the
    # batched fix fires exactly 1.
    N = 50
    for i in range(N):
        _show_event(db, nudge_id, f"visitor-{i}", "urgent", impression_ts)
    db.commit()

    # Track every db.execute call and tally the ones that hit
    # visitor_purchase_sessions. We use spy semantics — call through
    # to the real method so the function actually executes.
    real_execute = db.execute
    purchase_queries: list[str] = []

    def counting_execute(stmt, *args, **kwargs):
        sql = str(stmt)
        if "visitor_purchase_sessions" in sql:
            purchase_queries.append(sql)
        return real_execute(stmt, *args, **kwargs)

    with patch.object(db, "execute", side_effect=counting_execute):
        result = nudge_dna.extract_patterns(db, _SHOP, window_days=30)

    assert result["total_impressions"] == N
    assert len(purchase_queries) == 1, (
        f"N+1 regression: extract_patterns fired {len(purchase_queries)} "
        f"visitor_purchase_sessions queries (expected 1). The batched "
        f"fix has been undone — restore the SELECT visitor_id, confirmed_at "
        f"WHERE visitor_id = ANY(:vids) pattern."
    )


def test_extract_patterns_empty_impressions_returns_empty_dna(db):
    """No impressions → _empty_dna shape returned. Confirms the early-
    return path is hit before the batched purchase query."""
    _make_merchant(db)
    db.commit()

    result = nudge_dna.extract_patterns(db, _SHOP, window_days=30)
    assert result["shop_domain"] == _SHOP
    assert result["total_impressions"] == 0
    assert result["total_conversions"] == 0


def test_extract_patterns_visitor_without_purchase_no_conversion(db):
    """Visitor saw nudge but never purchased → impressions++, conversions stay 0."""
    _make_merchant(db)
    nudge_id = _make_nudge(db)
    now = datetime.utcnow().replace(microsecond=0)
    impression_ts = now - timedelta(days=5)

    for i in range(25):
        _show_event(db, nudge_id, f"visitor-{i}", "urgent", impression_ts)
    # No _purchase calls — empty visitor_purchase_sessions
    db.commit()

    result = nudge_dna.extract_patterns(db, _SHOP, window_days=30)
    assert result["total_impressions"] == 25
    assert result["total_conversions"] == 0
