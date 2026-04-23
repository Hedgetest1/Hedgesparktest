# Full-stack hardening roadmap (post backend-scaling sprint)

> **Born:** 2026-04-23, after the uvicorn `--workers 4` flip closed the
> backend request-path scaling bottleneck. This document is the ordered
> sequence of remaining scale/reliability weak links, with effort estimates
> and acceptance criteria. Execute in order — each tier assumes the ones
> above it are already shipped.
>
> **Approved tier envelope this session:** `BLANKET_TIER01_TIER02` — any
> task below can ship without per-task approval ceremony, except TIER_2
> files (ecosystem.config.js, migrations/, .env) which require the normal
> TIER_2 escalation per CLAUDE.md §10.
>
> **Companion state:** `SESSION_STATE.md` tracks what's in flight today.
> This document is frozen-at-write and updated only when a tier closes or
> a new bottleneck emerges.

---

## Current scale posture (as of 2026-04-23 after workers-4 flip)

| Layer | State | Capacity today | Next ceiling |
|---|---|---|---|
| Traefik (reverse proxy) | Docker, single instance, HTTP/2, Let's Encrypt | >10k req/s | Single host failure |
| FastAPI backend | uvicorn --workers 4 | ~2-4k req/s | Postgres conn ceiling |
| Next.js dashboard | 1 instance, SSR | ~100 concurrent SSR | CPU-bound on SSR |
| Postgres | Single host, `max_connections=100` | ~80 conn after backend flip | Connection exhaustion past 5k merchants |
| Redis | Single, RDB-only persistence | ~50k ops/s | Durability gap + failover |
| Tracker | Client-side, stateless | Unlimited | n/a |
| Workers (7 singletons) | PM2 fork, per-cycle batches | Sequential | Per-shop latency past 1k merchants |

Bottleneck rank for the next 10× (2 → 20 merchants): **Postgres + Redis** are
the real chokepoints. Dashboard is fine for 100+ merchants. Backend is fine
for 2-5k concurrent.

---

## Tier 1 — Postgres durability & concurrency (highest leverage)

**Why first:** Postgres is the shared substrate for every other layer.
Backend flip pushed us to ~80 conn steady state; past 5k merchants the
conn ceiling starts blocking requests even with the Redis fallbacks in
place. Also: no read replica means analytics queries (RARS, cohort,
retention) contend with tracker-event inserts on the hot path.

**1.1 PgBouncer in transaction mode (~1.5 day)**
- Install PgBouncer (Docker sidecar next to Postgres).
- Point backend `DATABASE_URL` → PgBouncer port (6432 default).
- PgBouncer pools to real Postgres with ~20 server conns total.
- Effect: 4 uvicorn workers × 60 client conn = 240 CLIENT conn to
  PgBouncer, which funnels down to 20 SERVER conn on Postgres.
  Postgres `max_connections=100` becomes pure headroom instead of
  a live ceiling.
- Backend pool config stays `DB_POOL_SIZE=5, DB_MAX_OVERFLOW=10` —
  PgBouncer absorbs the bursts.
- Acceptance: 2000-req burst at `/system/health` still completes in
  ≤25s (vs current 20s); `pg_stat_activity` count stays ≤30 during
  burst.
- Companion test: `tests/test_pgbouncer_compat.py` — covers
  prepared-statement mode incompatibilities (PgBouncer transaction
  pool disables prepared statements by default; any `psycopg2` code
  path using named prepared statements needs the `?pool_mode=session`
  override).

**1.2 Read replica for analytics (~1 day)**
- Provision a Postgres streaming replica (or WAL-based for managed).
- Set `DATABASE_READ_URL` in ecosystem.config.js env block.
- Migrate the 8 call sites already listed in `app/core/database.py`
  comment (roi_hero, cac_ltv, mta, mta_engine, forecasts,
  compliance_evidence, customer_churn_scorer, nudge_dna) to use
  `get_read_db()` / `ReadSession()`.
- Replication lag monitoring: emit ops_alert if `pg_stat_replication`
  lag > 30s.
- Acceptance: primary `pg_stat_activity` drops by 20-30% during peak
  analytics usage; replica shows the expected query mix; every write
  path still uses `SessionLocal()` (guarded by `audit_tenant_isolation`
  + a new `audit_read_replica_writes.py`).

**1.3 Upgrade Postgres `max_connections` to 200 (~30 min)**
- Belt-and-suspenders for the transition period while PgBouncer is
  rolled out. Requires one Postgres restart (~5s downtime).
- Acceptance: preflight smoke + E2E remain green across the restart.

---

## Tier 2 — Redis durability & failover

**Why second:** 40% of HedgeSpark's cross-worker correctness relies on
Redis (LLM budget, rate limiters, cooldowns, snapshot caches, nonces).
Today it's a single Docker container with RDB snapshots only — a host
crash loses up to 1 hour of counter state AND blocks every worker
until it restarts.

