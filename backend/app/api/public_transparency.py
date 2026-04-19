"""
public_transparency.py — Public transparency page backend.

  GET /public/transparency — anonymized, cache-friendly snapshot of
                             trust-signal metrics that competitors
                             cannot honestly publish.

Shape covers six dimensions:
    1. self_healing         — autonomous-fix receipts (7d/30d)
    2. llm_drift            — last weekly drift corpus run (B2)
    3. pii_guard            — PII violations blocked in last 7d
    4. audit_integrity      — hash-chain tamper detection
    5. preflight            — count of structural guards run on each commit
    6. holdout_proof        — actions measured with outcome evaluation

No PII. No merchant data. Aggregated counts only. Response cached
60s in Redis to absorb traffic from the public `/transparency` page.

This endpoint is paired with the public `/status` endpoint (which
answers "is the system up RIGHT NOW?"). Transparency answers
"is this company trustworthy, with receipts?" — a distinct question
and UI surface.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import engine

router = APIRouter(tags=["public_transparency"])
log = logging.getLogger(__name__)

_CACHE_KEY = "hs:public_transparency:v1"
_CACHE_TTL = 60
_AUDIT_SCRIPT_DIR = Path("/opt/wishspark/backend/scripts")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _self_healing_section() -> dict:
    """Autonomous-fix counts from append-only audit_log."""
    out = {
        "autonomous_fixes_7d": 0,
        "autonomous_fixes_30d": 0,
        "last_fix_at": None,
    }
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS n7,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS n30,
                    MAX(created_at) AS last_at
                FROM audit_log
                WHERE action_type IN (
                    'bugfix_applied',
                    'bugfix_auto_approved',
                    'governed_tier1_applied',
                    'alert_triage'
                )
                  AND status IN ('completed', 'triaged')
            """)).first()
        if row:
            out["autonomous_fixes_7d"] = int(row[0] or 0)
            out["autonomous_fixes_30d"] = int(row[1] or 0)
            last_at = row[2]
            out["last_fix_at"] = (
                last_at.isoformat() if last_at else None
            )
    except Exception as exc:
        log.warning("transparency: self_healing query failed: %s", exc)
    return out


def _llm_drift_section() -> dict:
    """Last-week real-model drift run (B2). Reads Redis history — if
    the run hasn't executed yet we return shape with status=pending."""
    out: dict = {
        "status": "pending",
        "last_run_iso_week": None,
        "json_parse_rate": None,
        "refusal_rate": None,
        "severity_valid_rate": None,
        "provider": None,
        "model": None,
    }
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("public_transparency.llm_drift.no_client")
            return out
        raw = rc.lrange("hs:llm_realmodel_drift:history", 0, 0) or []
        if not raw:
            return out
        first = raw[0]
        if isinstance(first, bytes):
            first = first.decode("utf-8")
        entry = json.loads(first)
        out["status"] = "measured"
        out["last_run_iso_week"] = entry.get("iso_week")
        out["json_parse_rate"] = entry.get("json_parse_rate")
        out["refusal_rate"] = entry.get("refusal_rate")
        out["severity_valid_rate"] = entry.get("severity_valid_rate")
        out["provider"] = entry.get("provider")
        out["model"] = entry.get("model")
    except Exception as exc:
        log.warning("transparency: llm_drift read failed: %s", exc)
    return out


def _pii_guard_section() -> dict:
    """PII violations blocked in last 7 days. Zero is the healthy
    number — if we're routinely blocking violations, something
    upstream is leaky."""
    out = {"violations_7d": 0, "counter_available": True}
    try:
        from app.core.llm_pii_guard import get_violation_count_7d
        out["violations_7d"] = get_violation_count_7d()
    except Exception as exc:
        log.warning("transparency: pii_guard read failed: %s", exc)
        out["counter_available"] = False
    return out


