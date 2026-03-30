"""
meta_reviewer.py — System-level strategic prioritization layer.

Runs weekly via agent_worker. Produces a structured meta-review that:
  - Ranks pending proposals by strategic priority
  - Detects conflicts (multiple proposals touching same files)
  - Deprioritizes proposal classes with poor historical outcomes
  - Provides budget guidance based on LLM spend
  - Identifies the weekly focus area

The meta-review is consumed by:
  - evolution_converter.py (priority ordering instead of FIFO)
  - Telegram /meta-review command (operator visibility)
  - monthly_evolution_audit.py (Opus receives meta-review summary)

Public interface:
    run_meta_review(db) -> dict
    get_latest_meta_review(db) -> dict | None
    get_proposal_priority_order(db) -> list[int]   # ordered proposal IDs
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text, desc
from sqlalchemy.orm import Session

from app.models.meta_review import MetaReview
from app.models.evolution_proposal import EvolutionProposal
from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("meta_reviewer")

# Cooldown: 7 days between meta-reviews
_REVIEW_COOLDOWN_SECONDS = 7 * 86400
_last_review_run: float | None = None

# Meta-review is stale after 10 days (gives 3-day grace past 7-day cycle)
_STALENESS_DAYS = 10

MAX_TOKENS = 2048


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _review_window() -> str:
    """ISO week, e.g. '2026-W13'."""
    return _now().strftime("%G-W%V")


def should_run_meta_review() -> bool:
    global _last_review_run
    if _last_review_run is None:
        return True
    return (time.monotonic() - _last_review_run) >= _REVIEW_COOLDOWN_SECONDS


def mark_meta_review_run():
    global _last_review_run
    _last_review_run = time.monotonic()


# ---------------------------------------------------------------------------
# Context aggregation — all inputs for the meta-reviewer
# ---------------------------------------------------------------------------

def _gather_pending_proposals(db: Session) -> list[dict]:
    """Fetch all open evolution proposals with metadata."""
    proposals = (
        db.query(EvolutionProposal)
        .filter(EvolutionProposal.status == "open")
        .order_by(EvolutionProposal.created_at)
        .limit(30)
        .all()
    )
    return [
        {
            "id": p.id,
            "type": p.proposal_type,
            "risk_level": p.risk_level,
            "target_file": p.target_file,
            "reason": (p.reason or "")[:200],
            "auto_applicable": p.auto_applicable,
            "age_days": (_now() - p.created_at).days if p.created_at else 0,
            "dedup_key": p.dedup_key,
        }
        for p in proposals
    ]


def _gather_pending_candidates(db: Session) -> list[dict]:
    """Fetch active bugfix candidates."""
    candidates = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed"]))
        .order_by(BugFixCandidate.created_at)
        .limit(10)
        .all()
    )
    return [
        {
            "id": c.id,
            "source_type": c.source_type,
            "title": (c.title or "")[:150],
            "status": c.status,
            "risk_tier": c.patch_risk_tier,
        }
        for c in candidates
    ]


def _gather_outcome_stats(db: Session) -> dict:
    """Bugfix outcome effectiveness stats."""
    try:
        from app.services.evolution_outcomes import get_effectiveness_stats
        return get_effectiveness_stats(db)
    except Exception:
        return {"total_measured": 0, "by_source": {}}


def _gather_support_trends(db: Session) -> dict:
    """Support incident clusters."""
    cutoff = _now() - timedelta(days=30)
    result = {"bug_clusters": [], "feature_clusters": [], "total": 0}
    try:
        total = db.execute(
            text("SELECT COUNT(*) FROM support_incidents WHERE created_at >= :c"),
            {"c": cutoff},
        ).fetchone()
        result["total"] = total[0] if total else 0

        bugs = db.execute(text("""
            SELECT affected_area, COUNT(*) AS cnt
            FROM support_incidents
            WHERE classification = 'bug_report' AND created_at >= :c
              AND affected_area IS NOT NULL AND affected_area != 'unknown'
            GROUP BY affected_area ORDER BY cnt DESC LIMIT 5
        """), {"c": cutoff}).fetchall()
        result["bug_clusters"] = [{"area": r[0], "count": r[1]} for r in bugs]

        feats = db.execute(text("""
            SELECT affected_area, COUNT(*) AS cnt
            FROM support_incidents
            WHERE classification = 'feature_request' AND created_at >= :c
              AND affected_area IS NOT NULL AND affected_area != 'unknown'
            GROUP BY affected_area ORDER BY cnt DESC LIMIT 5
        """), {"c": cutoff}).fetchall()
        result["feature_clusters"] = [{"area": r[0], "count": r[1]} for r in feats]
    except Exception:
        pass
    return result


def _gather_budget_state(db: Session) -> dict:
    """Current LLM budget and cost state."""
    try:
        from app.core.llm_budget import get_usage_summary
        return get_usage_summary()
    except Exception:
        return {}


def _gather_brain_summary(db: Session) -> str:
    """Latest project brain summary if available."""
    try:
        from app.services.project_brain import get_brain_summary
        result = get_brain_summary(db)
        if isinstance(result, dict):
            return json.dumps(result, indent=2, default=str)
        return str(result)
    except Exception:
        return "Project brain unavailable."


def _detect_conflicts(proposals: list[dict]) -> list[dict]:
    """
    Deterministic conflict detection: find proposals targeting the same file.
    """
    by_file: dict[str, list[int]] = {}
    for p in proposals:
        target = p.get("target_file")
        if not target:
            continue
        # Strip line number suffix (e.g., "file.py:42" → "file.py")
        base_file = target.split(":")[0]
        by_file.setdefault(base_file, []).append(p["id"])

    conflicts = []
    for file_path, ids in by_file.items():
        if len(ids) > 1:
            conflicts.append({
                "proposal_ids": ids,
                "reason": f"Multiple proposals target {file_path}",
            })
    return conflicts


def _deprioritize_classes(outcome_stats: dict) -> list[dict]:
    """
    Identify proposal source types with 0% effectiveness in last 90 days.
    """
    deprioritized = []
    for src, data in outcome_stats.get("by_source", {}).items():
        if data["total"] >= 3 and data["effective"] == 0:
            deprioritized.append({
                "source_type": src,
                "reason": f"{data['total']} bugfixes from {src} measured, 0 effective",
                "total_measured": data["total"],
            })
    return deprioritized


# ---------------------------------------------------------------------------
# LLM call — Opus strategic review
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the Meta Reviewer for HedgeSpark — a senior AI systems architect performing system-level strategic review.

You receive:
- Open evolution proposals (with types, risk levels, target files, ages)
- Active bugfix candidates
- Historical outcome data (which fix sources are effective)
- Merchant support trends (pain points by area)
- Budget/cost constraints
- Project brain summary

Your task: produce a STRATEGIC META-REVIEW.

Output valid JSON with this exact structure:
{
  "weekly_focus_area": "reliability|performance|product|security|refactor",
  "priorities": [
    {
      "proposal_id": 42,
      "priority_score": 85,
      "recommendation": "convert_next|defer|investigate|reject_stale"
    }
  ],
  "budget_guidance": "one sentence on LLM/cost status",
  "summary": "2-3 sentence strategic assessment"
}

Rules:
- priority_score: 0-100 (higher = more urgent)
- Include ALL open proposals in priorities list
- Rank by: merchant impact > reliability > performance > cosmetic
- If a proposal source type has 0% effectiveness, deprioritize to score < 20
- If proposals conflict (same target file), note the higher-priority one
- If budget is tight, recommend deferring low-priority items
- Be specific, not generic"""


