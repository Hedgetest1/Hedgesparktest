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

# Redis key for persistent cooldown (survives PM2 restarts)
_REDIS_COOLDOWN_KEY = "hs:cooldown:monthly_audit"

# Hard limits
# Strategic-bet redesign: CTOs make 3 bets under constraint, not 10 ideas.
# Fewer is fine. Zero is fine when nothing is worth doing this cycle.
MAX_PROPOSALS_PER_RUN = 3
MAX_TOKENS = 4096

# Expanded type enum — engineering vocabulary blocked strategic thinking.
# growth / retention / conversion / experiment / deprecate are the business
# categories an elite CTO reasons in. Old engineering types retained for
# backward-compat and real infra work.
_VALID_TYPES = {
    # Business categories
    "growth", "retention", "conversion", "experiment", "deprecate",
    # Engineering categories (existing)
    "architecture", "performance", "reliability", "product",
}

_VALID_COST_ESTIMATES = {"none", "small", "medium", "large"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit_cycle_id() -> str:
    """Monthly cycle identifier, e.g. '2026-M03'."""
    return _now().strftime("%Y-M%m")


# ---------------------------------------------------------------------------
# Scheduling — Redis-persistent cooldown (survives PM2 restarts)
# ---------------------------------------------------------------------------

def should_run_monthly_audit(db: Session | None = None) -> bool:
    """
    Three-layer cooldown check, in order of durability:

      1. DATABASE (source of truth, db arg required) — any EvolutionProposal
         row with dedup_key prefix 'monthly_opus:{cycle}:' proves the audit
         already ran this cycle. Survives Redis loss, PM2 restart, process
         crash. This is the ONLY protection against duplicate Opus calls
         if Redis is ever unavailable.
      2. IN-PROCESS monotonic clock (fast path) — skip the work if we
         already ran in this process.
      3. REDIS key hs:cooldown:monthly_audit (30-day TTL) — survives
         restarts; lost if Redis is flushed or unreachable.
    """
    global _last_audit_run
    # Layer 1: DB check — the durable source of truth.
    if db is not None:
        try:
            cycle = _audit_cycle_id()
            prefix = f"monthly_opus:{cycle}:"
            exists = (
                db.query(EvolutionProposal.id)
                .filter(EvolutionProposal.dedup_key.like(f"{prefix}%"))
                .first()
            )
            if exists is not None:
                return False
        except Exception as exc:
            # DB check failing is non-fatal — fall through to Redis/in-proc.
            log.warning("monthly_audit: DB cooldown check failed (non-fatal): %s", type(exc).__name__)

    # Layer 2: In-process check (fast path).
    if _last_audit_run is not None:
        if (time.monotonic() - _last_audit_run) < _AUDIT_COOLDOWN_SECONDS:
            return False

    # Layer 3: Redis check (survives restarts).
    try:
        from app.core.redis_client import cache_get
        if cache_get(_REDIS_COOLDOWN_KEY) is not None:
            return False
    except Exception:
        pass

    return True


def mark_monthly_audit_run(ttl_seconds: int | None = None):
    """
    Mark that a monthly audit attempt has occurred.

    ttl_seconds: override the default 30-day cooldown. Used to shorten
    the cooldown on transient failures (e.g. LLM unavailable) so the
    audit retries the next day instead of losing an entire month.
    Default: 30 days (success path).
    """
    global _last_audit_run
    ttl = ttl_seconds if ttl_seconds is not None else _AUDIT_COOLDOWN_SECONDS
    # Normalize in-proc timestamp so the same ttl applies there too.
    _last_audit_run = time.monotonic() - (_AUDIT_COOLDOWN_SECONDS - ttl)
    try:
        from app.core.redis_client import cache_set
        cache_set(_REDIS_COOLDOWN_KEY, True, ttl)
    except Exception:
        pass


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


def _build_prior_monthly_audits(db: Session) -> str:
    """
    Closed-loop memory: show the last 3 monthly-audit cycles with the
    MEASURED OUTCOME of each proposal (effective / ineffective / inconclusive
    / unmeasured) and the rolling effectiveness rate.

    This is the core feedback signal. Opus sees exactly which of its past
    strategic proposals actually delivered impact, so it can prioritize
    proven-working directions and stop re-pitching failed ideas.
    """
    try:
        rows = (
            db.query(EvolutionProposal)
            .filter(EvolutionProposal.dedup_key.like("monthly_opus:%"))
            .order_by(EvolutionProposal.created_at.desc())
            .limit(60)
            .all()
        )
        if not rows:
            return "Prior monthly audits: None (this is the first cycle)."

        # Group by cycle, keep most recent 3 cycles.
        cycles: dict[str, list] = {}
        for r in rows:
            cycles.setdefault(r.audit_cycle or "unknown", []).append(r)
        recent_cycles = sorted(cycles.keys(), reverse=True)[:3]

        # Rolling technical effectiveness across ALL monthly_opus proposals.
        from app.services.evolution_proposal_outcomes import get_proposal_effectiveness_stats
        stats = get_proposal_effectiveness_stats(db, limit_cycles=6)

        # Business success rates by domain (revenue feedback loop).
        from app.services.evolution_business_outcomes import (
            compute_category_success_rates,
            combined_outcome_label,
        )
        biz_rates = compute_category_success_rates(db, days=180)

        # Reinforcement weights — close the loop by BIASING Opus toward
        # categories that have historically made money.
        from app.services.evolution_reinforcement import (
            compute_reinforcement_weights,
            format_for_opus_prompt,
            get_retired_domains,
            exploration_required,
        )
        weights = compute_reinforcement_weights(db)
        retired = get_retired_domains(weights)
        explore_req, dominant = exploration_required(weights)

        lines = [
            "Prior monthly audits — LEARN FROM TECH + BUSINESS OUTCOMES:",
            f"  Rolling TECH effectiveness: {stats['effectiveness_rate']*100:.0f}% "
            f"(tech counts: {stats['by_outcome']})",
            f"  Rolling BUSINESS success rates (trend-adjusted CVR/revenue):",
        ]
        for domain, s in biz_rates.items():
            lines.append(
                f"    {domain}: {s['success_rate']*100:.0f}% "
                f"({s['improved']} improved / {s['declined']} declined / "
                f"{s['stable']} stable, n={s['total']})"
            )
        lines.append("")
        lines.append(format_for_opus_prompt(weights))
        lines.append("")

        # --- RETIRED DOMAINS — hard "do not propose" list ---
        if retired:
            lines.append("RETIRED DOMAINS (DO NOT PROPOSE — measured and found not to move revenue):")
            for r in retired:
                lines.append(
                    f"  - {r['domain']}: {r['reason']}"
                )
            lines.append("")

        # --- EXPLORATION REQUIREMENT ---
        if explore_req and dominant:
            lines.append(
                f"EXPLORATION REQUIRED: '{dominant}' domain dominates past wins. "
                f"At least 1 of your 3 bets MUST be an exploration_bet=true "
                f"targeting a non-'{dominant}' domain."
            )
            lines.append("")
        lines.append(
            "Last 3 cycles — each proposal shown as "
            "[status / tech_outcome / business_outcome / combined]:"
        )
        for c in recent_cycles:
            lines.append(f"  Cycle {c}:")
            status_totals: dict[str, int] = {}
            combined_totals: dict[str, int] = {}
            for p in cycles[c]:
                status_totals[p.status] = status_totals.get(p.status, 0) + 1
                lbl = combined_outcome_label(p.outcome_status, p.business_outcome)
                combined_totals[lbl] = combined_totals.get(lbl, 0) + 1
            lines.append(f"    Status: {status_totals}")
            lines.append(f"    Combined outcomes: {combined_totals}")
            for p in cycles[c][:10]:
                # dedup_key format: monthly_opus:{cycle}:{title[:60]}
                title = (p.dedup_key or "").split(":", 2)[-1] if p.dedup_key else p.reason[:60]
                t_out = p.outcome_status or "unmeasured"
                b_out = p.business_outcome or "unmeasured"
                combined = combined_outcome_label(p.outcome_status, p.business_outcome)
                commit_note = f" commit={p.applied_commit_sha[:8]}" if p.applied_commit_sha else ""
                lines.append(
                    f"    - [{p.status}/tech={t_out}/biz={b_out}/{combined}{commit_note}] {title}"
                )
        return "\n".join(lines)
    except Exception as exc:
        return f"Prior monthly audits unavailable: {exc}"


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
    """
    Closed-loop: bugfix outcome stats — what worked and what didn't.

    ISOLATION: Only real_merchant outcomes are shown to Opus for strategic
    reasoning. Pre-merchant/test/sandbox stats are excluded to prevent
    synthetic evidence from influencing product direction. A separate
    technical-only stats line is shown for transparency.
    """
    try:
        from app.services.evolution_outcomes import get_effectiveness_stats
        # Product-grade stats (real merchant only) — drives strategic reasoning
        stats = get_effectiveness_stats(db, product_only=True)
        # All-source stats (technical reference only)
        all_stats = get_effectiveness_stats(db, product_only=False)

        if stats["total_measured"] == 0 and all_stats["total_measured"] == 0:
            return "Bugfix effectiveness (90d): No outcomes measured yet."

        lines = []
        if stats["total_measured"] > 0:
            lines.append(f"Bugfix effectiveness (90d, REAL MERCHANT ONLY — use for strategic decisions): {stats['total_measured']} measured")
            for src, data in stats["by_source"].items():
                eff = round(data["effective"] / data["total"] * 100) if data["total"] > 0 else 0
                lines.append(
                    f"  {src}: {data['total']} total, {data['effective']} effective ({eff}%), "
                    f"{data['ineffective']} ineffective, {data['inconclusive']} inconclusive"
                )
        else:
            lines.append("Bugfix effectiveness (90d, REAL MERCHANT): No real merchant outcomes yet. DO NOT use pre-merchant data for strategic reasoning.")

        # Technical reference (all sources) — informational only
        if all_stats["total_measured"] > stats["total_measured"]:
            pre_merchant_count = all_stats["total_measured"] - stats["total_measured"]
            lines.append(f"  [TECHNICAL REFERENCE — {pre_merchant_count} pre-merchant outcomes exist but are excluded from strategic reasoning]")

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

_SYSTEM_PROMPT = """You are a senior SaaS CTO making strategic decisions UNDER CONSTRAINT.
You are NOT an AI generating ideas.

Hedge Spark is an AI commerce intelligence system for Shopify. Your job is
to protect merchant trust, move revenue, and make at most 3 bets this month.

RULES OF ENGAGEMENT
===================

0. STRATEGIC LOCK (HIGHEST PRIORITY).
   The "CURRENT STRATEGY" block in the context defines the SINGLE axis
   the system competes on. Every bet MUST align with that strategy.
   Good ideas OUTSIDE the strategy are REJECTED — we prefer strategic
   focus over good ideas. Each bet MUST include:
     - why_this_bet_aligns_with_strategy: 1 sentence tying the bet to
       the core loop (detect behavioural leak → intervene in-session →
       measure causally). Without this field, the bet is rejected.

1. HARD CAP: AT MOST 3 BETS PER CYCLE.
   Fewer is fine. Zero is fine. If nothing is worth doing this month, say
   so. Returning an empty bet list is a valid, respected answer.

2. EACH BET MUST ANSWER 4 QUESTIONS (all required):
   (a) revenue_thesis       Concrete mechanism for how this moves revenue.
                            NOT "improves performance" or "enhances UX" —
                            a specific causal chain: "X → Y → +Z% CVR".
   (b) rejected_alternatives  At least 2 alternatives you CONSIDERED and
                              explicitly rejected, with the reason for each.
                              This is the decision memory. Show your work.
   (c) expected_impact      Specific, measurable outcome ("~+€300/mo on
                            top-10 products", not "higher conversion").
   (d) infra_cost_estimate  one of: none | small | medium | large
                            + a 1-sentence cost reasoning.

3. RESPECT RETIRED DOMAINS.
   If the context shows a "RETIRED DOMAINS" section, you MUST NOT propose
   anything in those areas. They have been measured and found to not move
   revenue. Propose them = get rejected.

4. EXPLORATION FLOOR.
   If the context flags "EXPLORATION REQUIRED" (one domain dominates past
   wins), at least 1 of your bets MUST be an exploration bet in a
   non-dominant domain. Mark it with exploration_bet=true.
   Otherwise, exploration is welcome but optional.

5. UX STABILITY (ABSOLUTE).
   DO NOT propose bets that change merchant-visible structure: dashboard
   layout, KPI labels/definitions, navigation, notification cadence,
   terminology. These are reserved for human-led RFCs. If you need to
   propose a UX change, mark risk_level='LEVEL_3' and say why it is
   critical in the revenue_thesis.

6. COST DISCIPLINE.
   - 'large' cost bets require >10x revenue lift vs monthly cost.
   - 'medium' cost bets require a concrete scaling-blocker removed.
   - 'none' and 'small' flow freely IF the revenue thesis is concrete.

7. OPTIMIZE FOR MONEY, NOT CORRECTNESS.
   Combined outcomes rank: BOTH > BUSINESS_SUCCESS > TECH_SUCCESS > NEITHER > NOISE.
   Target BOTH-shaped wins. Avoid pure infra work when the rolling
   business rate is low — fix conversion/retention paths first.

8. DEFERRED WORK.
   You MUST include a "deferred" field — 1 sentence stating WHAT you chose
   NOT to bet on this cycle and WHY. If you returned 3 bets, this explains
   what didn't make the cut. If you returned 0, this explains why nothing
   cleared the bar.

ALLOWED BET TYPES
=================
  growth        net-new acquisition, onboarding lift
  retention     churn reduction, re-engagement, LTV
  conversion    CVR lift, funnel fixes, checkout path
  experiment    A/B or exploratory bet with clear kill-criteria
  deprecate     remove something that doesn't work
  architecture  structural code change (only if revenue-unblocking)
  performance   speed / latency (only if tied to revenue)
  reliability   stability (only if tied to revenue)
  product       UX / feature (requires critical justification)

OUTPUT
======
{
  "strategy_reminder": "1-sentence restatement of the locked strategy as you understand it",
  "still_on_path_because": "1-2 sentences on why the strategy is still the right war this cycle",
  "bets": [
    {
      "title": "...",
      "type": "growth|retention|conversion|experiment|deprecate|architecture|performance|reliability|product",
      "why_this_bet_aligns_with_strategy": "1 sentence tying bet to detect→intervene→measure loop",
      "revenue_thesis": "concrete causal chain: X → Y → +Z",
      "rejected_alternatives": [
        {"alternative": "...", "why_rejected": "..."},
        {"alternative": "...", "why_rejected": "..."}
      ],
      "expected_impact": "specific measurable outcome (with % / € / time unit)",
      "risk_level": "LEVEL_2|LEVEL_3",
      "infra_cost_estimate": "none|small|medium|large",
      "infra_cost_reasoning": "1 sentence",
      "exploration_bet": true|false
    }
  ],
  "not_doing_this_month": ["item1", "item2", "item3"],
  "deferred": "1 sentence on what you chose NOT to bet on and why"
}
"""


def _call_opus(context: str) -> str:
    """
    Call Opus via Anthropic API. Budget-guarded. 429-aware. Protection-gated.
    Returns raw response text or empty string.
    """
    from app.core.llm_budget import check_budget, record_usage, record_blocked, is_provider_backed_off, record_429
    from app.core.protection_state import should_skip_optional_llm, protection_state

    # SELF-PROTECTION: the Opus audit is the single most expensive optional
    # LLM call. Even if caller passed earlier gates, re-check here — system
    # state can change between worker cycle start and actual LLM call.
    if should_skip_optional_llm():
        ps = protection_state()
        log.info("protection_state: %s — _call_opus refused (optional Opus)", ps["level"])
        record_blocked("monthly_opus_audit", f"protection_state:{ps['level']}")
        return ""

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

def _parse_proposals(raw: str, retired_domains: list[dict] | None = None) -> list[dict]:
    """
    Parse LLM response into bet dicts — ELITE CTO discipline enforced.

    The parser PREFERS rejecting bad thinking over silently normalizing it.
    Each discipline check rejects-and-logs rather than coerces-and-ships.

    Caller-supplied retired_domains enables STEP 6 hard-block: any bet
    mapping to a retired domain is rejected at parse time, not just
    de-prioritized.
    """
    from app.services.evolution_bet_governance import (
        check_type_valid,
        override_underestimated_cost,
        classify_ux_sensitivity,
        validate_expected_impact,
        reject_if_retired_domain,
        normalize_fingerprint,
        is_fingerprint_duplicate,
    )

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("monthly_audit: invalid JSON from Opus")
        return []

    # Accept 'bets' (new) or 'proposals' (legacy)
    raw_items = data.get("bets") or data.get("proposals") or []
    if not isinstance(raw_items, list):
        return []

    valid: list[dict] = []
    seen_fingerprints: set[str] = set()
    retired = retired_domains or []

    for p in raw_items[:MAX_PROPOSALS_PER_RUN]:
        # --- STEP 1: Type must be valid — NO silent fallback ---
        type_err = check_type_valid(p)
        if type_err:
            log.warning("monthly_audit: rejected bet — %s", type_err)
            continue
        ptype = p.get("type", "").strip().lower()

        # --- STEP 6: Retired-domain hard block ---
        retired_err = reject_if_retired_domain(ptype, retired)
        if retired_err:
            log.warning("monthly_audit: rejected bet — %s", retired_err)
            continue

        # --- STRATEGIC ALIGNMENT GATE ---
        # Bets outside the North Star are rejected here — strategy beats
        # quality. A technically excellent bet outside the war plan is
        # still a distraction.
        from app.services.evolution_strategy import (
            check_strategy_alignment, STRATEGY_VERSION,
        )
        strategy_err, alignment_score, alignment_verdict, alignment_hits = (
            check_strategy_alignment(p)
        )
        if strategy_err:
            log.warning(
                "monthly_audit: rejected bet — %s (score=%s verdict=%s hits=%s) title=%r",
                strategy_err, alignment_score, alignment_verdict, alignment_hits[:5],
                str(p.get("title"))[:80],
            )
            continue

        # --- Required discipline: revenue_thesis + rejected_alternatives ---
        revenue_thesis = str(p.get("revenue_thesis") or p.get("reasoning") or "").strip()
        if not revenue_thesis:
            log.warning("monthly_audit: rejected bet — missing revenue_thesis")
            continue
        if len(revenue_thesis) < 20:
            log.warning("monthly_audit: rejected bet — revenue_thesis too thin (<20 chars)")
            continue

        alts_raw = p.get("rejected_alternatives") or []
        alts: list[dict] = []
        if isinstance(alts_raw, list):
            for a in alts_raw:
                if not isinstance(a, dict):
                    continue
                alt = str(a.get("alternative", "")).strip()[:200]
                why = str(a.get("why_rejected", "")).strip()[:300]
                if alt and why:
                    alts.append({"alternative": alt, "why_rejected": why})
        if len(alts) < 2:
            log.warning(
                "monthly_audit: rejected bet — needs >=2 rejected_alternatives, got %d",
                len(alts),
            )
            continue

        # --- STEP 4: expected_impact must be MEASURABLE ---
        impact_err = validate_expected_impact(p)
        if impact_err:
            log.warning("monthly_audit: rejected bet — %s", impact_err)
            continue

        # --- STEP 7: wording-independent dedup fingerprint (Jaccard overlap) ---
        fingerprint = normalize_fingerprint(
            f"{p.get('title', '')} {revenue_thesis}"
        )
        if fingerprint and is_fingerprint_duplicate(fingerprint, seen_fingerprints):
            log.warning("monthly_audit: rejected bet — duplicate fingerprint=%s", fingerprint)
            continue
        if fingerprint:
            seen_fingerprints.add(fingerprint)

        # --- Risk level: LEVEL_2 or LEVEL_3 only ---
        risk = p.get("risk_level", "LEVEL_3")
        if risk not in ("LEVEL_2", "LEVEL_3"):
            risk = "LEVEL_3"

        # --- STEP 2: Cost heuristic override ---
        effective_cost, cost_override_reason = override_underestimated_cost(p)
        if cost_override_reason:
            log.warning(
                "monthly_audit: %s for bet='%s' (declared=%s → effective=%s)",
                cost_override_reason, p.get("title"),
                p.get("infra_cost_estimate"), effective_cost,
            )

        # --- STEP 3: UX sensitivity classification ---
        ux_sensitive, impact_radius = classify_ux_sensitivity(p)

        # --- Exploration flag ---
        exploration = bool(p.get("exploration_bet", False))

        # UX-sensitive bets are forced to LEVEL_3 — they cannot auto-convert,
        # and LEVEL_3 is proposal-only (no auto-apply ever).
        if ux_sensitive and risk != "LEVEL_3":
            log.info(
                "monthly_audit: forcing LEVEL_3 on UX-sensitive bet='%s' (was %s)",
                p.get("title"), risk,
            )
            risk = "LEVEL_3"

        valid.append({
            "title": str(p.get("title", "Untitled"))[:200],
            "type": ptype,
            "reasoning": revenue_thesis[:1000],
            "revenue_thesis": revenue_thesis[:1000],
            "rejected_alternatives": alts,
            "expected_impact": str(p.get("expected_impact", ""))[:500],
            "risk_level": risk,
            "infra_cost_estimate": effective_cost,
            "infra_cost_reasoning": str(p.get("infra_cost_reasoning", ""))[:200],
            "exploration_bet": exploration,
            "ux_sensitive": ux_sensitive,
            "impact_radius": impact_radius,
            "fingerprint": fingerprint,
            "strategy_alignment_score": alignment_score,
            "strategy_version": STRATEGY_VERSION,
            "why_this_bet_aligns_with_strategy": str(
                p.get("why_this_bet_aligns_with_strategy", "")
            )[:400],
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
            reason=f"[{p['title']}] {p.get('reasoning') or p.get('revenue_thesis') or ''}",
            expected_impact=p["expected_impact"],
            auto_applicable=False,  # NEVER auto-apply
            status="open",
            audit_cycle=cycle,
            dedup_key=dedup_key,
            # Strategic-bet fields — decision memory + cost awareness
            revenue_thesis=p.get("revenue_thesis"),
            rejected_alternatives=(
                json.dumps(p["rejected_alternatives"]) if p.get("rejected_alternatives") else None
            ),
            infra_cost_estimate=p.get("infra_cost_estimate"),
            exploration_bet=bool(p.get("exploration_bet", False)),
            ux_sensitive=bool(p.get("ux_sensitive", False)),
            impact_radius=p.get("impact_radius"),
            strategy_alignment_score=p.get("strategy_alignment_score"),
            strategy_version=p.get("strategy_version"),
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

    # Build context — strategic war plan goes first. Every downstream
    # context block is interpreted through the lens of the locked strategy.
    from app.services.evolution_strategy import format_strategy_for_prompt
    context_parts = [
        f"Monthly strategic audit for cycle: {cycle}",
        "",
        format_strategy_for_prompt(),
        "",
        _build_codebase_summary(),
        "",
        _build_bugfix_history(db),
        "",
        _build_bugfix_effectiveness(db),
        "",
        _build_prior_monthly_audits(db),
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

    # Compute governance state BEFORE parsing so retired domains can
    # hard-block bets at parse time, not just be de-prioritized.
    _retired_domains: list[dict] = []
    _explore_req = False
    _dominant: str | None = None
    try:
        from app.services.evolution_reinforcement import (
            compute_reinforcement_weights,
            get_retired_domains,
            exploration_required,
        )
        _weights = compute_reinforcement_weights(db)
        _retired_domains = get_retired_domains(_weights)
        _explore_req, _dominant = exploration_required(_weights)
    except Exception as exc:
        log.warning("monthly_audit: governance state unavailable (non-fatal): %s", type(exc).__name__)

    # Parse + validate with retired-domain hard-block
    proposals = _parse_proposals(raw, retired_domains=_retired_domains)

    # NO STRATEGIC BETS THIS MONTH — valid answer. Never force output.
    if not proposals:
        return {
            "status": "completed",
            "reason": "no_strategic_bets_this_month",
            "proposals_created": 0,
            "proposals": [],
        }

    # Batch-level discipline gates
    from app.services.evolution_bet_governance import (
        check_batch_diversification,
        check_exploration_floor,
    )
    diversification_err = check_batch_diversification(proposals)
    if diversification_err:
        log.warning("monthly_audit: rejected batch — %s", diversification_err)
        return {
            "status": "completed",
            "reason": diversification_err,
            "proposals_created": 0,
            "proposals": [],
        }

    exploration_err = check_exploration_floor(proposals, _explore_req)
    if exploration_err:
        log.warning(
            "monthly_audit: rejected batch — %s (dominant=%s)",
            exploration_err, _dominant,
        )
        return {
            "status": "completed",
            "reason": exploration_err,
            "proposals_created": 0,
            "proposals": [],
        }

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
