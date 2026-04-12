"""
data_retention.py — time-based deletion sweep for personal data.

GDPR Art. 5(1)(e) (storage limitation) requires that personal data is
kept "no longer than is necessary". Shopify's targeted GDPR webhooks
(`customers/redact`, `shop/redact`, handled by `gdpr_processor.py`)
satisfy the event-driven erasure requirement, but they do NOT cover
the passive retention ceiling — data that sits in our tables after
the business purpose has elapsed is still a compliance gap.

This module implements the scheduled retention sweep:

    run_retention_sweep(db) -> dict

It deletes rows older than a configurable TTL from the tables that
hold visitor-level behavioral and attribution data:

    * `events`                      — raw tracker events (visitor_id, urls)
    * `visitor_purchase_sessions`   — visitor→order attribution rows

The cutoff is loaded from environment variables so legal / DPO can
tune without a deploy:

    DATA_RETENTION_EVENTS_DAYS       (default 395 — 13 months)
    DATA_RETENTION_VPS_DAYS          (default 730 — 24 months)
    DATA_RETENTION_BATCH_SIZE        (default 5000 — rows per DELETE)
    DATA_RETENTION_PAUSED            ("1" disables the sweep entirely)

Each run is batched (`LIMIT _BATCH_SIZE`) so we never open a long
transaction on the write-path table. The worker loop calls this once
per calendar day (Europe/Rome) via `agent_worker._run_data_retention`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("data_retention")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _retention_config() -> dict:
    return {
        "events_days": _env_int("DATA_RETENTION_EVENTS_DAYS", 395),
        "vps_days": _env_int("DATA_RETENTION_VPS_DAYS", 730),
        "batch_size": _env_int("DATA_RETENTION_BATCH_SIZE", 5000),
        "paused": os.getenv("DATA_RETENTION_PAUSED", "").strip() == "1",
    }


def _delete_events_older_than(
    db: Session, cutoff_epoch_ms: int, batch_size: int,
) -> int:
    """Delete up to `batch_size` rows from `events` whose `timestamp`
    (epoch milliseconds) is older than `cutoff_epoch_ms`. Uses an
    id-scoped WHERE clause so we never lock the whole table."""
    result = db.execute(sql_text("""
        DELETE FROM events
        WHERE id IN (
            SELECT id FROM events
            WHERE timestamp IS NOT NULL
              AND timestamp < :cutoff
            ORDER BY id
            LIMIT :batch
        )
    """), {"cutoff": cutoff_epoch_ms, "batch": batch_size})
    return int(result.rowcount or 0)


def _delete_vps_older_than(
    db: Session, cutoff_dt: datetime, batch_size: int,
) -> int:
    """Delete up to `batch_size` rows from `visitor_purchase_sessions`
    whose `confirmed_at` is older than `cutoff_dt`."""
    result = db.execute(sql_text("""
        DELETE FROM visitor_purchase_sessions
        WHERE id IN (
            SELECT id FROM visitor_purchase_sessions
            WHERE confirmed_at < :cutoff
            ORDER BY id
            LIMIT :batch
        )
    """), {"cutoff": cutoff_dt, "batch": batch_size})
    return int(result.rowcount or 0)


def run_retention_sweep(db: Session) -> dict:
    """Single-pass retention sweep. Returns a structured report dict."""
    cfg = _retention_config()
    report = {
        "status": "ok",
        "paused": cfg["paused"],
        "events_deleted": 0,
        "vps_deleted": 0,
        "events_cutoff_days": cfg["events_days"],
        "vps_cutoff_days": cfg["vps_days"],
        "batch_size": cfg["batch_size"],
        "ran_at": _now_utc().isoformat(),
    }
    if cfg["paused"]:
        report["status"] = "paused"
        return report

    # events: epoch millisecond cutoff
    try:
        events_cutoff_dt = _now_utc() - timedelta(days=cfg["events_days"])
        events_cutoff_ms = int(events_cutoff_dt.timestamp() * 1000)
        deleted = _delete_events_older_than(db, events_cutoff_ms, cfg["batch_size"])
        db.commit()
        report["events_deleted"] = deleted
    except Exception as exc:
        log.warning("data_retention: events sweep failed: %s", exc)
        report["events_error"] = type(exc).__name__
        try:
            db.rollback()
        except Exception:
            pass

    # visitor_purchase_sessions: DateTime cutoff
    try:
        vps_cutoff_dt = _now_utc() - timedelta(days=cfg["vps_days"])
        deleted = _delete_vps_older_than(db, vps_cutoff_dt, cfg["batch_size"])
        db.commit()
        report["vps_deleted"] = deleted
    except Exception as exc:
        log.warning("data_retention: vps sweep failed: %s", exc)
        report["vps_error"] = type(exc).__name__
        try:
            db.rollback()
        except Exception:
            pass

    if report["events_deleted"] or report["vps_deleted"]:
        log.info(
            "data_retention: events=%d vps=%d (events_ttl=%dd vps_ttl=%dd)",
            report["events_deleted"], report["vps_deleted"],
            cfg["events_days"], cfg["vps_days"],
        )
    return report
