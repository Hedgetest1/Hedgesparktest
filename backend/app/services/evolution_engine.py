"""
evolution_engine.py — Safe self-improvement system.

Scans codebase and operational data to identify improvements.
Classifies each into risk levels. Stores structured proposals.
Runs weekly via agent_worker.

FORBIDDEN targets (never auto-apply, never propose changes to):
    - dashboard/frontend code
    - billing/pricing logic
    - auth/session/encryption
    - merchant-facing API response shapes

Risk levels:
    LEVEL_1: Safe auto-apply (test-only, signal text, dead code removal)
    LEVEL_2: PR + human approval (service logic, worker changes)
    LEVEL_3: Proposal only (architecture, schema, multi-file refactors)

Closed-loop scanners:
    _scan_support_patterns: merchant bug reports clustered by area
    _scan_feature_requests: repeated feature requests from merchants
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal, ENGINE_DEDUP_STATUSES

log = logging.getLogger("evolution_engine")

_BACKEND_DIR = Path("/opt/wishspark/backend")

# Cooldown: no audit more than once per 6 days
_AUDIT_COOLDOWN_SECONDS = 6 * 86400
_last_audit_run: float | None = None

# Forbidden: never propose changes to these areas.
# Imported from tier_check — single source of truth for protected paths.
try:
    from app.core.tier_check import SCAN_FORBIDDEN_PATTERNS as _FORBIDDEN_TARGETS
    from app.core.tier_check import is_forbidden_path as _is_forbidden_path
except ImportError:
    # Fallback if tier_check not available (should not happen in production)
    _FORBIDDEN_TARGETS = [
        "dashboard/", "app/api/billing", "app/api/shopify_oauth",
        "app/core/token_crypto", "app/core/merchant_session", "app/core/deps.py",
        "app/services/orchestrator.py", "app/models/action_approval",
        "migrations/",
    ]
    _is_forbidden_path = lambda path: any(f in path for f in _FORBIDDEN_TARGETS)  # noqa: E731


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit_cycle_id() -> str:
    """ISO week identifier for dedup, e.g. '2026-W13'."""
    return _now().strftime("%G-W%V")


_REDIS_COOLDOWN_KEY = "hs:cooldown:evolution_audit"


def should_run_audit() -> bool:
    global _last_audit_run
    if _last_audit_run is not None:
        if (time.monotonic() - _last_audit_run) < _AUDIT_COOLDOWN_SECONDS:
            return False
    try:
        from app.core.redis_client import cache_get
        if cache_get(_REDIS_COOLDOWN_KEY) is not None:
            return False
    except Exception:
        pass
    return True


def mark_audit_run():
    global _last_audit_run
    _last_audit_run = time.monotonic()
    try:
        from app.core.redis_client import cache_set
        cache_set(_REDIS_COOLDOWN_KEY, True, _AUDIT_COOLDOWN_SECONDS)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deterministic scanners (no LLM — pure code/data analysis)
# ---------------------------------------------------------------------------

def _scan_large_files() -> list[dict]:
    """Find Python files > 500 lines that may benefit from splitting."""
    proposals = []
    for py in _BACKEND_DIR.rglob("*.py"):
        if any(f in str(py) for f in ["venv/", "node_modules/", "__pycache__", ".next/"]):
            continue
        rel = str(py.relative_to(_BACKEND_DIR))
        if _is_forbidden_path(rel):
            continue
        try:
            lines = len(py.read_text().splitlines())
        except Exception:
            continue
        if lines > 500:
            proposals.append({
                "proposal_type": "refactor",
                "target_file": rel,
                "risk_level": "LEVEL_3",
                "reason": f"File has {lines} lines — consider splitting for maintainability",
                "expected_impact": "Improved readability and testability",
                "auto_applicable": False,
                "dedup_key": f"large_file:{rel}",
            })
    return proposals


def _scan_missing_tests() -> list[dict]:
    """Find service files without corresponding test files."""
    proposals = []
    services_dir = _BACKEND_DIR / "app" / "services"
    tests_dir = _BACKEND_DIR / "tests"
    if not services_dir.exists() or not tests_dir.exists():
        return proposals

    test_files = {f.name for f in tests_dir.glob("test_*.py")}

    for svc in services_dir.glob("*.py"):
        if svc.name.startswith("__"):
            continue
        rel = str(svc.relative_to(_BACKEND_DIR))
        if _is_forbidden_path(rel):
            continue
        expected_test = f"test_{svc.stem}.py"
        if expected_test not in test_files:
            proposals.append({
                "proposal_type": "reliability",
                "target_file": rel,
                "risk_level": "LEVEL_1",
                "reason": f"Service {svc.name} has no dedicated test file ({expected_test})",
                "expected_impact": "Improved test coverage and regression detection",
                "auto_applicable": True,
                "dedup_key": f"missing_test:{svc.name}",
            })
    return proposals


def _scan_todo_fixme() -> list[dict]:
    """Find TODO/FIXME comments that indicate unfinished work."""
    proposals = []
    for py in _BACKEND_DIR.rglob("*.py"):
        if any(f in str(py) for f in ["venv/", "node_modules/", "__pycache__"]):
            continue
        rel = str(py.relative_to(_BACKEND_DIR))
        if _is_forbidden_path(rel):
            continue
        if "evolution_engine" in rel:
            continue  # don't scan self
        try:
            content = py.read_text()
        except Exception:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue  # only scan comment lines
            for marker in ("TODO", "FIXME", "HACK", "XXX"):
                if marker in stripped:
                    proposals.append({
                        "proposal_type": "reliability",
                        "target_file": f"{rel}:{i}",
                        "risk_level": "LEVEL_2",
                        "reason": f"{marker} found: {line.strip()[:120]}",
                        "expected_impact": "Resolve technical debt",
                        "auto_applicable": False,
                        "dedup_key": f"todo:{rel}:{i}:{marker}",
                    })
                    break  # one marker per line
    return proposals


def _scan_worker_health(db: Session) -> list[dict]:
    """Check worker_log for chronic error patterns."""
    from sqlalchemy import text
    proposals = []
    cutoff = _now() - timedelta(days=7)
    rows = db.execute(text("""
        SELECT worker_name, SUM(errors) AS total_errors, COUNT(*) AS cycles
        FROM worker_log
        WHERE started_at >= :cutoff
        GROUP BY worker_name
        HAVING SUM(errors) > 0
    """), {"cutoff": cutoff}).fetchall()

    for r in rows:
        error_rate = round(100 * r[1] / r[2]) if r[2] > 0 else 0
        if error_rate > 10:
            proposals.append({
                "proposal_type": "reliability",
                "target_file": f"app/workers/{r[0]}.py",
                "risk_level": "LEVEL_2",
                "reason": f"Worker {r[0]} has {error_rate}% error rate over 7 days ({r[1]} errors in {r[2]} cycles)",
                "expected_impact": "Improved worker stability",
                "auto_applicable": False,
                "dedup_key": f"worker_errors:{r[0]}",
            })
    return proposals


def _scan_unpinned_deps() -> list[dict]:
    """Check if requirements.txt has unpinned dependencies."""
    proposals = []
    req_file = _BACKEND_DIR / "requirements.txt"
    if not req_file.exists():
        return proposals
    for line in req_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line and ">=" not in line:
            proposals.append({
                "proposal_type": "security",
                "target_file": "requirements.txt",
                "risk_level": "LEVEL_2",
                "reason": f"Dependency '{line}' is not pinned — supply chain risk",
                "expected_impact": "Reproducible builds, reduced supply chain risk",
                "auto_applicable": False,
                "dedup_key": f"unpinned:{line}",
            })
    return proposals


# ---------------------------------------------------------------------------
# Closed-loop scanners — learn from merchant feedback + bug outcomes
# ---------------------------------------------------------------------------

def _scan_support_patterns(db: Session) -> list[dict]:
    """
    Find affected_area clusters in support incidents from the last 30 days.
    If 3+ bug_report incidents cluster in the same area → propose investigation.
    """
    from sqlalchemy import text
    proposals = []
    cutoff = _now() - timedelta(days=30)

    try:
        rows = db.execute(text("""
            SELECT affected_area, COUNT(*) AS cnt
            FROM support_incidents
            WHERE classification = 'bug_report'
              AND created_at >= :cutoff
              AND affected_area IS NOT NULL
              AND affected_area != 'unknown'
            GROUP BY affected_area
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            LIMIT 5
        """), {"cutoff": cutoff}).fetchall()
    except Exception:
        return proposals

    for r in rows:
        area = r[0]
        count = r[1]
        proposals.append({
            "proposal_type": "reliability",
            "target_file": None,
            "risk_level": "LEVEL_2",
            "reason": f"{count} merchant bug reports in '{area}' area over 30 days — investigate root cause",
            "expected_impact": f"Resolve recurring merchant-reported issues in {area}",
            "auto_applicable": False,
            "dedup_key": f"support_cluster:{area}",
        })

    return proposals


def _scan_feature_requests(db: Session) -> list[dict]:
    """
    Find repeated feature requests from merchants in the last 30 days.
    If 2+ feature_request incidents mention the same area → surface for roadmap.
    """
    from sqlalchemy import text
    proposals = []
    cutoff = _now() - timedelta(days=30)

    try:
        rows = db.execute(text("""
            SELECT affected_area, COUNT(*) AS cnt
            FROM support_incidents
            WHERE classification = 'feature_request'
              AND created_at >= :cutoff
              AND affected_area IS NOT NULL
              AND affected_area != 'unknown'
            GROUP BY affected_area
            HAVING COUNT(*) >= 2
            ORDER BY COUNT(*) DESC
            LIMIT 5
        """), {"cutoff": cutoff}).fetchall()
    except Exception:
        return proposals

    for r in rows:
        area = r[0]
        count = r[1]
        proposals.append({
            "proposal_type": "product",
            "target_file": None,
            "risk_level": "LEVEL_3",
            "reason": f"{count} merchants requested features in '{area}' area over 30 days",
            "expected_impact": f"Product improvement aligned with merchant demand in {area}",
            "auto_applicable": False,
            "dedup_key": f"feature_demand:{area}",
        })

    return proposals


# ---------------------------------------------------------------------------
# Main audit runner
# ---------------------------------------------------------------------------

def _sort_by_weakness(proposals: list[dict]) -> list[dict]:
    """
    Sort proposals so weak-domain proposals come first.
    Proposals targeting domains with higher weakness scores are prioritized
    for DB insertion (they pass dedup first and "win" the slot).
    Proposals without a target_file or with no weakness signal sort last.
    """
    try:
        from app.services.loop_health import score_subsystem_weakness
        from app.services.project_brain import classify_file
        from app.core.database import engine
        from sqlalchemy.orm import Session as _Sess
        # Use a read-only session for weakness query (no writes)
        with _Sess(engine) as tmp_db:
            ranking = score_subsystem_weakness(tmp_db, lookback_days=30)
        weakness_map = {w["domain"]: w["score"] for w in ranking}
    except Exception:
        return proposals  # fallback: keep original order

    if not weakness_map:
        return proposals

    def _score(p: dict) -> float:
        target = p.get("target_file")
        if not target:
            return 0
        try:
            domain = classify_file(target.split(":")[0])["domain"]
            return weakness_map.get(domain, 0)
        except Exception:
            return 0

    return sorted(proposals, key=_score, reverse=True)


def run_evolution_audit(db: Session) -> dict:
    """
    Run all evolution scanners. Deduplicate against existing proposals
    that are open, accepted, or needs_revalidation (still under review).
    Store new proposals in DB. Weak-domain proposals are prioritized.
    Returns summary.
    """
    cycle = _audit_cycle_id()
    summary = {"scanned": 0, "new": 0, "deduped": 0}

    all_proposals: list[dict] = []
    all_proposals.extend(_scan_large_files())
    all_proposals.extend(_scan_missing_tests())
    all_proposals.extend(_scan_todo_fixme())
    all_proposals.extend(_scan_worker_health(db))
    all_proposals.extend(_scan_unpinned_deps())
    all_proposals.extend(_scan_support_patterns(db))
    all_proposals.extend(_scan_feature_requests(db))

    # Sort: weak-domain proposals first so they win dedup slots
    all_proposals = _sort_by_weakness(all_proposals)

    summary["scanned"] = len(all_proposals)

    for p in all_proposals:
        dedup = p.get("dedup_key")
        if dedup:
            existing = db.query(EvolutionProposal).filter(
                EvolutionProposal.dedup_key == dedup,
                EvolutionProposal.status.in_(ENGINE_DEDUP_STATUSES),
            ).first()
            if existing:
                summary["deduped"] += 1
                continue

        db.add(EvolutionProposal(
            proposal_type=p["proposal_type"],
            target_file=p.get("target_file"),
            risk_level=p["risk_level"],
            reason=p["reason"],
            expected_impact=p.get("expected_impact"),
            auto_applicable=p.get("auto_applicable", False),
            status="open",
            audit_cycle=cycle,
            dedup_key=p.get("dedup_key"),
        ))
        summary["new"] += 1

    if summary["new"] > 0:
        db.flush()
        log.info("evolution: cycle=%s scanned=%d new=%d deduped=%d", cycle, summary["scanned"], summary["new"], summary["deduped"])

    return summary


# ---------------------------------------------------------------------------
# LEVEL_2 proposal auto-escalation
# ---------------------------------------------------------------------------

_LEVEL2_ESCALATION_DAYS = 14  # escalate after 14 days of inaction


def escalate_stale_proposals(db: Session) -> dict:
    """
    Auto-escalate LEVEL_2 proposals that have been in 'open' status for >14 days
    without human action. Creates an ops_alert to surface them for operator review.

    This prevents detected intelligence from becoming dead letters.
    """
    from datetime import timedelta
    from sqlalchemy import text

    cutoff = _now() - timedelta(days=_LEVEL2_ESCALATION_DAYS)
    summary = {"checked": 0, "escalated": 0}

    stale = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.status == "open",
            EvolutionProposal.risk_level.in_(["LEVEL_2", "LEVEL_3"]),
            EvolutionProposal.created_at <= cutoff,
        )
        .order_by(EvolutionProposal.created_at)
        .limit(5)
        .all()
    )

    for p in stale:
        summary["checked"] += 1

        # Check if we already escalated this one (avoid spam)
        from app.services.alerting import _check_dedup
        existing = _check_dedup(
            db, source="evolution_escalation",
            alert_type="stale_level2_proposal",
            shop_domain=None,
        )
        if existing:
            continue  # already escalated recently

        from app.services.alerting import write_alert
        age_days = (_now() - p.created_at).days
        write_alert(
            db, severity="info", source="evolution_escalation",
            alert_type="stale_level2_proposal",
            summary=f"Evolution proposal #{p.id} ({p.risk_level}) unreviewed for {age_days}d: {p.reason[:120]}",
            detail={
                "proposal_id": p.id,
                "risk_level": p.risk_level,
                "proposal_type": p.proposal_type,
                "target_file": p.target_file,
                "age_days": age_days,
            },
        )
        summary["escalated"] += 1

    if summary["escalated"] > 0:
        db.flush()
        log.info("evolution_escalation: escalated=%d stale LEVEL_2/3 proposals", summary["escalated"])

    return summary