def _call_opus(context: str) -> str:
    """Call Opus for meta-review. Budget-guarded. 429-aware."""
    from app.core.llm_budget import check_budget, record_usage, record_blocked, is_provider_backed_off, record_429

    allowed, reason = check_budget("monthly_opus_audit")  # shares budget pool with monthly audit
    if not allowed:
        record_blocked("monthly_opus_audit", reason)
        log.info("meta_reviewer: blocked by budget: %s", reason)
        return ""

    if is_provider_backed_off("anthropic"):
        record_blocked("monthly_opus_audit", "anthropic_429_backoff")
        log.info("meta_reviewer: Anthropic backed off (429 cooldown)")
        return ""

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        log.info("meta_reviewer: no ANTHROPIC_API_KEY — cannot run meta-review")
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
                "temperature": 0.1,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": context}],
            },
            timeout=60.0,
        )
        if resp.status_code == 200:
            text_out = resp.json().get("content", [{}])[0].get("text", "")
            tokens = resp.json().get("usage", {}).get("output_tokens", len(text_out) // 4)
            record_usage("monthly_opus_audit", tokens_used=tokens, provider="anthropic", model=OPUS)
            return text_out
        if resp.status_code == 429:
            record_429("anthropic")
            return ""
        log.warning("meta_reviewer: Opus returned %d", resp.status_code)
    except Exception as exc:
        log.warning("meta_reviewer: Opus call failed: %s", type(exc).__name__)

    return ""


def _parse_review(raw: str, proposals: list[dict], conflicts: list[dict], deprioritized: list[dict]) -> dict:
    """Parse LLM output and merge with deterministic data."""
    review = {
        "weekly_focus_area": "reliability",
        "priorities": [],
        "conflicts": conflicts,
        "deprioritized_classes": deprioritized,
        "budget_guidance": "",
        "summary": "",
    }

    try:
        # Handle markdown code blocks
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(clean)
    except (json.JSONDecodeError, TypeError):
        log.warning("meta_reviewer: invalid JSON from Opus — using deterministic fallback")
        # Fallback: assign scores based on age and type
        for p in proposals:
            score = min(90, p["age_days"] * 2 + (30 if p["type"] == "reliability" else 10))
            review["priorities"].append({
                "proposal_id": p["id"],
                "priority_score": score,
                "recommendation": "convert_next" if p["auto_applicable"] and score > 50 else "defer",
            })
        review["summary"] = "Deterministic fallback — Opus response was not valid JSON."
        return review

    review["weekly_focus_area"] = data.get("weekly_focus_area", "reliability")
    review["budget_guidance"] = str(data.get("budget_guidance", ""))[:500]
    review["summary"] = str(data.get("summary", ""))[:1000]

    # Parse priorities — validate proposal IDs exist
    valid_ids = {p["id"] for p in proposals}
    for entry in data.get("priorities", []):
        pid = entry.get("proposal_id")
        if pid not in valid_ids:
            continue
        review["priorities"].append({
            "proposal_id": pid,
            "priority_score": max(0, min(100, int(entry.get("priority_score", 50)))),
            "recommendation": entry.get("recommendation", "defer"),
        })

    # Ensure all proposals are included (LLM may have missed some)
    seen = {p["proposal_id"] for p in review["priorities"]}
    for p in proposals:
        if p["id"] not in seen:
            review["priorities"].append({
                "proposal_id": p["id"],
                "priority_score": 30,  # default mid-low
                "recommendation": "defer",
            })

    # Apply deprioritization override
    deprioritized_sources = {d["source_type"] for d in deprioritized}
    proposal_sources = {p["id"]: p.get("dedup_key", "").split(":")[0] for p in proposals}
    for entry in review["priorities"]:
        src = proposal_sources.get(entry["proposal_id"], "")
        if src in deprioritized_sources:
            entry["priority_score"] = min(entry["priority_score"], 15)
            entry["recommendation"] = "defer"

    # Sort by priority_score descending
    review["priorities"].sort(key=lambda x: x["priority_score"], reverse=True)

    return review


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_meta_review(db: Session) -> dict:
    """
    Run weekly meta-review. Returns structured result dict.
    """
    window = _review_window()
    now = _now()

    # Dedup: don't run twice for the same window
    existing = db.query(MetaReview).filter(MetaReview.review_window == window).first()
    if existing:
        log.info("meta_reviewer: already ran for window %s — skipping", window)
        return {"status": "skipped", "reason": "already_ran_this_window"}

    # Gather inputs
    proposals = _gather_pending_proposals(db)
    candidates = _gather_pending_candidates(db)
    outcome_stats = _gather_outcome_stats(db)
    support = _gather_support_trends(db)
    budget = _gather_budget_state(db)
    brain = _gather_brain_summary(db)

    # Deterministic analysis (always runs, even without LLM)
    conflicts = _detect_conflicts(proposals)
    deprioritized = _deprioritize_classes(outcome_stats)

    # Skip if nothing to review
    if not proposals and not candidates:
        row = MetaReview(
            created_at=now,
            review_window=window,
            status="skipped",
            skipped_reason="no_pending_proposals_or_candidates",
            proposals_evaluated=0,
        )
        db.add(row)
        db.flush()
        log.info("meta_reviewer: skipped — no proposals or candidates")
        return {"status": "skipped", "reason": "no_pending_proposals_or_candidates"}

    # Build LLM context
    context_parts = [
        f"Meta-review for window: {window}",
        f"Date: {now.strftime('%Y-%m-%d')}",
        "",
        f"OPEN PROPOSALS ({len(proposals)}):",
        json.dumps(proposals, indent=2, default=str),
        "",
        f"ACTIVE BUGFIX CANDIDATES ({len(candidates)}):",
        json.dumps(candidates, indent=2, default=str),
        "",
        f"BUGFIX OUTCOME STATS (90d):",
        json.dumps(outcome_stats, indent=2, default=str),
        "",
        f"SUPPORT TRENDS (30d):",
        json.dumps(support, indent=2, default=str),
        "",
        f"CONFLICTS DETECTED (deterministic):",
        json.dumps(conflicts, indent=2, default=str),
        "",
        f"DEPRIORITIZED CLASSES (0% effectiveness):",
        json.dumps(deprioritized, indent=2, default=str),
        "",
        f"LLM BUDGET:",
        json.dumps(budget, indent=2, default=str),
        "",
        f"PROJECT BRAIN SUMMARY:",
        brain,
    ]
    context = "\n".join(context_parts)

    # Call Opus
    raw = _call_opus(context)
    model_used = None

    if raw:
        from app.core.llm_router import OPUS
        model_used = OPUS
        review = _parse_review(raw, proposals, conflicts, deprioritized)
    else:
        # Deterministic fallback when Opus is unavailable
        review = _parse_review("", proposals, conflicts, deprioritized)
        review["summary"] = "LLM unavailable — using deterministic priority (age + type)."

    # Add window dates
    # ISO week starts on Monday
    from datetime import date
    iso_year, iso_week, _ = now.isocalendar()
    week_start = date.fromisocalendar(iso_year, iso_week, 1)
    week_end = date.fromisocalendar(iso_year, iso_week, 7)
    review["review_window_start"] = week_start.isoformat()
    review["review_window_end"] = week_end.isoformat()

    # Store
    row = MetaReview(
        created_at=now,
        review_window=window,
        status="completed",
        review_json=json.dumps(review, default=str),
        proposals_evaluated=len(proposals),
        model_used=model_used,
    )
    db.add(row)
    db.flush()

    # Audit log
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="system",
            actor_name="meta_reviewer",
            action_type="meta_review_completed",
            target_type="meta_review",
            target_id=window,
            after_state={
                "proposals_evaluated": len(proposals),
                "conflicts": len(conflicts),
                "focus_area": review["weekly_focus_area"],
            },
            status="completed",
        )
    except Exception:
        pass

    log.info(
        "meta_reviewer: window=%s proposals=%d conflicts=%d focus=%s model=%s",
        window, len(proposals), len(conflicts), review["weekly_focus_area"], model_used,
    )

    return {"status": "completed", "window": window, "review": review}


