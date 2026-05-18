"""Contract tests — J3-part-2 async analytics-event buffer (2026-05-17).

Locks: (a) the §0 revenue boundary — ONLY non-purchase events are
buffered, purchases stay synchronous; (b) the field-set never drifts
from track.py; (c) buffer mechanics (enqueue / atomic LPOP-count /
overflow-trim / Redis-down shed); (d) drain bulk-INSERT shape.
"""
from __future__ import annotations

import ast
import json
from unittest.mock import MagicMock, patch

from app.services import ingest_buffer as ib


# ── §0 REVENUE BOUNDARY (the non-negotiable one) ────────────────────

def test_track_buffers_ONLY_non_purchase_events():
    """Structural lock: track.py must enqueue ONLY under
    `event_type != "purchase"`. A regression that buffers purchases
    risks losing revenue/attribution rows (a §0 violation). Asserted
    on the AST so it can't silently drift."""
    tree = ast.parse(open("app/api/track.py").read())
    enqueue_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "enqueue_event"
    ]
    assert enqueue_calls, "track.py no longer enqueues — J3-part-2 lost"
    src = open("app/api/track.py").read()
    # The enqueue lives inside an `if payload.event_type != "purchase":`
    assert 'payload.event_type != "purchase"' in src
    i_guard = src.index('payload.event_type != "purchase"')
    i_enq = src.index("enqueue_event(_fields)")
    i_persist = src.index("_persist_purchase(db, payload)")
    assert i_guard < i_enq, "enqueue must be guarded by the non-purchase branch"
    # The synchronous purchase persistence stays AFTER the early return
    # (i.e. only reached when event_type == 'purchase').
    assert i_enq < i_persist, "purchase persistence must remain synchronous"


def test_event_fields_match_track_fields_dict():
    """ib._EVENT_FIELDS MUST equal the keys produced by track.py's
    single field source. As of 2026-05-18 that source is the shared
    helper `_event_fields_from_payload` (used by /track AND
    /track/batch — the inline `_fields = {…}` literal was unified
    away; the old brittle source-text parse moved with it). A drift
    here = wrong/dropped columns in the buffer bulk-INSERT. Asserted
    the robust way (call the producer) instead of regexing source."""
    from app.api.track import TrackPayload, _event_fields_from_payload

    sample = TrackPayload(
        shop_domain="s.myshopify.com",
        visitor_id="v1",
        event_type="product_view",
    )
    keys = set(_event_fields_from_payload(sample).keys())
    assert keys == set(ib._EVENT_FIELDS), (
        f"drift: track _event_fields_from_payload "
        f"{keys ^ set(ib._EVENT_FIELDS)} differs from "
        f"ingest_buffer._EVENT_FIELDS")


# ── buffer mechanics (pure Redis, no DB) ────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.l: list = []

    def llen(self, k):
        return len(self.l)

    def ltrim(self, k, a, b):
        self.l = self.l[a:] if b == -1 else self.l[a:b + 1]

    def rpush(self, k, v):
        self.l.append(v)

    def lpop(self, k, n=None):
        if not self.l:
            return None
        if n is None:
            return self.l.pop(0)
        out, self.l = self.l[:n], self.l[n:]
        return out