**2.1 Enable AOF persistence (~1 hour)**
- Redis `appendonly yes` + `appendfsync everysec`.
- Trade-off: ≤1s data loss on crash vs RDB's up-to-1-hour gap.
- Effect: LLM budget counters survive restarts; rate-limit state
  survives deploys (currently a backend restart drops `_buckets`
  in-process — fine today because Redis is primary, but AOF makes
  Redis itself crash-proof too).
- Acceptance: `redis-cli INFO persistence | grep aof_enabled` = 1;
  Redis restart preserves `hs:llm:monthly_cost:*` keys; preflight +
  tests green.

**2.2 Redis replica + sentinel (~1.5 days)**
- Second Redis container on the same Docker network, replica-of
  primary. Redis Sentinel for automatic failover detection.
- Backend reads `REDIS_URL` normally; on primary failure Sentinel
  promotes replica and clients reconnect.
- Acceptance: kill primary Redis; session-durability E2E suite
  continues to pass (tracks that the Redis failover is transparent
  to the request path).

**2.3 Redis memory policy (~15 min)**
- Verify `maxmemory-policy` set (currently may be default=noeviction,
  which would crash Redis under OOM).
- Set to `allkeys-lru` or `volatile-lru` per the TTL-hygiene rule in
  CLAUDE.md §13 (every HedgeSpark Redis key has a TTL, so
  `volatile-lru` is safe and correct).

---

## Tier 3 — Observability aggregation across workers

**Why third:** The 2026-04-23 audit exposed that `app/core/metrics.py`
is per-worker. `/metrics` endpoint returns 1/4 of real traffic —
monitoring is degraded under multi-worker even though the request path
is correct. Alerts that fire on "traffic volume" or "error rate" read
1/4 of reality.

**3.1 Redis-backed metrics aggregator (~1 day)**
- Replace module-level `_Histogram` / `_Counter` in metrics.py with
  Redis-native equivalents:
  - Histograms → `HINCRBY` on bucket keys (one key per histogram per
    minute window, TTL 1h)
  - Counters → `INCR` with daily TTL
- `/metrics` renders from Redis aggregation, NOT worker-local state.
- Acceptance: 4 concurrent uvicorn workers receiving load each emit
  identical `/metrics` output (modulo timing); external Prometheus
  scrape reflects total fleet traffic.

**3.2 Request ID worker tag (~30 min)**
- Include uvicorn worker PID in the request ID prefix (e.g.
  `req_8042_12345`) so logs can be traced to the specific worker.
- Useful for post-mortem when one worker misbehaves in a fleet.

**3.3 Worker-fleet health endpoint (~1 hour)**
- New `/system/workers` endpoint that returns:
  - master PID + config
  - per-worker PID, uptime, request count from Redis metrics aggregator
  - per-worker connection pool checked-out count
- Surface in dashboard operator ops view.

---

## Tier 4 — Cross-layer circuit breakers

**Why fourth:** HedgeSpark depends on Anthropic, OpenAI, Shopify,
Resend, Klaviyo, Hostinger DNS. Each call site today has ad-hoc retry
+ timeout logic (some good, some not). A formalized circuit-breaker
pattern catches partial-outage gradients (slow responses, not just
hard failures) before they stall workers.

**4.1 `app/core/circuit_breaker.py` shared helper (~0.5 day)**
- Redis-backed circuit state: closed / half-open / open.
- Counts failures + slow responses in a sliding window.
- Opens on N failures in M seconds; half-opens after cooldown.
- Callers wrap external calls in `with circuit_breaker("anthropic"):`.

**4.2 Retrofit the 6 external integrations (~1.5 days)**
- Anthropic LLM calls (`app/core/llm_router.py`)
- OpenAI LLM calls (same file)
- Shopify Admin API (`app/core/shopify_client.py` — already has
  retry + backoff; add breaker for prolonged outages)
- Resend transactional email (`app/services/email_orchestrator.py`)
- Klaviyo export (`app/services/klaviyo_export.py`)
- Hostinger DNS verify (`app/services/email_deliverability.py`)
- Each retrofit preserves existing retry behavior; breaker is an
  OUTER layer that short-circuits when the integration is provably
  down.

**4.3 Dashboard surface for breaker state (~1 hour)**
- `/system/health` includes per-integration breaker state (closed /
  half-open / open).
- Operator sees at a glance which integration is degraded.

---

## Tier 5 — Load-test harness

**Why fifth:** Every prior tier changes the scale model. Without a
reproducible load-test, we can't prove that tier N+1 actually
increases capacity vs tier N.

**5.1 Staging environment (~1 day + ongoing infra cost)**
- Second Docker Compose stack on the same host or a separate VM
  (a €5/month Hetzner CPX11 suffices for load gen).
- Identical config to prod, pointed at a COPY of the prod DB
  (pg_dump + restore on deploy).
- Never receives real merchant traffic.

