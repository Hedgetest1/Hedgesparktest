#!/usr/bin/env python3
"""
explain_at_scale.py — truth-grounded per-query plan sweep at synthetic 10k scale.

WHY: pg_stat_statements + the slow-query log only reflect *current* production
(a handful of merchants, ~174 shop_orders). They prove "nothing is pathological
NOW"; they cannot answer "does this uncached endpoint's query plan cliff at
10k-merchant data volume". The only honest answer is to measure the real plan
against a representative volume — NOT to inspect the SQL and reason about it
(reasoning is what the 10k ledger labels ASSUMED; this script produces
PROVEN-TRUTH).

HOW (the proven zero-pollution recipe, reused from the ltv_engine sweep):
  1. Open ONE transaction.
  2. Seed a synthetic shop with N orders / M repeat-customers spread over a
     realistic history window, server-side (generate_series) so seeding is
     seconds not minutes.
  3. ANALYZE the seeded tables INSIDE the txn (PostgreSQL ANALYZE sees the
     current txn's uncommitted rows, so the planner gets real stats for the
     synthetic volume — verified, not assumed: each query prints the scanned
     row count so a stats-didn't-take failure is visible, never silent).
  4. EXPLAIN (ANALYZE, BUFFERS) every registered query.
  5. ROLLBACK in a finally — zero rows persisted, ever, even on crash/ctrl-c.

The query registry holds the EXACT SQL the service code runs (copied verbatim,
not paraphrased) so the measured plan is the plan production gets.

Usage:
    ./venv/bin/python scripts/explain_at_scale.py --orders 50000
    ./venv/bin/python scripts/explain_at_scale.py --orders 200000 --query churn2
    ./venv/bin/python scripts/explain_at_scale.py --orders 200000 --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

# Run as `./venv/bin/python scripts/explain_at_scale.py` from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import DATABASE_URL  # noqa: E402

SYNTH_SHOP = "_explain_synthetic.myshopify.com"

# DEFAULT TO THE TEST DB, NOT LIVE PROD. txn-rollback prevents *row*
# persistence but NOT *page bloat*: ~5M rolled-back inserts left live
# shop_orders at 884 MB for 174 rows (reusable free space, but ugly and
# only reclaimable via an exclusive-lock VACUUM FULL on the revenue
# table). wishspark_test has schema+index parity (verified) and zero
# merchant impact. Pass --prod only with a deliberate reason.
def _target_session(prod: bool) -> "sessionmaker":
    url = make_url(DATABASE_URL)
    if not prod:
        url = url.set(database="wishspark_test")
    eng = create_engine(url, pool_pre_ping=True)
    return sessionmaker(bind=eng)

# Big tables a 10k-scale plan must NOT Seq-Scan (per-shop slice should ride an
# index). A seq scan here = the cliff.
BIG_TABLES = ("shop_orders", "events", "nudge_events", "visitor_purchase_sessions")


# ---------------------------------------------------------------------------
# Query registry — EXACT SQL the service runs. Keep the SQL byte-identical to
# the source so the measured plan is production's plan; cite the source.
# ---------------------------------------------------------------------------
QUERIES: dict[str, dict] = {
    # customer_churn_scorer.score_shop_customers (customer_churn.py:183-207).
    # UNCACHED request path (GET /pro/customer-churn). NO created_at window on
    # the scan -> full per-shop lifetime history GROUP BY customer_email.
    # Agent-flagged #1 likely 10k cliff.
    "churn2": {
        "src": "customer_churn_scorer.py:183-207  (GET /pro/customer-churn, uncached)",
        "sql": """
            SELECT
                customer_email,
                MIN(created_at) AS first_at,
                MAX(created_at) AS last_at,
                COUNT(*) AS total_orders,
                COALESCE(AVG(CAST(total_price AS FLOAT)), 0) AS avg_value,
                COUNT(*) FILTER (WHERE created_at >= :c90) AS orders_90d,
                COUNT(*) FILTER (WHERE created_at >= :c180 AND created_at < :c90) AS orders_prior_90d
            FROM shop_orders
            WHERE shop_domain = :shop
              AND customer_email IS NOT NULL AND customer_email <> ''
            GROUP BY customer_email
            HAVING COUNT(*) >= 2
            LIMIT 5000
        """,
        "params": lambda: {
            "shop": SYNTH_SHOP,
            "c90": "now() - interval '90 days'",
            "c180": "now() - interval '180 days'",
        },
        # c90/c180 are timestamps in the real code; bind as literals here.
        "literal_params": {
            "c90": "(now() - interval '90 days')",
            "c180": "(now() - interval '180 days')",
        },
    },
    # cohort_engine.get_cohort_retention step-1 (cohort_engine.py:76-87), used
    # by get_cohort_summary(weeks=26) -> 27-week window. UNCACHED request path
    # (GET /pro/cohorts/*). Windowed but ORDER BY customer_email,created_at.
    "cohort26": {
        "src": "cohort_engine.py:76-87  (GET /pro/cohorts/* via get_cohort_summary, uncached)",
        "sql": """
            SELECT
                customer_email,
                created_at,
                CAST(total_price AS FLOAT) AS total_price
            FROM shop_orders
            WHERE shop_domain     = :shop
              AND customer_email IS NOT NULL
              AND customer_email != ''
              AND created_at     >= (now() - interval '27 weeks')
            ORDER BY customer_email, created_at
        """,
        "params": lambda: {"shop": SYNTH_SHOP},
        "literal_params": {},
    },
}


def _seed(session, orders: int, customers: int, history_days: int,
          bg_orders: int, bg_shops: int) -> None:
    """Seed a realistic multi-tenant 10k shape:

      * `bg_orders` rows spread across `bg_shops` OTHER shops — so the target
        shop is a SELECTIVE fraction of the table. WITHOUT this the single
        synthetic shop is ~100% of the table and a Seq Scan is *correctly*
        optimal (reading ~all rows) — a false-positive "cliff" and the exact
        single-shop test artifact the 10k ledger warns about. The background
        fill forces the planner to face the real 10k decision: pull ONE
        shop's slice out of a big multi-tenant table.
      * `orders` rows for SYNTH_SHOP, `customers` distinct repeat customers,
        created_at uniform over the last `history_days`.
    """
    # DETERMINISTIC distribution (no random()): every run produces a
    # byte-identical table -> identical ANALYZE stats -> identical planner
    # decision. A stochastic benchmark whose plan flips run-to-run is not
    # truth; a reproducible one is. created_at is spread by a hash of g so
    # the per-customer history is interleaved (realistic), not block-ordered.
    if bg_orders > 0:
        session.execute(
            text("""
                INSERT INTO shop_orders
                    (shop_domain, shopify_order_id, total_price, currency,
                     customer_id, customer_email, line_items, created_at,
                     ingested_at, source)
                SELECT
                    '_explain_bg_' || (g % :bg_shops) || '.myshopify.com',
                    'bg_' || g,
                    (10 + (g % 191))::numeric(10,2),
                    'EUR',
                    'cid_' || g,
                    'bg' || g || '@synthetic.example',
                    '[]'::jsonb,
                    now() - (((g * 2654435761) % :history_days)
                             || ' days')::interval,
                    now(),
                    'synthetic'
                FROM generate_series(1, :bg_orders) g
            """),
            {"bg_shops": max(1, bg_shops), "bg_orders": bg_orders,
             "history_days": history_days},
        )
    session.execute(
        text("""
            INSERT INTO shop_orders
                (shop_domain, shopify_order_id, total_price, currency,
                 customer_id, customer_email, line_items, created_at,
                 ingested_at, source)
            SELECT
                :shop,
                'synth_' || g,
                (10 + (g % 191))::numeric(10,2),
                'EUR',
                'cid_' || (g % :customers),
                'cust' || (g % :customers) || '@synthetic.example',
                '[]'::jsonb,
                now() - (((g * 2654435761) % :history_days)
                         || ' days')::interval,
                now(),
                'synthetic'
            FROM generate_series(1, :orders) g
        """),
        {
            "shop": SYNTH_SHOP,
            "orders": orders,
            "customers": customers,
            "history_days": history_days,
        },
    )


def _explain(session, name: str, spec: dict) -> dict:
    sql = spec["sql"].strip()
    # Inline literal time params (real code binds datetimes; literal interval
    # math produces the identical plan shape and keeps the harness simple).
    for k, v in spec.get("literal_params", {}).items():
        sql = sql.replace(f":{k}", v)
    bind = {k: val for k, val in spec["params"]().items()
            if f":{k}" in sql}
    t0 = time.perf_counter()
    rows = session.execute(
        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql), bind
    ).fetchall()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    plan = "\n".join(r[0] for r in rows)

    flags: list[str] = []
    for tbl in BIG_TABLES:
        if re.search(rf"\bSeq Scan on {tbl}\b", plan):
            flags.append(f"SEQ-SCAN:{tbl}")
    if "external merge  Disk:" in plan or re.search(r"Sort Method: external", plan):
        m = re.search(r"external merge\s+Disk:\s*(\d+)kB", plan)
        flags.append(f"DISK-SORT:{m.group(1) + 'kB' if m else 'spilled'}")
    exec_m = re.search(r"Execution Time:\s*([\d.]+) ms", plan)
    exec_ms = float(exec_m.group(1)) if exec_m else None
    scanned_m = re.search(r"on shop_orders[^\n]*\(actual time=[^\n]*rows=(\d+)", plan)
    scanned = int(scanned_m.group(1)) if scanned_m else None
    if exec_ms is not None and exec_ms > 1000:
        flags.append(f"SLOW:{exec_ms:.0f}ms")

    return {
        "name": name,
        "src": spec["src"],
        "exec_ms": exec_ms,
        "rows_scanned_shop_orders": scanned,
        "flags": flags,
        "verdict": "CLIFF" if flags else "CLEAN",
        "plan": plan,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=50000,
                    help="synthetic shop_orders rows for the ONE target shop")
    ap.add_argument("--customers", type=int, default=0,
                    help="distinct customers (default orders//8 — realistic repeat)")
    ap.add_argument("--history-days", type=int, default=800,
                    help="created_at spread (>27w so cohort window is exercised)")
    ap.add_argument("--bg-orders", type=int, default=1_000_000,
                    help="background rows across other shops so target shop is "
                         "SELECTIVE (defeats the single-shop seq-scan artifact)")
    ap.add_argument("--bg-shops", type=int, default=400,
                    help="number of background shops to spread bg-orders over")
    ap.add_argument("--query", default="", help="run only this registry key")
    ap.add_argument("--prove-churn-fix", action="store_true",
                    help="EXPLAIN churn2, then CREATE the candidate covering "
                         "index IN-TXN, re-ANALYZE, re-EXPLAIN — proves the "
                         "fix eliminates the disk sort before it is proposed "
                         "(index rolled back, zero pollution)")
    ap.add_argument("--prod", action="store_true",
                    help="run against LIVE prod DB (default: wishspark_test; "
                         "prod leaves reusable page bloat — use deliberately)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-plan", action="store_true",
                    help="print the full EXPLAIN plan text")
    args = ap.parse_args()

    customers = args.customers or max(1, args.orders // 8)
    keys = [args.query] if args.query else list(QUERIES)
    for k in keys:
        if k not in QUERIES:
            print(f"unknown query '{k}'; known: {list(QUERIES)}", file=sys.stderr)
            return 2

    session = _target_session(args.prod)()
    if not args.json:
        print(f"target DB: {'PROD (wishspark)' if args.prod else 'wishspark_test'}")
    results: list[dict] = []
    try:
        t0 = time.perf_counter()
        _seed(session, args.orders, customers, args.history_days,
              args.bg_orders, args.bg_shops)
        session.execute(text("ANALYZE shop_orders"))
        seed_ms = (time.perf_counter() - t0) * 1000.0
        # Verify the planner actually sees the synthetic volume + that the
        # target shop is SELECTIVE (defends both the "ANALYZE didn't take"
        # failure mode AND the single-shop seq-scan artifact; never assume).
        seen = session.execute(
            text("SELECT count(*) FROM shop_orders WHERE shop_domain = :s"),
            {"s": SYNTH_SHOP},
        ).scalar()
        total = session.execute(text("SELECT count(*) FROM shop_orders")).scalar()
        sel = 100.0 * seen / max(1, total)
        if not args.json:
            print(f"\nseeded target={seen:,} + bg={total - seen:,} "
                  f"({customers:,} customers, {args.history_days}d history) "
                  f"in {seed_ms:.0f}ms — table={total:,} rows, "
                  f"target selectivity={sel:.2f}% — ANALYZE done\n")
        if seen < args.orders:
            print(f"ABORT: only {seen} rows visible — seed/txn broken",
                  file=sys.stderr)
            return 3

        for k in keys:
            results.append(_explain(session, k, QUERIES[k]))

        if args.prove_churn_fix:
            before = _explain(session, "churn2", QUERIES["churn2"])
            # Candidate covering partial index: yields rows in
            # (shop_domain, customer_email, created_at) order with
            # total_price carried -> index-only scan -> GroupAggregate
            # streams with NO sort -> no disk spill. Partial predicate
            # matches the query's customer_email filter exactly.
            session.execute(text(
                "CREATE INDEX ix_so_churn_cover ON shop_orders "
                "(shop_domain, customer_email, created_at) INCLUDE (total_price) "
                "WHERE customer_email IS NOT NULL AND customer_email <> ''"
            ))
            session.execute(text("ANALYZE shop_orders"))
            after = _explain(session, "churn2+idx", QUERIES["churn2"])
            results += [before, after]
    finally:
        session.rollback()  # zero-pollution invariant — always
        session.close()

    if args.json:
        print(json.dumps([{x: r[x] for x in r if x != "plan"}
                          for r in results], indent=2))
    else:
        for r in results:
            print(f"── {r['name']}  [{r['verdict']}]")
            print(f"   src:    {r['src']}")
            print(f"   exec:   {r['exec_ms']:.0f} ms" if r['exec_ms']
                  else "   exec:   n/a")
            print(f"   scanned:{r['rows_scanned_shop_orders']:,} shop_orders rows"
                  if r['rows_scanned_shop_orders'] else "   scanned: n/a")
            print(f"   flags:  {', '.join(r['flags']) or 'none'}")
            if args.show_plan:
                print("   ── plan ──")
                print("\n".join("   " + ln for ln in r["plan"].splitlines()))
            print()
        cliffs = [r["name"] for r in results if r["verdict"] == "CLIFF"]
        print(f"VERDICT: {len(cliffs)} cliff(s): {cliffs or 'none — all CLEAN'}")
    return 1 if any(r["verdict"] == "CLIFF" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
