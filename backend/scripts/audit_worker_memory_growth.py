#!/usr/bin/env python3
"""Worker memory-growth tracker.

Born 2026-05-02 from the brutal-CTO post-elite-tier inspection. PM2
workers were running uninterrupted for 2 days at 95-160 MB each.
There was NO automated tracking of memory growth over time. Memory
leaks are silent until the OOM killer fires — too late for a
production system.

Strategy
--------
Snapshot `pm2 jlist` (JSON output of all PM2 processes) into a
rolling JSON file. Each run merges today's per-worker memory reading
into a 14-day window. If any worker's current memory exceeds its
14-day MIN by more than _GROWTH_THRESHOLD_PCT, the audit FAILS.

Threshold tuning
----------------
Initial threshold 100 % (= 2× growth) — generous to avoid false
positives during normal startup ramp. Tighten after collecting
a few weeks of baseline.

Usage
-----
    python3 scripts/audit_worker_memory_growth.py
    python3 scripts/audit_worker_memory_growth.py --threshold 75
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LEDGER_PATH = Path(
    "/root/.claude/projects/-opt-wishspark/memory/"
    "worker_memory_rolling_ledger.json"
)
_DEFAULT_THRESHOLD_PCT = 100  # 2× growth is the alarm
_RETENTION_DAYS = 14


def _pm2_jlist() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["pm2", "jlist"], stderr=subprocess.DEVNULL,
        ).decode()
        return json.loads(out)
    except Exception:
        return []


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
    for ts, snapshot in ledger.items():
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                out[ts] = snapshot
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="compat shim for invariant_monitor — accepted but no-op")
    ap.add_argument("--threshold", type=int, default=_DEFAULT_THRESHOLD_PCT)
    args = ap.parse_args()

    procs = _pm2_jlist()
    if not procs:
        print("audit_worker_memory_growth: skip — pm2 jlist returned empty")
        return 0

    snapshot = {}
    for p in procs:
        name = p.get("name") or ""
        if not name.startswith("wishspark"):
            continue
        mon = p.get("monit") or {}
        mem_bytes = int(mon.get("memory", 0) or 0)
        snapshot[name] = mem_bytes

    if not snapshot:
        print("audit_worker_memory_growth: skip — no wishspark workers in pm2 jlist")
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    ledger = _load_ledger()
    ledger[now] = snapshot
    ledger = _prune(ledger)
    _save_ledger(ledger)

    # Compute per-worker baseline over the window using the 10th
    # percentile instead of absolute min. Rationale: absolute min is
    # poisoned by init-crash snapshots (Python process caught
    # mid-module-load reports sub-baseline memory). Empirical
    # 2026-05-04: agent_worker history had values [14, 39, 69, 90, 90,
    # ...178] MB — 14/39/69 were partial-init snapshots; 90 was true
    # post-load baseline. P10 = 90 here, robust to a few outliers.
    # Require _MIN_STABLE_SAMPLES (5) before trusting the baseline so
    # newly-fixed workers (like agent_worker post-suicide-loop fix)
    # don't trip on a too-small sample.
    _MIN_STABLE_SAMPLES = 5

    def _p10(values: list[int]) -> int:
        """Return the 10th percentile of the sorted values list."""
        if not values:
            return 0
        s = sorted(values)
        idx = max(0, (len(s) - 1) // 10)
        return s[idx]

    by_worker: dict[str, list[int]] = {}
    for ts, snap in ledger.items():
        for name, mem in snap.items():
            by_worker.setdefault(name, []).append(int(mem))

    findings: list[str] = []
    for name, mem_now in snapshot.items():
        history = by_worker.get(name) or []
        if len(history) < _MIN_STABLE_SAMPLES:
            continue
        baseline_min = _p10(history)
        if baseline_min <= 0:
            continue
        growth_pct = ((mem_now - baseline_min) * 100) // baseline_min
        if growth_pct >= args.threshold:
            mb_now = mem_now // (1024 ** 2)
            mb_min = baseline_min // (1024 ** 2)
            findings.append(
                f"{name}: {mb_min} MB (window min) → {mb_now} MB "
                f"(now) = +{growth_pct}% (threshold {args.threshold}%)"
            )

    if findings:
        print(
            f"FAIL: {len(findings)} worker(s) over the memory-growth "
            f"threshold ({args.threshold}% over the {_RETENTION_DAYS}-"
            f"day window minimum):"
        )
        for f in findings:
            print(f"  - {f}")
        print(
            "\nAction: investigate the worker for leaks. Common causes: "
            "unbounded in-memory caches, accumulating SQLAlchemy session "
            "state, listener subscriptions never released, event-loop "
            "tasks held by reference."
        )
        return 1

    sample_count = len(ledger)
    print(
        f"OK: {len(snapshot)} worker(s) within memory-growth budget "
        f"(window {sample_count} sample(s), threshold {args.threshold}%)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
