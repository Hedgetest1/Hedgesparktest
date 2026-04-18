#!/usr/bin/env python3
"""
audit_redis_footprint.py — Worst-case Redis memory at N merchants.

Computes projected memory for every HedgeSpark Redis namespace at a
given merchant count. Output is a table: namespace | per-shop bytes |
total at N shops | % of target budget.

Default target: 256 MB (fits comfortably on any VPS + leaves headroom
for OS buffers). At 10k merchants we want <200 MB.

Usage:
    ./venv/bin/python scripts/audit_redis_footprint.py                    # default 10k
    ./venv/bin/python scripts/audit_redis_footprint.py --merchants 50000
    ./venv/bin/python scripts/audit_redis_footprint.py --strict           # exit 1 if over budget
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


# Measured empirically via `redis-cli memory usage` on prod (2026-04-18):
#   - A small scalar string (ISO code, ISO timestamp) = ~100 B total
#     (Redis string overhead dominates 3-byte payload).
#   - A medium string (JSON with ~8 keys, 200-400 chars) = ~300-500 B.
#   - A small SET of 3-5 string members = ~200 B.
#   - A list of 14 JSON blobs (Lighthouse history) = ~2.5 KB.
REDIS_ENCODING_OVERHEAD_BYTES = 80


@dataclass
class Namespace:
    name: str
    per_shop_keys: float        # keys per merchant (can be fractional if shared across shops)
    avg_bytes_per_key: int      # measured or estimated
    ttl_seconds: int            # 0 = no TTL
    description: str

    def per_shop_bytes(self) -> float:
        return self.per_shop_keys * self.avg_bytes_per_key

    def total_bytes(self, merchants: int) -> float:
        return self.per_shop_bytes() * merchants


# Catalog from CLAUDE.md §13 + pass 7/8 additions.
# `per_shop_keys`: how many keys a single merchant creates/day. Entries
# that are global (not per-shop) use a fraction (e.g. 1/10k = 0.0001).
# `avg_bytes_per_key`: conservative upper bound (measured where possible).
NAMESPACES: list[Namespace] = [
    # ─── Shop caches (established before 2026-04-17) ─────────────────
    Namespace("hs:shop_ccy:v1:{shop}",  1.0,   110, 3600,
              "primary currency cache — small scalar"),
    Namespace("hs:shop_tz:v1:{shop}",   1.0,   110, 3600,
              "timezone cache — small scalar"),
    Namespace("hs:shop_aov:v1:{shop}:{ccy}", 1.2, 130, 300,
              "AOV cache — scalar × ~1.2 currencies/shop"),
    # ─── Tracker error telemetry (A1) ────────────────────────────────
    Namespace("hs:trkerr:tot:{shop}:{date}",  1.0,   110, 7*86400,
              "daily tracker error total counter"),
    Namespace("hs:trkerr:hash:{shop}:{date}", 1.0,   500, 7*86400,
              "distinct error hash SET (avg 5 members × 80B)"),
    Namespace("hs:trkerr:sample:{shop}:{date}:{hash}", 5.0, 350, 7*86400,
              "per-hash first-seen detail JSON (~5 hashes/shop/day)"),
    Namespace("hs:trkerr:burst:{shop}",  0.1,  100, 60,
              "burst rate-limit counter (rarely active)"),
    Namespace("hs:trkerr:day:{shop}",    0.5,  100, 86400,
              "daily rate-limit counter (only for erroring shops)"),
    # ─── p95 latency snapshots (A4) ──────────────────────────────────
    # Shared across all merchants — keyed by route, not shop. Estimate
    # 50 routes × 24 hours = 1200 keys total, amortized over N merchants.
    Namespace("hs:p95:{route}:{hour}",   0.12, 250, 8*86400,
              "50 routes × 24h buckets amortized"),
    Namespace("hs:p95:last_flush_ts",    0.00001, 100, 600,
              "single global timestamp key"),
    Namespace("hs:p95:flush_lock",       0.00001, 100, 60,
              "single global flush lock"),
    # ─── Lighthouse history (A3) ─────────────────────────────────────
    # Also shared across merchants, ~6 routes × 14 entries history.
    Namespace("hs:lighthouse:last_run:{date}", 0.0001, 100, 30*3600,
              "single global daily-dedup key"),
    Namespace("hs:lighthouse:hist:{route}",    0.0006, 2500, 14*86400,
              "6 routes × 14-entry list (2.5KB each)"),
    # ─── LLM benchmark history (A5) ──────────────────────────────────
    Namespace("hs:llm_bench:last_run:{iso_week}", 0.0001, 100, 8*86400,
              "single global weekly-dedup key"),
    Namespace("hs:llm_bench:history", 0.0001, 1200, 90*86400,
              "single global 8-week rolling list"),
    # ─── Spike cooldowns (A1/A2/A6/A7/A4) ────────────────────────────
    Namespace("hs:spike:tracker_runtime:{shop}:{day}", 0.1, 100, 86400,
              "cooldown only when shop had a spike today"),
    Namespace("hs:spike:frontend_error:{hour}",    0.0001, 100, 3600,
              "global cooldown, fires ≤1/hour total"),
    Namespace("hs:spike:ux_frustration:{shop}:{day}", 0.05, 100, 86400,
              "cooldown for frustration spikes"),
    Namespace("hs:spike:sentry_rate:{hour}",       0.0001, 100, 3600,
              "global Sentry rate-spike cooldown"),
    Namespace("hs:spike:sentry_regression:{fp}:{hour}", 0.01, 100, 3600,
              "per-fingerprint regression cooldown"),
    Namespace("hs:spike:p95_drift:{route}:{day}",  0.005, 100, 86400,
              "per-route p95-drift cooldown"),
    Namespace("hs:spike:sentry_triage_stuck:{hour}", 0.00005, 100, 3600,
              "global Sentry triage stuck-queue cooldown"),
    Namespace("hs:alert:agg_cycle_slow:{hour}",    0.00005, 100, 3600,
              "global aggregation-cycle slow cooldown"),
    # ─── Pre-existing keys that scale per-merchant ───────────────────
    Namespace("hs:symap:{shop}:{shopify_y}",  50.0, 120, 90*86400,
              "visitor identity bridge — ~50 visitors/shop at any time"),
    Namespace("hs:wh_status:{shop}",      1.0, 200, 48*3600,
              "webhook health snapshot JSON"),
    Namespace("hs:mdigest:{shop}:{week}", 0.03, 100, 14*86400,
              "merchant weekly digest dedup"),
]


BUDGET_BYTES = 256 * 1024 * 1024   # 256 MB soft budget
WARN_BYTES = 200 * 1024 * 1024     # 200 MB warning threshold


def format_bytes(n: float) -> str:
    if n >= 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024 * 1024):.1f} GB"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n:.0f} B"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchants", type=int, default=10_000,
                        help="Target merchant count (default 10000)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if projected usage > 200 MB")
    args = parser.parse_args()

    N = args.merchants

    print(f"\nRedis footprint projection @ {N:,} merchants")
    print(f"Soft budget: {format_bytes(BUDGET_BYTES)}"
          f"  |  Warn threshold: {format_bytes(WARN_BYTES)}\n")
    print(f"{'namespace':52s}  {'per-shop':>10s}  {'total':>12s}  {'% budget':>9s}")
    print(f"{'-' * 52}  {'-' * 10}  {'-' * 12}  {'-' * 9}")

    total = 0.0
    for ns in sorted(NAMESPACES, key=lambda n: -n.total_bytes(N)):
        per_shop = ns.per_shop_bytes()
        total_ns = ns.total_bytes(N)
        pct = total_ns / BUDGET_BYTES * 100
        total += total_ns
        print(f"{ns.name[:52]:52s}  {format_bytes(per_shop):>10s}  "
              f"{format_bytes(total_ns):>12s}  {pct:>8.1f}%")

    print(f"{'-' * 52}  {'-' * 10}  {'-' * 12}  {'-' * 9}")
    total_pct = total / BUDGET_BYTES * 100
    print(f"{'TOTAL':52s}  {'':>10s}  {format_bytes(total):>12s}  {total_pct:>8.1f}%")
    print()

    if total > BUDGET_BYTES:
        print(f"❌ OVER BUDGET: {format_bytes(total)} > {format_bytes(BUDGET_BYTES)}")
        return 1 if args.strict else 0
    if total > WARN_BYTES:
        print(f"🟡 WARN: {format_bytes(total)} > {format_bytes(WARN_BYTES)} "
              f"(still under hard budget)")
        return 0

    print(f"✅ Under budget: {format_bytes(total)} / {format_bytes(BUDGET_BYTES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
