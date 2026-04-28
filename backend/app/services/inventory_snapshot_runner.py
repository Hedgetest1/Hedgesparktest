"""inventory_snapshot_runner.py — Gap #4 worker phase.

Once-per-day guard: each cycle of the aggregation_worker picks up to
_BATCH_PER_CYCLE active merchants who have NOT been snapshotted today,
runs `fetch_and_snapshot` for each, and stamps a Redis flag so the
next cycle skips them.

Spreading: at 5min cycles × 288 cycles/day, _BATCH_PER_CYCLE=20 covers
5760 merchants/day worst case. At our scale (target 10k merchants in
12 months) we'd raise the batch or split workers — invariant_monitor
catches snapshot freshness drift either way.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.redis_client import _client as _redis_client
from app.services.inventory_snapshot_fetcher import fetch_and_snapshot

log = logging.getLogger("inventory_snapshot_runner")

_BATCH_PER_CYCLE = 20
_DONE_FLAG_KEY = "hs:inv_snap:done:{shop}:{date}"
_DONE_FLAG_TTL = 36 * 3600   # 36h — survives a full day + slack
_LOCK_KEY = "hs:inv_snap:lock"   # Single-writer guard across (future) workers
_LOCK_TTL = 300                  # 5min — same as cycle interval


def run_phase(db: Session) -> dict[str, Any]:
    """Run the inventory snapshot phase. Returns a summary dict."""
    summary: dict[str, Any] = {
        "ran": False,
        "candidates": 0,
        "processed": 0,
        "ok": 0,
        "skipped_already_done": 0,
        "errors": 0,
        "rows_upserted_total": 0,
    }

    rc = _redis_client()
    # Cross-worker single-writer lock — defense if the singleton invariant
    # is ever weakened (today aggregation_worker runs as instances:1).
    if rc is not None:
        try:
            got = bool(rc.set(_LOCK_KEY, "1", ex=_LOCK_TTL, nx=True))
            if not got:
                summary["skipped_already_done"] = -1  # marker: lock contended
                return summary
        except Exception as exc:  # noqa: BLE001
            log.warning("inventory snapshot lock probe failed: %s", exc)

    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()

    # Query: active merchants not yet snapshotted today.
    # We use a NOT EXISTS against inventory_snapshots so the query is
    # bounded by the index on (shop_domain, snapshot_date).
    rows = db.execute(text(
        """
        SELECT m.shop_domain
        FROM merchants m
        WHERE m.install_status = 'active'
          AND m.access_token IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM inventory_snapshots ins
              WHERE ins.shop_domain = m.shop_domain
                AND ins.snapshot_date = :today
          )
        ORDER BY m.id
        LIMIT :batch
        """
    ), {"today": today, "batch": _BATCH_PER_CYCLE}).fetchall()

    summary["candidates"] = len(rows)
    if not rows:
        summary["ran"] = True
        return summary

    summary["ran"] = True

    for (shop,) in rows:
        # Belt-and-braces with the Redis day flag (handles race when
        # two workers both pick the same merchant before the row lands)
        flag_key = _DONE_FLAG_KEY.format(shop=shop, date=today_iso)
        if rc is not None:
            try:
                if rc.get(flag_key):
                    summary["skipped_already_done"] += 1
                    continue
            except Exception as exc:  # noqa: BLE001
                log.warning("inventory snapshot flag read failed for %s: %s", shop, exc)

        result = fetch_and_snapshot(db, shop)
        summary["processed"] += 1
        if result.get("ok"):
            summary["ok"] += 1
            summary["rows_upserted_total"] += result.get("rows_upserted", 0)
            if rc is not None:
                try:
                    rc.set(flag_key, "1", ex=_DONE_FLAG_TTL)
                except Exception as exc:  # noqa: BLE001
                    log.warning("inventory snapshot flag write failed for %s: %s", shop, exc)
        else:
            summary["errors"] += 1
            err = result.get("error") or "unknown"
            log.warning(
                "inventory snapshot failed shop=%s err=%s products=%s pages=%s",
                shop, err, result.get("products_seen"), result.get("pages_fetched"),
            )

    return summary
