"""
dashboard_asset_probe_task.py — Runtime probe that catches the
"stale Next.js in-memory manifest" class of silent production bugs.

Context
-------
On 2026-04-18 the landing silently rendered as unstyled copy on a white
background. The Next.js `/` route returned HTTP 200, so every existing
monitor (PM2 `online`, `/system/health`, Lighthouse scheduled runs)
reported green. Merchants saw broken rendering; nothing alerted.

Root cause: a rebuild mid-process-lifetime replaced on-disk chunks while
the running PM2 dashboard kept its old in-memory manifest. The served
HTML referenced CSS chunks that no longer existed → 500 on the stylesheet
→ unstyled page. Skipping `pm2 restart` after `npx next build` is all it
took.

What this task does
-------------------
Every 5 minutes, fetches the landing, extracts every `/_next/static/...`
reference, and HEAD-probes each. Any non-200 writes an ops_alert and a
night_shift report line. Cooldowns dedup so a stuck deploy doesn't burn
the alert channel.

Self-heal routing
-----------------
The alert type `dashboard_asset_drift` is registered in the self-healing
triage engine — when fired, the autonomous pipeline knows this is almost
always a "ran next build, forgot pm2 restart" bug, so the fix candidate
proposes `pm2 restart wishspark-dashboard` first.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List

import httpx

_log = logging.getLogger("worker.agent.dashboard_asset_probe")

_INTERVAL_S = 300  # 5 minutes
_PROBE_TIMEOUT_S = 3.0
_HOST = "http://127.0.0.1:3000"
_PROBE_PATHS = ["/", "/app", "/pricing"]
_ASSET_RE = re.compile(r'/_next/static/(?:chunks|media)/[A-Za-z0-9_~.\-]+\.[A-Za-z0-9]+')
_ALERT_TYPE = "dashboard_asset_drift"
# Cooldown bucket keys by UTC hour so an incident repeating within the
# same hour stays deduped; rolling window opens fresh each hour.
_COOLDOWN_KEY = "hs:spike:dashboard_asset_drift:hour"
_COOLDOWN_TTL_S = 3600

_last_run: float | None = None


def should_run() -> bool:
    if _last_run is None:
        return True
    return (time.monotonic() - _last_run) >= _INTERVAL_S


def mark_done() -> None:
    global _last_run
    _last_run = time.monotonic()


def _probe_host(client: httpx.Client) -> List[str]:
    """Return list of failure strings. Empty list means green."""
    failures: List[str] = []
    seen: set[str] = set()
    for path in _PROBE_PATHS:
        try:
            r = client.get(f"{_HOST}{path}", timeout=_PROBE_TIMEOUT_S)
        except Exception as exc:
            failures.append(f"{path}: fetch error ({type(exc).__name__})")
            continue
        if r.status_code >= 400:
            failures.append(f"{path}: page HTTP {r.status_code}")
            continue
        for asset in _ASSET_RE.findall(r.text):
            if asset in seen:
                continue
            seen.add(asset)
            try:
                ar = client.head(f"{_HOST}{asset}", timeout=_PROBE_TIMEOUT_S)
            except Exception as exc:
                failures.append(
                    f"{path}: {asset} probe error ({type(exc).__name__})"
                )
                continue
            if ar.status_code != 200:
                failures.append(
                    f"{path}: asset {asset} returned HTTP {ar.status_code}"
                )
    return failures


def run() -> None:
    from app.core.database import SessionLocal
    from app.services.alerting import write_alert

    try:
        with httpx.Client(follow_redirects=False) as client:
            # Reachability prerequisite: if landing itself is unreachable,
            # that's a different bug class (service down) handled by the
            # existing ops health check. Skip silently.
            try:
                r = client.get(f"{_HOST}/", timeout=_PROBE_TIMEOUT_S)
                if r.status_code >= 500:
                    return
            except Exception:
                return

            failures = _probe_host(client)
    except Exception as exc:
        _log.warning("dashboard_asset_probe: probe harness error: %s", exc)
        return

    if not failures:
        return

    # Reuse the canonical cooldown pattern — SETNX under Redis, fail-open
    # on outage (emit duplicate rather than lose the alert).
    from app.services.observability_spikes import _cooldown_ok
    if not _cooldown_ok(_COOLDOWN_KEY, _COOLDOWN_TTL_S):
        _log.info("dashboard_asset_probe: %d failure(s), cooldown active, skipping alert",
                  len(failures))
        return

    db = SessionLocal()
    try:
        # heal-detection: asset-probe alert is fired once per detected chunk failure — auto-deploy-driven recovery is the heal mechanism (next deploy resolves)
        write_alert(
            db,
            severity="critical",
            source="dashboard_asset_probe",
            alert_type=_ALERT_TYPE,
            summary=(
                f"Dashboard served HTML references {len(failures)} asset(s) "
                "that do not resolve 200 — merchants likely see broken rendering"
            ),
            detail={
                "failures": failures[:20],  # cap at 20 to keep payload small
                "failure_count": len(failures),
                "probe_paths": _PROBE_PATHS,
                "remedy": (
                    "Almost always: a rebuild happened mid-process-lifetime. "
                    "Run `./dashboard/scripts/deploy.sh --no-build` (restart + "
                    "verify) or, if build itself is stale, "
                    "`./dashboard/scripts/deploy.sh` for full rebuild."
                ),
            },
        )
        db.commit()
        # Cooldown was already acquired via _cooldown_ok() SETNX before
        # this write, so subsequent runs within the TTL stay deduped.
        _log.warning("dashboard_asset_probe: %d failure(s) — alert raised",
                     len(failures))
    except Exception as exc:
        _log.warning("dashboard_asset_probe: alert write failed: %s", exc)
        db.rollback()
    finally:
        db.close()