**5.2 k6 load-test suite (~1 day)**
- `scripts/load_tests/baseline.js` — the repeatable run we measure
  all tier deltas against. Hits `/system/health`, `/merchant/me`,
  `/pro/rars`, `/brief/today` in realistic ratios.
- `scripts/load_tests/burst.js` — 10× spike for 60s, verifies graceful
  degradation not collapse.
- `scripts/load_tests/mixed.js` — 100 virtual merchants, each firing
  a realistic session (oauth → dashboard load → 5 card refreshes →
  brief → idle). Measures TRUE capacity.

**5.3 Pre-deploy gate (~0.5 day)**
- Preflight adds `scripts/load_test_smoke.sh --strict` on commits that
  touch the request path or ecosystem.config.js. 500 req over 10s
  against local dev backend; fails commit if p95 > 200ms or any 5xx.
- Acceptance: zero 5xx across baseline run; p95 within ±10% of prior
  baseline.

---

## Tier 6 — Dashboard SSR scaling (deferrable)

**Why deferrable:** Dashboard is CPU-bound only when 100+ concurrent
merchants actively open new tabs in the same minute. We have 4 today.
At the scale where this matters (>500 merchants), we revisit; for now
the bottleneck doesn't exist.

**6.1 Decision: cluster-mode vs Vercel/Netlify edge (~0.5 day decision)**
- Cluster mode requires sticky-session routing via Traefik (cookie
  hash) since React 19 RSC streaming is connection-bound.
- Edge render moves infrastructure off Hetzner to a CDN — TCO calc
  needed against baseline hosting cost.

**6.2 Implementation (~2 days, whichever path picked)**
- Cluster: `instances: 4` in ecosystem.config.js + Traefik hash
  cookie, verify dashboard session-durability suite still 15/15.
- Edge: migrate routes + build pipeline; bigger change.

---

## Acceptance gates between tiers

Each tier must satisfy before the next starts:

1. **Tier 1 closed** → backend can sustain 1000 req/s on `/merchant/me`
   for 60s without Postgres conn timeouts; session-durability E2E
   15/15 × 3 on staging + prod.
2. **Tier 2 closed** → Redis primary kill leaves request path intact
   for 30s failover window; LLM budget counters survive Redis restart.
3. **Tier 3 closed** → `/metrics` reflects fleet totals, not per-worker
   slice; alert volume calibration no longer off by 4×.
4. **Tier 4 closed** → simulated Anthropic/OpenAI outage returns
   deterministic degradation in ≤10s instead of stalled requests.
5. **Tier 5 closed** → every subsequent deploy produces a comparable
   load-test number; regressions caught at preflight.
6. **Tier 6 shipped** → dashboard CPU headroom proven at 500+ concurrent.

---

## Running totals

| Tier | Effort | Cumulative | New monthly infra cost |
|---|---|---|---|
| 1 Postgres | ~3 days | 3 d | €0 (Docker add-on) or ~€15 (managed replica) |
| 2 Redis | ~3 days | 6 d | €0 (Docker add-on) |
| 3 Observability | ~2 days | 8 d | €0 |
| 4 Circuit breakers | ~2 days | 10 d | €0 |
| 5 Load-test | ~2.5 days | 12.5 d | ~€5 (staging VM) |
| 6 Dashboard SSR | ~2.5 days | 15 d | €0-€20 (depends on path) |

**Total:** ~15 days of focused work to take HedgeSpark from the current
"correct at 2-10 merchants, scaling hardened at the backend" posture
to "verifiable at 5k-10k merchants, automatic failover on every layer".
That's 15 Claude-days, not 15 calendar days — calendar includes
waiting on manual verification steps between tiers.

---

## What's NOT on this roadmap (and why)

- **Kubernetes / multi-host** — not needed until single-host resource
  ceiling hits. That's well past 10k merchants at current usage shape.
  Adding Kubernetes before then is infrastructure-for-its-own-sake.
- **Message queue (RabbitMQ/Redis Streams)** — the 7 singleton PM2
  workers are a simpler correct model for our cadence (5 min / 15 min
  cycles). A queue adds ops surface without closing a real bottleneck.
- **GraphQL gateway** — the REST + typed openapi-fetch contract works.
  No clear win worth the migration cost.
- **Sentry self-hosting** — Sentry SaaS is €26/month for our volume.
  Self-host has never returned positive ROI at this scale.

If any of these become legitimate needs later, they go in Tier 7+
with the same acceptance-gate discipline.

---

## Post-tier state (all 6 shipped)

- Backend: 4 workers × 4 hosts (if we ever horizontally scale via
  cluster mode), circuit-broken, load-tested
- Postgres: PgBouncer in front + replica for analytics + monitoring
- Redis: AOF + sentinel + replica
- Observability: fleet-aggregated metrics, worker PID in request ID
- Dashboard: ready for SSR scale-out when needed
- Every layer has an E2E path that proves it's correct

Rubric target at close: **9.8/10 scale readiness** (`project_brutal_
scoring_rubric.md` domain: scale + ops). Today after the workers-4
flip: 7.5/10.
