"""Contract: /track/batch routes NON-purchase items through the async
ingest buffer (ZERO request DB connection — pool-cascade-immune like
single /track post J3-part-2), keeps the purchase path fully
synchronous, and BOTH handlers build Event from the single
_event_fields_from_payload source (== ingest_buffer._EVENT_FIELDS).

Born 2026-05-18 (honest-residual #7 — the §11 sibling of the
single-/track lazy/buffered fix). These tests pin:
  1. field-source ↔ buffer contract (regression pin for the pre-fix
     batch attribution-drift that dropped utm_*/click_id/landing_page).
  2. all-non-purchase batch → SessionLocal NEVER constructed (0 conn)
     + every item enqueued (composes with the #6 guarded-commit
     lazy WRITE session).
  3. mixed batch → non-purchase enqueued, purchase goes the sync path.
  4. audit_track_batch_buffered is NON-VACUOUS (flags the pre-fix
     shape, green on the fixed + live tree).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import app.core.database as dbmod
import app.core.ingest_admission as ia
import app.services.ingest_buffer as ibuf
from app.api.track import (
    BatchTrackPayload,
    TrackPayload,
    _event_fields_from_payload,
    track_event_batch,
)

_BACKEND = Path(__file__).resolve().parent.parent


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_tbb", _BACKEND / "scripts" / "audit_track_batch_buffered.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _payload(event_type: str, vid: str = "v1") -> TrackPayload:
    return TrackPayload(
        shop_domain="x.myshopify.com",
        visitor_id=vid,
        event_type=event_type,
        page_url="https://x.myshopify.com/p",
        utm_source="google",
        click_id="ck123",
        landing_page="https://x.myshopify.com/land",
    )


def test_event_fields_match_buffer_contract():
    """The single source MUST stay == ingest_buffer._EVENT_FIELDS, and
    MUST carry the utm_*/click_id/landing_page columns the pre-fix
    batch silently dropped."""
    keys = set(_event_fields_from_payload(_payload("page_view")).keys())
    assert keys == set(ibuf._EVENT_FIELDS)
    for col in ("utm_source", "click_id", "landing_page"):
        assert col in keys
    f = _event_fields_from_payload(_payload("page_view"))
    assert f["utm_source"] == "google"
    assert f["click_id"] == "ck123"
    assert f["landing_page"] == "https://x.myshopify.com/land"


def test_all_non_purchase_batch_holds_zero_db_connections():
    """The honest-residual #7 invariant: a batch with no purchase item
    enqueues every event and NEVER constructs a pooled session."""
    pl = BatchTrackPayload(events=[
        _payload("page_view", "v1"),
        _payload("click", "v2"),
        _payload("add_to_cart", "v3"),
    ])
    with patch.object(dbmod, "SessionLocal") as SL, \
         patch.object(ia, "ingest_admit", return_value="tok"), \
         patch.object(ia, "ingest_release"), \
         patch.object(ibuf, "enqueue_event", return_value=True) as enq, \
         patch("app.api.track._store_shopify_y_mapping"):
        holder = dbmod._LazyDbSession()
        resp = track_event_batch(pl, db=holder)
        SL.assert_not_called()            # ← 0 pooled connections
        assert enq.call_count == 3        # every item buffered
    assert b'"accepted":3' in bytes(resp.body)
    assert b'"rejected":0' in bytes(resp.body)


def test_mixed_batch_buffers_non_purchase_and_syncs_purchase():
    pl = BatchTrackPayload(events=[
        _payload("page_view", "v1"),
        _payload("purchase", "v2"),
        _payload("click", "v3"),
    ])
    with patch.object(dbmod, "SessionLocal", return_value=MagicMock()), \
         patch.object(ia, "ingest_admit", return_value="tok"), \
         patch.object(ia, "ingest_release"), \
         patch.object(ibuf, "enqueue_event", return_value=True) as enq, \
         patch("app.api.track._store_shopify_y_mapping"), \
         patch("app.api.track._upsert_visitor") as upv, \
         patch("app.api.track._persist_purchase") as pp:
        holder = dbmod._LazyDbSession()
        resp = track_event_batch(pl, db=holder)
        assert enq.call_count == 2        # the 2 non-purchase items
        upv.assert_called_once()          # purchase visitor upsert
        pp.assert_called_once()           # purchase persisted
    assert b'"accepted":3' in bytes(resp.body)


def test_purchase_commit_integrityerror_keeps_buffered_accepted():
    """Regression pin for the #7 under-report: a purchase-commit
    IntegrityError must zero ONLY the synced count — the 2 buffered
    analytics events were already enqueued (Redis, independent) and
    stay accepted. Pre-fix this returned accepted=0."""
    from sqlalchemy.exc import IntegrityError

    pl = BatchTrackPayload(events=[
        _payload("page_view", "v1"),
        _payload("click", "v2"),
        _payload("purchase", "v3"),
    ])
    fake = MagicMock(name="SessionLocal()")
    fake.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))
    with patch.object(dbmod, "SessionLocal", return_value=fake), \
         patch.object(ia, "ingest_admit", return_value="tok"), \
         patch.object(ia, "ingest_release"), \
         patch.object(ibuf, "enqueue_event", return_value=True) as enq, \
         patch("app.api.track._store_shopify_y_mapping"), \
         patch("app.api.track._upsert_visitor"), \
         patch("app.api.track._persist_purchase"):
        holder = dbmod._LazyDbSession()
        resp = track_event_batch(pl, db=holder)
        assert enq.call_count == 2          # both non-purchase buffered
        fake.commit.assert_called_once()    # purchase commit attempted
        fake.rollback.assert_called_once()  # … and rolled back
    body = bytes(resp.body)
    assert b'"accepted":2' in body          # buffered survive (was 0)


def test_audit_track_batch_buffered_is_non_vacuous(tmp_path):
    audit = _load_audit()

    pre_fix = (
        "@router.post('/track/batch')\n"
        "def track_event_batch(payload, db):\n"
        "    for item in payload.events:\n"
        "        db.add(Event(shop_domain=item.shop_domain,\n"
        "                     visitor_id=item.visitor_id))\n"
        "    return {}\n"
    )
    fixed = (
        "@router.post('/track/batch')\n"
        "def track_event_batch(payload, db):\n"
        "    for item in payload.events:\n"
        "        if item.event_type != 'purchase':\n"
        "            enqueue_event(_event_fields_from_payload(item))\n"
        "            continue\n"
        "        db.add(Event(**_event_fields_from_payload(item)))\n"
        "    return {}\n"
    )
    f = tmp_path / "track.py"
    with patch.object(audit, "TARGET", f):
        f.write_text(pre_fix)
        assert audit.main() == 1          # no enqueue_event + literal Event
        f.write_text(fixed)
        assert audit.main() == 0          # buffered + Event(**source)

    # The live tree must hold the contract.
    assert audit.main() == 0
