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

import pytest

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

# track_event_batch now takes `request` (consent/GPC + per-shop-rate
# precondition parity with single /track). The gates are patched True
# by default so the buffer-MECHANISM tests stay focused; the dedicated
# precondition test below overrides them. `request` is never
# dereferenced once the gates are patched, so a stub suffices.
_REQ = MagicMock(name="request")


@pytest.fixture(autouse=True)
def _allow_gates():
    """Default: consent allowed + shop known + rate ok — so the
    mechanism tests assert buffering/0-conn, not the gates. The
    precondition test re-patches these locally (inner patch wins)."""
    with patch("app.api.track._consent_allows_ingestion", return_value=True), \
         patch("app.api.track._is_known_shop", return_value=True), \
         patch("app.api.track._check_per_shop_rate", return_value=True), \
         patch("app.api.track._bump_consent_metric"):
        yield


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_tbb", _BACKEND / "scripts" / "audit_track_batch_buffered.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _payload(event_type: str, vid: str = "v1",
             shop: str = "x.myshopify.com") -> TrackPayload:
    return TrackPayload(
        shop_domain=shop,
        visitor_id=vid,
        event_type=event_type,
        page_url=f"https://{shop}/p",
        utm_source="google",
        click_id="ck123",
        landing_page=f"https://{shop}/land",
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


def test_event_fields_never_emits_null_timestamp():
    """RISK #2 root (independent audit): events.timestamp is BIGINT
    NOT NULL with no DB default. The single source MUST guarantee a
    non-null int even when the client omits it — else the buffered row
    NOT-NULL-violates the drain batch. Client-supplied wins; server
    receive-time is the documented fallback."""
    p = TrackPayload(shop_domain="x.myshopify.com", visitor_id="v1",
                      event_type="click")  # no timestamp
    ts = _event_fields_from_payload(p)["timestamp"]
    assert isinstance(ts, int) and ts > 0, f"timestamp not defaulted: {ts!r}"
    # Client-supplied value is preserved untouched.
    p2 = TrackPayload(shop_domain="x.myshopify.com", visitor_id="v1",
                       event_type="click", timestamp=1747000000000)
    assert _event_fields_from_payload(p2)["timestamp"] == 1747000000000


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
         patch("app.api.track._store_shopify_y_mapping"), \
         patch("app.api.track._bump_heatmap_bucket"):
        holder = dbmod._LazyDbSession()
        resp = track_event_batch(_REQ, pl, db=holder)
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
         patch("app.api.track._bump_heatmap_bucket"), \
         patch("app.api.track._upsert_visitor") as upv, \
         patch("app.api.track._persist_purchase") as pp:
        holder = dbmod._LazyDbSession()
        resp = track_event_batch(_REQ, pl, db=holder)
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
         patch("app.api.track._bump_heatmap_bucket"), \
         patch("app.api.track._upsert_visitor"), \
         patch("app.api.track._persist_purchase"):
        holder = dbmod._LazyDbSession()
        resp = track_event_batch(_REQ, pl, db=holder)
        assert enq.call_count == 2          # both non-purchase buffered
        fake.commit.assert_called_once()    # purchase commit attempted
        fake.rollback.assert_called_once()  # … and rolled back
    body = bytes(resp.body)
    assert b'"accepted":2' in body          # buffered survive (was 0)


def test_batch_click_feeds_the_spatial_heatmap():
    """Side-effect parity: a batched click (the tracker's heatmap
    transport) MUST bump the Lite spatial heatmap, exactly like single
    /track's non-purchase branch. Pre-2026-05-18 the batch dropped it
    ⟹ the shipped HeatmapCard was structurally starved."""
    pl = BatchTrackPayload(events=[
        _payload("click", "v1"),
        _payload("page_view", "v2"),
    ])
    with patch.object(dbmod, "SessionLocal") as SL, \
         patch.object(ia, "ingest_admit", return_value="tok"), \
         patch.object(ia, "ingest_release"), \
         patch.object(ibuf, "enqueue_event", return_value=True), \
         patch("app.api.track._store_shopify_y_mapping"), \
         patch("app.api.track._bump_heatmap_bucket") as hm:
        holder = dbmod._LazyDbSession()
        track_event_batch(_REQ, pl, db=holder)
        SL.assert_not_called()                 # still 0-conn
        assert hm.call_count == 2              # both non-purchase items
        kw = hm.call_args_list[0].kwargs
        assert kw["event_type"] == "click"
        assert kw["shop_domain"] == "x.myshopify.com"
        assert "x_pct" in kw and "y_pct" in kw  # coords forwarded


def test_batch_drops_consent_denied_and_unknown_shop(monkeypatch):
    """Precondition parity (the bug the independent audit caught): an
    un-consented item AND an item for an unknown shop must be DROPPED
    (rejected++, never buffered/heatmap-captured), exactly like single
    /track. Only the fully-eligible item is accepted."""
    pl = BatchTrackPayload(events=[
        _payload("click", "v1", shop="okk.myshopify.com"),      # accepted
        _payload("click", "v2", shop="denied.myshopify.com"),   # consent→drop
        _payload("click", "v3", shop="unknown.myshopify.com"),  # unknown→drop
    ])

    with patch.object(dbmod, "SessionLocal") as SL, \
         patch.object(ia, "ingest_admit", return_value="tok"), \
         patch.object(ia, "ingest_release"), \
         patch.object(ibuf, "enqueue_event", return_value=True) as enq, \
         patch("app.api.track._store_shopify_y_mapping"), \
         patch("app.api.track._bump_heatmap_bucket") as hm, \
         patch("app.api.track._bump_consent_metric"), \
         patch("app.api.track._consent_allows_ingestion",
               side_effect=lambda item, request=None:
               item.shop_domain != "denied.myshopify.com"), \
         patch("app.api.track._is_known_shop",
               side_effect=lambda db, shop: shop != "unknown.myshopify.com"), \
         patch("app.api.track._check_per_shop_rate", return_value=True):
        resp = track_event_batch(_REQ, pl, db=dbmod._LazyDbSession())
        SL.assert_not_called()              # all non-purchase → 0-conn
        assert enq.call_count == 1          # only the eligible item
        assert hm.call_count == 1           # un-consented NOT captured
    body = bytes(resp.body)
    assert b'"accepted":1' in body
    assert b'"rejected":2' in body


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
        "def track_event_batch(request, payload, db):\n"
        "    for item in payload.events:\n"
        "        if not _consent_allows_ingestion(item, request=request):\n"
        "            continue\n"
        "        if not _is_known_shop(db, item.shop_domain):\n"
        "            continue\n"
        "        if item.event_type != 'purchase':\n"
        "            enqueue_event(_event_fields_from_payload(item))\n"
        "            _bump_heatmap_bucket(shop_domain=item.shop_domain)\n"
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
