/**
 * WishSpark PM2 ecosystem config — Phase 1
 *
 * Processes
 * ---------
 *   wishspark-dashboard          Next.js production server  (port 3000)
 *   wishspark-backend            FastAPI / uvicorn           (port 8000)
 *   wishspark-worker             intelligence_worker.py      (10-min cycle)
 *   wishspark-agent-worker       agent_worker.py             (15-min cycle)
 *   wishspark-aggregation-worker aggregation_worker.py       ( 5-min cycle)
 *
 * Path assumptions
 * ----------------
 *   Repo root              /opt/wishspark
 *   Python venv            /opt/wishspark/backend/venv
 *   Backend .env           /opt/wishspark/backend/.env   (read by load_dotenv())
 *   Log directory          /opt/wishspark/logs           (must exist before reload)
 *   Dashboard build        /opt/wishspark/dashboard/.next (must exist; run npm build first)
 *
 * Environment variables
 * ---------------------
 *   All Python processes set cwd to /opt/wishspark/backend so that
 *   load_dotenv() in the application code finds backend/.env automatically.
 *   Secrets (DATABASE_URL, REDIS_URL, OPENAI_API_KEY, …) are kept in
 *   backend/.env and are never inlined in this file.
 *
 * Exec mode / clustering
 * ----------------------
 *   All processes run in fork mode with instances: 1.
 *   Workers must remain singletons — multiple instances would create
 *   duplicate watermark advances and duplicate retention runs.
 *   The backend is kept in fork mode; cluster mode would require a
 *   reverse proxy and session-safe routing that is not yet in place.
 *
 * Restart policy
 * --------------
 *   autorestart: true — PM2 restarts on any non-zero exit code or crash.
 *   min_uptime: "10s" — a restart only counts against max_restarts if
 *     the process exits within 10 s of starting.  Workers that run their
 *     full cycle (≥ 5 min) reset the counter each time.
 *   max_restarts: 10 — if a process crashes 10 times while still within
 *     min_uptime, PM2 marks it errored and stops restarting.
 *   restart_delay: 5000 — 5 s back-off between restart attempts to
 *     avoid hammering the DB on transient startup failures.
 *
 * Memory safety
 * -------------
 *   max_memory_restart caps RSS growth.  Values are generous enough to
 *   avoid churn under normal load, but catch actual leaks or runaway jobs.
 *   backend: 512M   — handles concurrent requests, ASGI, SQLAlchemy pool
 *   dashboard: 300M — Next.js SSR; React render can spike on first load
 *   workers: 200M   — background Python; should stay well under 100 M
 *
 * Bind address
 * ------------
 *   backend and dashboard bind to 127.0.0.1 only.  Public traffic enters
 *   exclusively through Traefik on ports 80/443.  Direct port access is
 *   also blocked by ufw deny rules on 3000 and 8000.
 */

"use strict";

