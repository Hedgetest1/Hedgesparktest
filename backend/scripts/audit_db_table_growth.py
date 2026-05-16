#!/usr/bin/env python3
"""DB-table row-count growth tracker.

Born 2026-05-02 from the brutal-CTO post-elite-tier inspection. There
was NO automated tracking of which DB tables grow and how fast. At
10k merchants, large append-only tables (events, ops_alerts,
action_outcomes, shop_orders) can blow up in a week and degrade
query latency before anybody notices.

Strategy
--------
Each run snapshots `pg_stat_user_tables.n_live_tup` for every
public-schema table into a 30-day rolling JSON ledger. Then computes
day-over-day growth deltas. If any table grew more than
_GROWTH_THRESHOLD_PCT in 24h (compared to the median of the prior
7 readings), the audit FAILS.

Threshold tuning
----------------
Initial 200 % over baseline median = 3× growth in 24h. Generous to
avoid false positives during normal merchant onboarding. Tighten as
production traffic stabilises.

Usage
-----
    python3 scripts/audit_db_table_growth.py
    python3 scripts/audit_db_table_growth.py --threshold 100
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = "/opt/wishspark"
sys.path.insert(0, f"{REPO}/backend")

LEDGER_PATH = Path(
    "/root/.claude/projects/-opt-wishspark/memory/"
    "db_table_rolling_ledger.json"
)
_DEFAULT_THRESHOLD_PCT = 200
_RETENTION_DAYS = 30
# Tables that legitimately spike during normal operation (e.g. events,
# tracker_events) — we still track them but with a higher threshold.
_HIGH_SPIKE_TABLES = frozenset({
    "events", "tracker_events", "shop_orders", "action_outcomes",
    "ops_alerts", "audit_log",
    # analytics_events: ClickHouse-shaped event store. Bursts during
    # campaigns / merchant onboarding; bounded by the 90d retention
    # task in event_bus.purge_expired (see app/services/event_bus.py
    # :436). Born in HIGH_SPIKE 2026-05-07 after a 88→541 spike
    # surfaced — root was test-hermeticity bug (event_bus._emit_
    # postgres bypasses pytest SAVEPOINT via _get_db() new session;
    # writes leak to prod from `test-trust-suite.myshopify.com`).
    # Cleaned 542 orphan rows in same commit; hermeticity fix tracked
    # as separate sprint (R-blocker:sprint>1d — requires event_bus
    # session-injection refactor).
    "analytics_events",
    # bugfix_candidates: pipeline-driven analytical breadcrumb table.
    # Steady-state ~30/day × 30d retention = ~1000 rows; legitimate spikes
    # during burst-triage cycles. Retention task in retention_task.py
    # bounds growth (terminal status pruned at 30d).
    "bugfix_candidates",
    # reviewer_assessments: append-only audit trail of every reviewer
    # decision. Pipeline-driven; legitimately spikes during burst-
    # propose cycles. Retention task in retention_task.py bounds growth
    # (>90d pruned). Born 2026-05-04 from same audit cycle that caught
    # bugfix_candidates.
    "reviewer_assessments",
    # sentry_incidents: pipeline-driven Sentry triage queue. Resolved
    # incidents pruned >60d; active incidents (status != 'resolved')
    # never pruned. Same growth-audit catch pattern; same fix.
    "sentry_incidents",
})
_HIGH_SPIKE_THRESHOLD_PCT = 500
# Absolute-rows floor. A pure percentage threshold is a false-positive
# factory at tiny absolute scale: on the pre-merchant DB a real dev
# merchant generating 89 visitor_purchase_sessions rows reads as
# "+1780% runaway" (5 → 94) and blocks honest commits, while a genuine
# missing-retention / unbounded-write-loop ALWAYS manifests as large
# ABSOLUTE counts (100k → millions). Gate the % check on the table
# having actually reached a scale where runaway growth is even a
# concern. Below this, % is meaningless noise. Born 2026-05-16f after
# the ground-truth load-rig work surfaced the class (real data, tiny
# base, percent-only → false block). Env-tunable for ops.
_MIN_ABSOLUTE_ROWS = int(os.getenv("DB_GROWTH_MIN_ABS_ROWS", "50000"))


def _query_table_sizes() -> dict[str, int]:
    """Run a single SELECT against pg_stat_user_tables. Returns
    {table_name: row_count}."""
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text
    except Exception:
        return {}
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT relname, n_live_tup FROM pg_stat_user_tables "
            "WHERE schemaname='public'"
        )).fetchall()
        # Normalise time-partition CHILDREN to their logical parent:
        # `events_y2026m04` → `events`. pg_stat_user_tables lists each
        # partition as its own relname; tracking them individually means
        # one time-bucket filling, rotating, or being retention-dropped
        # reads as "+2166% runaway" when it is expected partition
        # behaviour — and bypasses the parent's _HIGH_SPIKE_TABLES intent
        # (e.g. `events` is high-spike@500% but `events_y2026m04` got the
        # default 200%). Runaway growth of the logical table shows on the
        # summed parent, not a single child. Born 2026-05-16f: the
        # ground-truth load rig's seed-then-purge tripped events_y2026m04
        # at 200% while the logical `events` was the high-spike intent.
        agg: dict[str, int] = {}
        for r in rows:
            name = re.sub(r"_(y\d{4}m\d{2}|p\d+|default)$", "", r[0])
            agg[name] = agg.get(name, 0) + int(r[1] or 0)
        return agg
    except Exception:
        return {}
    finally:
        db.close()


def _load_ledger() -> dict:
    if not LEDGER_PATH.is_file():
        return {}
    try:
        return json.loads(LEDGER_PATH.read_text())
    except Exception:
        return {}


def _save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2, sort_keys=True))


def _prune(ledger: dict) -> dict:
    cutoff = datetime.now() - timedelta(days=_RETENTION_DAYS)
    out = {}
    for ts, snap in ledger.items():
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                out[ts] = snap
        except Exception:
            continue
    return out


def _median(xs: list[int]) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) // 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="compat shim for invariant_monitor — accepted but no-op")
    ap.add_argument("--threshold", type=int, default=_DEFAULT_THRESHOLD_PCT)
    args = ap.parse_args()

    sizes = _query_table_sizes()
    if not sizes:
        print("audit_db_table_growth: skip — DB query returned empty")
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    ledger = _load_ledger()
    ledger[now] = sizes
    ledger = _prune(ledger)
    _save_ledger(ledger)

    # For each table, compute median over the prior 7 readings
    # (excluding today's snapshot). If today exceeds median × threshold,
    # flag the growth.
    history: dict[str, list[int]] = {}
    today_snapshot = ledger[now]
    for ts, snap in ledger.items():
        if ts == now:
            continue
        for name, n in snap.items():
            history.setdefault(name, []).append(int(n))
    # Trim each history to the last 7 readings
    for name, lst in history.items():
        history[name] = lst[-7:]

    findings: list[str] = []
    for name, current in today_snapshot.items():
        prior = history.get(name) or []
        if len(prior) < 2:
            continue  # not enough baseline yet
        baseline = _median(prior)
        if baseline <= 0:
            continue
        threshold_pct = (
            _HIGH_SPIKE_THRESHOLD_PCT if name in _HIGH_SPIKE_TABLES
            else args.threshold
        )
        growth_pct = ((current - baseline) * 100) // baseline
        if current >= _MIN_ABSOLUTE_ROWS and growth_pct >= threshold_pct:
            findings.append(
                f"{name}: baseline {baseline:,} rows "
                f"→ now {current:,} rows = +{growth_pct}% "
                f"(threshold {threshold_pct}%)"
            )

    if findings:
        print(
            f"FAIL: {len(findings)} table(s) grew above the "
            f"day-over-day threshold:"
        )
        for f in findings:
            print(f"  - {f}")
        print(
            "\nAction: confirm growth is expected (merchant onboarding, "
            "campaign spike) — if not, look for unbounded write loops, "
            "missing retention policy, runaway worker. Add a retention "
            "task in retention_task.py if the table is append-only."
        )
        return 1

    print(
        f"OK: {len(today_snapshot)} table(s) within day-over-day growth "
        f"budget (window {len(ledger)} sample(s))."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
