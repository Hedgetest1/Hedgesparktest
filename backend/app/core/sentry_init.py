"""Single source of truth for Sentry initialization.

Why centralize
--------------
Pre-2026-04-24 the `sentry_sdk.init()` call lived only in `app/main.py`,
which meant every PM2 worker (`intelligence_worker`, `agent_worker`,
`aggregation_worker`, `segment_monitor_worker`, `nudge_optimization_worker`,
`gdpr_worker`) ran 100% blind to Sentry — uncaught exceptions in 7 of 8
processes were invisible. Centralizing here lets every entrypoint call
`init_sentry(component=<name>)` with the same opinionated defaults
(release SHA, PII scrub via llm_pii_guard, dynamic sampling, full
integration stack, profiling on the Team plan).

Tier
----
TIER_0 — pure observability config, no auth/data path.

What this module configures
---------------------------
1. **Release** — `SENTRY_RELEASE` env var, falling back to `git rev-parse
   HEAD` (dev). Without it, every event is orphaned across deploys.
2. **Environment** — `SENTRY_ENVIRONMENT` env var, default "production".
3. **Sample rates** — `traces_sample_rate` + `profiles_sample_rate` from
   env, with safe `0.0` defaults in dev (so a developer accidentally
   running with a real DSN doesn't burn quota). Errors always 100%.
4. **TracesSampler** — high-value endpoints (auth, billing, webhooks)
   sampled at 4× base rate so we always see them; rest at base.
5. **before_send PII scrub** — every outgoing event is run through
   `app.core.llm_pii_guard.sanitize` to redact emails, API keys,
   bearer tokens, IBAN, phone numbers, etc. matched in exception
   values, breadcrumb messages, and request bodies. Tagged
   `sentry.pii_scrubbed=true` when a hit fires so we know the filter
   ran.
6. **Integrations** — FastApi (backend only), SQLAlchemy, Httpx, Redis,
   Logging (warnings as breadcrumbs, errors captured).
7. **Component tagging** — every event tagged `component=<entrypoint>`
   so we can filter "agent_worker errors" in the Sentry dashboard.
8. **Backwards-compat shim** — `is_enabled()` returns the live state
   for callers that want to gate code behind Sentry availability.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

log = logging.getLogger("sentry_init")

_enabled = False
_initialized_for: str | None = None


def _resolve_release() -> str | None:
    """Prefer explicit env var (set by deploy.sh / CI), fall back to
    `git rev-parse HEAD` for dev. Returns None if neither works — Sentry
    accepts that and reports events without release tagging."""
    env_release = os.getenv("SENTRY_RELEASE", "").strip()
    if env_release:
        return env_release
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
        if sha:
            return f"hedgespark@{sha[:12]}"
    except Exception:
        pass  # SILENT-EXCEPT-OK: release tagging is best-effort
    return None


def _make_traces_sampler(base_rate: float):
    """High-value endpoints (auth/billing/webhooks) sampled at 4× base
    rate; rest at base. Errors are always 100% via Sentry's built-in
    error sampling — this only affects performance traces."""
    high_value_paths = ("/auth/", "/billing/", "/webhooks/", "/shopify/oauth")
    boost_rate = min(base_rate * 4, 0.5)

    def _sampler(sampling_context: dict) -> float:
        try:
            tx = sampling_context.get("transaction_context", {}) or {}
            name = tx.get("name", "") or ""
            op = tx.get("op", "") or ""
            if op == "http.server" and any(p in name for p in high_value_paths):
                return boost_rate
        except Exception:
            pass  # SILENT-EXCEPT-OK: sampler is best-effort, fall through to base
        return base_rate

    return _sampler


def _make_before_send():
    """PII scrub callback. Sanitizes exception messages, breadcrumb
    text, and request bodies. Tags scrubbed events so we have visibility
    into how often the filter is firing in prod."""
    try:
        from app.core.llm_pii_guard import sanitize
    except Exception:
        sanitize = None  # type: ignore[assignment]

    def _before_send(event: dict, hint: dict) -> dict | None:
        # Drop expected dev-misconfiguration noise — these 500s are not
        # bugs, they are the server saying "this endpoint requires
        # <SECRET> to be set in .env". Capturing them as Sentry incidents
        # inflates the error rate and triggers sentry_incidents probes.
        # Founder mandate 2026-05-05: 0 errori. Generalized 2026-05-06
        # via app.core.sentry_noise_filter to cover ALL secret-class
        # env vars (API_KEY/SECRET/TOKEN/WEBHOOK_URL/WEBHOOK_SECRET),
        # not just OPS_API_KEY (G7 close).
        try:
            from app.core.sentry_noise_filter import is_noise
            for ex in (event.get("exception", {}) or {}).get("values", []) or []:
                if is_noise(ex.get("value")):
                    return None
            top_msg = event.get("message")
            if isinstance(top_msg, dict):
                top_msg = top_msg.get("formatted")
            if is_noise(top_msg):
                return None
        except Exception:
            pass  # SILENT-EXCEPT-OK: filter best-effort; on any error fall through to standard event delivery rather than dropping legitimate exceptions.

        if sanitize is None:
            return event
        scrubbed = False
        try:
            # Exception messages
            for ex in (event.get("exception", {}) or {}).get("values", []) or []:
                v = ex.get("value")
                if isinstance(v, str) and v:
                    cleaned, hits = sanitize(v)
                    if hits:
                        ex["value"] = cleaned
                        scrubbed = True
            # Log message (capture_message)
            msg = event.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("formatted"), str):
                cleaned, hits = sanitize(msg["formatted"])
                if hits:
                    msg["formatted"] = cleaned
                    scrubbed = True
            elif isinstance(msg, str) and msg:
                cleaned, hits = sanitize(msg)
                if hits:
                    event["message"] = cleaned
                    scrubbed = True
            # Breadcrumb messages
            for bc in (event.get("breadcrumbs", {}) or {}).get("values", []) or []:
                bm = bc.get("message")
                if isinstance(bm, str) and bm:
                    cleaned, hits = sanitize(bm)
                    if hits:
                        bc["message"] = cleaned
                        scrubbed = True
            # Request body (POST data) — coerce to str then sanitize
            req = event.get("request", {}) or {}
            data = req.get("data")
            if data is not None:
                as_str = data if isinstance(data, str) else str(data)
                cleaned, hits = sanitize(as_str)
                if hits:
                    req["data"] = cleaned
                    scrubbed = True
        except Exception:
            pass  # SILENT-EXCEPT-OK: scrub is best-effort, never block event delivery

        if scrubbed:
            tags = event.setdefault("tags", {})
            tags["sentry.pii_scrubbed"] = "true"
        return event

    return _before_send


def init_sentry(component: str = "backend") -> bool:
    """Initialize Sentry for this process. Idempotent within a process —
    second call is a no-op.

    `component` is tagged on every event and trace so Sentry's UI can
    filter by entrypoint (`component:agent_worker`, `component:backend`,
    etc.).

    Returns True if initialization succeeded, False otherwise (missing
    DSN, missing SDK, init exception). Never raises — Sentry being
    unavailable must not crash a worker.
    """
    global _enabled, _initialized_for
    if _enabled:
        return True

    # Load backend/.env if it hasn't been loaded yet. env_bootstrap is
    # idempotent so calling it here is cheap and covers workers that may
    # import us before any module that loads dotenv. Backend main.py still
    # benefits from the same guarantee.
    try:
        from app.core.env_bootstrap import load_env
        load_env()
    except Exception:
        pass  # SILENT-EXCEPT-OK: env bootstrap is best-effort; os.environ already populated via PM2

    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    # Tests load backend/.env via conftest so they get DB credentials etc.
    # That same .env carries SENTRY_DSN for prod, and without this gate every
    # pytest run would emit thousands of events to the production project
    # (warnings logged by sqlalchemy SingletonThreadPool when the test SQLite
    # connection is finalized cross-thread, exception-caught HTTPException
    # paths exercised by test cases, etc.). APP_ENV=test is set
    # unconditionally by conftest before any app import; trust it as the
    # gate.
    if os.getenv("APP_ENV", "").strip().lower() == "test":
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        log.warning("sentry_init: sentry-sdk not installed (%s)", exc)
        return False

    env = os.getenv("SENTRY_ENVIRONMENT", "production").strip() or "production"
    is_prod = env == "production"
    base_traces = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05" if is_prod else "0.0"))
    base_profiles = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.10" if is_prod else "0.0"))

    # Logging: WARNING+ as breadcrumbs, ERROR+ as captured events.
    # Avoid INFO breadcrumb spam in noisy workers.
    logging_integration = LoggingIntegration(
        level=logging.WARNING,
        event_level=logging.ERROR,
    )

    # FastApi only makes sense in the backend process (the workers don't
    # serve HTTP). Including it in workers is harmless but adds startup
    # noise — guard for cleanliness.
    integrations: list[Any] = [
        SqlalchemyIntegration(),
        HttpxIntegration(),
        RedisIntegration(),
        logging_integration,
    ]
    if component == "backend":
        integrations.insert(0, FastApiIntegration(transaction_style="endpoint"))

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            release=_resolve_release(),
            traces_sampler=_make_traces_sampler(base_traces),
            profiles_sample_rate=base_profiles,
            send_default_pii=False,
            attach_stacktrace=True,
            max_breadcrumbs=50,
            shutdown_timeout=2,
            before_send=_make_before_send(),
            integrations=integrations,
        )
        # Component tag is process-global — every event/transaction
        # carries it. Useful for Sentry filtering: `component:agent_worker`.
        sentry_sdk.set_tag("component", component)
    except Exception as exc:
        log.warning("sentry_init: init failed for component=%s (%s)", component, exc)
        return False

    _enabled = True
    _initialized_for = component
    log.info(
        "sentry_init: initialized for component=%s env=%s traces=%s profiles=%s",
        component, env, base_traces, base_profiles,
    )

    # DA2 preventer — see _warn_if_dsn_shared for the rationale.
    if component == "backend":
        _warn_if_dsn_shared(dsn)
    return True


def _warn_if_dsn_shared(backend_dsn: str) -> None:
    """If the backend DSN is ALSO set as the frontend DSN, the two
    surfaces share a Sentry project. That's functional but makes stack-
    trace symbolication messy and merges quota accounting. Warn once at
    startup so the operator sees a loud recommendation rather than
    silently drifting into a misconfigured setup. Extracted as a
    standalone helper so it can be tested in isolation without mocking
    the whole sentry_sdk.init flow.

    See docs/SENTRY_OPS.md 'Separate frontend project' + ledger SENTRY-1.
    """
    public_dsn = os.getenv("NEXT_PUBLIC_SENTRY_DSN", "").strip()
    if public_dsn and public_dsn == backend_dsn:
        log.warning(
            "sentry_init: NEXT_PUBLIC_SENTRY_DSN == SENTRY_DSN — backend + frontend "
            "are posting to the SAME Sentry project. Works, but recommended to split "
            "(see docs/SENTRY_OPS.md 'Separate frontend project', ledger SENTRY-1). "
            "Source-map symbolication + quota dashboards improve with split projects."
        )


def is_enabled() -> bool:
    """True if init_sentry() succeeded for this process."""
    return _enabled


def get_component() -> str | None:
    """Returns the component name init_sentry was called with, if any."""
    return _initialized_for


def sentry_span(op: str, description: str, **data):
    """Context manager wrapping a code block in a Sentry performance span.

    Sentry's HttpxIntegration + SqlalchemyIntegration auto-create spans for
    HTTP calls and SQL queries; use this helper when you want to time a
    custom code section (e.g. an orchestrator phase, a multi-step
    business logic flow) and have it appear as a child span on the
    parent transaction.

    Usage:

        from app.core.sentry_init import sentry_span

        with sentry_span("orchestrator.run", "agent_worker_phase") as span:
            span.set_data("merchants_processed", count)
            ...

    Returns a no-op context object when sentry_sdk isn't installed or no
    transaction is currently active.
    """
    try:
        import sentry_sdk
        # `name` replaces the deprecated `description` kwarg in sentry-sdk v3.
        return sentry_sdk.start_span(op=op, name=description, **{"data": data} if data else {})
    except Exception:
        # No-op CM that mimics the .set_data() interface so callers don't
        # need to guard for missing Sentry.
        class _NoopSpan:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def set_data(self, *args, **kwargs):
                pass
            def set_tag(self, *args, **kwargs):
                pass
        return _NoopSpan()


def pipeline_breadcrumb(
    category: str,
    message: str,
    level: str = "info",
    data: dict | None = None,
) -> None:
    """Drop a structured Sentry breadcrumb for the self-healing pipeline.

    Born 2026-05-02 from the brutal-CTO audit: the pipeline was
    invisible in Sentry pre-this-wire — only `component:agent_worker`
    tag, zero breadcrumbs at the propose / apply / retro_check / fail
    boundaries. A brutal external CTO opening Sentry would see "what's
    even running here?". After wire-up, every pipeline event lands as
    a breadcrumb on the active Sentry scope so any subsequent error
    captures the recent pipeline trail.

    Categories used:
      - "pipeline.triage"          run_bug_triage cycle
      - "pipeline.propose"         propose_patch lifecycle
      - "pipeline.apply"           _apply_patch lifecycle
      - "pipeline.retro_check"     _post_apply_retro_check finding
      - "pipeline.promotion"       promotion_pipeline events
      - "pipeline.invariant"       invariant_monitor audit fires
      - "pipeline.quarantine"      thrashing / fingerprint dedup
      - "pipeline.fix_template"    template cache hit/miss

    Levels (Sentry standard): debug, info, warning, error, critical.

    Never raises — sentry_sdk may be absent or scope inactive.
    """
    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category=category,
            message=message,
            level=level,
            data=data or {},
        )
    except Exception:
        pass  # SILENT-EXCEPT-OK: defensive — sentry_sdk may be absent or scope inactive; raising here would defeat the "never raises" guarantee in the docstring above.


def cron_monitor(
    slug: str,
    interval_minutes: int,
    max_runtime_minutes: int | None = None,
    checkin_margin: int = 5,
):
    """Decorator factory wrapping a worker cycle in Sentry cron monitoring.

    **Quota-gated by env var `SENTRY_CRON_MONITORING`**. Default = empty
    (no worker monitored). Sentry Team plan base includes only 1 cron
    monitor — wiring all 6 workers saturates the quota immediately
    (learned empirically 2026-04-24). Operator sets a comma-separated
    allowlist of slugs in `.env` to opt specific workers in:

        SENTRY_CRON_MONITORING=agent_worker_cycle

    Anything not in the allowlist returns a no-op decorator, letting
    us ship the `@cron_monitor` decorators in every worker while
    respecting the current plan's quota. Upgrade the plan → expand
    the allowlist.

    Fallback observability: even without Sentry cron, the internal
    `invariant_monitor` already verifies worker health every 15min
    via the preflight audits + WorkerState.last_run_at DB queries.
    Sentry cron is additive, not load-bearing.

    Parameters as before: slug, interval_minutes, max_runtime_minutes
    (default 2× interval), checkin_margin (default 5).
    """
    if max_runtime_minutes is None:
        max_runtime_minutes = interval_minutes * 2

    # Quota gate: empty allowlist = every call is a no-op.
    allowlist_raw = os.getenv("SENTRY_CRON_MONITORING", "").strip()
    allowlist = {s.strip() for s in allowlist_raw.split(",") if s.strip()}

    if slug not in allowlist:
        def _noop_decorator(fn):
            return fn
        return _noop_decorator

    try:
        from sentry_sdk.crons import monitor as _monitor
    except ImportError:
        def _noop_decorator(fn):
            return fn
        return _noop_decorator

    monitor_config = {
        "schedule": {"type": "interval", "value": interval_minutes, "unit": "minute"},
        "checkin_margin": checkin_margin,
        "max_runtime": max_runtime_minutes,
        "timezone": "UTC",
    }
    return _monitor(monitor_slug=slug, monitor_config=monitor_config)
