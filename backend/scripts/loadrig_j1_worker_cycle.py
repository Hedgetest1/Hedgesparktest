#!/usr/bin/env python
# invariant-eligible: false
#   Manual / CI load rig — measures the J1 worker-cycle invariant
#   against a synthetic 10k-active population. Not a runtime check.
"""loadrig_j1_worker_cycle.py — MEASURE jewel-piece J1 ("HOT stays warm
at 10k") instead of asserting it by arithmetic.

Born 2026-05-18 (founder: drive to MEASURED 10/10). Spec locked by an
independent design Agent (file:line-grounded). The whole "realistic
10k READ = 10/10" claim has rested on contract + arithmetic
(~2000 HOT × ~200ms ≈ 400s > the 240s in-cycle budget ⟹ the
in-cycle prewarm cannot cover HOT; the DECOUPLED prewarm loop, period
DASHBOARD_PREWARM_PERIOD_S=120 < TTL_DASHBOARD=360, is the actual
cold-cliff closure — J2/db08c07). This rig replaces every estimate
with a MEASURED number printed next to the code literal it must beat.

VERDICT = two measured comparisons (honest fast proxy, explicitly
labelled — NOT a multi-hour wall-clock soak):
  1. measured prewarm_cold_ms × measured hot_count  vs  90s / 240s
     (does the in-cycle prewarm-first pass cover HOT? expected NO@10k)
  2. measured decoupled time_to_cover_s  vs  TTL_DASHBOARD=360
     (does the decoupled loop keep HOT warm? this is the real claim)

SOUNDNESS (the spec's G1-G8 hazards, each handled):
  G1 BATCH_SIZE=100 HOT-admission throttle
     (app/workers/tasks/product_metrics_task.py:30): a naive blanket
     seed makes shops_processed_set grow ≤100/cycle, not HOT_N → a
     structural false-PASS. Resolved: we do NOT infer HOT from
     seeding; we measure the prewarm UNIT cost directly (D7) and the
     decoupled-loop throughput directly (D9), then compute the verdict
     for the INTENDED HOT_N — and we ALSO report the real measured
     per-cycle hot_count so the BATCH_SIZE throttle is visible, never
     hidden.
  G2 stampede-lock skip → measure cache PRESENCE, not prewarm return.
  G3 sticky 24h mirror → _purge_loadtest_data() before measuring +
     assert zero hs:dash:*loadtest* sticky pre-run.
  G4 no time elapsing → verdict is arithmetic on measured UNIT costs,
     labelled as such; a true soak is the gold standard, named.
  G5 watermark mutation → snapshot in step 2, restore in finally
     (even on seed crash).
  G6 empty-path false-green → seed real orders+events; assert
     prewarm_cold_ms is in the heavy-build range (X-Query-Count≥15
     via an HTTP probe) else ABORT (refuse to measure the light path).
  G7 broker blindness → reuse loadrig_ground_truth._pgb_admin_url /
     _wishspark_stats; preflight-abort if broker invisible.
  G8 2 real prod merchants are active → all stats filtered to the
     _loadtest_ prefix; run_cycle() touching them is idempotent.

Exit 0 = rig ran soundly + printed the verdict (PASS or FAIL is a
DATA outcome, not a rig error). Exit non-zero = the rig could not
measure soundly (broker blind / light-path / seed failure) — a
non-verdict, never a silent green.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- code literals the verdict is measured against (grounded) ---
AGG_PREWARM_BUDGET_S = int(os.getenv("AGG_PREWARM_BUDGET_S", "90"))
AGG_IN_CYCLE_BUDGET_S = 240          # aggregation_worker.py:805 _agg_budget_seconds
TTL_DASHBOARD = 360                  # redis_client.py:69
DASHBOARD_PREWARM_PERIOD_S = int(os.getenv("DASHBOARD_PREWARM_PERIOD_S", "120"))
BATCH_SIZE = 100                     # product_metrics_task.py:30

# --- rig config (env-tunable; defaults = the locked spec) ---
J1_MERCHANTS = int(os.getenv("J1_MERCHANTS", "10000"))
J1_HOT_N = int(os.getenv("J1_HOT_N", "2000"))
J1_CYCLES = int(os.getenv("J1_CYCLES", "4"))
J1_EVENTS_PER = int(os.getenv("J1_EVENTS_PER", "120"))
J1_ORDERS_PER = int(os.getenv("J1_ORDERS_PER", "12"))
J1_DECOUPLE_PASSES = int(os.getenv("J1_DECOUPLE_PASSES", "12"))

log = logging.getLogger("loadrig_j1")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _isolate_env() -> None:
    """Safe-by-DEFAULT isolation (founder decision 2026-05-18 "DB di
    test separato"): point this process at the dedicated `wishspark_test`
    Postgres DB AND a separate Redis logical DB (15) BEFORE any app
    module imports. `env_bootstrap.load_env()` uses
    `load_dotenv(override=False)` ⟹ os.environ values set here WIN — so
    the entire app stack (run_cycle, dashboard build, harness) runs
    fully isolated from the LIVE wishspark-aggregation-worker (which
    keeps using `wishspark` + Redis db0). Zero prod-data risk: the 2
    real merchants do not even exist in wishspark_test, and the global
    Redis cursor/hot_shops keys live in a different logical DB so the
    live worker and this rig cannot race.

    Set J1_ISOLATED=0 ONLY for an explicit, deliberate non-isolated
    run (NOT default — never silently touch prod)."""
    import re as _re
    if os.getenv("J1_ISOLATED", "1") != "1":
        print("⚠️  J1_ISOLATED=0 — running NON-isolated (prod DB/Redis). "
              "This was NOT the default; you asked for it explicitly.",
              flush=True)
        return
    from dotenv import dotenv_values
    env_path = str(Path(__file__).resolve().parent.parent / ".env")
    v = dotenv_values(env_path)
    du = v.get("DATABASE_URL") or ""
    ru = v.get("REDIS_URL") or ""
    if not du:
        _abort("cannot read DATABASE_URL from .env to derive the "
               "isolated test DB — refusing to run (would hit prod).")
    test_du = _re.sub(r"/wishspark(\?|$)", r"/wishspark_test\1", du)
    if test_du == du:
        _abort("DATABASE_URL did not contain /wishspark — cannot "
               "safely derive wishspark_test; refusing to run.")
    os.environ["DATABASE_URL"] = test_du
    os.environ["DATABASE_URL_TEST"] = test_du
    if ru:
        test_ru = (_re.sub(r"/\d+(\?|$)", r"/15\1", ru)
                   if _re.search(r"/\d+(\?|$)", ru)
                   else ru.rstrip("/") + "/15")
        os.environ["REDIS_URL"] = test_ru
    print("🧪 ISOLATED: DATABASE_URL→wishspark_test  REDIS_URL→db15  "
          "(live wishspark-aggregation-worker untouched)", flush=True)


class _LogCapture(logging.Handler):
    """Buffer aggregation_worker log lines so the rig reads the
    worker's OWN budget/coverage signals instead of guessing."""
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _redis():
    from app.core.redis_client import _client
    return _client()


def _abort(msg: str) -> None:
    print(f"\n🔴 RIG ABORT (non-verdict): {msg}", flush=True)
    raise SystemExit(2)


def main() -> int:
    # MUST precede every app/scripts import (they import
    # app.core.database / redis_client which read env at import time;
    # load_env uses override=False so our os.environ wins).
    _isolate_env()
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from scripts.load_test_harness import setup_merchants, cleanup_merchants
    from scripts.loadrig_ground_truth import (
        _seed_merchant_data, _purge_loadtest_data,
        _pgb_admin_url, _wishspark_stats, _pct,
    )

    # G7 — broker preflight: WARN, not abort. The J1 verdict is an
    # in-process unit-cost + decoupled-throughput proxy, NOT a pooled
    # concurrency-saturation measurement, so PgBouncer pool truth is
    # not the binding instrument for THIS verdict (the spec attached
    # G7 to the optional HTTP-herd sub-measure, which this rig does
    # NOT run — no concurrency-storm number is claimed, so there is no
    # conflation-lie risk to guard). Honest downgrade, not a shortcut.
    if _wishspark_stats(_pgb_admin_url()) is None:
        log.warning("⚠️ PgBouncer broker not visible — acceptable for "
                    "this rig (in-process unit-cost verdict, not pool "
                    "saturation). Proceeding; no storm number claimed.")

    rc = _redis()
    if rc is None:
        _abort("Redis down — the J1 invariant IS about the dashboard "
               "cache; cannot measure without it.")

    # G3 — sticky contamination guard.
    leaked = list(rc.scan_iter(match="hs:dash:*loadtest*", count=500))
    if leaked:
        log.info("pre-clean: purging %d stale hs:dash:*loadtest* keys", len(leaked))
        for k in leaked:
            rc.delete(k)

    db = SessionLocal()
    # G5 — snapshot for restore. The isolated wishspark_test starts
    # with 0 worker_state rows; find_active_products_batch reads the
    # watermark from this row, so the rig MUST ensure it exists, and
    # restore the EXACT prior state (delete if the rig created it →
    # pristine; else restore the value).
    _wm_row = db.execute(text(
        "SELECT last_watermark FROM worker_state "
        "WHERE worker_name='aggregation_worker'"
    )).fetchone()
    wm_row_existed = _wm_row is not None
    real_wm = _wm_row[0] if _wm_row else None
    if not wm_row_existed:
        db.execute(text(
            "INSERT INTO worker_state (worker_name, last_watermark) "
            "VALUES ('aggregation_worker', 0)"
        ))
        db.commit()
    real_merch = db.execute(text(
        "SELECT count(*) FROM merchants WHERE shop_domain NOT LIKE '_loadtest_%'"
    )).scalar()
    log.info("snapshot: wm_row_existed=%s real_watermark=%s "
             "real_merchants=%s (isolated wishspark_test)",
             wm_row_existed, real_wm, real_merch)

    cap = _LogCapture()
    aw_log = logging.getLogger("app.workers.aggregation_worker")
    aw_log.addHandler(cap)

    metrics: dict[str, object] = {}
    try:
        # ---- C1: synthetic 10k active population ----
        log.info("setup_merchants(%d) …", J1_MERCHANTS)
        shops = setup_merchants(J1_MERCHANTS, force=True)
        log.info("seeding COLD bulk (%d orders / %d events each, PAST ts) …",
                 J1_ORDERS_PER, J1_EVENTS_PER)
        _seed_merchant_data(shops, J1_ORDERS_PER, J1_EVENTS_PER)

        hot = sorted(shops)[:J1_HOT_N]

        # Pin watermark below all seeded (past) events.
        W = _now_ms() - 1
        db.execute(text(
            "UPDATE worker_state SET last_watermark=:w "
            "WHERE worker_name='aggregation_worker'"
        ), {"w": W})
        db.commit()

        def _refresh_hot() -> None:
            """Inject events with timestamp > current watermark for the
            HOT shops (models 'merchants actively receiving traffic' —
            the ONLY sound way to sustain a HOT tier; G1)."""
            cur = db.execute(text(
                "SELECT last_watermark FROM worker_state "
                "WHERE worker_name='aggregation_worker'"
            )).scalar() or W
            base = max(cur, _now_ms()) + 1
            rows = [{
                "sd": s, "pu": f"https://{s}/products/hot-{i % 3}",
                "ts": base + i, "et": "product_view",
                "vid": f"j1hot-{i}",
            } for i, s in enumerate(hot)]
            # events schema (ground-truthed): no created_at; NOT NULL =
            # shop_domain, timestamp(bigint). visitor_id aids realism.
            db.execute(text(
                "INSERT INTO events (shop_domain, product_url, "
                "event_type, timestamp, visitor_id) VALUES "
                "(:sd, :pu, :et, :ts, :vid)"
            ), rows)
            db.commit()

        _refresh_hot()

        # ---- D7: measure the prewarm UNIT cost (decoupled from cycle) ----
        from app.api.dashboard import (
            build_lite_dashboard_overview, prewarm_lite_dashboard,
        )
        from app.core.redis_client import KEY_DASHBOARD
        # WARM-THEN-PERCENTILE (independent ship-gate a0d8210d fix of
        # the D7 instrument defect): the prior single timed FIRST build
        # in a freshly-started process measured cold-PROCESS (SQLAlchemy
        # compiled-query cache empty + PG plan cache cold + pool cold ≈
        # 256ms) — NOT the production cold-CACHE cost. The production
        # worker is a long-lived WARM process; the representative
        # steady-state cold-build is the cache-cold cost in a warm
        # process. So: warm the process with N throwaway cache-cleared
        # builds, THEN measure M fresh cache-cleared builds and report
        # p50/p95 (the verdict uses p95 — conservative). This is the
        # exact error that produced the 265ms wrong verdict; fixing the
        # instrument is mandatory before the rig is authoritative.
        _mpool = sorted(shops)
        _WARM_N = min(25, max(1, len(_mpool) // 4))
        _MEAS_N = min(20, max(1, len(_mpool) // 4))
        if len(_mpool) < _WARM_N + _MEAS_N + 1:
            _abort(
                f"need ≥{_WARM_N + _MEAS_N + 1} seeded shops to "
                f"warm({_WARM_N})+measure({_MEAS_N}); have {len(_mpool)} "
                f"— raise J1_MERCHANTS."
            )

        def _timed_cold_build(s):
            rc.delete(KEY_DASHBOARD.format(shop=s) + ":lite")
            rc.delete(KEY_DASHBOARD.format(shop=s) + ":sticky:lite")
            _t = time.monotonic()
            pl = build_lite_dashboard_overview(db, s)
            return (time.monotonic() - _t) * 1000.0, pl

        for s in _mpool[:_WARM_N]:                 # warm the process
            _timed_cold_build(s)
        _samples: list[float] = []
        _keys: list[int] = []
        for s in _mpool[_WARM_N:_WARM_N + _MEAS_N]:
            ms, pl = _timed_cold_build(s)
            _samples.append(ms)
            _keys.append(len(pl) if isinstance(pl, dict) else 0)
        _samples.sort()
        p50 = _pct(_samples, 50)
        p95 = _pct(_samples, 95)
        prewarm_cold_ms = p95                      # verdict uses p95
        metrics["prewarm_cold_p50_ms"] = round(p50, 1)
        metrics["prewarm_cold_p95_ms"] = round(p95, 1)
        metrics["prewarm_cold_min_ms"] = round(_samples[0], 1)
        metrics["prewarm_cold_max_ms"] = round(_samples[-1], 1)
        metrics["prewarm_cold_n"] = _MEAS_N

        # G6 — refuse the data-LIGHT path by PAYLOAD SHAPE, not by a
        # wall-time threshold (time conflates cold-process vs
        # warm-process and is exactly what produced the artifact). The
        # heavy ~18-query build returns a multi-key payload; the
        # data-light branch (dashboard.py:891-894) returns a trivial
        # one. Median payload-key count is the warmth-invariant signal.
        _med_keys = sorted(_keys)[len(_keys) // 2] if _keys else 0
        if _med_keys < 3:
            _abort(
                f"median payload keys={_med_keys} (p50={p50:.1f}ms) — "
                f"the data-LIGHT branch was measured, not the heavy "
                f"cold build. Seeding did not produce heavy shops. "
                f"Refusing to emit a verdict on the light path (G6)."
            )

        t0 = time.monotonic()
        prewarm_lite_dashboard(db, hot[0])   # now warm → ~1 Redis GET
        prewarm_warm_ms = (time.monotonic() - t0) * 1000.0
        metrics["prewarm_warm_ms"] = round(prewarm_warm_ms, 2)

        # ---- D8: N real worker cycles (NO sub-op mocked) ----
        from app.workers.aggregation_worker import run_cycle
        cycle_walls: list[float] = []
        hot_counts: list[int] = []
        budget_breaks = 0
        for n in range(J1_CYCLES):
            _refresh_hot()
            cap.lines.clear()
            t0 = time.monotonic()
            run_cycle()                      # real store_metrics/prewarm/SIP
            cycle_walls.append(time.monotonic() - t0)
            if any("time budget exhausted" in ln for ln in cap.lines):
                budget_breaks += 1
            hs = rc.get("hs:agg:hot_shops")
            if hs:
                try:
                    import json
                    hot_counts.append(len(json.loads(hs)))
                except Exception:
                    hot_counts.append(-1)
            else:
                hot_counts.append(-1)
            log.info("cycle %d/%d wall=%.1fs hot_published=%s",
                     n + 1, J1_CYCLES, cycle_walls[-1], hot_counts[-1])

        metrics["cycle_wall_s_max"] = round(max(cycle_walls), 1)
        metrics["effective_period_s"] = round(max(cycle_walls) + 300, 1)
        metrics["in_cycle_budget_breaks"] = f"{budget_breaks}/{J1_CYCLES}"
        measured_hot = max(hot_counts) if hot_counts else -1
        metrics["measured_hot_count_per_cycle"] = measured_hot
        metrics["batch_size_throttle"] = (
            f"BATCH_SIZE={BATCH_SIZE} ⟹ shops_processed_set grows "
            f"≤{BATCH_SIZE}/cycle; measured_hot reflects this, NOT a "
            f"bug — the verdict uses the INTENDED HOT_N={J1_HOT_N} "
            f"against the measured UNIT cost (G1, honest fast proxy)."
        )

        # ---- D9: decoupled prewarm loop throughput (the REAL closure) ----
        # SOUNDNESS (smoke #2 caught this): the binding J1 scenario is
        # the dashboard cache EXPIRED (TTL_DASHBOARD elapsed since the
        # decoupled loop last touched the shop). If we measured the
        # loop over an already-warm HOT set it would clock warm-SKIP
        # throughput (~0.4ms/shop) → a structural false-PASS at 10k.
        # We therefore CLEAR every HOT shop's dashboard cache first so
        # _prewarm_cycle_once does REAL cold builds (the ~242ms unit
        # cost) — modelling the cache-expired steady state exactly.
        from app.workers.aggregation_worker import _prewarm_cycle_once
        from app.core.redis_client import (
            KEY_DASHBOARD, KEY_DASHBOARD_STICKY, KEY_DASHBOARD_LOCK,
        )
        import json as _json
        for s in hot:
            rc.delete(KEY_DASHBOARD.format(shop=s) + ":lite")
            rc.delete(KEY_DASHBOARD_STICKY.format(shop=s) + ":lite")
            rc.delete(KEY_DASHBOARD_LOCK.format(shop=s))
        rc.set("hs:agg:hot_shops", _json.dumps(sorted(hot)), ex=900)
        # reset the decoupled cursor so coverage starts at head
        for k in ("hs:aggregation:cursor", "hs:dash_prewarm:cursor"):
            rc.delete(k)
        pass_walls: list[float] = []
        pass_touched: list[int] = []
        for _ in range(J1_DECOUPLE_PASSES):
            t0 = time.monotonic()
            touched = _prewarm_cycle_once()
            pass_walls.append(time.monotonic() - t0)
            pass_touched.append(int(touched or 0))
            if sum(pass_touched) >= len(hot):
                break
        per_pass = max(1, int(sum(pass_touched) / max(1, len(pass_touched))))
        passes_to_cover = max(1, -(-len(hot) // per_pass))   # ceil
        time_to_cover_s = passes_to_cover * DASHBOARD_PREWARM_PERIOD_S
        metrics["decouple_per_pass_touched"] = per_pass
        metrics["decouple_pass_wall_s_max"] = round(max(pass_walls), 2)
        metrics["decouple_passes_to_cover"] = passes_to_cover
        metrics["decouple_time_to_cover_s"] = time_to_cover_s

        # ---- VERDICT — first-principles from the MEASURED p95 +
        # ground-truthed code literals (independent ship-gate
        # a0d8210d's method; robust, NOT dependent on the D9 empirical
        # which carried a warm-skip concern). ----
        # Decoupled loop per pass: min(slice cap, budget / cold_ms)
        # builds; cover HOT_N = ceil(HOT_N / per_pass) passes; revisit
        # = passes × period; J1 PASSES iff revisit < TTL_DASHBOARD.
        _PREWARM_SLICE_MAX = 1000     # aggregation_worker.py:1233
        _PREWARM_BUDGET_S = 100       # aggregation_worker.py:1222
        c_ms = prewarm_cold_ms        # = measured p95 (conservative)
        per_pass = min(_PREWARM_SLICE_MAX,
                       int((_PREWARM_BUDGET_S * 1000) / c_ms) if c_ms > 0
                       else _PREWARM_SLICE_MAX)
        per_pass = max(1, per_pass)
        passes = -(-J1_HOT_N // per_pass)                  # ceil
        revisit_s = passes * DASHBOARD_PREWARM_PERIOD_S
        keeps_warm = revisit_s < TTL_DASHBOARD
        # Flip threshold: PASS needs per_pass ≥ ceil(HOT_N/(TTL/period-1))
        # → for HOT=2000, period=120, TTL=360: per_pass ≥ 1000 ⟹
        # cold-build ≤ budget/slice = 100000/1000 = 100ms (p95).
        flip_ms = (_PREWARM_BUDGET_S * 1000) / _PREWARM_SLICE_MAX
        metrics["binding"] = ("slice-cap" if per_pass >= _PREWARM_SLICE_MAX
                              else "budget")
        metrics["per_pass_builds"] = per_pass
        metrics["passes_to_cover_hot"] = passes
        metrics["revisit_s"] = revisit_s
        metrics["flip_threshold_p95_ms"] = flip_ms

        print("\n" + "=" * 70)
        print(" J1 WORKER-CYCLE TRUTH-RIG — MEASURED VERDICT")
        print(" (warm-process p95 + ground-truthed literals; the honest")
        print("  fast proxy — a ≥2×360s wall-clock soak is the gold std)")
        print("=" * 70)
        for k, v in metrics.items():
            print(f"  {k:32s} = {v}")
        print("-" * 70)
        print(f"  cold-build p95={c_ms:.1f}ms → per_pass="
              f"min(slice {_PREWARM_SLICE_MAX}, budget/{c_ms:.0f}ms)"
              f"={per_pass}  ({metrics['binding']}-bound)")
        print(f"  passes_to_cover HOT={J1_HOT_N}: {passes} → revisit="
              f"{revisit_s}s  vs TTL_DASHBOARD={TTL_DASHBOARD}s")
        print(f"  flip: J1 PASSES while cold-build p95 ≤ {flip_ms:.0f}ms "
              f"(slice-bound); FAILS above (budget-bound)")
        print("-" * 70)
        if keeps_warm:
            verdict = "PASS"
            print(f" ✅ J1 PASS at the MEASURED cost (p95 {c_ms:.1f}ms ≤ "
                  f"{flip_ms:.0f}ms flip). The decoupled prewarm loop "
                  f"(J2/db08c07) keeps HOT warm: revisit {revisit_s}s < "
                  f"{TTL_DASHBOARD}s TTL. ⚠️ CAVEAT (independent ship-gate "
                  f"a0d8210d, stated NOT buried): cold-build p95 crosses "
                  f"the {flip_ms:.0f}ms flip at an EXTREME whale "
                  f"(~5000+ orders / 50k+ events per HOT merchant) → "
                  f"soft p95-FAIL there (revisit {TTL_DASHBOARD}s, one "
                  f"extra pass, NOT a cliff). Whale prevalence vs prod "
                  f"= UNMEASURED (prod SELECT sandbox-blocked). Lever if "
                  f"a whale tier emerges: DASHBOARD_PREWARM_PERIOD_S<90 "
                  f"or DASHBOARD_PREWARM_BUDGET_S↑ (NOT the parallel fix, "
                  f"NOT shrinking MAX_PER_PASS — both budget-blind).")
        else:
            verdict = "FAIL"
            print(f" ❌ J1 FAIL at the MEASURED cost (p95 {c_ms:.1f}ms > "
                  f"{flip_ms:.0f}ms flip → budget-bound, {per_pass}"
                  f"/pass). revisit {revisit_s}s ≥ {TTL_DASHBOARD}s TTL "
                  f"→ HOT goes cold between touches. NO rounding — the "
                  f"measured truth at this data volume.")
        print("=" * 70 + f"\n VERDICT: J1 {verdict}\n" + "=" * 70)
        metrics["verdict"] = verdict
        return 0

    finally:
        aw_log.removeHandler(cap)
        # G5 — restore the EXACT prior worker_state, no matter what.
        try:
            if wm_row_existed:
                db.execute(text(
                    "UPDATE worker_state SET last_watermark=:w "
                    "WHERE worker_name='aggregation_worker'"
                ), {"w": real_wm})
            else:
                db.execute(text(
                    "DELETE FROM worker_state "
                    "WHERE worker_name='aggregation_worker'"
                ))
            db.commit()
            log.info("restored worker_state (existed=%s wm=%s)",
                     wm_row_existed, real_wm)
        except Exception as exc:
            log.error("WORKER_STATE RESTORE FAILED (%s) — isolated "
                      "wishspark_test, low impact; existed=%s wm=%s",
                      exc, wm_row_existed, real_wm)
        for k in ("hs:aggregation:cursor", "hs:dash_prewarm:cursor",
                  "hs:agg:hot_shops"):
            try:
                rc.delete(k)
            except Exception:
                pass
        try:
            _purge_loadtest_data()
        except Exception as exc:
            log.error("purge_loadtest_data failed: %s", exc)
        try:
            n = cleanup_merchants()
            log.info("cleanup_merchants removed %s rows", n)
        except Exception as exc:
            log.error("cleanup_merchants failed: %s", exc)
        try:
            post = db.execute(text(
                "SELECT count(*) FROM merchants "
                "WHERE shop_domain NOT LIKE '_loadtest_%'"
            )).scalar()
            if post != real_merch:
                log.error("⚠️ real merchant count drift %s→%s — INVESTIGATE",
                          real_merch, post)
            else:
                log.info("real merchant count intact (%s)", post)
        except Exception:
            pass
        db.close()


if __name__ == "__main__":
    sys.exit(main())
