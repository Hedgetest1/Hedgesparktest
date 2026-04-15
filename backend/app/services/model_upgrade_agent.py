"""
model_upgrade_agent.py — Detects, evaluates, and proposes model version upgrades.

Config-driven: candidate models are defined in CANDIDATE_MODELS, not discovered
from provider APIs (unreliable/undocumented). Update this config when new models
are released.

Flow:
    1. scan_for_upgrades() — compare current approved vs candidates, create proposals
    2. evaluate_upgrade() — run minimal benchmark, store result
    3. Operator approves/rejects via API
    4. activate_upgrade() — update llm_router config (separate from approval)

No auto-switch. No auto-activation. Human-gated at every step.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.llm_router import SONNET, OPUS, SONNET_OPENAI
from app.models.model_upgrade import ModelUpgradeProposal
from app.services.audit import write_audit_log

log = logging.getLogger("model_upgrade_agent")

# ---------------------------------------------------------------------------
# Model registry: currently approved models per module
# ---------------------------------------------------------------------------

def _get_current_approved(module: str, db=None) -> dict:
    """Read current approved model from persistent config."""
    try:
        from app.services.model_config import get_active_model
        return get_active_model(module, db)
    except Exception:
        return {"provider": "anthropic", "model": SONNET}

# Candidate models to consider (update manually when new versions release)
CANDIDATE_MODELS = [
    {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "modules": ["orchestrator", "bugfix_proposal", "evolution_audit"],
        "reason": "Latest Sonnet release — improved instruction following",
        "expected_benefit": "Better structured JSON output, fewer parse errors",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-4-20250514",
        "modules": ["bugfix_proposal"],
        "reason": "Opus 4 — stronger reasoning for complex multi-file patches",
        "expected_benefit": "Higher quality patches for TIER_1+ bugs",
    },
]

# Scheduling: max once per 7 days
_SCAN_COOLDOWN_S = 7 * 86400
_last_scan: float | None = None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def should_run_scan() -> bool:
    if _last_scan is None:
        return True
    return (time.monotonic() - _last_scan) >= _SCAN_COOLDOWN_S


def mark_scan_run():
    global _last_scan
    _last_scan = time.monotonic()


# ---------------------------------------------------------------------------
# Phase 1: Detect upgrade candidates
# ---------------------------------------------------------------------------

def scan_for_upgrades(db: Session) -> dict:
    """Compare current approved models against candidates. Create proposals. Dedup."""
    summary = {"scanned": 0, "created": 0, "deduped": 0}

    for candidate in CANDIDATE_MODELS:
        for module in candidate["modules"]:
            summary["scanned"] += 1
            current = _get_current_approved(module, db)

            # Skip if candidate IS the current model
            if current.get("model") == candidate["model"] and current.get("provider") == candidate["provider"]:
                continue

            # Dedup: no open/pending/evaluating proposal for same pair
            existing = (
                db.query(ModelUpgradeProposal)
                .filter(
                    ModelUpgradeProposal.current_model == current.get("model", "unknown"),
                    ModelUpgradeProposal.candidate_model == candidate["model"],
                    ModelUpgradeProposal.target_module == module,
                    ModelUpgradeProposal.status.in_(["pending", "evaluating", "evaluated"]),
                )
                .first()
            )
            if existing:
                summary["deduped"] += 1
                continue

            db.add(ModelUpgradeProposal(
                current_provider=current.get("provider", "unknown"),
                current_model=current.get("model", "unknown"),
                candidate_provider=candidate["provider"],
                candidate_model=candidate["model"],
                target_module=module,
                reason=candidate.get("reason", ""),
                expected_benefit=candidate.get("expected_benefit", ""),
                risk_level="LEVEL_2",
                status="pending",
            ))
            summary["created"] += 1

    if summary["created"] > 0:
        db.flush()
        log.info("model_upgrade: scanned=%d created=%d deduped=%d", summary["scanned"], summary["created"], summary["deduped"])

    return summary


# ---------------------------------------------------------------------------
# Phase 2: Evaluate candidate model (minimal benchmark)
# ---------------------------------------------------------------------------

# Fixed benchmark scenarios — deterministic, no randomness
_BENCHMARK_SCENARIOS = {
    "orchestrator": {
        "prompt": "## Alerts\n1 warning: webhook_repair_failed shop=test.myshopify.com\n## Workers\nAll OK\n## System Vitals\nEvents: 50, Merchants: 1\n\nPropose actions if needed. Return strict JSON.",
        "expected_keys": ["assessment", "actions"],
    },
    "bugfix_proposal": {
        "prompt": "## Bug: Worker intelligence_worker repeated failures\nSummary: 3 consecutive errors\nContext: {\"worker\": \"intelligence_worker\"}\n\nPropose a minimal fix. Return strict JSON with patch_summary, files, diff, test_command.",
        "expected_keys": ["patch_summary", "files"],
    },
    "evolution_audit": {
        "prompt": "Review this service for improvements: app/services/signal_text.py has 500 lines. Suggest one specific refactor. Return strict JSON with proposal_type, reason, expected_impact.",
        "expected_keys": ["reason"],
    },
}


def evaluate_upgrade(db: Session, proposal_id: int) -> str:
    """
    Run minimal benchmark for a candidate model. Budget-guarded.
    Returns: pass | inconclusive | fail | error
    """
    from app.core.llm_budget import check_budget, record_usage, record_blocked

    proposal = db.get(ModelUpgradeProposal, proposal_id)
    if not proposal or proposal.status not in ("pending", "evaluating"):
        return "invalid_status"

    proposal.status = "evaluating"
    db.flush()

    # Budget check
    allowed, reason = check_budget("evolution_audit")
    if not allowed:
        record_blocked("evolution_audit", reason)
        proposal.eval_result = "blocked"
        proposal.eval_detail = json.dumps({"error": f"budget_blocked: {reason}", "real_execution": False})
        proposal.eval_at = _now()
        proposal.status = "blocked"
        db.flush()
        log.info("model_upgrade: eval id=%d blocked by budget: %s", proposal.id, reason)
        return "blocked"

    scenario = _BENCHMARK_SCENARIOS.get(proposal.target_module, _BENCHMARK_SCENARIOS["orchestrator"])

    # Call candidate model
    import httpx
    import os

    text = ""
    provider = proposal.candidate_provider
    model = proposal.candidate_model

    from app.core.llm_budget import is_provider_backed_off, record_429

    if provider == "anthropic":
        if is_provider_backed_off("anthropic"):
            log.info("model_upgrade: Anthropic backed off (429 cooldown)")
        else:
            key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if key:
                try:
                    resp = httpx.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                        json={"model": model, "max_tokens": 512, "temperature": 0.1,
                              "messages": [{"role": "user", "content": scenario["prompt"]}]},
                        timeout=20.0,
                    )
                    if resp.status_code == 200:
                        text = resp.json().get("content", [{}])[0].get("text", "")
                    elif resp.status_code == 429:
                        record_429("anthropic")
                except Exception as exc:
                    log.warning("model_upgrade: eval call failed: %s", exc)
    elif provider == "openai":
        if is_provider_backed_off("openai"):
            log.info("model_upgrade: OpenAI backed off (429 cooldown)")
        else:
            key = os.getenv("OPENAI_API_KEY", "").strip()
            if key:
                try:
                    resp = httpx.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": model, "max_tokens": 512, "temperature": 0.1,
                              "response_format": {"type": "json_object"},
                              "messages": [{"role": "user", "content": scenario["prompt"]}]},
                        timeout=20.0,
                    )
                    if resp.status_code == 200:
                        text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    elif resp.status_code == 429:
                        record_429("openai")
                except Exception as exc:
                    log.warning("model_upgrade: eval call failed: %s", exc)

    if text:
        record_usage("evolution_audit", tokens_used=len(text) // 4, provider=provider, model=model)

    # If no text was produced, the API was never actually called successfully
    if not text:
        no_key = not os.getenv(
            "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY", ""
        ).strip()
        reason = "no_api_key" if no_key else "api_call_failed"
        proposal.eval_result = "blocked"
        proposal.eval_detail = json.dumps({"error": reason, "real_execution": False})
        proposal.eval_at = _now()
        proposal.status = "blocked"

        write_audit_log(
            db, actor_type="system", actor_name="model_upgrade_agent",
            action_type="model_eval", target_type="model",
            target_id=f"{proposal.candidate_provider}:{proposal.candidate_model}",
            after_state={"result": "blocked", "reason": reason, "module": proposal.target_module},
            status="skipped", approval_mode="autonomous",
        )
        db.flush()
        log.info("model_upgrade: eval id=%d model=%s blocked — %s", proposal.id, model, reason)
        return "blocked"

    # Evaluate response quality (only reaches here with real API response)
    result, detail = _evaluate_response(text, scenario["expected_keys"])
    proposal.eval_result = result
    proposal.eval_detail = json.dumps(detail)
    proposal.eval_at = _now()
    proposal.status = "evaluated"

    write_audit_log(
        db, actor_type="system", actor_name="model_upgrade_agent",
        action_type="model_eval", target_type="model",
        target_id=f"{proposal.candidate_provider}:{proposal.candidate_model}",
        after_state={"result": result, "module": proposal.target_module},
        status="completed", approval_mode="autonomous",
    )
    db.flush()

    log.info("model_upgrade: eval id=%d model=%s result=%s", proposal.id, model, result)
    return result


def _evaluate_response(text: str, expected_keys: list[str]) -> tuple[str, dict]:
    """Evaluate LLM response against expected structure."""
    detail = {"response_length": len(text), "valid_json": False, "keys_present": [], "keys_missing": []}

    if not text:
        return "fail", {**detail, "error": "empty_response"}

    # Try parse JSON
    try:
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(clean)
        detail["valid_json"] = True
    except (json.JSONDecodeError, ValueError):
        return "fail", {**detail, "error": "invalid_json"}

    # Check expected keys
    for key in expected_keys:
        if key in data:
            detail["keys_present"].append(key)
        else:
            detail["keys_missing"].append(key)

    if detail["keys_missing"]:
        return "inconclusive", detail

    return "pass", detail


# ---------------------------------------------------------------------------
# Phase 3: Upgrade-driven evolution proposals
# ---------------------------------------------------------------------------

def generate_upgrade_evolution_proposals(db: Session, proposal_id: int) -> int:
    """
    If a model upgrade is evaluated as 'pass', generate LEVEL_2 evolution
    proposals for capabilities the new model might unlock.
    Returns number of proposals created.
    """
    from app.models.evolution_proposal import EvolutionProposal

    proposal = db.get(ModelUpgradeProposal, proposal_id)
    if not proposal or proposal.eval_result != "pass":
        return 0

    proposals_created = 0
    module = proposal.target_module
    model = proposal.candidate_model

    suggestions = []
    if module == "bugfix_proposal":
        suggestions.append({
            "type": "reliability",
            "reason": f"Model {model} may improve multi-file patch quality — review PATCH_TIER_0 safe paths for expansion",
            "impact": "Broader safe auto-apply coverage with better model",
            "file": "app/services/bugfix_pipeline.py",
        })
    if module == "orchestrator":
        suggestions.append({
            "type": "performance",
            "reason": f"Model {model} may produce better structured decisions — review orchestrator prompt for optimization",
            "impact": "More accurate autonomous decisions, fewer false proposals",
            "file": "app/services/orchestrator_llm.py",
        })
    if module == "evolution_audit":
        suggestions.append({
            "type": "refactor",
            "reason": f"Model {model} may enable LLM-driven evolution scanning alongside deterministic scanners",
            "impact": "Deeper code quality insights beyond pattern matching",
            "file": "app/services/evolution_engine.py",
        })

    for s in suggestions:
        dedup = f"model_upgrade:{model}:{s['file']}"
        existing = db.query(EvolutionProposal).filter(
            EvolutionProposal.dedup_key == dedup,
            EvolutionProposal.status.in_(["open", "accepted"]),
        ).first()
        if existing:
            continue

        db.add(EvolutionProposal(
            proposal_type=s["type"],
            target_file=s["file"],
            risk_level="LEVEL_2",
            reason=s["reason"],
            expected_impact=s["impact"],
            auto_applicable=False,
            status="open",
            audit_cycle=f"model_upgrade_{proposal.id}",
            dedup_key=dedup,
        ))
        proposals_created += 1

    if proposals_created:
        db.flush()
    return proposals_created
