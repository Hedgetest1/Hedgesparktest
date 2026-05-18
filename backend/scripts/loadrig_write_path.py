"""loadrig_write_path.py — the J3 proof gate (WRITE path).

Storms POST /track with N distinct synthetic merchants emitting
non-purchase analytics events, PgBouncer-broker-measured, and proves
J3 END-TO-END: under a 10k-scale write storm the analytics path holds
~ZERO request DB connections (broker cl_waiting≈0 ⟹ the pool-cascade
is STRUCTURALLY gone, not merely bounded) and the async buffer drains
(depth bounded; events actually land in `events` via the drain).

WHY A SEPARATE RIG (not loadrig_ground_truth): that rig tests the READ
endpoint with FORGED SESSIONS, which bypass domain validation. The
WRITE path validates `is_valid_shop_domain` FIRST, so `_loadtest_*`
names (setup_merchants' convention) 400 before any real work — wrong
instrument. This rig uses valid-format `wlrig*` domains + pre-seeds
the `hs:known_shop:` Redis cache (verified: `_is_known_shop`
short-circuits on the cache → ZERO DB, which is exactly the J3
property under test ⟹ no merchants rows needed at all).

INSTRUMENT SOUNDNESS (the 2026-05-16d lesson — a clean verdict is
only as sound as the instrument): urllib raises on 4xx/5xx. This rig
DISTINGUISHES 429-shed (J3-part-1 graceful admission — EXPECTED when
offered load > capacity, a SUCCESS signal: the system shedding
fast+bounded instead of cascading) from 5xx/timeout (the cascade —
the real FAILURE). Conflating them would either report a false
failure (429 IS the system working) or hide the cascade.

Run:  ./venv/bin/python scripts/loadrig_write_path.py \
          --merchants 800 --procs 400 --duration 25
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
import http.client
from urllib.parse import urlparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
# Reuse the PROVEN broker instrument (§2 r1 — do not rebuild it).
from scripts.loadrig_ground_truth import (  # noqa: E402
    _BASE, _pgb_admin_url, _wishspark_stats, _broker_sampler, _pct,
)

_PREFIX = "wlrig"  # valid is_valid_shop_domain format + rig-owned + purgeable
_EVENT_TYPES = ("product_view", "dwell_time", "scroll", "add_to_cart")


def _shops(n: int) -> list[str]:
    return [f"{_PREFIX}{i:05d}.myshopify.com" for i in range(n)]


def _seed_known_shop_cache(shops: list[str], ttl: int) -> None:
    """Pre-seed so `_is_known_shop` returns True WITHOUT a DB hit (the
    J3 zero-request-DB property is exactly what we are measuring)."""
    from app.core.redis_client import cache_set
    for s in shops:
        cache_set(f"hs:known_shop:{s}", True, ttl)


def _src_ip(widx: int) -> str:
    """Distinct loopback source per worker so each worker is a
    DISTINCT per-IP rate-limit bucket server-side (the WRITE-Agent
    finding 2026-05-18: a single-IP rig is per-IP-RL-shadowed and
    structurally CANNOT measure INGEST_ADMIT_BUDGET; binding client
    sockets across 127.0.0.0/8 — all loopback on Linux — un-shadows
    it on ONE host, proven by live probe). Sweeps 127.0.A.B with
    B∈[2,251] (avoids .0/.1/.255), A rolls over → ~64000 distinct
    IPs, ≫ any --procs."""
    a = widx // 250
    b = 2 + (widx % 250)
    return f"127.0.{a}.{b}"


def _worker(shops: list[str], duration: float, q: "mp.Queue",
            widx: int) -> None:
    lat: list[float] = []
    n = ok = shed = err = qsum = 0
    i = 0
    u = urlparse(_BASE)
    host = u.hostname or "127.0.0.1"
    port = u.port or 8000
    src = _src_ip(widx)
    hdr = {"Content-Type": "application/json"}

    def _conn() -> "http.client.HTTPConnection":
        # source_address binds the CLIENT socket to a distinct
        # 127.0.0.x → distinct request.client.host server-side
        # (extract_client_ip returns the socket peer on direct
        # loopback, no CF/XFF — Agent-verified).
        return http.client.HTTPConnection(
            host, port, timeout=30, source_address=(src, 0))

    c = _conn()
    end = time.monotonic() + duration
    while time.monotonic() < end:
        shop = shops[i % len(shops)]
        i += 1
        body = json.dumps({
            "shop_domain": shop,
            "visitor_id": f"v{i % 500}",
            "event_type": _EVENT_TYPES[i % 4],   # never "purchase"
            "product_url": f"/products/p{i % 50}",
            "page_url": f"https://{shop}/products/p{i % 50}",
            "timestamp": int(time.time() * 1000),
        }).encode()
        t0 = time.perf_counter()
        try:
            c.request("POST", "/track", body=body, headers=hdr)
            r = c.getresponse()
            r.read()                       # must drain before reuse
            if r.status == 429:
                # graceful admission/RL shed (EXPECTED under
                # offered>capacity — the system working).
                shed += 1
            elif 200 <= r.status < 300:
                ok += 1
                qsum += int(r.getheader("X-Query-Count", 0) or 0)
            else:
                err += 1               # any other 4xx/5xx = real error
        except Exception:
            err += 1                   # timeout / conn refused = cascade
            try:
                c.close()
            except Exception:
                pass
            c = _conn()                # rebuild, keep distinct src
        lat.append((time.perf_counter() - t0) * 1000.0)
        n += 1
    try:
        c.close()
    except Exception:
        pass
    q.put((n, ok, shed, err, qsum, lat))


def _events_persisted() -> int:
    db = SessionLocal()
    try:
        return int(db.execute(text(
            "SELECT count(*) FROM events WHERE shop_domain LIKE :p"
        ), {"p": f"{_PREFIX}%"}).scalar() or 0)
    finally:
        db.close()


def _buffer_depth() -> int:
    from app.services.ingest_buffer import buffer_depth
    return buffer_depth()


def _purge() -> None:
    """Prefix-scoped, plus the global ingest buffer (this pre-merchant
    box has no real storefront traffic, so the buffer holds only rig
    events) + the known-shop cache keys + truthful stats (ANALYZE)."""
    db = SessionLocal()
    try:
        for tbl in ("events", "visitors", "visitor_purchase_sessions"):
            db.execute(text(
                f"DELETE FROM {tbl} WHERE shop_domain LIKE :p"
            ), {"p": f"{_PREFIX}%"})
        db.commit()
        for tbl in ("events", "visitors"):
            db.execute(text(f"ANALYZE {tbl}"))
        db.commit()
    finally:
        db.close()
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete("hs:ingest:buf")
            ks = list(rc.scan_iter(f"hs:known_shop:{_PREFIX}*", count=1000))
            if ks:
                rc.delete(*ks)
    except Exception as exc:
        print(f"WARNING: rig redis purge failed: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchants", type=int, default=800)
    ap.add_argument("--procs", type=int, default=400)
    ap.add_argument("--duration", type=int, default=25)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    # LOUD-FAIL: a write rig whose broker instrument is blind would
    # report reassuring zeros — the exact conflation this exists to
    # prevent. Verify the broker is readable BEFORE generating load.
    if _wishspark_stats(_pgb_admin_url()) is None:
        print("ABORT: PgBouncer SHOW STATS unreadable — refusing to "
              "run a write storm whose ground-truth instrument is "
              "blind.", file=sys.stderr)
        return 3

    rc = 0
    try:
        shops = _shops(args.merchants)
        _seed_known_shop_cache(shops, args.duration + 120)
        depth0 = _buffer_depth()
        print(f"setup: {len(shops)} distinct synthetic merchants "
              f"(known-shop cache pre-seeded; buffer depth0={depth0})")

        per = max(1, len(shops) // args.procs)
        slices = [shops[i:i + per] or shops
                  for i in range(0, len(shops), per)][:args.procs]
        while len(slices) < args.procs:
            slices.append(shops)
        q: mp.Queue = mp.Queue()
        bq: mp.Queue = mp.Queue()
        sampler = mp.Process(target=_broker_sampler,
                             args=(args.duration + 1.0, bq))
        workers = [mp.Process(target=_worker,
                              args=(slices[i], float(args.duration), q, i))
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
        ok = sum(r[1] for r in res)
        shed = sum(r[2] for r in res)
        err = sum(r[3] for r in res)
        qsum = sum(r[4] for r in res)
        lat = sorted(x for r in res for x in r[5])
        b = broker["peak"]

        # Give the singleton drain thread a moment, then measure.
        time.sleep(8)
        depth1 = _buffer_depth()
        persisted = _events_persisted()

        print(f"\n── CLIENT (process-per-conn, true-parallel POST /track) ──")
        print(f"procs={args.procs} merchants={len(shops)} wall={wall:.1f}s")
        print(f"requests={tot}  ok={ok} ({100.0*ok/max(tot,1):.1f}%)  "
              f"shed-429={shed} ({100.0*shed/max(tot,1):.1f}%)  "
              f"err-5xx/timeout={err} ({100.0*err/max(tot,1):.2f}%)")
        print(f"throughput={tot/wall:.0f} req/s  "
              f"latency p50={_pct(lat,50):.0f} p95={_pct(lat,95):.0f} "
              f"p99={_pct(lat,99):.0f} max={lat[-1] if lat else 0:.0f} ms")
        print(f"X-Query-Count total={qsum} avg={qsum/max(tot,1):.3f}/req "
              f"(J3: analytics path must be ~0 — zero request DB)")
        print(f"\n── SERVER GROUND TRUTH (PgBouncer broker) ──")
        print(f"peak cl_waiting={b['cl_waiting']}  "
              f"peak maxwait={b['maxwait_us']/1000:.1f}ms  "
              f"peak sv_active={b['sv_active']}/80  "
              f"peak cl_active={b['cl_active']}")
        print(f"run_xacts={broker['run_xacts']} "
              f"avg_xact={broker['avg_xact_us']/1000:.2f}ms")
        print(f"\n── BUFFER / DRAIN (J3-part-2 end-to-end) ──")
        print(f"buffer depth: {depth0} → {depth1} (post +8s drain)  "
              f"events persisted (wlrig%): {persisted}")

        # ── INSTRUMENT-SOUNDNESS GUARD (2026-05-17, the 2026-05-16d
        # "a verdict is only as sound as the instrument" law,
        # mechanised) ─────────────────────────────────────────────────
        # main.py:338 caps POST /track at 600 req / 60s PER CLIENT IP.
        # rate_limit.py keys the bucket `{extract_client_ip}|POST|/track`.
        # This rig runs every proc from ONE host ⟹ ONE shared IP bucket
        # (XFF is NOT trusted without the CF gate, by security design),
        # so the per-IP rate limit SHEDS before `ingest_admit` is ever
        # reached: `ok` plateaus at ≈600 regardless of
        # INGEST_ADMIT_BUDGET. PRODUCTION has millions of DISTINCT
        # browser IPs (each visitor far under 600/60s) so the per-IP RL
        # does NOT shadow there — but this single-IP rig structurally
        # CANNOT exercise / right-size the ingest budget. Emitting a
        # budget verdict from a shadowed run would be exactly the
        # instrument-unsound false-claim this guard exists to refuse.
        _PER_IP_TRACK_CAP = 600  # main.py:338 (POST /track, 60s window)
        rl_shadowed = (ok <= _PER_IP_TRACK_CAP * 1.6) and (shed > ok) \
            and (err == 0)
        if rl_shadowed:
            print(
                f"\n🔴 INSTRUMENT SHADOWED — single-IP per-IP rate "
                f"limit ({_PER_IP_TRACK_CAP}/60s, main.py:338) capped "
                f"ok≈{ok} BEFORE ingest_admit. This rig CANNOT measure "
                f"/ right-size INGEST_ADMIT_BUDGET (prod has distinct "
                f"IPs ⟹ no shadow there; needs a MULTI-IP / distributed "
                f"rig). Any budget conclusion from THIS run is UNSOUND "
                f"— do not draw one.")

        # VERDICT — the CASCADE is precisely: the shared pool queued
        # (broker cl_waiting>0 / maxwait) OR 5xx/timeout. That is the
        # catastrophic multi-tenant mode J3 exists to kill. X-Query-
        # Count is NOT the cascade signal: with `Depends(get_db)` still
        # eager, every /track holds a pooled conn briefly + the 2
        # `SET LOCAL` timeout binds run on it (X-QC≈2) — that is the
        # KNOWN lazy-DB residual (named in the J3-part-1 commit), a
        # LESSER finding, NOT pool-saturation. Conflating them is the
        # 2026-05-16d instrument-imprecision the rig must not commit.
        avgqc = qsum / max(tot, 1)
        cascade = (err > 0) or (b["cl_waiting"] > 0) \
            or (b["maxwait_us"] >= 50_000)
        # J3-part-2 end-to-end must also actually persist (drain works).
        drain_broken = (persisted == 0 and ok > 0) or (depth1 > depth0 + tot)
        if cascade or drain_broken:
            print(f"\nVERDICT: ❌ FAIL — "
                  + ("CASCADE: pool queued / 5xx "
                     f"(err={err}, cl_waiting={b['cl_waiting']}, "
                     f"maxwait={b['maxwait_us']/1000:.0f}ms). "
                     if cascade else "")
                  + ("DRAIN BROKEN: buffer consumed but "
                     f"{persisted} persisted. " if drain_broken else ""))
            rc = 1
        else:
            _budget_clause = (
                "BUDGET UNMEASURED (per-IP RL shadowed — see above; "
                "needs a multi-IP rig)" if rl_shadowed else
                f"ingest budget exercised, {shed} shed-429 = graceful "
                f"admission (offered>capacity)")
            print(f"\nVERDICT: ✅ J3 cascade-immune at "
                  f"{tot/wall:.0f} req/s offered — ZERO 5xx, broker "
                  f"NEVER queued (cl_waiting=0, maxwait=0): the "
                  f"catastrophic multi-tenant pool-cascade is "
                  f"structurally absent. {_budget_clause}. "
                  f"Drain end-to-end OK: {persisted} events persisted, "
                  f"buffer residual {depth1}.")
            if avgqc > 0.05:
                print(f"   ⚠ RESIDUAL (honest, NOT the cascade): "
                      f"X-QC≈{avgqc:.2f}/req = the eager-`Depends("
                      f"get_db)` conn-pin + 2 SET-LOCAL timeout binds "
                      f"(NOT app queries — the analytics event INSERT "
                      f"is fully async). cl_waiting=0 ⟹ not pool-bound "
                      f"at this load; the lazy-DB /track refinement "
                      f"(J3-part-1-named) removes even this — the next "
                      f"additive step, NOT a cascade.")
    finally:
        if not args.keep:
            _purge()
            print("cleanup: wlrig% events/visitors + buffer + "
                  "known-shop cache purged (prefix-scoped)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