def get_latest_meta_review(db: Session) -> dict | None:
    """
    Get the latest completed meta-review. Returns parsed review_json or None.
    Used by Telegram /meta-review and ops API.
    """
    row = (
        db.query(MetaReview)
        .filter(MetaReview.status == "completed")
        .order_by(desc(MetaReview.created_at))
        .first()
    )
    if not row or not row.review_json:
        return None

    try:
        review = json.loads(row.review_json)
        review["_meta"] = {
            "review_window": row.review_window,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "model_used": row.model_used,
            "proposals_evaluated": row.proposals_evaluated,
        }
        return review
    except (json.JSONDecodeError, TypeError):
        return None


def get_proposal_priority_order(db: Session) -> list[int]:
    """
    Get ordered list of proposal IDs from the latest non-stale meta-review.
    Returns empty list if no fresh review exists (caller should use default FIFO).
    """
    staleness_cutoff = _now() - timedelta(days=_STALENESS_DAYS)

    row = (
        db.query(MetaReview)
        .filter(
            MetaReview.status == "completed",
            MetaReview.created_at >= staleness_cutoff,
        )
        .order_by(desc(MetaReview.created_at))
        .first()
    )
    if not row or not row.review_json:
        return []

    try:
        review = json.loads(row.review_json)
        priorities = review.get("priorities", [])
        # Return proposal IDs sorted by priority_score descending
        return [p["proposal_id"] for p in priorities if p.get("priority_score", 0) > 0]
    except (json.JSONDecodeError, TypeError):
        return []
