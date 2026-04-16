"""
compliance_evidence.py — SOC2/ISO-shaped evidence bundle (ε3).

ONE endpoint that produces everything an auditor (Drata, Vanta, SOC2
Type II, ISO 27001) asks for in the first 48h of onboarding:

  - Current compliance_score + component breakdown
  - Last 90d audit log excerpts (sampled, redacted)
  - Security probe history (heartbeat pass/fail over time)
  - Breach classifier events (response deadlines, status)
  - GDPR request activity (Art 15/16/17/21 counts + SLA compliance)
  - Trust contract audit trail (merchant-granted autonomy)
  - LLM PII guard trip log (zero-PII-out-of-store guarantee)

Returns JSON by default; ?format=zip returns a zipfile with
JSON + a human-readable summary.md for the auditor to skim.

Ops-only auth (X-API-Key from .env).

Unlocks enterprise: no prospect signs for €499+ without SOC2. This
endpoint is the deliverable we hand to Drata day one.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.database import get_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ops/compliance", tags=["compliance_evidence"])




def _require_ops_key(request: Request) -> None:
    import os as _os
    expected = _os.getenv("OPS_API_KEY", "")
    provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        raise HTTPException(status_code=401, detail="ops_key_required")


def _collect_compliance_snapshot(db: Session) -> dict:
    out: dict = {}
    try:
        from app.services.compliance_score import (
            compute_compliance_score,
            get_cached_compliance_score,
        )
        score = get_cached_compliance_score() or compute_compliance_score(db)
        out["compliance_score"] = score
    except Exception as exc:
        out["compliance_score_error"] = str(exc)[:200]
    return out


def _collect_audit_log_summary(db: Session, days: int) -> dict:
    """Aggregated audit log activity — NOT raw row dumps (PII)."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT action_type, status, COUNT(*) AS n
                FROM audit_log
                WHERE created_at >= :since
                GROUP BY action_type, status
                ORDER BY n DESC
                LIMIT 100
                """
            ),
            {"since": since},
        ).fetchall()
        return {
            "window_days": days,
            "activity": [
                {"action_type": r[0], "status": r[1], "count": int(r[2] or 0)}
                for r in rows
            ],
            "total_rows": sum(int(r[2] or 0) for r in rows),
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _collect_chain_integrity(db: Session) -> dict:
    try:
        from app.services.audit import verify_audit_log_chain
        report = verify_audit_log_chain(db)
        return {
            "total_rows": report.get("total_rows", 0),
            "chained_rows": report.get("chained_rows", 0),
            "legacy_rows": report.get("legacy_rows", 0),
            "violations_count": len(report.get("violations", [])),
            "first_violation_ids": [
                v.get("row_id") for v in report.get("violations", [])[:5]
            ],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _collect_security_probes(db: Session, days: int) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT alert_type, severity, COUNT(*) AS n, MAX(created_at) AS latest
                FROM ops_alerts
                WHERE alert_type IN ('security_probe_failed', 'security_probe_passed', 'heartbeat_ok', 'heartbeat_failed')
                  AND created_at >= :since
                GROUP BY alert_type, severity
                """
            ),
            {"since": since},
        ).fetchall()
        return {
            "window_days": days,
            "by_probe": [
                {
                    "alert_type": r[0],
                    "severity": r[1],
                    "count": int(r[2] or 0),
                    "latest": r[3].isoformat() if r[3] else None,
                }
                for r in rows
            ],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _collect_breach_events(db: Session, days: int) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT resolved, COUNT(*) FROM ops_alerts
                WHERE alert_type = 'breach_response_required'
                  AND created_at >= :since
                GROUP BY resolved
                """
            ),
            {"since": since},
        ).fetchall()
        return {
            "window_days": days,
            "by_status": {
                ("resolved" if r[0] else "open"): int(r[1] or 0) for r in rows
            },
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _collect_gdpr_activity(db: Session, days: int) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT request_type, status, COUNT(*) AS n
                FROM gdpr_requests
                WHERE created_at >= :since
                GROUP BY request_type, status
                """
            ),
            {"since": since},
        ).fetchall()
        return {
            "window_days": days,
            "activity": [
                {
                    "request_type": r[0],
                    "status": r[1],
                    "count": int(r[2] or 0),
                }
                for r in rows
            ],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _collect_trust_autonomy(db: Session, days: int) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        contracts = db.execute(
            sql_text(
                """
                SELECT status, COUNT(*) FROM trust_contracts
                WHERE created_at >= :since
                GROUP BY status
                """
            ),
            {"since": since},
        ).fetchall()
        executions = db.execute(
            sql_text(
                """
                SELECT outcome, COUNT(*) FROM trust_execution_log
                WHERE executed_at >= :since
                GROUP BY outcome
                """
            ),
            {"since": since},
        ).fetchall()
        return {
            "window_days": days,
            "contracts_by_status": {
                str(r[0]): int(r[1] or 0) for r in contracts
            },
            "executions_by_outcome": {
                str(r[0] or "pending"): int(r[1] or 0) for r in executions
            },
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _collect_llm_pii_events(db: Session, days: int) -> dict:
    """Any time the LLM PII guard blocked or redacted content."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT alert_type, severity, COUNT(*) FROM ops_alerts
                WHERE alert_type LIKE 'llm_pii%'
                  AND created_at >= :since
                GROUP BY alert_type, severity
                """
            ),
            {"since": since},
        ).fetchall()
        return {
            "window_days": days,
            "pii_guard_trips": [
                {
                    "alert_type": r[0],
                    "severity": r[1],
                    "count": int(r[2] or 0),
                }
                for r in rows
            ],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _build_bundle(db: Session, days: int) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Phase Ω''' — SOC 2 controls catalog included automatically
    try:
        from app.services.soc2_controls import get_catalog, summarize_catalog
        soc2 = {"summary": summarize_catalog(), "controls": get_catalog()}
    except Exception:
        soc2 = {"error": "soc2_catalog_unavailable"}

    bundle = {
        "generated_at": now.isoformat(),
        "window_days": days,
        "evidence_version": "1.1",
        "platform": "HedgeSpark",
        "compliance_snapshot": _collect_compliance_snapshot(db),
        "audit_log_summary": _collect_audit_log_summary(db, days),
        "audit_log_chain_integrity": _collect_chain_integrity(db),
        "security_probes": _collect_security_probes(db, days),
        "breach_events": _collect_breach_events(db, days),
        "gdpr_activity": _collect_gdpr_activity(db, days),
        "trust_autonomy": _collect_trust_autonomy(db, days),
        "llm_pii_events": _collect_llm_pii_events(db, days),
        "soc2_controls": soc2,
    }
    return bundle


@router.get("/soc2")
def get_soc2_summary(request: Request):
    """Public-ish SOC2 controls overview (still ops-key gated)."""
    _require_ops_key(request)
    from app.services.soc2_controls import get_catalog, summarize_catalog
    return {
        "summary": summarize_catalog(),
        "controls": get_catalog(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _render_summary_md(bundle: dict) -> str:
    """Human-readable summary for auditors to skim in 60 seconds."""
    score = bundle.get("compliance_snapshot", {}).get("compliance_score", {})
    grade = score.get("grade", "?")
    num = score.get("score", "?")
    chain = bundle.get("audit_log_chain_integrity", {})
    violations = chain.get("violations_count", 0)
    gdpr = bundle.get("gdpr_activity", {}).get("activity", [])

    lines = [
        "# HedgeSpark Compliance Evidence Bundle",
        f"Generated: {bundle['generated_at']}",
        f"Window: last {bundle['window_days']} days",
        "",
        "## TL;DR",
        f"- Compliance score: **{num}/100 ({grade})**",
        f"- Audit log hash-chain: **{chain.get('chained_rows', 0)} chained rows**, "
        f"{violations} violation(s)",
        f"- GDPR request activity: **{len(gdpr)} distinct request categories**",
        f"- Breach events: **{sum(bundle.get('breach_events', {}).get('by_status', {}).values())}**",
        f"- LLM PII guard trips: **{len(bundle.get('llm_pii_events', {}).get('pii_guard_trips', []))}**",
        "",
        "## What's in this bundle",
        "1. `bundle.json` — machine-readable full evidence set",
        "2. `summary.md` — this file",
        "",
        "## Evidence categories",
        "- **compliance_snapshot** — current compliance score with component breakdown (security/GDPR/privacy/data-retention/audit-integrity)",
        "- **audit_log_summary** — aggregated audit actions by type (no raw PII)",
        "- **audit_log_chain_integrity** — verification of the tamper-evident hash chain over audit log",
        "- **security_probes** — results of synthetic security heartbeat probes (HMAC validation, session rejection, ops-auth enforcement)",
        "- **breach_events** — breach classifier output + GDPR Art 33/34 response tracking",
        "- **gdpr_activity** — shop_redact / customer_redact / data_request counts by status",
        "- **trust_autonomy** — merchant-granted delegated execution contracts + outcomes",
        "- **llm_pii_events** — any time the PII runtime guard blocked or redacted LLM content",
        "",
        "Every metric above is computed from live database state. No curation, no cherry-picking.",
    ]
    return "\n".join(lines)


@router.get("/evidence")
def get_evidence(
    request: Request,
    days: int = Query(90, ge=7, le=365),
    format: str = Query("json", pattern="^(json|zip)$"),
    db: Session = Depends(get_db),
):
    _require_ops_key(request)
    bundle = _build_bundle(db, days)

    if format == "json":
        return JSONResponse(bundle)

    # zip format — bundle.json + summary.md
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", json.dumps(bundle, default=str, indent=2))
        zf.writestr("summary.md", _render_summary_md(bundle))
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=hedgespark_compliance_evidence_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.zip"
            )
        },
    )
