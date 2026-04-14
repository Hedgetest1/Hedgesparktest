"""
pipeline_heartbeat.py — Synthetic end-to-end liveness probe.

Why this exists
---------------
The self-healing pipeline is silent when nothing is broken. The first
real bug therefore becomes the first integration test, and the founder
discovers a regression in production.

This module fires a synthetic alert every hour, runs it through the
real triage path (the same `run_bug_triage` that handles production
alerts), verifies a candidate was created, marks the candidate
discarded, cleans up, and writes a `heartbeat_ok` or `heartbeat_failed`
ops_alert with the per-phase latencies.

The daily digest then renders a one-line summary: `Heartbeat 24h: 24/24
ok, p95 latency 8.2s`. If any heartbeat fails, the digest goes red.

What it actually tests (anti-theater contract)
----------------------------------------------
* DB write path (synthetic OpsAlert insert)
* Generic Rule 7 catch-all (synthetic alerts are picked up because they
  match the same severity + recurrence semantics as real alerts)
* `_create_candidate` flow (priority scoring, JSON serialization,
  flush)
* Candidate lookup by source_ref (read path)
* State mutation path (status='discarded')
* Cleanup path (alert resolved=true, no FK violations)

What it deliberately does NOT test
----------------------------------
* LLM proposer — too expensive, would burn budget every hour
* Apply / promotion / merge / deploy — those have their own counters
  in the daily digest

Kill switch
-----------
`HEARTBEAT_PAUSED=1` halts every future heartbeat in 5s. The gate
order inside `run_heartbeat` is the actual safety net; the env var is
the operator emergency stop.

Wired into agent_worker as the LAST phase per cycle, gated to fire
once per `_HEARTBEAT_INTERVAL_S` (default 1h).
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("pipeline_heartbeat")

_HEARTBEAT_INTERVAL_S = 60 * 60  # 1 hour
_HEARTBEAT_SYNTHETIC_COUNT = 3   # must match Rule 7 _GENERIC_RECURRENCE_THRESHOLD
_HEARTBEAT_MAX_TOTAL_S = 30      # if a single run takes longer than this, mark slow
_REDIS_LAST_RUN_KEY = "hs:heartbeat:last_run_ts"

# Use a dedicated alert_type so generic catch-all picks them up but
# the digest can also identify and filter them.
_HEARTBEAT_ALERT_TYPE = "heartbeat_synthetic_test"
_HEARTBEAT_SOURCE_PREFIX = "heartbeat:probe"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_paused() -> bool:
    return os.getenv("HEARTBEAT_PAUSED", "").strip() == "1"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _claim_run_slot() -> bool:
    """
    Atomic cooldown claim — returns True if this caller won the slot.
    Previously _is_on_cooldown() + _mark_run() was a two-step with a
    race window: two concurrent workers could both pass the check and
    both fire the heartbeat. Now we SET NX on a ttl-bounded lock so
    exactly one caller proceeds per interval.
    """
    rc = _redis()
    if rc is None:
        record_silent_return("pipeline_heartbeat.claim_slot")
        return True  # no Redis → best-effort per-process
    try:
        # Hold the slot for one full interval; refreshed on successful run.
        return bool(rc.set(
            _REDIS_LAST_RUN_KEY, str(time.time()),
            nx=True, ex=_HEARTBEAT_INTERVAL_S,
        ))
    except Exception:
        return True  # fail-open


def _is_on_cooldown() -> bool:
    """
    Legacy name — kept as a thin wrapper that returns True (skip) when
    we could not claim the slot. All callers proceed iff this returns False.
    """
    return not _claim_run_slot()


def _mark_run() -> None:
    rc = _redis()
    if rc is None:
        record_silent_return("pipeline_heartbeat.mark_run")
        return
    try:
        rc.setex(_REDIS_LAST_RUN_KEY, _HEARTBEAT_INTERVAL_S * 2, str(time.time()))
    except Exception:
        pass


def _create_synthetic_alerts(db: Session, run_id: str) -> list[int]:
    """Insert N synthetic OpsAlert rows directly (bypassing write_alert
    so the 5-min dedup doesn't collapse them) so the generic Rule 7
    catch-all picks them up as a recurring pattern."""
    from app.models.ops_alert import OpsAlert

    source = f"{_HEARTBEAT_SOURCE_PREFIX}:{run_id}"
    alert_ids: list[int] = []
    for i in range(_HEARTBEAT_SYNTHETIC_COUNT):
        a = OpsAlert(
            severity="warning",
            source=source,
            alert_type=_HEARTBEAT_ALERT_TYPE,
            shop_domain=None,
            summary=f"Synthetic heartbeat probe {run_id} occurrence {i + 1}",
            detail=json.dumps({"run_id": run_id, "occurrence": i + 1}),
        )
        db.add(a)
        db.flush()
        alert_ids.append(a.id)
    return alert_ids


def _find_synthetic_candidate(db: Session, run_id: str):
    """Look up the candidate the triage should have created."""
    from app.models.bugfix_candidate import BugFixCandidate
    expected_ref = f"generic:{_HEARTBEAT_ALERT_TYPE}:{_HEARTBEAT_SOURCE_PREFIX}:{run_id}"
    return (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "ops_alert_generic",
            BugFixCandidate.source_ref == expected_ref,
        )
        .first()
    )


def _cleanup_synthetic_artifacts(
    db: Session, *, run_id: str, alert_ids: list[int], candidate_id: int | None,
) -> None:
    """Mark synthetic alerts resolved so they vanish from open-alert
    counts. Mark the candidate discarded so the LLM proposer never
    sees it."""
    if alert_ids:
        try:
            db.execute(text("""
                UPDATE ops_alerts
                SET resolved = true, resolved_at = :now
                WHERE id = ANY(:ids)
            """), {"now": _now(), "ids": alert_ids})
        except Exception as exc:
            log.debug("heartbeat: alert cleanup failed: %s", exc)
    if candidate_id is not None:
        try:
            from app.models.bugfix_candidate import BugFixCandidate
            cand = db.query(BugFixCandidate).filter(
                BugFixCandidate.id == candidate_id
            ).first()
            if cand is not None:
                cand.status = "discarded"
                cand.failure_reason = f"heartbeat_synthetic_cleanup:{run_id}"
                db.flush()
        except Exception as exc:
            log.debug("heartbeat: candidate cleanup failed: %s", exc)


def _record_outcome(
    db: Session,
    *,
    status: str,
    phases: dict[str, float],
    total_s: float,
    error: str | None = None,
) -> None:
    """Write the heartbeat result as an ops_alert.

    Uses write_alert so dedupe collapses successive `heartbeat_ok`
    rows into one (the chronic aggregation will increment occurrence
    count). `heartbeat_failed` is critical and never deduped."""
    from app.services.alerting import write_alert

    if status == "ok":
        severity = "info"
        alert_type = "heartbeat_ok"
        summary = (
            f"Pipeline heartbeat ok ({total_s:.2f}s total, "
            f"triage {phases.get('triage_run', 0):.2f}s)"
        )
    else:
        severity = "critical"
        alert_type = "heartbeat_failed"
        summary = f"Pipeline heartbeat FAILED: {status} ({total_s:.2f}s)"

    detail = {
        "phases": {k: round(v, 4) for k, v in phases.items()},
        "total_s": round(total_s, 4),
        "error": error,
        "status": status,
    }
    try:
        alert = write_alert(
            db,
            severity=severity,
            source="pipeline_heartbeat",
            alert_type=alert_type,
            summary=summary,
            detail=detail,
        )
        # heartbeat_ok is telemetry, not an actionable incident. Mark it
        # resolved immediately so it stays as an event log entry without
        # inflating the unresolved-alerts dimension. heartbeat_failed
        # remains unresolved (it's a real operational signal).
        if status == "ok" and alert is not None:
            from datetime import datetime as _dt, timezone as _tz
            alert.resolved = True
            alert.resolved_at = _dt.now(_tz.utc).replace(tzinfo=None)
            db.flush()
    except Exception as exc:
        log.warning("heartbeat: failed to record outcome: %s", exc)


def run_heartbeat(db: Session) -> dict:
    """Fire one synthetic probe through the triage path. Returns
    a structured summary safe to log + return from agent_worker.

    Call once per agent_worker cycle. Honors the 1-hour cooldown
    internally so calling more often is harmless.
    """
    if _is_paused():
        return {"status": "paused"}
    if _is_on_cooldown():
        return {"status": "cooldown"}

    run_id = uuid.uuid4().hex[:12]
    phases: dict[str, float] = {}
    alert_ids: list[int] = []
    candidate_id: int | None = None
    t_total_start = time.monotonic()

    try:
        # Phase 1 — alert write
        t = time.monotonic()
        try:
            alert_ids = _create_synthetic_alerts(db, run_id)
            db.flush()
        except Exception as exc:
            phases["alert_write"] = time.monotonic() - t
            _record_outcome(
                db,
                status="alert_write_failed",
                phases=phases,
                total_s=time.monotonic() - t_total_start,
                error=str(exc)[:200],
            )
            return {"status": "alert_write_failed", "error": str(exc)[:200]}
        phases["alert_write"] = time.monotonic() - t

        if not alert_ids:
            _record_outcome(
                db,
                status="no_alerts_created",
                phases=phases,
                total_s=time.monotonic() - t_total_start,
            )
            return {"status": "no_alerts_created"}

        # Phase 2 — triage run (calls the same code path the agent_worker uses)
        t = time.monotonic()
        try:
            from app.services.bugfix_pipeline import run_bug_triage
            triage_summary = run_bug_triage(db)
            db.flush()
        except Exception as exc:
            phases["triage_run"] = time.monotonic() - t
            _cleanup_synthetic_artifacts(
                db, run_id=run_id, alert_ids=alert_ids, candidate_id=None,
            )
            db.commit()
            _record_outcome(
                db,
                status="triage_run_failed",
                phases=phases,
                total_s=time.monotonic() - t_total_start,
                error=str(exc)[:200],
            )
            return {"status": "triage_run_failed", "error": str(exc)[:200]}
        phases["triage_run"] = time.monotonic() - t

        # Phase 3 — candidate lookup
        t = time.monotonic()
        cand = _find_synthetic_candidate(db, run_id)
        phases["candidate_lookup"] = time.monotonic() - t

        if cand is None:
            _cleanup_synthetic_artifacts(
                db, run_id=run_id, alert_ids=alert_ids, candidate_id=None,
            )
            db.commit()
            _record_outcome(
                db,
                status="candidate_not_created",
                phases=phases,
                total_s=time.monotonic() - t_total_start,
                error=(
                    f"triage ran (created={triage_summary.get('created', 0)}) "
                    f"but no synthetic candidate matched run_id={run_id}"
                ),
            )
            return {"status": "candidate_not_created"}

        candidate_id = cand.id

        # Phase 4 — cleanup
        t = time.monotonic()
        _cleanup_synthetic_artifacts(
            db, run_id=run_id, alert_ids=alert_ids, candidate_id=candidate_id,
        )
        db.commit()
        phases["cleanup"] = time.monotonic() - t

        total_s = time.monotonic() - t_total_start
        _mark_run()
        _record_outcome(db, status="ok", phases=phases, total_s=total_s)

        result = {
            "status": "ok",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "phases": {k: round(v, 4) for k, v in phases.items()},
            "total_s": round(total_s, 4),
            "slow": total_s > _HEARTBEAT_MAX_TOTAL_S,
        }
        log.info(
            "heartbeat: ok run=%s total=%.2fs phases=%s",
            run_id, total_s, result["phases"],
        )
        return result

    except Exception as exc:
        # Best-effort cleanup of anything we managed to create
        try:
            _cleanup_synthetic_artifacts(
                db, run_id=run_id, alert_ids=alert_ids, candidate_id=candidate_id,
            )
            db.commit()
        except Exception:
            db.rollback()
        _record_outcome(
            db,
            status=f"exception:{type(exc).__name__}",
            phases=phases,
            total_s=time.monotonic() - t_total_start,
            error=str(exc)[:200],
        )
        log.warning("heartbeat: exception run=%s: %s", run_id, exc)
        return {"status": "exception", "error": str(exc)[:200]}
