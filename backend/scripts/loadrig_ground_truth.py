#!/usr/bin/env python3
"""
loadrig_ground_truth.py — distinct-cold-merchant load rig that reads the
SERVER's saturation from the connection broker, not from conflated client
latency.

WHY THIS EXISTS (the structural gap it closes):
Every prior 10k instrument measured client-observed latency only. Client
latency conflates THREE different failures into one identical symptom
(rising p95 / errors): (1) the asyncio harness's own coroutine/GIL
collapse, (2) PgBouncer pool exhaustion (clients queued), (3) Postgres
saturation. That conflation produced the documented false "99.58% err at
10k" reading — a CLIENT artifact misread as a server verdict. The only
way to tell them apart is to ask the broker itself: PgBouncer
`SHOW POOLS` (cl_waiting, maxwait_us, sv_active) and `SHOW STATS`
(avg_xact_time, avg_wait_time) are SERVER-side truth, independent of how
the client generates load. If client errors rise but cl_waiting stays 0,
the ceiling is the CLIENT (discard). If cl_waiting / maxwait climb, that
is the real measured SERVER ceiling.

DESIGN (no band-aid, no extrapolation):
  * Process-per-connection workers (real OS parallelism, NO asyncio /
    event loop / GIL contention) — the trustworthy model from the
    ad-hoc /tmp probe, now permanent + version-controlled.
  * DISTINCT COLD merchants: each request targets a different forged-
    session shop → distinct cache keys → NO stampede/sticky/warm-cache
    benefit. This is the realistic 10k cold-storm shape the ledger
    marks UNINVESTIGATED, not the "one warm shop" the old probe hit.
  * A broker-sampler process polls PgBouncer every 0.5s for the run,
    capturing peak cl_waiting / maxwait_us / sv_active and the
    run-window avg_xact_time + avg_wait_time (Little's Law inputs
    measured over EXACTLY this run, not lifetime-polluted).
  * Honest scope: one host cannot truthfully generate literal 10k
    simultaneous HTTP — claiming so would be the lie this rig exists
    to prevent. It generates the sustainable true-parallel ceiling the
    host CAN honestly produce, reads the broker's saturation SLOPE
    under distinct-cold load, and grounds the 10k headroom in Little's
    Law on MEASURED pool numbers. The literal-10k-from-a-distributed-
    rig residual stays explicit.

Usage:
    ./venv/bin/python scripts/loadrig_ground_truth.py \
        --merchants 400 --procs 256 --duration 25 --route /dashboard/overview
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.core.database import DATABASE_URL, SessionLocal  # noqa: E402
from app.core.merchant_session import (  # noqa: E402
    SESSION_COOKIE_NAME, create_session_token,
)
from scripts.load_test_harness import (  # noqa: E402  (reuse, don't rebuild)
    cleanup_merchants, setup_merchants,
)

_BASE = os.getenv("LOADRIG_BASE_URL", "http://127.0.0.1:8000")


def _pgb_admin_url() -> str:
    """DATABASE_URL with the db swapped to the pgbouncer admin pseudo-db.

    NB: `str(url)` MASKS the password as '***' (SQLAlchemy safe repr) →
    SASL auth fails → the sampler would silently read zeros and report a
    FALSE "not pool-bound" verdict. render_as_string(hide_password=False)
    is mandatory; the loud-fail guard below is the backstop."""
    from sqlalchemy.engine import make_url
    return make_url(DATABASE_URL).set(
        database="pgbouncer").render_as_string(hide_password=False)


def _pgb(url: str, show: str) -> list[dict]:
    """One `SHOW <x>` against the broker → list of row dicts. psql -tA so
    parsing is delimiter-stable; broker truth, never the app DB."""
    out = subprocess.run(
        ["psql", url, "-tAF|", "-c", f"SHOW {show};"],
        capture_output=True, text=True, timeout=5,
    )
    rows = []
    for ln in out.stdout.strip().splitlines():
        if ln:
            rows.append(ln.split("|"))
    return rows


def _wishspark_pool(url: str) -> dict | None:
    # SHOW POOLS columns: database user cl_active cl_waiting ... sv_active
    # ... sv_idle ... maxwait maxwait_us pool_mode
    for r in _pgb(url, "POOLS"):
        if len(r) > 13 and r[0] == "wishspark":
            return {"cl_active": int(r[2]), "cl_waiting": int(r[3]),
                    "sv_active": int(r[7]), "maxwait_us": int(r[13])}
    return None


def _wishspark_stats(url: str) -> dict | None:
    # SHOW STATS columns: database total_xact_count total_query_count
    # total_received total_sent total_xact_time total_query_time
    # total_wait_time ...
    for r in _pgb(url, "STATS"):
        if r and r[0] == "wishspark":
            return {"xact": int(r[1]), "xact_time": int(r[5]),
                    "wait_time": int(r[7])}
    return None


def _broker_sampler(duration: float, q: "mp.Queue") -> None:
    url = _pgb_admin_url()
    s0 = _wishspark_stats(url)
    peak = {"cl_waiting": 0, "maxwait_us": 0, "sv_active": 0, "cl_active": 0}
    end = time.monotonic() + duration
    while time.monotonic() < end:
        p = _wishspark_pool(url)
        if p:
            for k in peak:
                peak[k] = max(peak[k], p[k])
        time.sleep(0.5)
    s1 = _wishspark_stats(url)
    dx = (s1["xact"] - s0["xact"]) if (s0 and s1) else 0
    # run-window averages (µs) — Little's Law inputs measured over THIS
    # run only, immune to the lifetime pollution from EXPLAIN-harness txns.
    avg_xact_us = ((s1["xact_time"] - s0["xact_time"]) / dx) if dx else 0.0
    avg_wait_us = ((s1["wait_time"] - s0["wait_time"]) / dx) if dx else 0.0
    q.put({"peak": peak, "run_xacts": dx,
           "avg_xact_us": avg_xact_us, "avg_wait_us": avg_wait_us})


def _worker(shops: list[str], route: str, duration: float,
            q: "mp.Queue") -> None:
    # Each worker rotates across its DISTINCT shop slice → every request a
    # different forged session → distinct cache key → cold, no sticky.
    cookies = [f"{SESSION_COOKIE_NAME}={create_session_token(s)}"
               for s in shops]
    lat: list[float] = []
    n = errs = qsum = 0
    i = 0
    end = time.monotonic() + duration
    while time.monotonic() < end:
        req = urllib.request.Request(_BASE + route)
        req.add_header("Cookie", cookies[i % len(cookies)])
        i += 1
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
                if r.status >= 400:
                    errs += 1
                qsum += int(r.headers.get("X-Query-Count", 0) or 0)
        except Exception:
            errs += 1
        lat.append((time.perf_counter() - t0) * 1000.0)
        n += 1
    q.put((n, errs, qsum, lat))


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    return xs[min(int(len(xs) * p / 100), len(xs) - 1)]


def _seed_merchant_data(shops: list[str], n_orders: int,
                        n_events: int) -> tuple[int, int]:
    """Give EACH rig merchant `n_orders` orders + `n_events` events with
    recent timestamps so /dashboard/overview does its REAL cold build
    (~18 queries) instead of the empty fast-path. Without this the rig
    only measures the data-LIGHT path — the honest gap the founder
    challenged. One host, €0, deterministic. unnest×generate_series so
    every shop gets exactly the same volume."""
    db = SessionLocal()
    try:
        if n_orders > 0:
            db.execute(text("""
                INSERT INTO shop_orders
                    (shop_domain, shopify_order_id, total_price, currency,
                     customer_email, created_at, ingested_at, source)
                SELECT s, 'lr_' || s || '_' || g,
                       (10 + (g % 191))::numeric(10,2), 'EUR',
                       'c' || (g % 200) || '@loadrig.example',
                       now() - (((g * 2654435761) % 60) || ' days')::interval,
                       now(), 'loadrig'
                FROM unnest(:shops) s, generate_series(1, :n) g
            """), {"shops": shops, "n": n_orders})
        if n_events > 0:
            db.execute(text("""
                INSERT INTO events
                    (shop_domain, visitor_id, event_type, "timestamp",
                     product_url)
                SELECT s, 'v' || (g % 500),
                       (ARRAY['product_view','dwell_time','scroll',
                              'add_to_cart'])[1 + (g % 4)],
                       (extract(epoch from now())*1000)::bigint
                           - ((g::bigint * 2654435761)
                              % (60::bigint * 86400000)),
                       '/products/p' || (g % 50)
                FROM unnest(:shops) s, generate_series(1, :n) g
            """), {"shops": shops, "n": n_events})
        db.commit()
        return n_orders * len(shops), n_events * len(shops)
    finally:
        db.close()


def _purge_loadtest_data() -> None:
    """No orphan data (§2 r7): cleanup_merchants only removes merchant
    rows; the seeded orders/events are keyed by shop_domain and must be
    purged explicitly. Prefix-scoped, so it can never touch a real shop."""
    db = SessionLocal()
    try:
        tbls = ("shop_orders", "events", "nudge_events",
                "visitor_purchase_sessions")
        for tbl in tbls:
            db.execute(text(
                f"DELETE FROM {tbl} WHERE shop_domain LIKE '\\_loadtest\\_%'"
            ))
        db.commit()
        # n_live_tup is a planner ESTIMATE that lags DELETEs until
        # autovacuum. A tool that deletes its rows but leaves the
        # statistics reading the pre-purge count is itself a mini-
        # tampone: it made audit_db_table_growth read "events 1292"
        # when reality was 0 and blocked an honest commit. ANALYZE
        # forces the estimate to match reality NOW, so the rig leaves
        # the DB statistically truthful, not just row-truthful.
        for tbl in tbls:
            db.execute(text(f"ANALYZE {tbl}"))
        db.commit()
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchants", type=int, default=400,
                    help="distinct COLD synthetic merchants (no warm reuse)")
    ap.add_argument("--procs", type=int, default=256,
                    help="true-parallel OS worker processes")
    ap.add_argument("--duration", type=int, default=25)
    ap.add_argument("--route", default="/dashboard/overview")
    ap.add_argument("--seed-orders", type=int, default=0,
                    help="orders PER merchant — >0 exercises the real "
                         "data-heavy /overview cold build, not empty path")
    ap.add_argument("--seed-events", type=int, default=0,
                    help="events PER merchant (data-heavy path)")
    ap.add_argument("--keep", action="store_true",
                    help="skip cleanup (debug only)")
    args = ap.parse_args()

    # LOUD-FAIL guard: a ground-truth rig whose broker sampler is blind
    # is worse than no rig — it reports reassuring zeros (the exact
    # conflation lie this exists to prevent). Verify the broker is
    # readable BEFORE generating any load.
    if _wishspark_stats(_pgb_admin_url()) is None:
        print("ABORT: cannot read PgBouncer SHOW STATS (admin auth / "
              "reachability). Refusing to run a load test whose server "
              "ground-truth instrument is blind.", file=sys.stderr)
        return 3

    rc = 0
    # setup + seed are INSIDE the try so a seed failure still triggers
    # the finally purge (a prior wiring had them outside → a seed crash
    # leaked merchant rows; §19.1 smoke caught it).
    try:
        print(f"setup: {args.merchants} distinct cold merchants…")
        shops = setup_merchants(args.merchants, force=True)
        if args.seed_orders or args.seed_events:
            o, e = _seed_merchant_data(shops, args.seed_orders,
                                       args.seed_events)
            print(f"seeded data-heavy: {o:,} orders + {e:,} events across "
                  f"{len(shops)} merchants ({args.seed_orders}o/"
                  f"{args.seed_events}e each) — /overview real cold build")
        per = max(1, len(shops) // args.procs)
        slices = [shops[i:i + per] or shops
                  for i in range(0, len(shops), per)][:args.procs]
        while len(slices) < args.procs:           # more procs than slices:
            slices.append(shops)                  # share the full cold set
        q: mp.Queue = mp.Queue()
        bq: mp.Queue = mp.Queue()
        sampler = mp.Process(target=_broker_sampler,
                             args=(args.duration + 1.0, bq))
        workers = [mp.Process(target=_worker,
                              args=(slices[i], args.route,
                                    float(args.duration), q))
                   for i in range(args.procs)]
        t0 = time.monotonic()
        sampler.start()
        for w in workers:
            w.start()
        res = [q.get() for _ in workers]
        for w in workers:
            w.join()
        broker = bq.get()
        sampler.join()
        wall = time.monotonic() - t0

        tot = sum(r[0] for r in res)
        errs = sum(r[1] for r in res)
        qsum = sum(r[2] for r in res)
        lat = sorted(x for r in res for x in r[3])
        b = broker["peak"]
        axu = broker["avg_xact_us"]
        # Little's Law on MEASURED numbers: a transaction-mode server
        # connection is held only for a transaction; with default_pool_size
        # server conns, max sustainable xact throughput = pool / avg_xact.
        POOL = 80  # pgbouncer default_pool_size (live, verified)
        ll_ceiling = (POOL / (axu / 1e6)) if axu > 0 else float("inf")

        print(f"\n── CLIENT (process-per-conn, true-parallel) ──")
        print(f"procs={args.procs} merchants={len(shops)} (distinct cold) "
              f"route={args.route} wall={wall:.1f}s")
        print(f"requests={tot} errors={errs} "
              f"({100.0*errs/max(tot,1):.2f}%) "
              f"throughput={tot/wall:.0f} req/s")
        print(f"latency p50={_pct(lat,50):.0f} p95={_pct(lat,95):.0f} "
              f"p99={_pct(lat,99):.0f} max={lat[-1] if lat else 0:.0f} ms")
        print(f"X-Query-Count total={qsum} avg={qsum/max(tot,1):.1f}/req")
        print(f"\n── SERVER GROUND TRUTH (PgBouncer broker, this run) ──")
        print(f"peak cl_waiting={b['cl_waiting']}  "
              f"peak maxwait={b['maxwait_us']/1000:.1f}ms  "
              f"peak sv_active={b['sv_active']}/{POOL}  "
              f"peak cl_active={b['cl_active']}")
        print(f"run-window avg_xact_time={axu/1000:.2f}ms  "
              f"avg_wait_time={broker['avg_wait_us']/1000:.3f}ms  "
              f"run_xacts={broker['run_xacts']}")
        print(f"Little's-Law ceiling = pool(80)/avg_xact = "
              f"{ll_ceiling:.0f} req/s (MEASURED inputs)")
        # The structural verdict: distinguish client-collapse from
        # server-saturation — the whole point of this rig.
        if b["cl_waiting"] == 0 and b["maxwait_us"] < 50_000:
            verdict = ("SERVER NOT POOL-BOUND at this load — broker never "
                       "queued (cl_waiting=0). Any client errors here are "
                       "CLIENT artifact, not a server ceiling.")
        else:
            verdict = (f"SERVER POOL PRESSURE MEASURED — broker queued "
                       f"(peak cl_waiting={b['cl_waiting']}, "
                       f"maxwait={b['maxwait_us']/1000:.0f}ms). This IS the "
                       f"real server ceiling at this offered load.")
        print(f"\nVERDICT: {verdict}")
        rc = 1 if (errs > 0 or b["cl_waiting"] > 0) else 0
    finally:
        if not args.keep:
            _purge_loadtest_data()
            n = cleanup_merchants()
            print(f"\ncleanup: {n} merchant rows + seeded "
                  f"orders/events/nudge/vps purged (prefix-scoped)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
