"""
monthly_evolution_audit.py — Controlled monthly deep audit using Opus.

Runs at most once every 30 days. Uses Opus explicitly for strategic
analysis. Generates max 10 high-quality improvement proposals.

All proposals are:
  - LEVEL_2 or LEVEL_3 only
  - NEVER auto-applicable
  - Stored as evolution proposals with source="monthly_opus_audit"

Budget guard applies. No loops. No auto-apply.

Closed-loop enrichment:
  - Bugfix outcome effectiveness stats (what worked, what didn't)
  - Support incident patterns (merchant pain points by area)
  - Feature request patterns (merchant demand signals)
  - Action execution effectiveness (which action types actually improve metrics)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal

log = logging.getLogger("monthly_evolution_audit")

_BACKEND_DIR = Path("/opt/wishspark/backend")

# Cooldown: 30 days minimum between audits
_AUDIT_COOLDOWN_SECONDS = 30 * 86400
_last_audit_run: float | None = None

# Hard limits
MAX_PROPOSALS_PER_RUN = 10
MAX_TOKENS = 4096


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit_cycle_id() -> str:
    """Monthly cycle identifier, e.g. '2026-M03'."""
    return _now().strftime("%Y-M%m")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def should_run_monthly_audit() -> bool:
    global _last_audit_run
    if _last_audit_run is None:
        return True
    return (time.monotonic() - _last_audit_run) >= _AUDIT_COOLDOWN_SECONDS


def mark_monthly_audit_run():
    global _last_audit_run
    _last_audit_run = time.monotonic()


# ---------------------------------------------------------------------------
# Context builders (lightweight, no LLM)
# ---------------------------------------------------------------------------

def _build_codebase_summary() -> str:
    """Light codebase summary — file counts and sizes."""
    dirs = {
        "api": _BACKEND_DIR / "app" / "api",
        "services": _BACKEND_DIR / "app" / "services",
        "models": _BACKEND_DIR / "app" / "models",
        "workers": _BACKEND_DIR / "app" / "workers",
        "core": _BACKEND_DIR / "app" / "core",
        "tests": _BACKEND_DIR / "tests",
    }

    lines = []
    for name, d in dirs.items():
        if not d.exists():
            continue
        py_files = list(d.glob("*.py"))
        total_lines = 0
        for f in py_files:
            try:
                total_lines += len(f.read_text().splitlines())
            except Exception:
                pass
        lines.append(f"  {name}/: {len(py_files)} files, ~{total_lines} lines")

    return "Codebase structure:\n" + "\n".join(lines)


def _build_bugfix_history(db: Session) -> str:
    """Last 30 days of bugfix activity."""
    try:
        from app.models.bugfix_candidate import BugFixCandidate
        cutoff = _now() - timedelta(days=30)
        candidates = (
            db.query(BugFixCandidate)
            .filter(BugFixCandidate.created_at >= cutoff)
            .all()
        )
        if not candidates:
            return "Bugfix history (30d): No candidates."

        by_status = {}
        for c in candidates:
            by_status[c.status] = by_status.get(c.status, 0) + 1

        titles = [c.title for c in candidates[:10]]
        return (
            f"Bugfix history (30d): {len(candidates)} candidates\n"
            f"  Status breakdown: {by_status}\n"
            f"  Recent: {', '.join(titles)}"
        )
    except Exception as exc:
        return f"Bugfix history unavailable: {exc}"


def _build_evolution_history(db: Session) -> str:
    """Recent evolution proposals."""
    try:
        proposals = (
            db.query(EvolutionProposal)
            .order_by(EvolutionProposal.created_at.desc())
            .limit(20)
            .all()
        )
        if not proposals:
            return "Evolution history: No proposals yet."

        by_status = {}
        for p in proposals:
            by_status[p.status] = by_status.get(p.status, 0) + 1

        recent = [f"[{p.risk_level}] {p.reason[:60]}" for p in proposals[:5]]
        return (
            f"Evolution proposals: {len(proposals)} recent\n"
            f"  Status breakdown: {by_status}\n"
            f"  Recent:\n    " + "\n    ".join(recent)
        )
    except Exception as exc:
        return f"Evolution history unavailable: {exc}"


def _build_system_metrics(db: Session) -> str:
    """System metrics summary for LLM context."""
    try:
        from app.services.system_summary import build_system_summary
        s = build_system_summary(db)
        ram = s["infra"]["ram"]
        workers = s["infra"]["workers"]
        llm = s["llm_usage"]
        cost = s["cost_estimate"]

        return (
            f"System metrics:\n"
            f"  RAM: {ram.get('usage_pct', '?')}% used ({ram.get('used_mb', '?')}MB / {ram.get('total_mb', '?')}MB)\n"
            f"  Workers: {workers.get('cycles_24h', 0)} cycles/24h, {workers.get('error_rate_pct', 0)}% error rate\n"
            f"  LLM: {llm.get('global_calls_today', 0)} calls today, {llm.get('blocked_today', 0)} blocked\n"
            f"  Cost: €{cost.get('total_monthly_eur', '?')}/month estimated\n"
            f"  Warnings: {s.get('warnings', [])}"
        )
    except Exception as exc:
        return f"System metrics unavailable: {exc}"


def _build_bugfix_effectiveness(db: Session) -> str:
    """Closed-loop: bugfix outcome stats — what worked and what didn't."""
    try:
        from app.services.evolution_outcomes import get_effectiveness_stats
        stats = get_effectiveness_stats(db)
        if stats["total_measured"] == 0:
            return "Bugfix effectiveness (90d): No outcomes measured yet."

        lines = [f"Bugfix effectiveness (90d): {stats['total_measured']} measured"]
        for src, data in stats["by_source"].items():
            eff = round(data["effective"] / data["total"] * 100) if data["total"] > 0 else 0
            lines.append(
                f"  {src}: {data['total']} total, {data['effective']} effective ({eff}%), "
                f"{data['ineffective']} ineffective, {data['inconclusive']} inconclusive"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Bugfix effectiveness unavailable: {exc}"


def _build_support_patterns(db: Session) -> str:
    """Closed-loop: merchant pain points from support incidents."""
    from sqlalchemy import text
    try:
        cutoff = _now() - timedelta(days=30)

        # Bug report clusters
        bug_rows = db.execute(text("""
            SELECT affected_area, COUNT(*) AS cnt
            FROM support_incidents
            WHERE classification = 'bug_report'
              AND created_at >= :cutoff
              AND affected_area IS NOT NULL AND affected_area != 'unknown'
            GROUP BY affected_area
            ORDER BY COUNT(*) DESC LIMIT 5
        """), {"cutoff": cutoff}).fetchall()

        # Feature request clusters
        feat_rows = db.execute(text("""
            SELECT affected_area, COUNT(*) AS cnt
            FROM support_incidents
            WHERE classification = 'feature_request'
              AND created_at >= :cutoff
              AND affected_area IS NOT NULL AND affected_area != 'unknown'
            GROUP BY affected_area
            ORDER BY COUNT(*) DESC LIMIT 5
        """), {"cutoff": cutoff}).fetchall()

        # Total incidents
        total_row = db.execute(text("""
            SELECT COUNT(*) FROM support_incidents WHERE created_at >= :cutoff
        """), {"cutoff": cutoff}).fetchone()
        total = total_row[0] if total_row else 0

        lines = [f"Support incidents (30d): {total} total"]
        if bug_rows:
            lines.append("  Bug report clusters:")
            for r in bug_rows:
                lines.append(f"    {r[0]}: {r[1]} reports")
        if feat_rows:
            lines.append("  Feature request clusters:")
            for r in feat_rows:
                lines.append(f"    {r[0]}: {r[1]} requests")
        if not bug_rows and not feat_rows:
            lines.append("  No clusters detected.")

        return "\n".join(lines)
    except Exception as exc:
        return f"Support patterns unavailable: {exc}"


def _build_action_effectiveness(db: Session) -> str:
    """Closed-loop: which action types actually improve merchant metrics."""
    try:
        from app.services.action_proof import get_action_effectiveness
        stats = get_action_effectiveness(db)
        if not stats:
            return "Action effectiveness (90d): No action outcomes measured yet."

        lines = ["Action effectiveness (90d):"]
        for at, data in sorted(stats.items(), key=lambda x: -x[1]["total"]):
            eff_pct = round(data["effectiveness"] * 100)
            lines.append(
                f"  {at}: {data['total']} measured, {data['improved']} improved ({eff_pct}%), "
                f"{data['declined']} declined, {data['stable']} stable"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Action effectiveness unavailable: {exc}"


# ---------------------------------------------------------------------------
# LLM call (Opus, budget-guarded)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior AI systems architect reviewing the Hedge Spark platform.
Hedge Spark is an AI commerce intelligence system for Shopify.

Your task: produce a strategic monthly audit with concrete improvement proposals.

You have access to CLOSED-LOOP DATA:
- Bugfix effectiveness: which fix source types (ops_alert, support_incident, evolution)
  have the best/worst track record
- Support incident patterns: which areas merchants report bugs/features in most
- Action effectiveness: which merchant action types (nudges, price tests, etc.)
  actually improve conversion rates vs. which don't

Use this data to prioritize proposals that address REAL, measured weaknesses.

Rules:
- Output valid JSON only
- Max 10 proposals
- Each proposal must have: title, type, reasoning, expected_impact, risk_level
- type must be one of: architecture, performance, reliability, product
- risk_level must be LEVEL_2 or LEVEL_3 only (never LEVEL_1)
- Focus on HIGH IMPACT improvements only
- Be specific and actionable, not generic advice
- Consider the closed-loop effectiveness data when making recommendations
- Prioritize areas with low fix effectiveness or high merchant complaint volume

Output format:
{
  "proposals": [
    {
      "title": "...",
      "type": "architecture|performance|reliability|product",
      "reasoning": "...",
      "expected_impact": "...",
      "risk_level": "LEVEL_2|LEVEL_3"
    }
  ],
  "summary": "1-2 sentence overall assessment"
}"""


def _call_opus(context: str) -> str:
    """
    Call Opus via Anthropic API. Budget-guarded. 429-aware.
    Returns raw response text or empty string.
    """
    from app.core.llm_budget import check_budget, record_usage, record_blocked, is_provider_backed_off, record_429

    allowed, reason = check_budget("monthly_opus_audit")
    if not allowed:
        record_blocked("monthly_opus_audit", reason)
        log.info("monthly_audit: blocked by budget: %s", reason)
        return ""

    if is_provider_backed_off("anthropic"):
        record_blocked("monthly_opus_audit", "anthropic_429_backoff")
        log.info("monthly_audit: Anthropic backed off (429 cooldown)")
        return ""

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        log.info("monthly_audit: no ANTHROPIC_API_KEY — cannot run Opus audit")
        return ""

    from app.core.llm_router import OPUS

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": OPUS,
                "max_tokens": MAX_TOKENS,
                "temperature": 0.2,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": context}],
            },
            timeout=60.0,
        )

        if resp.status_code == 200:
            text = resp.json().get("content", [{}])[0].get("text", "")
            tokens_used = resp.json().get("usage", {}).get("output_tokens", len(text) // 4)
            record_usage("monthly_opus_audit", tokens_used=tokens_used, provider="anthropic", model=OPUS)
            return text

        if resp.status_code == 429:
            record_429("anthropic")
            return ""

        log.warning("monthly_audit: Opus returned %d", resp.status_code)
        return ""

    except Exception as exc:
        log.warning("monthly_audit: Opus call failed: %s", type(exc).__name__)
        return ""


# ---------------------------------------------------------------------------
# Proposal parsing + storage
# ---------------------------------------------------------------------------

def _parse_proposals(raw: str) -> list[dict]:
    """Parse LLM response into proposal dicts. Enforces safety rules."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("monthly_audit: invalid JSON from Opus")
        return []

    proposals = data.get("proposals", [])
    if not isinstance(proposals, list):
        return []

    valid = []
    for p in proposals[:MAX_PROPOSALS_PER_RUN]:
        # Enforce safety: only LEVEL_2 or LEVEL_3
        risk = p.get("risk_level", "LEVEL_3")
        if risk not in ("LEVEL_2", "LEVEL_3"):
            risk = "LEVEL_3"

        # Enforce type
        ptype = p.get("type", "architecture")
        if ptype not in ("architecture", "performance", "reliability", "product"):
            ptype = "architecture"

        valid.append({
            "title": str(p.get("title", "Untitled"))[:200],
            "type": ptype,
            "reasoning": str(p.get("reasoning", ""))[:1000],
            "expected_impact": str(p.get("expected_impact", ""))[:500],
            "risk_level": risk,
        })

    return valid


def _store_proposals(db: Session, proposals: list[dict], cycle: str) -> int:
    """Store proposals as EvolutionProposal rows. Returns count stored."""
    stored = 0
    for p in proposals:
        dedup_key = f"monthly_opus:{cycle}:{p['title'][:60]}"

        exists = (
            db.query(EvolutionProposal)
            .filter(EvolutionProposal.dedup_key == dedup_key)
            .first()
        )
        if exists:
            continue

        row = EvolutionProposal(
            proposal_type=p["type"],
            target_file=None,
            risk_level=p["risk_level"],
            reason=f"[{p['title']}] {p['reasoning']}",
            expected_impact=p["expected_impact"],
            auto_applicable=False,  # NEVER auto-apply
            status="open",
            audit_cycle=cycle,
            dedup_key=dedup_key,
        )
        db.add(row)
        stored += 1

    db.flush()
    return stored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_monthly_opus_audit(db: Session) -> dict:
    """
    Run the monthly Opus evolution audit.

    Returns: {"status": str, "proposals_created": int, "proposals": list}
    """
    cycle = _audit_cycle_id()

    # Build context — now includes closed-loop learning signals
    context_parts = [
        f"Monthly strategic audit for cycle: {cycle}",
        "",
        _build_codebase_summary(),
        "",
        _build_bugfix_history(db),
        "",
        _build_bugfix_effectiveness(db),
        "",
        _build_evolution_history(db),
        "",
        _build_support_patterns(db),
        "",
        _build_action_effectiveness(db),
        "",
        _build_system_metrics(db),
    ]
    context = "\n".join(context_parts)

    # Call Opus
    raw = _call_opus(context)
    if not raw:
        return {"status": "skipped", "reason": "llm_unavailable", "proposals_created": 0, "proposals": []}

    # Parse + validate
    proposals = _parse_proposals(raw)
    if not proposals:
        return {"status": "completed", "reason": "no_valid_proposals", "proposals_created": 0, "proposals": []}

    # Store
    stored = _store_proposals(db, proposals, cycle)

    # Audit log
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="system",
            actor_name="monthly_opus_audit",
            action_type="monthly_evolution_audit",
            target_type="evolution_proposals",
            target_id=cycle,
            after_state={"proposals_created": stored, "cycle": cycle},
            status="completed",
        )
    except Exception:
        pass

    log.info("monthly_audit: cycle=%s proposals=%d stored=%d", cycle, len(proposals), stored)

    return {
        "status": "completed",
        "cycle": cycle,
        "proposals_created": stored,
        "proposals": proposals,
    }
