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
    """ib._EVENT_FIELDS MUST equal the keys of track.py's `_fields`
    dict (the in-code comment promises it; a drift = wrong/dropped
    columns in the bulk INSERT)."""
    src = open("app/api/track.py").read()
    start = src.index("_fields = {")
    body = src[start:src.index("}", start)]
    keys = set(__import__("re").findall(r'"([a-z_]+)":', body))
    assert keys == set(ib._EVENT_FIELDS), (
        f"drift: track _fields {keys ^ set(ib._EVENT_FIELDS)} "
        f"differs from ingest_buffer._EVENT_FIELDS")


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

def test_drain_bulk_inserts_events_and_upserts_visitors(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr("app.core.redis_client._client", lambda: fr)
    ib.enqueue_event({"shop_domain": "s.myshopify.com",
                      "visitor_id": "v1", "event_type": "product_view"})
    ib.enqueue_event({"shop_domain": "s.myshopify.com",
                      "visitor_id": "v2", "event_type": "scroll"})

    fake_cur = MagicMock()
    fake_raw = MagicMock()
    fake_raw.cursor.return_value = fake_cur
    fake_sess = MagicMock()
    fake_sess.connection.return_value.connection = fake_raw
    calls: list[str] = []
    monkeypatch.setattr("app.core.database.SessionLocal",
                        lambda: fake_sess)
    monkeypatch.setattr(
        "psycopg2.extras.execute_values",
        lambda cur, sql, rows, template=None: calls.append(sql))

    n = ib.drain_events(max_total=100)
    assert n == 2
    assert any("INSERT INTO events" in c for c in calls)
    assert any("INSERT INTO visitors" in c and "ON CONFLICT" in c
               for c in calls)
    fake_raw.commit.assert_called()
    assert ib._take_batch(fr, 10) == []                  # buffer drained
