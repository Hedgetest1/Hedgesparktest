"""Tests for app.services.data_retention — GDPR Art. 5(1)(e) sweep.

Contract:
  1. Events older than DATA_RETENTION_EVENTS_DAYS are deleted.
  2. Events newer than the cutoff are preserved.
  3. VPS rows older than DATA_RETENTION_VPS_DAYS are deleted.
  4. VPS rows newer than the cutoff are preserved.
  5. Batch size is respected — one run cannot delete more than batch.
  6. DATA_RETENTION_PAUSED=1 is a no-op.
  7. Report dict reflects actual counts.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.event import Event
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services.data_retention import (
    _delete_events_older_than,
    _delete_vps_older_than,
    _retention_config,
    run_retention_sweep,
)


def _epoch_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _unique_shop() -> str:
    return f"retention_{uuid.uuid4().hex[:10]}.myshopify.com"


# ---------- Config ----------

def test_defaults_when_env_unset(monkeypatch):
    for k in ("DATA_RETENTION_EVENTS_DAYS", "DATA_RETENTION_VPS_DAYS",
              "DATA_RETENTION_BATCH_SIZE", "DATA_RETENTION_PAUSED"):
        monkeypatch.delenv(k, raising=False)
    cfg = _retention_config()
    assert cfg["events_days"] == 395
    assert cfg["vps_days"] == 730
    assert cfg["batch_size"] == 5000
    assert cfg["paused"] is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("DATA_RETENTION_EVENTS_DAYS", "30")
    monkeypatch.setenv("DATA_RETENTION_VPS_DAYS", "60")
    monkeypatch.setenv("DATA_RETENTION_BATCH_SIZE", "100")
    cfg = _retention_config()
    assert cfg["events_days"] == 30
    assert cfg["vps_days"] == 60
    assert cfg["batch_size"] == 100


def test_pause_kill_switch(monkeypatch, db):
    monkeypatch.setenv("DATA_RETENTION_PAUSED", "1")
    report = run_retention_sweep(db)
    assert report["status"] == "paused"
    assert report["events_deleted"] == 0
    assert report["vps_deleted"] == 0


# ---------- Events deletion ----------

def test_deletes_old_events(db):
    shop = _unique_shop()
    now = datetime.utcnow()

    # Ancient event (2 years ago)
    old_ts = _epoch_ms(now - timedelta(days=730))
    db.add(Event(
        visitor_id=f"v_{uuid.uuid4().hex[:8]}",
        event_type="page_view",
        url="/",
        shop_domain=shop,
        timestamp=old_ts,
    ))
    # Recent event (1 day ago)
    recent_ts = _epoch_ms(now - timedelta(days=1))
    recent_id = None
    recent = Event(
        visitor_id=f"v_{uuid.uuid4().hex[:8]}",
        event_type="page_view",
        url="/",
        shop_domain=shop,
        timestamp=recent_ts,
    )
    db.add(recent)
    db.flush()
    recent_id = recent.id

    # Cutoff = 365 days
    cutoff_dt = now - timedelta(days=365)
    cutoff_ms = _epoch_ms(cutoff_dt)
    deleted = _delete_events_older_than(db, cutoff_ms, batch_size=100)
    db.commit()

    assert deleted >= 1
    # Recent event must still exist
    still_there = db.query(Event).filter(Event.id == recent_id).first()
    assert still_there is not None


def test_batch_cap_is_enforced(db):
    shop = _unique_shop()
    now = datetime.utcnow()
    old_ts = _epoch_ms(now - timedelta(days=800))

    for _ in range(15):
        db.add(Event(
            visitor_id=f"v_{uuid.uuid4().hex[:6]}",
            event_type="page_view",
            url="/",
            shop_domain=shop,
            timestamp=old_ts,
        ))
    db.commit()

    cutoff_ms = _epoch_ms(now - timedelta(days=365))
    deleted = _delete_events_older_than(db, cutoff_ms, batch_size=5)
    db.commit()
    assert deleted == 5


# ---------- VPS deletion ----------

def test_deletes_old_visitor_purchase_sessions(db):
    shop = _unique_shop()
    now = datetime.utcnow()

    old = VisitorPurchaseSession(
        shop_domain=shop,
        visitor_id=f"v_{uuid.uuid4().hex[:8]}",
        shopify_order_id=f"order_{uuid.uuid4().hex[:10]}",
        confirmed_at=now - timedelta(days=800),
        ingested_at=now - timedelta(days=800),
    )
    recent = VisitorPurchaseSession(
        shop_domain=shop,
        visitor_id=f"v_{uuid.uuid4().hex[:8]}",
        shopify_order_id=f"order_{uuid.uuid4().hex[:10]}",
        confirmed_at=now - timedelta(days=10),
        ingested_at=now - timedelta(days=10),
    )
    db.add_all([old, recent])
    db.flush()
    recent_id = recent.id

    cutoff_dt = now - timedelta(days=365)
    deleted = _delete_vps_older_than(db, cutoff_dt, batch_size=100)
    db.commit()

    assert deleted >= 1
    still = db.query(VisitorPurchaseSession).filter(
        VisitorPurchaseSession.id == recent_id,
    ).first()
    assert still is not None


# ---------- Full sweep ----------

def test_full_sweep_returns_structured_report(db, monkeypatch):
    shop = _unique_shop()
    now = datetime.utcnow()
    db.add(Event(
        visitor_id=f"v_{uuid.uuid4().hex[:8]}",
        event_type="page_view",
        url="/",
        shop_domain=shop,
        timestamp=_epoch_ms(now - timedelta(days=1000)),
    ))
    db.commit()

    monkeypatch.setenv("DATA_RETENTION_EVENTS_DAYS", "395")
    monkeypatch.setenv("DATA_RETENTION_VPS_DAYS", "730")
    report = run_retention_sweep(db)
    assert report["status"] == "ok"
    assert report["events_deleted"] >= 1
    assert "ran_at" in report
    assert report["events_cutoff_days"] == 395
    assert report["vps_cutoff_days"] == 730
