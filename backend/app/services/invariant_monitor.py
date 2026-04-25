"""
invariant_monitor.py — Periodic post-merge invariant check.

Problem solved
--------------
Preflight audits (backend/scripts/audit_*.py) block commits at git
pre-commit hook time. But if someone bypasses the hook (--no-verify,
merge conflict resolution, emergency fix), a structural regression
can land in main without the hook firing. No runtime signal exists
for that class of regression because the invariants are LATENT — no
merchant triggers them today, so nothing writes to ops_alerts, so the
bugfix pipeline never sees the problem.

This module runs the critical audits on the live source tree on a
schedule (agent_worker cycle, every 15 min) and writes an ops_alert
when any audit fails. From there, bug_triage Rule 7 (generic
≥3-recurrence catch-all) creates a BugFixCandidate after 45 minutes
of the invariant being broken, and the normal self-healing flow
takes it from there.

Design constraints
------------------
- Read-only: this module MUST NOT attempt to fix anything. Only
  detect + alert. Fix proposals go through the standard bugfix
  pipeline (LLM propose → reviewer_layer → governed apply).
- Cheap: subprocess to existing audit scripts. No new LLM calls.
- Fail-safe: if the audit script itself errors, log but don't
  raise — never take down the worker loop.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
_PYTHON_BIN = str(_BACKEND_ROOT / "venv" / "bin" / "python")

# Registered audits to run on each cycle. Each entry is
# (script_name, alert_type_on_failure, source_key). source_key is the
# stable identifier in ops_alerts.source so dedup + thrash detection
# key off the same name across repeated failures.
_AUDITS: list[tuple[str, str, str]] = [
    (
        "audit_session_durability_invariants.py",
        "invariant_regression",
        "invariant:session_durability",
    ),
    # Multi-worker safety: added 2026-04-23 after the uvicorn --workers 4
    # flip. Runtime recognition for the class of bug that the 2026-04-23
    # sprint just fixed — any new module-level mutable state introduced
    # without a `# multi-worker:` annotation will trip this on live source.
    # Preflight catches at commit; this catches at runtime (fires within
    # 15min of a --no-verify merge).
    (
        "audit_multiworker_safety.py",
        "invariant_regression",
        "invariant:multiworker_safety",
    ),
    # Dev-flag leaks: added 2026-04-23 after the AUTO_DETECT_ENABLED=1
    # leak was found live in prod .env. Runtime recognition for the
    # class of bug where a dev-only env var is active while APP_URL
    # points at hedgesparkhq.com. Preflight cannot see .env (gitignored);
    # this is the only layer that catches an .env-driven leak — fires
    # within 15 min of backend boot with leaky env.
    (
        "audit_dev_flag_leaks.py",
        "invariant_regression",
        "invariant:dev_flag_leaks",
    ),
    # Exception-sinks: added 2026-04-24 after the SINK-01..04 sweep
    # closed all 4 CRITICAL write_no_rollback findings. Runtime
    # recognition for the class of bug where a try/db.commit + bare
    # except: log handler omits db.rollback(), leaving the SQLAlchemy
    # session unusable for the caller's next ORM op. Preflight catches
    # at commit; this catches at runtime within 15min of any
    # --no-verify or merge-conflict-resolution bypass.
    (
        "audit_exception_sinks.py",
        "invariant_regression",
        "invariant:exception_sinks",
    ),
    # Sentry invariants: added 2026-04-24 after the C1..C4 sweep
    # centralized init_sentry + wired all 7 PM2 processes + PII scrub +
    # dashboard SDK. Runtime recognition for the class of regression
    # where someone deletes the sentry_init module, removes init_sentry
    # from a worker, or drops the Sentry CSP allowlist entry — fires
    # within 15min instead of silently losing observability coverage.
    (
        "audit_sentry_invariants.py",
        "invariant_regression",
        "invariant:sentry_invariants",
    ),
    # Sentry alert-rules drift: added 2026-04-24 (D10 closure). Runtime
    # recognition for "YAML edited but never synced to Sentry" — same
    # class as Terraform-state-drift in IaC. Fires within 15min if a
    # commit slips through preflight via --no-verify with stale lock.
    (
        "audit_sentry_alert_rules_drift.py",
        "invariant_regression",
        "invariant:sentry_alert_rules_drift",
    ),
    # Dashboard a11y patterns: added 2026-04-25 night (F6 + post-DA
    # closure). Runtime recognition for low-contrast small text
    # (slate-500/600 + ≤13px), inline-style hex (#64748b / #45556c),
    # icon-only buttons missing aria-label. Preflight runs --strict;
    # this catches the same class within 15min of any --no-verify
    # bypass. The audit is fast (<1s), pure static scan of
    # dashboard/src.
    (
        "audit_dashboard_a11y.py",
        "invariant_regression",
        "invariant:dashboard_a11y",
    ),
]

_TIMEOUT_SECONDS = 30


def run_invariant_check(db: Session) -> dict:
    """
    Run every registered audit once. Emit an ops_alert for each
    failure. Returns a summary dict for agent_worker logging.

    Never raises — a broken audit script writes a `critical` alert
    rather than crashing the worker loop.
    """
    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    if not os.path.isdir(_SCRIPTS_DIR):
        log.warning("invariant_monitor: scripts dir missing at %s", _SCRIPTS_DIR)
        return summary
    if not os.path.isfile(_PYTHON_BIN):
        log.warning("invariant_monitor: venv python missing at %s", _PYTHON_BIN)
        return summary

    from app.services.alerting import write_alert

    # Runtime checks that are NOT subprocess-audits (live state queries).
    # Each appends directly to summary and optionally writes an alert.
    try:
        _check_fleet_workers_reporting(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: fleet-workers check failed: %s", exc)
    try:
        _check_redis_durability(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: redis-durability check failed: %s", exc)
    try:
        _check_postgres_capacity(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: postgres-capacity check failed: %s", exc)
    try:
        _check_bugfix_proposal_provenance(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: bugfix-provenance check failed: %s", exc)
    try:
        _check_silent_audits(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: silent-audits check failed: %s", exc)
    try:
        _check_audit_findings_trend(db, summary)
    except Exception as exc:
        log.warning("invariant_monitor: audit-findings-trend check failed: %s", exc)

    for script_name, alert_type, source in _AUDITS:
        summary["checked"] += 1
        script_path = _SCRIPTS_DIR / script_name
        if not script_path.is_file():
            log.warning("invariant_monitor: audit script missing: %s", script_path)
            continue
        try:
            # --strict forces exit 1 on any finding. Without it, audits
            # default to report-only (exit 0) for preflight readability;
            # runtime-check path MUST see failures as non-zero to trigger
            # the ops_alert branch below.
            result = subprocess.run(
                [_PYTHON_BIN, str(script_path), "--strict"],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                cwd=str(_BACKEND_ROOT),
            )
        except subprocess.TimeoutExpired:
            # Audit itself hung — treat as critical so operators notice
            summary["failed"] += 1
            try:
                write_alert(
                    db,
                    severity="critical",
                    source=source,
                    alert_type="invariant_audit_timeout",
                    summary=f"{script_name} timed out after {_TIMEOUT_SECONDS}s",
                    detail={"script": script_name, "timeout": _TIMEOUT_SECONDS},
                )
                summary["alerts_written"] += 1
            except Exception as exc:
                log.error("invariant_monitor: failed to write timeout alert: %s", exc)
            continue
        except Exception as exc:
            log.error("invariant_monitor: subprocess failed for %s: %s", script_name, exc)
            continue

        if result.returncode == 0:
            # Audit green — no action. The chronic-aggregation logic in
            # write_alert handles the case where a previous failure has
            # now healed (alert stays open until resolved explicitly).
            continue

        summary["failed"] += 1
        # Trim audit output to a reasonable detail size
        stdout_tail = "\n".join(result.stdout.splitlines()[-40:])
        stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
        try:
            write_alert(
                db,
                severity="critical",
                source=source,
                alert_type=alert_type,
                summary=f"{script_name} failed — structural invariant broken on main",
                detail={
                    "script": script_name,
                    "exit_code": result.returncode,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "remediation": (
                        "Restore the invariant in source OR update the E2E "
                        "suite + audit to reflect the intentional design "
                        "change. See dashboard/e2e/session_durability.spec.ts "
                        "and backend/scripts/audit_session_durability_invariants.py."
                    ),
                },
            )
            summary["alerts_written"] += 1
        except Exception as exc:
            log.error("invariant_monitor: failed to write invariant_regression alert: %s", exc)

    return summary


# ---------------------------------------------------------------------------
# Live-state runtime checks (added 2026-04-23 post --workers 4 flip)
# ---------------------------------------------------------------------------
#
# Per `feedback_post_fix_pipeline_recognition.md`: every hardening fix
# must teach the self-debug pipeline to recognize the class at runtime.
# The 2026-04-23 sprint closed 4 classes — each has a detector below.

def _check_fleet_workers_reporting(db: Session, summary: dict) -> None:
    """Expect 4 uvicorn workers reporting to /metrics within last 60s.

    If fewer, either a worker crashed silently or the fleet metrics
    aggregator (commit 7dace25) regressed.
    """
    expected_min = int(os.getenv("EXPECTED_UVICORN_WORKERS", "4"))
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("invariant_monitor.fleet_workers.no_redis")
            return
        reporting = 0
        for _ in rc.scan_iter(match="hs:metrics:worker:*", count=50):
            reporting += 1
    except Exception as exc:
        record_silent_return("invariant_monitor.fleet_workers.redis_error")
        log.warning("invariant_monitor: fleet-workers scan failed: %s", exc)
        return

    summary["checked"] += 1
    if reporting >= expected_min:
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="critical",
            source="invariant:fleet_workers_reporting",
            alert_type="invariant_regression",
            summary=(
                f"Fleet workers reporting to /metrics: {reporting} "
                f"(expected >= {expected_min})"
            ),
            detail={
                "reporting": reporting,
                "expected_min": expected_min,
                "remediation": (
                    "Check pm2 logs wishspark-backend — a worker may have "
                    "crashed silently. Restart backend if needed. If the "
                    "value is persistently low, /metrics aggregator "
                    "(app/core/metrics.py) may have regressed."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: fleet-workers alert write failed: %s", exc)


def _check_redis_durability(db: Session, summary: dict) -> None:
    """Redis must have AOF enabled + maxmemory-policy not noeviction.

    Closes the 2026-04-23 gap where Redis was RDB-snapshot-only (1h data
    loss window) and had no eviction policy (crash-on-OOM risk).
    """
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            record_silent_return("invariant_monitor.redis_durability.no_redis")
            return
        info = rc.info("persistence")
        aof_enabled = int(info.get("aof_enabled", 0)) == 1
        policy = rc.config_get("maxmemory-policy").get("maxmemory-policy", "")
    except Exception as exc:
        record_silent_return("invariant_monitor.redis_durability.redis_error")
        log.warning("invariant_monitor: redis-durability probe failed: %s", exc)
        return

    summary["checked"] += 1
    problems = []
    if not aof_enabled:
        problems.append("aof_disabled")
    if policy == "noeviction":
        problems.append(f"maxmemory_policy_unsafe={policy}")

    if not problems:
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="critical",
            source="invariant:redis_durability",
            alert_type="invariant_regression",
            summary=f"Redis durability regressed: {', '.join(problems)}",
            detail={
                "problems": problems,
                "aof_enabled": aof_enabled,
                "maxmemory_policy": policy,
                "remediation": (
                    "redis-cli CONFIG SET appendonly yes && "
                    "redis-cli CONFIG SET maxmemory-policy volatile-lru && "
                    "redis-cli CONFIG REWRITE"
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: redis-durability alert write failed: %s", exc)


def _check_bugfix_proposal_provenance(db: Session, summary: dict) -> None:
    """BugFixCandidate.proposal_provider must be populated whenever propose
    was attempted — runtime regression detector for the 2026-04-23 fix.

    Background: an E2E probe on 2026-04-23 exposed a latent observability
    gap — when a downstream validator (diff-structure, semantic, security)
    rejected an LLM-proposed patch, `candidate.proposal_provider` stayed
    NULL even though the LLM had been called and budget had been charged.
    Post-hoc cost attribution was therefore impossible. The fix threads
    actual_provider from _call_llm back into propose_patch and persists
    it BEFORE any validation gate (plus `"template_cache"` sentinel on
    cache-hit path for truthful accounting).

    This check fires if ANY BugFixCandidate from the last 24h has
    `proposal_attempted_at IS NOT NULL AND proposal_provider IS NULL`.
    Such a row would mean the fix regressed silently — either via direct
    edit of propose_patch, via a new caller that forgot the contract, or
    via a new proposal source (not LLM, not cache) that was wired in
    without an explicit sentinel.

    Threshold: 1 (zero tolerance — the fix is trivial to get right).
    """
    _expected_min_window_hours = int(os.getenv(
        "EXPECTED_BUGFIX_PROVENANCE_WINDOW_HOURS", "24"
    ))
    from sqlalchemy import text as _text
    # Hours is int-coerced from env above, safe to interpolate directly.
    # SQLAlchemy text() does not support parameter-binding for INTERVAL
    # literal values, hence the f-string.
    sql = (
        "SELECT id, title, source_type, proposal_attempted_at, status "
        "FROM bugfix_candidates "
        "WHERE proposal_attempted_at IS NOT NULL "
        "  AND proposal_provider IS NULL "
        f"  AND proposal_attempted_at > NOW() - INTERVAL '{_expected_min_window_hours} hours' "
        "ORDER BY proposal_attempted_at DESC LIMIT 5"
    )
    try:
        rows = db.execute(_text(sql)).fetchall()
    except Exception as exc:
        log.warning("invariant_monitor: bugfix-provenance probe failed: %s", exc)
        return

    summary["checked"] += 1
    if not rows:
        return

    summary["failed"] += 1
    sample = [
        {
            "candidate_id": r[0],
            "title": (r[1] or "")[:80],
            "source_type": r[2],
            "proposal_attempted_at": r[3].isoformat() if r[3] else None,
            "status": r[4],
        }
        for r in rows
    ]
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="invariant:bugfix_proposal_provenance",
            alert_type="invariant_regression",
            summary=(
                f"{len(rows)} BugFixCandidate row(s) in last "
                f"{_expected_min_window_hours}h have proposal_attempted_at "
                "set but proposal_provider=NULL — observability regression"
            ),
            detail={
                "window_hours": _expected_min_window_hours,
                "rows_affected_sample": sample,
                "class": "bugfix_proposal_provenance_regression",
                "remediation": (
                    "Check app/services/bugfix_pipeline.py::propose_patch — "
                    "actual_provider from _call_llm must be written to "
                    "candidate.proposal_provider BEFORE any validation gate. "
                    "Template-cache hits must set proposal_provider="
                    "'template_cache'. See 2026-04-23 E2E probe commit."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: bugfix-provenance alert write failed: %s", exc)


def _check_postgres_capacity(db: Session, summary: dict) -> None:
    """Postgres max_connections must be >= 200 (bumped from 100 on 2026-04-23)."""
    expected_min = int(os.getenv("EXPECTED_PG_MAX_CONNECTIONS", "200"))
    try:
        from sqlalchemy import text as _text
        val = db.execute(_text("SHOW max_connections")).scalar()
        current = int(val or 0)
    except Exception as exc:
        log.warning("invariant_monitor: postgres-capacity probe failed: %s", exc)
        return

    summary["checked"] += 1
    if current >= expected_min:
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="invariant:postgres_capacity",
            alert_type="invariant_regression",
            summary=f"Postgres max_connections={current} < expected {expected_min}",
            detail={
                "current": current,
                "expected_min": expected_min,
                "remediation": (
                    "Edit /etc/postgresql/*/main/postgresql.conf, set "
                    "max_connections = 200, systemctl restart postgresql"
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: postgres-capacity alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Silent-audit detection — catches regression where a wired audit stops
# emitting telemetry to /ops/audit-telemetry.
# ---------------------------------------------------------------------------
#
# Two failure modes detected:
#  1. "Silent with history" — audit emitted within the last 14 days but the
#     most recent emission is older than SILENT_THRESHOLD_DAYS days. Most
#     likely: audit was removed from preflight, renamed without updating
#     the shim, or its call path short-circuits before reaching the
#     decorator.
#  2. "Never observed" — audit is listed in WIRED_AUDITS but has ZERO
#     telemetry entries in the last INITIAL_GRACE_DAYS. Fires only after
#     a generous grace window so the first run of a freshly-wired audit
#     doesn't page us.
#
# Cooldown: ops_alerts dedups by (alert_type, source) for 24h, so firing
# every 15min per cycle doesn't spam. Alert stays open until resolved
# manually OR the audit starts emitting again (dedup source resolves).

_SILENT_THRESHOLD_DAYS = int(os.getenv("AUDIT_SILENT_THRESHOLD_DAYS", "7"))
_INITIAL_GRACE_DAYS = int(os.getenv("AUDIT_INITIAL_GRACE_DAYS", "14"))
_TELEMETRY_WINDOW_DAYS = 30  # how far back we look for history


def _check_silent_audits(db: Session, summary: dict) -> None:
    """Alert on wired audits that stopped (or never started) emitting
    telemetry to the /ops/audit-telemetry rollup."""
    try:
        from app.core.wired_audits import WIRED_AUDITS
        from app.services.audit_telemetry import read_all_audits
    except Exception as exc:
        log.warning("invariant_monitor: silent-audits import failed: %s", exc)
        return

    try:
        telemetry = read_all_audits(days=_TELEMETRY_WINDOW_DAYS)
    except Exception as exc:
        log.warning("invariant_monitor: silent-audits read failed: %s", exc)
        return

    summary["checked"] += 1

    from datetime import date
    today = date.today()

    silent_with_history: list[tuple[str, int]] = []
    never_observed: list[str] = []

    for audit_file in WIRED_AUDITS:
        audit_name = audit_file[:-3] if audit_file.endswith(".py") else audit_file
        entry = telemetry.get(audit_name)
        if entry is None:
            never_observed.append(audit_name)
            continue
        last_day_str = entry.get("last_day", "")
        if not last_day_str:
            never_observed.append(audit_name)
            continue
        try:
            last_day = date.fromisoformat(last_day_str)
        except (ValueError, TypeError):
            continue
        gap_days = (today - last_day).days
        if gap_days > _SILENT_THRESHOLD_DAYS:
            silent_with_history.append((audit_name, gap_days))

    # Grace window for the "never observed" bucket: only alert after the
    # telemetry system has been running long enough that every wired
    # audit SHOULD have had at least one preflight cycle to emit. Gate:
    # at least ONE audit has `days_seen >= INITIAL_GRACE_DAYS` within
    # the TELEMETRY_WINDOW_DAYS query window — that proves the system
    # has been live for at least that many distinct days.
    grace_window_passed = any(
        entry.get("days_seen", 0) >= _INITIAL_GRACE_DAYS
        for entry in telemetry.values()
    )

    reportable_never = never_observed if grace_window_passed else []

    if not silent_with_history and not reportable_never:
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="invariant:silent_audits",
            alert_type="invariant_regression",
            summary=(
                f"Audit telemetry gaps: "
                f"{len(silent_with_history)} silent >"
                f"{_SILENT_THRESHOLD_DAYS}d, "
                f"{len(reportable_never)} never observed"
            ),
            detail={
                "silent_with_history": [
                    {"audit": n, "days_since_last_emission": g}
                    for n, g in sorted(silent_with_history, key=lambda x: -x[1])
                ],
                "never_observed": sorted(reportable_never),
                "threshold_days": _SILENT_THRESHOLD_DAYS,
                "initial_grace_days": _INITIAL_GRACE_DAYS,
                "window_days": _TELEMETRY_WINDOW_DAYS,
                "remediation": (
                    "Check /ops/audit-telemetry. For each silent audit: "
                    "(a) confirm it still runs in preflight.sh, (b) confirm "
                    "the @telemetered decorator is present on main(), "
                    "(c) restart backend if the shim module itself was "
                    "changed and cached imports are stale."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: silent-audits alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Audit findings-trend detection — catches the "slow accumulation" class.
# ---------------------------------------------------------------------------
#
# Complements silent detection: a silent audit stopped emitting, a trending
# audit is accumulating findings without tripping the preflight gate. The
# detection compares two halves of a 14-day window:
#
#   first_half:  days [8..14] ago (7 days)
#   second_half: days [0..7]  ago (7 days)
#
# If second_half findings > first_half findings AND exceeds absolute +
# relative thresholds, emit an alert. Thresholds tuned to avoid noise:
#   - At least AUDIT_TREND_MIN_ABSOLUTE_DELTA new findings in second half
#     (default 5) so a 0→1 blip doesn't page.
#   - Second-half sum must be >= AUDIT_TREND_MIN_SECOND_HALF (default 3)
#     so a "always noisy" audit with jitter doesn't trip.

_TREND_WINDOW_DAYS = 14
_TREND_MIN_ABSOLUTE_DELTA = int(os.getenv("AUDIT_TREND_MIN_ABSOLUTE_DELTA", "5"))
_TREND_MIN_SECOND_HALF = int(os.getenv("AUDIT_TREND_MIN_SECOND_HALF", "3"))


def _check_audit_findings_trend(db: Session, summary: dict) -> None:
    """Alert on wired audits where findings count is trending up —
    i.e., accumulating a regression that doesn't individually trip
    preflight."""
    try:
        from app.core.wired_audits import WIRED_AUDITS
        from app.services.audit_telemetry import read_audit_history
    except Exception as exc:
        log.warning("invariant_monitor: trend import failed: %s", exc)
        return

    from datetime import date, timedelta
    today = date.today()
    half = _TREND_WINDOW_DAYS // 2
    # Symmetric split: first_half covers the OLDER 7 days, second_half
    # covers the NEWER 7 days. The exact boundary day (today - 7) is
    # excluded so both halves are strictly equal-length.
    first_cutoff = (today - timedelta(days=half)).isoformat()
    second_cutoff_start = (today - timedelta(days=half - 1)).isoformat()

    trending: list[dict] = []
    for audit_file in WIRED_AUDITS:
        audit_name = audit_file[:-3] if audit_file.endswith(".py") else audit_file
        try:
            history = read_audit_history(audit_name, days=_TREND_WINDOW_DAYS)
        except Exception:
            continue
        if not history:
            continue

        first_half_total = 0
        second_half_total = 0
        for rec in history:
            day = rec.get("day", "")
            findings = rec.get("findings", 0) or 0
            if day < first_cutoff:
                first_half_total += findings
            elif day >= second_cutoff_start:
                second_half_total += findings
            # day == first_cutoff is the boundary day — skipped so halves stay equal-length

        delta = second_half_total - first_half_total
        if (
            delta >= _TREND_MIN_ABSOLUTE_DELTA
            and second_half_total >= _TREND_MIN_SECOND_HALF
        ):
            trending.append({
                "audit": audit_name,
                "first_half_findings": first_half_total,
                "second_half_findings": second_half_total,
                "delta": delta,
            })

    summary["checked"] += 1
    if not trending:
        return

    summary["failed"] += 1
    try:
        from app.services.alerting import write_alert
        # Cap detail to top 20 worst trends to avoid payload bloat in
        # the ops_alerts row.
        trending.sort(key=lambda x: -x["delta"])
        top_trends = trending[:20]
        write_alert(
            db,
            severity="warning",
            source="invariant:audit_findings_trend",
            alert_type="invariant_regression",
            summary=(
                f"Audit findings trending up: {len(trending)} audit(s) "
                f"with >= {_TREND_MIN_ABSOLUTE_DELTA} more findings in "
                f"last {_TREND_WINDOW_DAYS // 2}d vs prior {_TREND_WINDOW_DAYS // 2}d"
            ),
            detail={
                "trending_audits": top_trends,
                "total_trending": len(trending),
                "window_days": _TREND_WINDOW_DAYS,
                "min_absolute_delta": _TREND_MIN_ABSOLUTE_DELTA,
                "min_second_half": _TREND_MIN_SECOND_HALF,
                "remediation": (
                    "Check /ops/audit-telemetry for each trending audit. "
                    "A regression class is accumulating without tripping "
                    "preflight. Investigate and either fix the regressions "
                    "or tighten the preflight gate to catch them earlier."
                ),
            },
        )
        summary["alerts_written"] += 1
    except Exception as exc:
        log.error("invariant_monitor: trend alert write failed: %s", exc)