def _audit_integrity_section() -> dict:
    """Hash-chain integrity over the audit_log. Walks the full table
    (limited to 500 most-recent rows for the public endpoint so we
    don't pin a connection for minutes on long histories) and reports
    violation count. A non-zero violation count is a trust breach we
    want visible — hiding it would defeat the point."""
    out: dict = {
        "chained_rows": 0,
        "legacy_rows": 0,
        "violations": 0,
        "head_matches_redis": None,
    }
    try:
        from app.services.audit import verify_audit_log_chain
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            report = verify_audit_log_chain(db, limit=500)
            out["chained_rows"] = int(report.get("chained_rows", 0))
            out["legacy_rows"] = int(report.get("legacy_rows", 0))
            out["violations"] = len(report.get("violations", []) or [])
            out["head_matches_redis"] = report.get("head_matches_redis")
        finally:
            db.close()
    except Exception as exc:
        log.warning("transparency: audit_integrity read failed: %s", exc)
    return out


def _preflight_section() -> dict:
    """Count of structural preflight guards. A file-count is the
    honest metric — each script blocks commits on a distinct failure
    mode. Recomputed from disk so the count stays truthful if
    scripts are added or removed."""
    out = {"audit_count": 0, "audit_names": []}
    try:
        if _AUDIT_SCRIPT_DIR.exists():
            names = sorted(
                p.stem.replace("audit_", "")
                for p in _AUDIT_SCRIPT_DIR.glob("audit_*.py")
            )
            out["audit_count"] = len(names)
            out["audit_names"] = names
    except Exception as exc:
        log.warning("transparency: preflight count failed: %s", exc)
    return out


def _holdout_proof_section() -> dict:
    """Number of actions evaluated with outcome measurement in the
    last 30 days. This is the holdout-proof surface — every outcome
    row links an audit_log action to a measured delta."""
    out = {
        "actions_measured_30d": 0,
        "actions_success_30d": 0,
        "actions_no_effect_30d": 0,
    }
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE outcome_status = 'success') AS success,
                    COUNT(*) FILTER (WHERE outcome_status = 'no_effect') AS no_effect
                FROM action_outcomes
                WHERE executed_at >= NOW() - INTERVAL '30 days'
                  AND outcome_status != 'pending'
            """)).first()
        if row:
            out["actions_measured_30d"] = int(row[0] or 0)
            out["actions_success_30d"] = int(row[1] or 0)
            out["actions_no_effect_30d"] = int(row[2] or 0)
    except Exception as exc:
        log.warning("transparency: holdout_proof query failed: %s", exc)
    return out


def _tests_section() -> dict:
    """Published backend test count. Pulled from a cached Redis key
    the preflight harness writes on each successful commit — avoids
    re-running pytest on every /transparency hit."""
    out = {"backend_tests_passing": None, "last_preflight_at": None}
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("public_transparency.tests.no_client")
            return out
        raw = rc.get("hs:preflight:last_ok")
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode()
            entry = json.loads(raw)
            out["backend_tests_passing"] = entry.get("tests_passing")
            out["last_preflight_at"] = entry.get("at")
    except Exception as exc:
        log.warning("transparency: tests read failed: %s", exc)
    return out


@router.get("/public/transparency")
def get_public_transparency():
    """Public, unauthenticated transparency snapshot. Cached 60s."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cached = rc.get(_CACHE_KEY)
            if cached:
                try:
                    return json.loads(cached)
                except Exception:
                    pass  # SILENT-EXCEPT-OK: cache corruption falls through to recompute
    except Exception as exc:
        log.warning("transparency: cache read failed: %s", exc)
        rc = None

    result = {
        "self_healing": _self_healing_section(),
        "llm_drift": _llm_drift_section(),
        "pii_guard": _pii_guard_section(),
        "audit_integrity": _audit_integrity_section(),
        "preflight": _preflight_section(),
        "holdout_proof": _holdout_proof_section(),
        "tests": _tests_section(),
        "checked_at": _now_iso(),
    }

    if rc is not None:
        try:
            rc.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(result, default=str))
        except Exception as exc:
            log.warning("transparency: cache write failed: %s", exc)

    return result
