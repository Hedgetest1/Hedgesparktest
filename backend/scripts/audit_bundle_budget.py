#!/usr/bin/env python
"""
audit_bundle_budget.py — Tier 6.4: dashboard bundle-size budget gate.

Policy
------
Top-1 means measurable perception: the dashboard must feel fast and stay
fast. Bundle size is the single biggest lever on first-paint and
hydration time. Today it grows unmonitored; a careless import of a
heavy library can add 200 KB to the root chunk and nobody would notice
until merchants on 3G report a slow /app.

This script enforces three budgets against the built Next.js output in
`dashboard/.next/`:

* `largest_chunk_bytes`  — the single largest file under
  `.next/static/chunks/`. Catches "someone added lodash to a route".
* `root_main_total_bytes` — sum of `rootMainFiles` from
  `build-manifest.json`. This is the shared first-load payload every
  route pays. The metric that matters most for time-to-interactive.
* `chunks_total_bytes`   — total size of `.next/static/chunks/*.js`.
  A backstop against "split the big chunk into five medium chunks".
* `chunks_count_max`     — a ceiling on chunk count so we don't trade
  size for request fan-out.

Budgets live in `dashboard/bundle-budget.json` alongside a recorded
baseline. Updating a budget is an intentional, reviewable commit.

Behavior
--------
* Fail fast when a budget is exceeded (exit 1).
* Skip cleanly when `.next/build-manifest.json` is missing — preflight
  should not force a dashboard rebuild on every backend commit. The
  gate still runs in CI where the build is produced first.
* Honors `SKIP_BUNDLE_BUDGET=1` for explicit opt-out.

Usage:
    ./venv/bin/python scripts/audit_bundle_budget.py
    ./venv/bin/python scripts/audit_bundle_budget.py --detail
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

DASHBOARD = pathlib.Path("/opt/wishspark/dashboard")
BUDGET_FILE = DASHBOARD / "bundle-budget.json"
MANIFEST = DASHBOARD / ".next" / "build-manifest.json"
CHUNK_DIR = DASHBOARD / ".next" / "static" / "chunks"


def _fmt(n: int) -> str:
    return f"{n:,} B ({n / 1024:.1f} KB)"


def _load_budget() -> dict:
    if not BUDGET_FILE.exists():
        print(f"audit_bundle_budget: budget file missing at {BUDGET_FILE}")
        sys.exit(2)
    return json.loads(BUDGET_FILE.read_text())["budgets"]


def _gather() -> dict | None:
    if not MANIFEST.exists() or not CHUNK_DIR.exists():
        return None
    manifest = json.loads(MANIFEST.read_text())
    root_files = manifest.get("rootMainFiles", [])
    root_total = 0
    for rel in root_files:
        path = DASHBOARD / ".next" / rel
        if path.exists():
            root_total += path.stat().st_size
    chunks = sorted(CHUNK_DIR.glob("*.js"))
    chunk_sizes = [(p, p.stat().st_size) for p in chunks]
    largest = max(chunk_sizes, key=lambda kv: kv[1]) if chunk_sizes else (None, 0)
    return {
        "root_total": root_total,
        "root_files": root_files,
        "chunks_total": sum(s for _, s in chunk_sizes),
        "chunks_count": len(chunk_sizes),
        "largest_path": largest[0].name if largest[0] else None,
        "largest_size": largest[1],
        "all": chunk_sizes,
    }


def main() -> int:
    if os.environ.get("SKIP_BUNDLE_BUDGET") == "1":
        print("audit_bundle_budget: SKIP_BUNDLE_BUDGET=1 — skipped")
        return 0

    metrics = _gather()
    if metrics is None:
        print(
            "audit_bundle_budget: no .next build found — skipped "
            f"(run `cd {DASHBOARD} && npx next build` to produce one)"
        )
        return 0

    budget = _load_budget()
    checks = [
        ("largest_chunk_bytes", metrics["largest_size"], budget["largest_chunk_bytes"]),
        ("root_main_total_bytes", metrics["root_total"], budget["root_main_total_bytes"]),
        ("chunks_total_bytes", metrics["chunks_total"], budget["chunks_total_bytes"]),
        ("chunks_count_max", metrics["chunks_count"], budget["chunks_count_max"]),
    ]

    print(f"audit_bundle_budget: checked {DASHBOARD / '.next'}")
    print()
    print(f"  largest chunk  : {_fmt(metrics['largest_size'])}  ({metrics['largest_path']})")
    print(f"  rootMainFiles  : {_fmt(metrics['root_total'])}  ({metrics['chunks_count']} chunks shared root)")
    print(f"  chunks total   : {_fmt(metrics['chunks_total'])}")
    print(f"  chunks count   : {metrics['chunks_count']}")
    print()

    over: list[tuple[str, int, int]] = []
    for name, actual, cap in checks:
        headroom = cap - actual
        marker = "OK " if actual <= cap else "OVER"
        pct = (actual / cap * 100) if cap else 0
        print(f"  {marker}  {name:<24s}  {actual:>10,}  /  {cap:>10,}  ({pct:5.1f}%, headroom {headroom:+,})")
        if actual > cap:
            over.append((name, actual, cap))

    if "--detail" in sys.argv:
        print()
        print("All chunks (sorted desc):")
        for path, size in sorted(metrics["all"], key=lambda kv: kv[1], reverse=True):
            print(f"  {size:>10,}  {path.name}")

    if over:
        print()
        print(f"FAIL: {len(over)} budget(s) exceeded:")
        for name, actual, cap in over:
            print(f"  {name}: {_fmt(actual)} > {_fmt(cap)} (delta {actual - cap:+,} B)")
        print()
        print(
            "If the regression is intentional, update "
            f"{BUDGET_FILE.name} with a reviewed delta and the new baseline."
        )
        return 1

    print()
    print("OK: all bundle budgets within cap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
