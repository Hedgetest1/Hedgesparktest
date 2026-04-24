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

    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
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
    return True


def is_enabled() -> bool:
    """True if init_sentry() succeeded for this process."""
    return _enabled


def get_component() -> str | None:
    """Returns the component name init_sentry was called with, if any."""
    return _initialized_for
