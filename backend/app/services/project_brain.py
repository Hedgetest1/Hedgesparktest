"""
project_brain.py — Persistent, structured project knowledge layer.

Builds and maintains a comprehensive brain snapshot from:
    - Filesystem scan (codebase index, domain classification, criticality)
    - Database queries (alerts, bugfixes, merges, evolution, model config, etc.)
    - Strategic constitution (code-defined, versioned, reviewable)

The brain is the ONLY input to the reviewer layer. It replaces ad-hoc
context assembly with a single, persistent, auditable knowledge base.

Public interface:
    build_full_snapshot(db) -> ProjectBrainSnapshot
    build_codebase_index() -> dict
    build_runtime_state(db) -> dict
    get_latest_snapshot(db, snapshot_type="full") -> ProjectBrainSnapshot | None
    get_constitution() -> dict
    classify_file(path) -> dict  {"domain", "criticality", "is_sensitive"}

Cooldown: once per 24 hours (in-process monotonic clock).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.project_brain_snapshot import ProjectBrainSnapshot

log = logging.getLogger("project_brain")

_BACKEND_DIR = Path("/opt/wishspark/backend")

# ---------------------------------------------------------------------------
# Cooldown — once per 24 hours
# ---------------------------------------------------------------------------

_BRAIN_COOLDOWN_SECONDS = 24 * 3600
_last_brain_run: float | None = None


def should_refresh_brain() -> bool:
    if _last_brain_run is None:
        return True
    return (time.monotonic() - _last_brain_run) >= _BRAIN_COOLDOWN_SECONDS


def mark_brain_refreshed():
    global _last_brain_run
    _last_brain_run = time.monotonic()


# ---------------------------------------------------------------------------
# Strategic Constitution — the project's operating doctrine
# ---------------------------------------------------------------------------

CONSTITUTION_VERSION = "v1"

CONSTITUTION = {
    "version": CONSTITUTION_VERSION,
    "principles": [
        {
            "id": "minimal_safe_fixes",
            "rule": "Prefer minimal, safe fixes over broad rewrites.",
            "rationale": "Reduces blast radius and regression risk.",
        },
        {
            "id": "protect_core",
            "rule": "Never auto-apply changes to billing, auth, Shopify OAuth, session crypto, or orchestrator.",
            "rationale": "These are business-critical paths where a regression can cause revenue loss or security breach.",
        },
        {
            "id": "merchant_ux_coherence",
            "rule": "Merchant-facing UX must stay coherent. No random frontend changes.",
            "rationale": "Product trust depends on consistent merchant experience.",
        },
        {
            "id": "tier0_safety",
            "rule": "TIER_0 auto-apply only for truly safe, test-verified, small changes in non-critical paths.",
            "rationale": "Autonomy requires trust, and trust requires proven safety boundaries.",
        },
        {
            "id": "autonomy_over_cosmetics",
            "rule": "Never sacrifice autonomy stability for cosmetic improvements.",
            "rationale": "System self-management capability is more valuable than code aesthetics.",
        },
        {
            "id": "saas_killer_focus",
            "rule": "Favor features that create SaaS competitive advantage, but not through reckless rewrites.",
            "rationale": "Product differentiation must be sustainable, not fragile.",
        },
        {
            "id": "no_overengineering",
            "rule": "Avoid speculative abstractions, premature optimization, and unused flexibility.",
            "rationale": "Complexity is a liability. Build what is needed, not what might be needed.",
        },
        {
            "id": "long_term_ai_management",
            "rule": "Optimize for long-term self-management by AI agents.",
            "rationale": "The system should become increasingly autonomous over time.",
        },
        {
            "id": "no_regressions",
            "rule": "No regressions in business-critical paths (billing, webhooks, order processing, auth).",
            "rationale": "Breaking revenue flow or merchant trust is unacceptable.",
        },
        {
            "id": "scalable_efficient",
            "rule": "Design for horizontal scale and operational efficiency. Minimize fixed costs.",
            "rationale": "SaaS margins depend on lean infrastructure.",
        },
    ],
}


def get_constitution() -> dict:
    """Return the current strategic constitution."""
    return CONSTITUTION


# ---------------------------------------------------------------------------
# Domain & Criticality Classification
# ---------------------------------------------------------------------------

# Domain classification rules — ordered, first match wins
_DOMAIN_RULES: list[tuple[str, str]] = [
    # Critical business domains
    ("app/api/billing", "billing"),
    ("app/services/billing", "billing"),
    ("app/api/shopify_oauth", "shopify_auth"),
    ("app/services/shopify_auth", "shopify_auth"),
    ("app/services/shopify_admin", "shopify_integration"),
    ("app/api/shopify_admin", "shopify_integration"),
    ("app/core/token_crypto", "auth"),
    ("app/core/merchant_session", "auth"),
    ("app/core/deps", "auth"),
    ("app/api/webhooks", "webhooks"),
    ("app/services/order_ingestion", "webhooks"),

    # Orchestration / AI governance
    ("app/services/orchestrator", "orchestrator"),
    ("app/services/bugfix_pipeline", "autofix"),
    ("app/services/promotion_pipeline", "autofix"),
    ("app/services/merge_intelligence", "autofix"),
    ("app/services/evolution", "evolution"),
    ("app/services/model_upgrade", "model_governance"),
    ("app/services/model_config", "model_governance"),
    ("app/core/llm_router", "llm_infra"),
    ("app/core/llm_budget", "llm_infra"),
    ("app/services/reviewer_layer", "reviewer"),
    ("app/services/project_brain", "reviewer"),

    # Worker infrastructure
    ("app/workers/", "workers"),

    # Competitive feature services — added in the 2026-04-11 killer sprint
    ("app/services/benchmarks", "benchmarks"),
    ("app/api/benchmarks", "benchmarks"),
    ("app/services/refund_loss", "refund_loss"),
    ("app/api/refund_loss", "refund_loss"),
    ("app/services/goals", "goals"),
    ("app/api/goals", "goals"),
    ("app/services/revenue_at_risk", "rars"),
    ("app/api/revenue_at_risk", "rars"),
    ("app/services/annotations", "annotations"),
    ("app/api/annotations", "annotations"),

    # Frontend / merchant-facing — ordered from most-specific to catch-all.
    # Billing/onboarding/auth flows in the dashboard are as critical as their
    # backend counterparts: a broken checkout surface = revenue loss.
    ("dashboard/src/app/components/billing", "frontend_billing"),
    ("dashboard/src/app/components/onboarding", "frontend_onboarding"),
    ("dashboard/src/app/components/auth", "frontend_auth"),
    ("dashboard/src/app/install", "frontend_onboarding"),
    ("dashboard/src/app/pricing", "frontend_billing"),
    ("dashboard/", "frontend"),
    ("app/api/merchant", "merchant_api"),
    ("app/api/brief", "merchant_api"),
    ("app/api/nudge", "nudges"),
    ("app/services/nudge", "nudges"),
    ("app/api/chat_support", "support"),
    ("app/services/merchant_chatbot", "support"),

    # Intelligence engines
    ("app/services/intent_engine", "intelligence"),
    ("app/services/price_intelligence", "intelligence"),
    ("app/services/brief_engine", "intelligence"),
    ("app/services/revenue", "intelligence"),
    ("app/services/product_intelligence", "intelligence"),
    ("app/services/cohort", "intelligence"),
    ("app/services/conversion", "intelligence"),

    # Data / tracking
    ("app/api/track", "tracking"),
    ("app/services/event", "tracking"),

    # Observability
    ("app/services/alerting", "observability"),
    ("app/services/audit", "observability"),
    ("app/services/system_summary", "observability"),
    ("app/services/scaling_intelligence", "observability"),
    ("app/services/telegram_agent", "observability"),

    # Infrastructure
    ("app/core/database", "infra"),
    ("app/core/redis", "infra"),
    ("app/core/rate_limit", "infra"),
    ("migrations/", "migrations"),

    # Tests
    ("tests/", "tests"),
]

# Criticality by domain
_DOMAIN_CRITICALITY: dict[str, str] = {
    "billing": "critical",
    "shopify_auth": "critical",
    "auth": "critical",
    "webhooks": "critical",
    "shopify_integration": "high",
    "orchestrator": "high",
    "autofix": "high",
    "model_governance": "high",
    "llm_infra": "high",
    "merchant_api": "medium",
    "nudges": "medium",
    "workers": "medium",
    "support": "medium",
    "reviewer": "medium",
    "intelligence": "low",
    "tracking": "low",
    "observability": "low",
    "infra": "high",
    "migrations": "high",
    "frontend": "high",
    "frontend_billing": "critical",
    "frontend_onboarding": "critical",
    "frontend_auth": "critical",
    # Killer feature sprint domains — the hero features of the product
    "benchmarks": "high",       # peer comparison — critical demo feature
    "refund_loss": "medium",    # loss detection module
    "goals": "medium",          # merchant target tracking
    "rars": "high",             # THE hero number of the dashboard
    "annotations": "low",       # UX enhancement
    "tests": "low",
}

# Sensitive domains where auto_approvable should be false
SENSITIVE_DOMAINS = {
    "billing", "shopify_auth", "auth", "webhooks", "shopify_integration",
    "orchestrator", "migrations",
}

# All known domains
ALL_DOMAINS = set(_DOMAIN_CRITICALITY.keys())


def classify_file(path: str) -> dict:
    """
    Classify a file path into domain, criticality, and sensitivity.
    Returns {"domain", "criticality", "is_sensitive"}.
    """
    for prefix, domain in _DOMAIN_RULES:
        if prefix in path:
            return {
                "domain": domain,
                "criticality": _DOMAIN_CRITICALITY.get(domain, "low"),
                "is_sensitive": domain in SENSITIVE_DOMAINS,
            }
    return {"domain": "other", "criticality": "low", "is_sensitive": False}


# ---------------------------------------------------------------------------
# Codebase Index Builder
# ---------------------------------------------------------------------------

_SKIP_DIRS = {"venv/", "node_modules/", "__pycache__", ".next/", ".git/", "sandbox/"}


def build_codebase_index() -> dict:
    """
    Scan the backend codebase and build a structured index.
    Returns: {files, domains, stats}

    Does NOT read file contents beyond line counts.
    Safe on large codebases — filesystem metadata only.
    """
    files = []
    domain_counts: dict[str, dict] = {}
    tests_dir = _BACKEND_DIR / "tests"
    test_names = set()
    if tests_dir.exists():
        test_names = {f.stem for f in tests_dir.glob("test_*.py")}

    for py in _BACKEND_DIR.rglob("*.py"):
        rel = str(py.relative_to(_BACKEND_DIR))
        if any(skip in rel for skip in _SKIP_DIRS):
            continue

        try:
            lines = len(py.read_text().splitlines())
        except Exception:
            lines = 0

        classification = classify_file(rel)
        domain = classification["domain"]

        # Check if this service has a test file
        has_test = False
        if rel.startswith("app/services/"):
            stem = py.stem
            has_test = f"test_{stem}" in test_names

        entry = {
            "path": rel,
            "domain": domain,
            "criticality": classification["criticality"],
            "is_sensitive": classification["is_sensitive"],
            "lines": lines,
            "has_test": has_test,
        }
        files.append(entry)

        if domain not in domain_counts:
            domain_counts[domain] = {
                "criticality": classification["criticality"],
                "file_count": 0,
                "total_lines": 0,
                "sensitive": classification["is_sensitive"],
            }
        domain_counts[domain]["file_count"] += 1
        domain_counts[domain]["total_lines"] += lines

    # Compute stats
    critical_files = sum(1 for f in files if f["criticality"] in ("critical", "high"))
    services = sum(1 for f in files if f["path"].startswith("app/services/"))
    models = sum(1 for f in files if f["path"].startswith("app/models/"))
    apis = sum(1 for f in files if f["path"].startswith("app/api/"))

    return {
        "files": files,
        "domains": domain_counts,
        "stats": {
            "total_files": len(files),
            "total_lines": sum(f["lines"] for f in files),
            "critical_files": critical_files,
            "services": services,
            "models": models,
            "apis": apis,
            "domains_count": len(domain_counts),
        },
    }


# ---------------------------------------------------------------------------
# Runtime State Builder
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_runtime_state(db: Session) -> dict:
    """
    Query operational state from all relevant tables.
    Returns structured runtime knowledge for the brain.
    """
    now = _now()
    runtime: dict = {}

    # Alerts
    try:
        alert_rows = db.execute(text("""
            SELECT severity, COUNT(*) FROM ops_alerts
            WHERE resolved = false
            GROUP BY severity
        """)).fetchall()
        alert_counts = {r[0]: r[1] for r in alert_rows}
        recent_alerts = db.execute(text("""
            SELECT id, severity, alert_type, summary, created_at
            FROM ops_alerts WHERE resolved = false
            ORDER BY created_at DESC LIMIT 5
        """)).fetchall()
        runtime["alerts"] = {
            "total": sum(alert_counts.values()),
            "critical": alert_counts.get("critical", 0),
            "warning": alert_counts.get("warning", 0),
            "info": alert_counts.get("info", 0),
            "recent": [
                {"id": r[0], "severity": r[1], "type": r[2], "summary": r[3],
                 "created_at": r[4].isoformat() + "Z" if r[4] else None}
                for r in recent_alerts
            ],
        }
    except Exception:
        runtime["alerts"] = {"total": 0, "critical": 0, "warning": 0, "info": 0, "recent": []}

    # Bugfixes
    try:
        bf_rows = db.execute(text("""
            SELECT status, COUNT(*) FROM bugfix_candidates
            GROUP BY status
        """)).fetchall()
        bf_counts = {r[0]: r[1] for r in bf_rows}
        runtime["bugfixes"] = {
            "open": bf_counts.get("open", 0),
            "applied": bf_counts.get("applied", 0),
            "failed": bf_counts.get("apply_failed", 0) + bf_counts.get("rolled_back", 0),
            "total": sum(bf_counts.values()),
        }
    except Exception:
        runtime["bugfixes"] = {"open": 0, "applied": 0, "failed": 0, "total": 0}

    # Merge outcomes
    try:
        merge_rows = db.execute(text("""
            SELECT evaluation_status, COUNT(*) FROM merge_outcomes
            GROUP BY evaluation_status
        """)).fetchall()
        merge_counts = {r[0]: r[1] for r in merge_rows}
        runtime["merges"] = {
            "total": sum(merge_counts.values()),
            "healthy": merge_counts.get("healthy", 0),
            "regressed": merge_counts.get("regressed", 0),
            "pending": merge_counts.get("pending", 0),
        }
    except Exception:
        runtime["merges"] = {"total": 0, "healthy": 0, "regressed": 0, "pending": 0}

    # Evolution proposals
    try:
        evo_rows = db.execute(text("""
            SELECT status, risk_level, COUNT(*) FROM evolution_proposals
            GROUP BY status, risk_level
        """)).fetchall()
        open_count = sum(r[2] for r in evo_rows if r[0] == "open")
        by_risk = {}
        gc_summary = {}
        for r in evo_rows:
            if r[0] == "open":
                by_risk[r[1]] = by_risk.get(r[1], 0) + r[2]
            if r[0] in ("obsolete", "resolved_indirectly", "needs_revalidation"):
                gc_summary[r[0]] = gc_summary.get(r[0], 0) + r[2]
        runtime["evolution"] = {
            "open": open_count,
            "by_risk": by_risk,
            "gc_summary": gc_summary,
        }
    except Exception:
        runtime["evolution"] = {"open": 0, "by_risk": {}, "gc_summary": {}}

    # Model config
    try:
        model_rows = db.execute(text("""
            SELECT module, provider, model_name FROM active_model_configs
            WHERE is_active = true
        """)).fetchall()
        runtime["model_config"] = {
            "modules": {r[0]: {"provider": r[1], "model": r[2]} for r in model_rows},
        }
    except Exception:
        runtime["model_config"] = {"modules": {}}

    # System vitals (lightweight — reuse existing service)
    try:
        from app.services.system_summary import build_system_summary
        runtime["system_vitals"] = build_system_summary(db)
    except Exception:
        runtime["system_vitals"] = {}

    # LLM budget
    try:
        from app.core.llm_budget import get_usage_summary
        runtime["llm_budget"] = get_usage_summary()
    except Exception:
        runtime["llm_budget"] = {}

    # Support incidents
    try:
        si_rows = db.execute(text("""
            SELECT status, COUNT(*) FROM support_incidents
            GROUP BY status
        """)).fetchall()
        si_counts = {r[0]: r[1] for r in si_rows}
        runtime["support_incidents"] = {
            "open": si_counts.get("open", 0) + si_counts.get("triaged", 0) + si_counts.get("investigating", 0),
            "resolved": si_counts.get("resolved", 0),
            "total": sum(si_counts.values()),
        }
    except Exception:
        runtime["support_incidents"] = {"open": 0, "resolved": 0, "total": 0}

    # Scaling recommendations
    try:
        scale_count = db.execute(text(
            "SELECT COUNT(*) FROM scaling_recommendations WHERE status = 'active'"
        )).scalar() or 0
        runtime["scaling"] = {"active_recommendations": scale_count}
    except Exception:
        runtime["scaling"] = {"active_recommendations": 0}

    return runtime


# ---------------------------------------------------------------------------
# Full Snapshot Builder
# ---------------------------------------------------------------------------

def build_full_snapshot(db: Session) -> ProjectBrainSnapshot:
    """
    Build a complete brain snapshot: codebase index + runtime state.
    Stores in DB and returns the row.
    """
    codebase = build_codebase_index()
    runtime = build_runtime_state(db)

    snapshot = ProjectBrainSnapshot(
        snapshot_type="full",
        codebase_json=json.dumps(codebase, default=str),
        runtime_json=json.dumps(runtime, default=str),
        total_files=codebase["stats"]["total_files"],
        critical_files=codebase["stats"]["critical_files"],
        open_alerts=runtime.get("alerts", {}).get("total", 0),
        open_bugfixes=runtime.get("bugfixes", {}).get("open", 0),
        open_evolution=runtime.get("evolution", {}).get("open", 0),
        constitution_version=CONSTITUTION_VERSION,
    )
    db.add(snapshot)
    db.flush()

    log.info(
        "brain: snapshot=%d files=%d critical=%d alerts=%d bugfixes=%d evolution=%d",
        snapshot.id, snapshot.total_files, snapshot.critical_files,
        snapshot.open_alerts, snapshot.open_bugfixes, snapshot.open_evolution,
    )
    return snapshot


def get_latest_snapshot(db: Session, snapshot_type: str = "full") -> ProjectBrainSnapshot | None:
    """Return the most recent brain snapshot of the given type."""
    return (
        db.query(ProjectBrainSnapshot)
        .filter(ProjectBrainSnapshot.snapshot_type == snapshot_type)
        .order_by(ProjectBrainSnapshot.created_at.desc())
        .first()
    )


def get_brain_summary(db: Session) -> dict:
    """
    Operator-facing summary of current brain state.
    Returns structured dict suitable for API response.
    """
    snapshot = get_latest_snapshot(db)
    if not snapshot:
        return {"status": "no_snapshot", "message": "No brain snapshot exists yet. Run a refresh."}

    age_hours = 0
    if snapshot.created_at:
        age_hours = round((_now() - snapshot.created_at).total_seconds() / 3600, 1)

    # Parse codebase stats without returning full file list
    codebase_stats = {}
    if snapshot.codebase_json:
        try:
            cb = json.loads(snapshot.codebase_json)
            codebase_stats = cb.get("stats", {})
            codebase_stats["domains"] = {
                k: {"criticality": v["criticality"], "files": v["file_count"]}
                for k, v in cb.get("domains", {}).items()
            }
        except (ValueError, TypeError):
            pass

    # Parse runtime summary
    runtime_summary = {}
    if snapshot.runtime_json:
        try:
            rt = json.loads(snapshot.runtime_json)
            runtime_summary = {
                "alerts": rt.get("alerts", {}).get("total", 0),
                "critical_alerts": rt.get("alerts", {}).get("critical", 0),
                "open_bugfixes": rt.get("bugfixes", {}).get("open", 0),
                "merge_health": f"{rt.get('merges', {}).get('healthy', 0)} healthy / {rt.get('merges', {}).get('regressed', 0)} regressed",
                "open_evolution": rt.get("evolution", {}).get("open", 0),
                "support_incidents_open": rt.get("support_incidents", {}).get("open", 0),
                "active_scaling_recs": rt.get("scaling", {}).get("active_recommendations", 0),
            }
        except (ValueError, TypeError):
            pass

    return {
        "status": "active",
        "snapshot_id": snapshot.id,
        "snapshot_type": snapshot.snapshot_type,
        "created_at": snapshot.created_at.isoformat() + "Z" if snapshot.created_at else None,
        "age_hours": age_hours,
        "stale": age_hours > 48,
        "constitution_version": snapshot.constitution_version,
        "codebase": codebase_stats,
        "runtime": runtime_summary,
        "summary_stats": {
            "total_files": snapshot.total_files,
            "critical_files": snapshot.critical_files,
            "open_alerts": snapshot.open_alerts,
            "open_bugfixes": snapshot.open_bugfixes,
            "open_evolution": snapshot.open_evolution,
        },
    }