def test_enqueue_then_take_roundtrip(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr("app.core.redis_client._client", lambda: fr)
    assert ib.enqueue_event({"shop_domain": "s.myshopify.com",
                             "visitor_id": "v1",
                             "event_type": "product_view"}) is True
    got = ib._take_batch(fr, 10)
    assert len(got) == 1 and got[0]["visitor_id"] == "v1"
    assert all(f in got[0] for f in ib._EVENT_FIELDS)   # full field set
    assert ib._take_batch(fr, 10) == []                  # atomically removed


def test_enqueue_redis_down_sheds_not_raises(monkeypatch):
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    assert ib.enqueue_event({"shop_domain": "s", "visitor_id": "v"}) is False


def test_overflow_trims_to_bounded_ram(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr("app.core.redis_client._client", lambda: fr)
    monkeypatch.setattr(ib, "_BUF_MAX", 10)
    for i in range(25):
        ib.enqueue_event({"shop_domain": "s", "visitor_id": f"v{i}"})
    assert len(fr.l) <= 10 + 1, "buffer must stay bounded (drop-oldest)"


def test_take_batch_is_atomic_lpop_count(monkeypatch):
    fr = _FakeRedis()
    for i in range(5):
        fr.rpush("k", json.dumps({"visitor_id": f"v{i}"}))
    b1 = ib._take_batch(fr, 3)
    b2 = ib._take_batch(fr, 3)
    ids = [x["visitor_id"] for x in b1 + b2]
    assert ids == [f"v{i}" for i in range(5)], "disjoint, FIFO, no double-read"


# ── drain bulk-INSERT shape (hermetic — mocked DB, no SAVEPOINT bypass) ──

def test_drain_REALLY_persists_events_and_visitors():
    """REAL round-trip (NOT a mock): enqueue → drain_events → assert
    rows actually land in `events` + `visitors`. Born 2026-05-17 after
    the prior MOCKED version gave a FALSE GREEN — it recorded the SQL
    string but never executed it, so it missed a runtime
    execute_values template/value-arity mismatch in the visitor upsert
    that made EVERY production drain batch fail (buffer drained, 0
    persisted; caught only by the write-rig smoke). The instrument
    must exercise the real failure mode (the 2026-05-16d lesson).
    Uses a unique prefix + explicit cleanup (drain_events runs its own
    SessionLocal — the accepted pattern for SessionLocal-path code,
    same as the load rigs)."""
    import uuid
    from app.core.database import SessionLocal
    from sqlalchemy import text
    shop = f"wlrig_buftest_{uuid.uuid4().hex[:8]}.myshopify.com"
    db0 = SessionLocal()
    try:
        for vid, et in (("vt1", "product_view"), ("vt2", "scroll"),
                        ("vt1", "add_to_cart")):  # vt1 twice → upsert
            assert ib.enqueue_event({
                "shop_domain": shop, "visitor_id": vid,
                "event_type": et, "timestamp": 1747000000000}) is True
        n = ib.drain_events(max_total=100)
        assert n == 3, f"drain wrote {n}, expected 3"
        ev = db0.execute(text(
            "SELECT count(*) FROM events WHERE shop_domain=:s"),
            {"s": shop}).scalar()
        vi = db0.execute(text(
            "SELECT count(*) FROM visitors WHERE shop_domain=:s"),
            {"s": shop}).scalar()
        assert ev == 3, f"events persisted={ev}, expected 3 (drain SQL broke)"
        assert vi == 2, f"visitors upserted={vi}, expected 2 distinct"
        assert ib.buffer_depth() == 0, "buffer must be fully drained"
    finally:
        db0.execute(text("DELETE FROM events WHERE shop_domain=:s"),
                    {"s": shop})
        db0.execute(text("DELETE FROM visitors WHERE shop_domain=:s"),
                    {"s": shop})
        db0.commit()
        db0.close()


def test_drain_isolates_poison_row_keeps_the_rest():
    """RISK #2 (independent audit 2026-05-18): a single poison row
    (timestamp=None → events.timestamp BIGINT NOT NULL violation in the
    batched execute_values) must NOT drop the whole _DRAIN_BATCH. The
    row-resilient fallback persists the good rows and drops only the
    bad one. Pre-fix: 0 persisted (all ≤1000 lost). REAL round-trip."""
    import uuid
    from app.core.database import SessionLocal
    from sqlalchemy import text
    shop = f"wlrig_poison_{uuid.uuid4().hex[:8]}.myshopify.com"
    db0 = SessionLocal()
    try:
        assert ib.enqueue_event({"shop_domain": shop, "visitor_id": "g1",
                                 "event_type": "product_view",
                                 "timestamp": 1747000000000}) is True
        assert ib.enqueue_event({"shop_domain": shop, "visitor_id": "bad",
                                 "event_type": "scroll"}) is True  # NO timestamp → poison
        assert ib.enqueue_event({"shop_domain": shop, "visitor_id": "g2",
                                 "event_type": "add_to_cart",
                                 "timestamp": 1747000000001}) is True
        n = ib.drain_events(max_total=100)
        ev = db0.execute(text(
            "SELECT count(*) FROM events WHERE shop_domain=:s"),
            {"s": shop}).scalar()
        # 2 good rows salvaged, the timestamp=None row dropped — NOT 0.
        assert ev == 2, f"poison row dropped the batch (events={ev}, expected 2)"
        assert n == 2, f"drain returned {n}, expected 2 salvaged"
        assert ib.buffer_depth() == 0, "buffer must be fully drained"
    finally:
        db0.execute(text("DELETE FROM events WHERE shop_domain=:s"),
                    {"s": shop})
        db0.execute(text("DELETE FROM visitors WHERE shop_domain=:s"),
                    {"s": shop})
        db0.commit()
        db0.close()


def test_drain_bulk_insert_sql_shape(monkeypatch):
    """Cheap shape guard (complements the real round-trip above):
    drain issues an events bulk INSERT + a visitor upsert ON CONFLICT,
    and — the regression that shipped — every value row's arity MUST
    match the execute_values template's %s count."""
    fr = _FakeRedis()
    monkeypatch.setattr("app.core.redis_client._client", lambda: fr)
    ib.enqueue_event({"shop_domain": "s.myshopify.com",
                      "visitor_id": "v1", "event_type": "product_view"})
    fake_raw = MagicMock()
    fake_raw.cursor.return_value = MagicMock()
    fake_sess = MagicMock()
    fake_sess.connection.return_value.connection = fake_raw
    monkeypatch.setattr("app.core.database.SessionLocal",
                        lambda: fake_sess)
    seen: list[str] = []

    def _ev(cur, sql, rows, template=None):
        seen.append(sql)
        if template is not None and rows:
            assert len(rows[0]) == template.count("%s"), (
                f"value arity {len(rows[0])} != template %s count "
                f"{template.count('%s')} — the exact bug that made "
                f"every drain batch fail")

    monkeypatch.setattr("psycopg2.extras.execute_values", _ev)
    ib.drain_events(max_total=100)
    assert any("INSERT INTO events" in s for s in seen)
    assert any("INSERT INTO visitors" in s and "ON CONFLICT" in s
               for s in seen)
    fake_raw.commit.assert_called()