module.exports = {
  apps: [

    // -------------------------------------------------------------------------
    // Next.js dashboard — production server
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-dashboard",
      script:              "/opt/wishspark/dashboard/node_modules/.bin/next",
      args:                "start -H 127.0.0.1",
      cwd:                 "/opt/wishspark/dashboard",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      kill_timeout:        10000,  // 10s graceful shutdown — finish SSR renders
      max_memory_restart:  "300M",
      out_file:            "/opt/wishspark/logs/dashboard-out.log",
      error_file:          "/opt/wishspark/logs/dashboard-error.log",
      merge_logs:          false,
      env: {
        NODE_ENV: "production",
        HOSTNAME:  "127.0.0.1",
      },
    },

    // -------------------------------------------------------------------------
    // FastAPI backend — uvicorn ASGI server with 4 worker subprocesses.
    //
    // PM2 runs 1 instance of the uvicorn MASTER; uvicorn itself forks 4
    // worker children to handle requests. This is the correct multi-
    // worker pattern — setting PM2 `instances: 4` would launch 4 uvicorn
    // MASTERS all binding the same port, which fails.
    //
    // DB pool is env-tuned in database.py (DB_POOL_SIZE=8,
    // DB_MAX_OVERFLOW=15 in backend/.env for this config): 4×(8+15) = 92
    // conn from backend, well below Postgres max_connections=200.
    // Bumped 2026-05-04 from 5+10=15 to 8+15=23 per worker after Item 8
    // load-test surfaced p99 = 16s + 24% timeout under 100 concurrent
    // merchants. Pool exhaustion was the root cause; new ceiling 92
    // gives 53% more headroom.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-backend",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      kill_timeout:        10000,  // 10s graceful shutdown — finish in-flight requests
      max_memory_restart:  "1024M",  // 4 workers × ~200M each
      out_file:            "/opt/wishspark/logs/backend-out.log",
      error_file:          "/opt/wishspark/logs/backend-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH:        "/opt/wishspark/backend",
        // DB pool tuned for 4 uvicorn workers. Total backend conn:
        // 4 × (8 + 15) = 92; + 7 singleton PM2 workers × ~2 = 14;
        // + admin/psql headroom ~10; = ~116, below Postgres max_connections=200.
        // Bumped 2026-05-04 (Item 7-bis) from 5+10 → 8+15 after the
        // load-test harness reported p99 = 16s and 24% timeout rate at
        // 100 concurrent merchants (pool exhaustion). The new ceiling
        // gives 53% more headroom for the cold-cache 18-query path.
        // See app/core/database.py for the env-read logic.
        DB_POOL_SIZE:      "8",
        DB_MAX_OVERFLOW:   "15",
      },
    },

    // -------------------------------------------------------------------------
    // Intelligence worker — opportunity scoring across all products
    // Cycle: 10 minutes.  No per-shop dimension; shops_processed always 0.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-worker",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "app/workers/intelligence_worker.py",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      max_memory_restart:  "200M",
      out_file:            "/opt/wishspark/logs/worker-out.log",
      error_file:          "/opt/wishspark/logs/worker-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH: "/opt/wishspark/backend",
      },
    },

    // -------------------------------------------------------------------------
    // Agent worker — sandbox analysis runs
    // Cycle: 15 minutes.  No per-shop dimension; shops_processed always 0.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-agent-worker",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "app/workers/agent_worker.py",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      max_memory_restart:  "200M",
      out_file:            "/opt/wishspark/logs/agent-worker-out.log",
      error_file:          "/opt/wishspark/logs/agent-worker-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH: "/opt/wishspark/backend",
      },
    },

    // -------------------------------------------------------------------------
    // Aggregation worker — product_metrics pre-aggregation (Phase 1 addition)
    // Cycle: 5 minutes.  Singleton — multiple instances would create duplicate
    // watermark advances and duplicate retention runs.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-aggregation-worker",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "app/workers/aggregation_worker.py",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      max_memory_restart:  "200M",
      out_file:            "/opt/wishspark/logs/aggregation-worker-out.log",
      error_file:          "/opt/wishspark/logs/aggregation-worker-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH: "/opt/wishspark/backend",
      },
    },

    // -------------------------------------------------------------------------
    // Segment monitor worker — proactive hot-segment action task creation
    // Cycle: 5 minutes.  Scans Pro shops for hot audience segments and creates
    // SCARCITY_NUDGE action tasks when revenue windows are open.
    // Singleton — duplicate instances would create duplicate action tasks
    // despite the dedup guard, and waste segment computation cycles.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-segment-monitor",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "app/workers/segment_monitor_worker.py",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      max_memory_restart:  "200M",
      out_file:            "/opt/wishspark/logs/segment-monitor-out.log",
      error_file:          "/opt/wishspark/logs/segment-monitor-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH: "/opt/wishspark/backend",
      },
    },

    // -------------------------------------------------------------------------
    // Nudge optimization worker — autonomous A/B winner selection + challenger gen
    // Cycle: 6 hours (NUDGE_OPTIMIZER_INTERVAL_HOURS).  Evaluates all active A/B
    // nudges, promotes winners when MDE threshold is met, queues AI challengers.
    // Singleton — duplicate instances would trigger duplicate promotions and
    // duplicate AI composer calls.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-nudge-optimizer",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "app/workers/nudge_optimization_worker.py",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      max_memory_restart:  "200M",
      out_file:            "/opt/wishspark/logs/nudge-optimizer-out.log",
      error_file:          "/opt/wishspark/logs/nudge-optimizer-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH: "/opt/wishspark/backend",
      },
    },

    // -------------------------------------------------------------------------
    // GDPR worker — processes GDPR deletion/redaction requests
    // Cycle: 5 minutes.  Picks up pending GdprRequest rows created by
    // Shopify GDPR webhook endpoints and executes data deletion/redaction.
    // Singleton — duplicate instances would process the same request twice.
    // -------------------------------------------------------------------------
    {
      name:                "wishspark-gdpr-worker",
      script:              "/opt/wishspark/backend/venv/bin/python",
      args:                "app/workers/gdpr_worker.py",
      cwd:                 "/opt/wishspark/backend",
      interpreter:         "none",
      exec_mode:           "fork",
      instances:           1,
      autorestart:         true,
      min_uptime:          "10s",
      max_restarts:        10,
      restart_delay:       5000,
      max_memory_restart:  "200M",
      out_file:            "/opt/wishspark/logs/gdpr-worker-out.log",
      error_file:          "/opt/wishspark/logs/gdpr-worker-error.log",
      merge_logs:          false,
      env: {
        PYTHONPATH: "/opt/wishspark/backend",
      },
    },

  ],
};
