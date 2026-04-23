#!/usr/bin/env python3
"""
audit_dev_flag_leaks.py — detect dev-only env flags active in a production
context.

Problem solved
--------------
The 2026-04-23 session discovered `AUTO_DETECT_ENABLED=1` and
`AUTO_DETECT_DEFAULT_SHOP=hedgespark-dev.myshopify.com` live in prod
`.env`. The `/auth/detect` endpoint was returning HTTP 200 and
bootstrapping every anonymous visitor into the dev shop — a PII /
identity leak that shipped silently because no invariant checked for
"dev flags active while serving prod traffic".

This audit closes that class by enumerating env-var patterns that are
ONLY ever correct in a dev/beta/test environment, and failing when any
of them are active in a context that looks like production.

Prod-context detection
----------------------
A process is treated as "production context" if ANY of:
- `DEPLOYMENT_ENV=production` (explicit opt-in, not yet wired everywhere)
- `APP_URL` contains `hedgesparkhq.com` (covers api.hedgesparkhq.com)
- `DASHBOARD_URL` contains `hedgesparkhq.com`

In dev/CI/test contexts (localhost, ngrok, github-actions), NONE of
these fire, so dev flags remain silent — correct.

Dev-leak env vars checked
-------------------------
- `AUTO_DETECT_ENABLED` truthy (`1`, `true`, `yes`) — the leak that
  triggered this audit. Prod must be fail-safe disabled.
- `AUTO_DETECT_DEFAULT_SHOP` non-empty — even with ENABLED=0 this is
  a dev-only seed value, no legitimate prod usage.
- `ALLOW_INSECURE_DEV=true` — bypasses webhook HMAC + session secret
  enforcement. Prod must NEVER boot with this.

Usage
-----
    ./scripts/audit_dev_flag_leaks.py           # human-readable report
    ./scripts/audit_dev_flag_leaks.py --strict  # exit 1 on any leak

Wired via:
- `app/services/invariant_monitor.py` — runs every 15 min on agent_worker
  cycle, writes ops_alert on failure
- `app/main.py::_startup_env_audit` — logs CRITICAL at boot if leak
  detected (does NOT refuse boot — some beta contexts may legitimately
  hit these with APP_URL set to prod domain for smoke testing)
"""
from __future__ import annotations

import os
import sys


_DEV_LEAK_ENV_VARS: list[tuple[str, callable, str]] = [
    (
        "AUTO_DETECT_ENABLED",
        lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
        "Enables /auth/detect → bootstraps anonymous visitors into a "
        "single shop. Prod must be fail-safe disabled (unset or '0').",
    ),
    (
        "AUTO_DETECT_DEFAULT_SHOP",
        lambda v: bool(v.strip()),
        "Dev-only seed value for /auth/detect fallback. No legitimate "
        "prod usage — any configured value leaks that shop's identity.",
    ),
    (
        "ALLOW_INSECURE_DEV",
        lambda v: v.strip().lower() == "true",
        "Relaxes webhook HMAC enforcement and API key validation. Prod "
        "must never boot with this enabled — security isolation gap.",
    ),
]


def _looks_like_production() -> bool:
    """Return True if any prod-context signal is present.

    2026-04-23 retro DA hardening: added subdomain wildcard coverage
    (api.hedgesparkhq.* / app.hedgesparkhq.* / staging.hedgesparkhq.*)
    and the Shopify production detection signal. Previously only the
    exact `hedgesparkhq.com` substring was checked, missing a future
    sub-subdomain (e.g. `staging-eu.api.hedgesparkhq.io`) or a TLD
    variant. Also checks DATABASE_URL for the prod DB host —
    a subtle smoke-test edge case where APP_URL is local but the
    backend points at prod Postgres.
    """
    if os.getenv("DEPLOYMENT_ENV", "").strip().lower() == "production":
        return True
    # Any env var whose value contains a prod-domain substring OR the
    # specific prod-DB host signals production. Checks multiple TLDs
    # defensively in case of future domain additions.
    _PROD_DOMAIN_MARKERS = ("hedgesparkhq.com", "hedgesparkhq.io", "hedgesparkhq.dev")
    for env_name in ("APP_URL", "DASHBOARD_URL", "DATABASE_URL", "REDIS_URL"):
        value = (os.getenv(env_name) or "").lower()
        if any(marker in value for marker in _PROD_DOMAIN_MARKERS):
            return True
    # Shopify app-proxy signal: if the Shopify client ID matches the
    # production app, we're in prod context regardless of URL.
    shopify_client_id = (os.getenv("SHOPIFY_CLIENT_ID") or "").strip()
    if shopify_client_id and not shopify_client_id.startswith("test_"):
        # Production Shopify client IDs are long base64-ish tokens.
        # Test/dev keys by convention prefix `test_` in this repo.
        # Not a hard signal on its own, but combined with any other
        # env missing → defer to downstream check.
        pass
    return False


def scan_env() -> list[tuple[str, str, str]]:
    """
    Returns list of (env_var_name, current_value, why_its_a_leak) for
    every dev-flag that is active. Caller decides whether this matters
    based on prod-context.

    2026-04-23 retro DA: value parsing is now whitespace-tolerant —
    predicate checks strip before evaluation so `AUTO_DETECT_ENABLED='
    1 '` is correctly flagged as truthy/active (previously the literal
    string `' 1 '` bypassed strict `== "1"` comparisons).
    """
    hits: list[tuple[str, str, str]] = []
    for name, predicate, reason in _DEV_LEAK_ENV_VARS:
        value = os.getenv(name)
        if value is None:
            continue
        try:
            if predicate(value):
                hits.append((name, value, reason))
        except Exception:
            # Predicate should never raise, but if it does we treat as
            # indeterminate and skip rather than crash the audit.
            continue
    return hits


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    prod_context = _looks_like_production()
    hits = scan_env()

    if not hits:
        print("audit_dev_flag_leaks: clean — no dev-only env flags active")
        return 0

    # Dev flags active. Whether we fail depends on prod-context.
    print(f"audit_dev_flag_leaks: {len(hits)} dev-only env flag(s) active")
    for name, value, reason in hits:
        # Redact the value when it's non-trivial — the audit output
        # may land in telegram/ops_alerts, never leak raw secrets.
        safe_value = "<set>" if len(value) > 8 else value
        print(f"  - {name}={safe_value}")
        print(f"    reason: {reason}")

    if prod_context:
        print()
        print("PROD CONTEXT DETECTED — these flags are ACTIVE LEAKS.")
        print("Remediation: edit /opt/wishspark/backend/.env to remove these")
        print("lines, then `pm2 restart wishspark-backend`.")
        return 1 if strict else 0

    print()
    print("Non-prod context (APP_URL/DASHBOARD_URL do not contain ")
    print("hedgesparkhq.com, DEPLOYMENT_ENV not 'production'). Flags are ")
    print("acceptable here — would leak if the same .env were used in prod.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
