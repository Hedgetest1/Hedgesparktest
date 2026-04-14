"""
adaptive_governance.py — Bounded evidence-aware threshold computation.

Replaces static governance constants with evidence-driven values that adapt
to system health while remaining within hard safety bounds.

Design principles:
    - Pure computation from current health data — no stored state
    - Every threshold has hard min/max bounds that cannot be exceeded
    - Minimum evidence requirements before any adjustment
    - Conservative fallback: insufficient evidence → static default
    - Deterministic: same health data → same thresholds
    - Observable: every computed value includes its reasoning

Public interface:
    get_adaptive_thresholds(db) -> AdaptiveThresholds
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("adaptive_governance")


# ---------------------------------------------------------------------------
# Static defaults (unchanged from original constants)
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "max_auto_applies_per_day": 5,
    "domain_budget_default": 2,
    "weakness_unstable_threshold": 15,
    "weakness_quarantine_threshold": 30,
    "circuit_breaker_threshold": 3,
    "promote_confidence": 0.9,
}

# ---------------------------------------------------------------------------
# Hard bounds — NEVER exceeded regardless of evidence
# ---------------------------------------------------------------------------

_BOUNDS = {
    "max_auto_applies_per_day":       {"min": 2,    "max": 8},
    "domain_budget_default":          {"min": 1,    "max": 3},
    "weakness_unstable_threshold":    {"min": 10,   "max": 20},
    "weakness_quarantine_threshold":  {"min": 20,   "max": 40},
    "circuit_breaker_threshold":      {"min": 2,    "max": 5},
    "promote_confidence":             {"min": 0.80, "max": 0.95},
}

# Minimum measured outcomes before adapting (prevents noise-driven changes)
_MIN_EVIDENCE_FOR_ADAPTATION = 5


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ThresholdValue:
    """A single adaptive threshold with full context."""
    name: str
    value: float | int
    default: float | int
    min_bound: float | int
    max_bound: float | int
    reason: str
    adapted: bool  # True if value differs from default

    @property
    def is_tightened(self) -> bool:
        """Whether the threshold is stricter than default."""
        # For caps/budgets: lower = tighter. For confidence: higher = tighter.
        if self.name == "promote_confidence":
            return self.value > self.default
        return self.value < self.default


@dataclass
class AdaptiveThresholds:
    """Complete adaptive governance state."""
    max_auto_applies_per_day: int = 5
    domain_budget_default: int = 2
    weakness_unstable_threshold: int = 15
    weakness_quarantine_threshold: int = 30
    circuit_breaker_threshold: int = 3
    promote_confidence: float = 0.9

    evidence: dict = field(default_factory=dict)
    details: list[ThresholdValue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "thresholds": {d.name: {"value": d.value, "default": d.default, "adapted": d.adapted, "reason": d.reason}
                           for d in self.details},
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------

def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _gather_evidence(db: Session) -> dict:
    """
    Gather the health signals used to compute adaptive thresholds.
    Returns dict with all evidence fields.
    """
    now = _now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    evidence = {
        "effectiveness_30d_pct": None,
        "effectiveness_7d_pct": None,
        "total_measured_30d": 0,
        "failure_rate_30d_pct": 0.0,
        "trend_direction": "stable",
        "active_thrashing_sources": 0,
        "lesson_accuracy_rate": None,
        "total_lessons_measured": 0,
    }

    try:
        # 30-day effectiveness
        rows = db.execute(text("""
            SELECT outcome_status, COUNT(*) FROM bugfix_candidates
            WHERE outcome_status IS NOT NULL AND outcome_measured_at >= :cutoff
            GROUP BY outcome_status
        """), {"cutoff": month_ago}).fetchall()
        outcome_map = {r[0]: r[1] for r in rows}
        total = sum(outcome_map.values())
        evidence["total_measured_30d"] = total
        if total >= _MIN_EVIDENCE_FOR_ADAPTATION:
            effective = outcome_map.get("effective", 0)
            evidence["effectiveness_30d_pct"] = round(effective / total * 100, 1)

        # 7-day effectiveness
        rows_7d = db.execute(text("""
            SELECT outcome_status, COUNT(*) FROM bugfix_candidates
            WHERE outcome_status IS NOT NULL AND outcome_measured_at >= :cutoff
            GROUP BY outcome_status
        """), {"cutoff": week_ago}).fetchall()
        map_7d = {r[0]: r[1] for r in rows_7d}
        total_7d = sum(map_7d.values())
        if total_7d > 0:
            evidence["effectiveness_7d_pct"] = round(map_7d.get("effective", 0) / total_7d * 100, 1)

        # Failure rate
        attempted = db.execute(text("""
            SELECT COUNT(*) FROM bugfix_candidates
            WHERE created_at >= :cutoff AND status NOT IN ('open', 'analyzed')
        """), {"cutoff": month_ago}).fetchone()
        failed = db.execute(text("""
            SELECT COUNT(*) FROM bugfix_candidates
            WHERE created_at >= :cutoff AND status IN ('apply_failed', 'rolled_back', 'rejected')
        """), {"cutoff": month_ago}).fetchone()
        att = attempted[0] if attempted else 0
        fl = failed[0] if failed else 0
        evidence["failure_rate_30d_pct"] = round(fl / att * 100, 1) if att > 0 else 0.0

        # Trend
        try:
            from app.services.loop_health import _compute_trend
            trend = _compute_trend(db, now)
            evidence["trend_direction"] = trend.get("direction", "stable")
        except Exception:
            pass

        # Active thrashing sources
        try:
            from app.services.loop_health import check_thrashing
            thrashing = check_thrashing(db)
            evidence["active_thrashing_sources"] = len(thrashing)
        except Exception:
            pass

        # Lesson accuracy (how often lesson-assisted proposals succeed)
        try:
            lesson_outcomes = db.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN outcome_status = 'effective' THEN 1 END) AS effective
                FROM bugfix_candidates
                WHERE lesson_ids_used IS NOT NULL
                  AND outcome_status IS NOT NULL
                  AND outcome_measured_at >= :cutoff
            """), {"cutoff": month_ago}).fetchone()
            if lesson_outcomes and lesson_outcomes[0] > 0:
                evidence["total_lessons_measured"] = lesson_outcomes[0]
                evidence["lesson_accuracy_rate"] = round(lesson_outcomes[1] / lesson_outcomes[0] * 100, 1)
        except Exception:
            pass

        # Global operator feedback
        try:
            feedback = _gather_operator_feedback(db)
            evidence["operator_feedback"] = feedback.get("global", {})
        except Exception:
            evidence["operator_feedback"] = {}

    except Exception as exc:
        log.warning("adaptive_governance: evidence gathering failed: %s", exc)

    return evidence


# ---------------------------------------------------------------------------
# Threshold computation
# ---------------------------------------------------------------------------

def _clamp(value: float | int, bounds: dict) -> float | int:
    """Clamp a value to its hard bounds."""
    return max(bounds["min"], min(bounds["max"], value))


def _compute_daily_cap(evidence: dict) -> ThresholdValue:
    """
    Adapt daily auto-apply cap based on effectiveness and trend.

    Logic:
    - Default 5
    - If effectiveness > 70% AND trend improving → increase to 7
    - If effectiveness > 60% AND trend stable → keep 5
    - If effectiveness < 40% OR trend degrading → reduce to 3
    - If failure rate > 40% → reduce to 2
    """
    name = "max_auto_applies_per_day"
    default = _DEFAULTS[name]
    bounds = _BOUNDS[name]
    eff = evidence.get("effectiveness_30d_pct")
    trend = evidence.get("trend_direction", "stable")
    failure_rate = evidence.get("failure_rate_30d_pct", 0)
    total = evidence.get("total_measured_30d", 0)

    if total < _MIN_EVIDENCE_FOR_ADAPTATION:
        return ThresholdValue(name, default, default, bounds["min"], bounds["max"],
                              "insufficient evidence — using default", adapted=False)

    value = default
    op_feedback = evidence.get("operator_feedback", {})
    op_approval = op_feedback.get("approval_rate")
    op_decisions = op_feedback.get("total", 0)

    if failure_rate > 40:
        value = 2
        reason = f"failure_rate={failure_rate}% > 40% — restricting to minimum"
    elif eff is not None and eff < 40 or trend == "degrading":
        value = 3
        reason = f"effectiveness={eff}% or trend={trend} — restricting"
    elif eff is not None and eff > 70 and trend == "improving":
        value = 7
        reason = f"effectiveness={eff}% + improving trend — increased"
    else:
        value = default
        reason = f"effectiveness={eff}% trend={trend} — default"

    # Operator feedback adjustment: if operator rejects > 60% of proposals, restrict
    if op_decisions >= 5 and op_approval is not None and op_approval < 40:
        value = max(bounds["min"], value - 1)
        reason += f" -1 (operator_approval={op_approval}%)"

    value = _clamp(value, bounds)
    return ThresholdValue(name, int(value), default, bounds["min"], bounds["max"],
                          reason, adapted=(value != default))


def _compute_domain_budget(evidence: dict) -> ThresholdValue:
    """
    Adapt default domain budget based on overall system health.

    Logic:
    - Default 2
    - If effectiveness > 70% AND no thrashing → increase to 3
    - If effectiveness < 40% OR thrashing > 2 → reduce to 1
    """
    name = "domain_budget_default"
    default = _DEFAULTS[name]
    bounds = _BOUNDS[name]
    eff = evidence.get("effectiveness_30d_pct")
    thrashing = evidence.get("active_thrashing_sources", 0)
    total = evidence.get("total_measured_30d", 0)

    if total < _MIN_EVIDENCE_FOR_ADAPTATION:
        return ThresholdValue(name, default, default, bounds["min"], bounds["max"],
                              "insufficient evidence — using default", adapted=False)

    if eff is not None and eff > 70 and thrashing == 0:
        value = 3
        reason = f"effectiveness={eff}% + no thrashing — increased"
    elif (eff is not None and eff < 40) or thrashing > 2:
        value = 1
        reason = f"effectiveness={eff}% or thrashing={thrashing} — restricted"
    else:
        value = default
        reason = f"effectiveness={eff}% thrashing={thrashing} — default"

    value = _clamp(value, bounds)
    return ThresholdValue(name, int(value), default, bounds["min"], bounds["max"],
                          reason, adapted=(value != default))


def _compute_weakness_thresholds(evidence: dict) -> tuple[ThresholdValue, ThresholdValue]:
    """
    Adapt weakness thresholds based on trend direction.

    Logic:
    - When degrading: tighten thresholds (lower = more sensitive → more quarantines)
    - When improving: loosen slightly (higher = less sensitive → fewer quarantines)
    - When stable: use defaults
    """
    trend = evidence.get("trend_direction", "stable")
    total = evidence.get("total_measured_30d", 0)

    unstable_default = _DEFAULTS["weakness_unstable_threshold"]
    quarantine_default = _DEFAULTS["weakness_quarantine_threshold"]
    ub = _BOUNDS["weakness_unstable_threshold"]
    qb = _BOUNDS["weakness_quarantine_threshold"]

    if total < _MIN_EVIDENCE_FOR_ADAPTATION:
        return (
            ThresholdValue("weakness_unstable_threshold", unstable_default, unstable_default,
                           ub["min"], ub["max"], "insufficient evidence", adapted=False),
            ThresholdValue("weakness_quarantine_threshold", quarantine_default, quarantine_default,
                           qb["min"], qb["max"], "insufficient evidence", adapted=False),
        )

    if trend == "degrading":
        uv = _clamp(unstable_default - 3, ub)  # 15 → 12
        qv = _clamp(quarantine_default - 5, qb)  # 30 → 25
        reason = f"trend=degrading — tightening thresholds"
    elif trend == "improving":
        uv = _clamp(unstable_default + 2, ub)  # 15 → 17
        qv = _clamp(quarantine_default + 3, qb)  # 30 → 33
        reason = f"trend=improving — loosening slightly"
    else:
        uv, qv = unstable_default, quarantine_default
        reason = f"trend=stable — default"

    return (
        ThresholdValue("weakness_unstable_threshold", int(uv), unstable_default,
                       ub["min"], ub["max"], reason, adapted=(uv != unstable_default)),
        ThresholdValue("weakness_quarantine_threshold", int(qv), quarantine_default,
                       qb["min"], qb["max"], reason, adapted=(qv != quarantine_default)),
    )


def _compute_circuit_breaker(evidence: dict) -> ThresholdValue:
    """
    Adapt circuit breaker threshold based on trend.

    Logic:
    - When degrading: tighten to 2 (trip faster)
    - When improving: loosen to 4 (more tolerance)
    - Default: 3
    """
    name = "circuit_breaker_threshold"
    default = _DEFAULTS[name]
    bounds = _BOUNDS[name]
    trend = evidence.get("trend_direction", "stable")

    if trend == "degrading":
        value = 2
        reason = "trend=degrading — trip faster"
    elif trend == "improving":
        value = 4
        reason = "trend=improving — more tolerance"
    else:
        value = default
        reason = "trend=stable — default"

    value = _clamp(value, bounds)
    return ThresholdValue(name, int(value), default, bounds["min"], bounds["max"],
                          reason, adapted=(value != default))


def _compute_promote_confidence(evidence: dict) -> ThresholdValue:
    """
    Adapt lesson promotion confidence threshold based on lesson accuracy.

    Logic:
    - If lessons are proving accurate (>70% effective when used): lower bar to 0.85
    - If lessons are inaccurate (<40%): raise bar to 0.95
    - Default: 0.90
    """
    name = "promote_confidence"
    default = _DEFAULTS[name]
    bounds = _BOUNDS[name]
    accuracy = evidence.get("lesson_accuracy_rate")
    total = evidence.get("total_lessons_measured", 0)

    if total < 3:
        return ThresholdValue(name, default, default, bounds["min"], bounds["max"],
                              "insufficient lesson data — using default", adapted=False)

    if accuracy is not None and accuracy > 70:
        value = 0.85
        reason = f"lesson_accuracy={accuracy}% — lowering promotion bar"
    elif accuracy is not None and accuracy < 40:
        value = 0.95
        reason = f"lesson_accuracy={accuracy}% — raising promotion bar"
    else:
        value = default
        reason = f"lesson_accuracy={accuracy}% — default"

    value = _clamp(value, bounds)
    return ThresholdValue(name, round(value, 2), default, bounds["min"], bounds["max"],
                          reason, adapted=(value != default))


# ---------------------------------------------------------------------------
# Per-domain intelligence
# ---------------------------------------------------------------------------

@dataclass
class DomainProfile:
    """Per-domain adaptive governance state."""
    domain: str
    effectiveness_pct: float | None  # 30d effectiveness for this domain
    total_measured: int              # total measured outcomes for this domain
    budget: int                      # computed daily apply budget for this domain
    weakness_score: float            # from loop_health weakness ranking
    operator_approval_rate: float | None  # operator approval rate for this domain
    operator_decisions: int          # total operator decisions for this domain
    reason: str                      # why budget was set to this value
    adapted: bool                    # True if budget differs from global default

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "effectiveness_pct": self.effectiveness_pct,
            "total_measured": self.total_measured,
            "budget": self.budget,
            "weakness_score": self.weakness_score,
            "operator_approval_rate": self.operator_approval_rate,
            "operator_decisions": self.operator_decisions,
            "reason": self.reason,
            "adapted": self.adapted,
        }


def _gather_domain_effectiveness(db: Session) -> dict[str, dict]:
    """
    Compute per-domain effectiveness from measured bugfix outcomes.
    Returns {domain: {"effective": n, "ineffective": n, "total": n, "pct": float}}.
    """
    try:
        from datetime import timedelta
        cutoff = _now() - timedelta(days=90)

        rows = db.execute(text("""
            SELECT affected_domain, outcome_status, COUNT(*) AS cnt
            FROM bugfix_candidates
            WHERE status = 'applied'
              AND outcome_status IS NOT NULL
              AND outcome_measured_at >= :cutoff
              AND affected_domain IS NOT NULL
            GROUP BY affected_domain, outcome_status
        """), {"cutoff": cutoff}).fetchall()

        result: dict[str, dict] = {}
        for domain, status, cnt in rows:
            if domain not in result:
                result[domain] = {"effective": 0, "ineffective": 0, "inconclusive": 0, "total": 0}
            result[domain][status] = result[domain].get(status, 0) + cnt
            result[domain]["total"] += cnt

        for domain, data in result.items():
            total = data["total"]
            data["pct"] = round(data["effective"] / total * 100, 1) if total > 0 else None

        return result
    except Exception as exc:
        log.warning("adaptive_governance: domain effectiveness query failed: %s", exc)
        return {}


def _gather_operator_feedback(db: Session) -> dict[str, dict]:
    """
    Aggregate operator approval/rejection decisions from audit_log.
    Returns {domain: {"approved": n, "rejected": n, "total": n, "approval_rate": float}}.

    Also returns a global summary for domain-agnostic feedback.
    """
    try:
        from datetime import timedelta
        cutoff = _now() - timedelta(days=90)

        # Bugfix approvals/rejections — extract domain from metadata_json
        rows = db.execute(text("""
            SELECT action_type, target_id, metadata_json
            FROM audit_log
            WHERE actor_type = 'human'
              AND action_type IN ('bugfix_approved', 'bugfix_rejected',
                                  'lesson_promotion_approved', 'lesson_promotion_rejected')
              AND created_at >= :cutoff
        """), {"cutoff": cutoff}).fetchall()

        import json
        per_domain: dict[str, dict] = {}
        global_feedback = {"approved": 0, "rejected": 0, "total": 0}

        for action_type, target_id, meta_json in rows:
            is_approved = action_type.endswith("_approved")
            domain = None

            # Extract domain from metadata
            if meta_json:
                try:
                    meta = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
                    domain = meta.get("domain")
                except Exception:
                    pass

            # For bugfix actions without domain in metadata, look up the candidate
            if not domain and "bugfix" in action_type and target_id:
                try:
                    from app.models.bugfix_candidate import BugFixCandidate
                    c = db.query(BugFixCandidate).get(int(target_id))
                    if c:
                        domain = getattr(c, "affected_domain", None)
                except Exception:
                    pass

            # Aggregate
            global_feedback["total"] += 1
            if is_approved:
                global_feedback["approved"] += 1
            else:
                global_feedback["rejected"] += 1

            if domain:
                if domain not in per_domain:
                    per_domain[domain] = {"approved": 0, "rejected": 0, "total": 0}
                per_domain[domain]["total"] += 1
                if is_approved:
                    per_domain[domain]["approved"] += 1
                else:
                    per_domain[domain]["rejected"] += 1

        # Compute approval rates
        for d_data in per_domain.values():
            if d_data["total"] > 0:
                d_data["approval_rate"] = round(d_data["approved"] / d_data["total"] * 100, 1)
            else:
                d_data["approval_rate"] = None

        if global_feedback["total"] > 0:
            global_feedback["approval_rate"] = round(
                global_feedback["approved"] / global_feedback["total"] * 100, 1
            )
        else:
            global_feedback["approval_rate"] = None

        return {"per_domain": per_domain, "global": global_feedback}
    except Exception as exc:
        log.warning("adaptive_governance: operator feedback query failed: %s", exc)
        return {"per_domain": {}, "global": {"approved": 0, "rejected": 0, "total": 0, "approval_rate": None}}


# Per-domain budget bounds
_DOMAIN_BUDGET_BOUNDS = {"min": 0, "max": 4}
_MIN_DOMAIN_EVIDENCE = 3  # minimum measured outcomes before domain-level adaptation


def _compute_domain_profile(
    domain: str,
    global_budget_default: int,
    weakness_score: float,
    unstable_threshold: int,
    quarantine_threshold: int,
    domain_effectiveness: dict | None,
    domain_feedback: dict | None,
) -> DomainProfile:
    """
    Compute per-domain adaptive budget from effectiveness + weakness + operator feedback.

    Logic:
    1. Start from weakness-based tier (quarantine/unstable/default)
    2. Adjust based on per-domain effectiveness IF sufficient evidence
    3. Adjust based on operator feedback IF sufficient decisions
    4. Clamp to bounds
    """
    # Step 1: Weakness-based tier (existing logic)
    if weakness_score >= quarantine_threshold:
        budget = 0
        reason = f"quarantined (weakness={weakness_score:.0f} >= {quarantine_threshold})"
        return DomainProfile(
            domain=domain, effectiveness_pct=None, total_measured=0,
            budget=0, weakness_score=weakness_score,
            operator_approval_rate=None, operator_decisions=0,
            reason=reason, adapted=True,
        )

    if weakness_score >= unstable_threshold:
        budget = 1
        reason = f"unstable (weakness={weakness_score:.0f} >= {unstable_threshold})"
    else:
        budget = global_budget_default
        reason = f"default (weakness={weakness_score:.0f})"

    # Step 2: Domain effectiveness adjustment
    eff_pct = None
    total_measured = 0
    if domain_effectiveness:
        total_measured = domain_effectiveness.get("total", 0)
        eff_pct = domain_effectiveness.get("pct")

        if total_measured >= _MIN_DOMAIN_EVIDENCE and eff_pct is not None:
            if eff_pct >= 75:
                budget = min(budget + 1, _DOMAIN_BUDGET_BOUNDS["max"])
                reason += f" +1 (effectiveness={eff_pct}%)"
            elif eff_pct < 30:
                budget = max(budget - 1, _DOMAIN_BUDGET_BOUNDS["min"])
                reason += f" -1 (effectiveness={eff_pct}%)"

    # Step 3: Operator feedback adjustment
    approval_rate = None
    operator_decisions = 0
    if domain_feedback:
        operator_decisions = domain_feedback.get("total", 0)
        approval_rate = domain_feedback.get("approval_rate")

        if operator_decisions >= 3 and approval_rate is not None:
            if approval_rate < 30:
                # Operator rejects most proposals for this domain → restrict
                budget = max(budget - 1, _DOMAIN_BUDGET_BOUNDS["min"])
                reason += f" -1 (operator_approval={approval_rate}%)"
            elif approval_rate >= 80 and eff_pct is not None and eff_pct >= 60:
                # Operator approves AND domain is effective → trust
                budget = min(budget + 1, _DOMAIN_BUDGET_BOUNDS["max"])
                reason += f" +1 (operator_approval={approval_rate}% + effective)"

    # Clamp
    budget = max(_DOMAIN_BUDGET_BOUNDS["min"], min(_DOMAIN_BUDGET_BOUNDS["max"], budget))

    return DomainProfile(
        domain=domain,
        effectiveness_pct=eff_pct,
        total_measured=total_measured,
        budget=budget,
        weakness_score=weakness_score,
        operator_approval_rate=approval_rate,
        operator_decisions=operator_decisions,
        reason=reason,
        adapted=(budget != global_budget_default),
    )


def get_domain_profiles(db: Session) -> dict[str, DomainProfile]:
    """
    Compute per-domain adaptive profiles for all known domains.

    Returns {domain: DomainProfile} with budget, effectiveness, and operator feedback.
    """
    try:
        # Get global adaptive thresholds for baseline values
        thresholds = get_adaptive_thresholds(db)

        # Get per-domain data
        domain_effectiveness = _gather_domain_effectiveness(db)
        operator_feedback = _gather_operator_feedback(db)
        feedback_by_domain = operator_feedback.get("per_domain", {})

        # Get weakness ranking
        from app.services.loop_health import score_subsystem_weakness
        weakness_ranking = score_subsystem_weakness(db, lookback_days=30)
        weakness_map = {w["domain"]: w["score"] for w in weakness_ranking}

        # Compute profiles for all domains that have any signal
        all_domains = set(weakness_map.keys()) | set(domain_effectiveness.keys()) | set(feedback_by_domain.keys())

        profiles: dict[str, DomainProfile] = {}
        for domain in sorted(all_domains):
            profiles[domain] = _compute_domain_profile(
                domain=domain,
                global_budget_default=thresholds.domain_budget_default,
                weakness_score=weakness_map.get(domain, 0),
                unstable_threshold=thresholds.weakness_unstable_threshold,
                quarantine_threshold=thresholds.weakness_quarantine_threshold,
                domain_effectiveness=domain_effectiveness.get(domain),
                domain_feedback=feedback_by_domain.get(domain),
            )

        return profiles
    except Exception as exc:
        log.debug("adaptive_governance: domain profiles failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_adaptive_thresholds(db: Session) -> AdaptiveThresholds:
    """
    Compute all adaptive thresholds from current health evidence.

    Pure computation — no side effects, no stored state.
    Safe under failure — returns defaults if evidence gathering fails.
    """
    evidence = _gather_evidence(db)

    daily_cap = _compute_daily_cap(evidence)
    domain_budget = _compute_domain_budget(evidence)
    weakness_unstable, weakness_quarantine = _compute_weakness_thresholds(evidence)
    circuit_breaker = _compute_circuit_breaker(evidence)
    promote_conf = _compute_promote_confidence(evidence)

    details = [daily_cap, domain_budget, weakness_unstable, weakness_quarantine,
               circuit_breaker, promote_conf]

    adapted_count = sum(1 for d in details if d.adapted)
    if adapted_count > 0:
        log.info(
            "adaptive_governance: %d/%d thresholds adapted — %s",
            adapted_count, len(details),
            ", ".join(f"{d.name}={d.value}" for d in details if d.adapted),
        )

    return AdaptiveThresholds(
        max_auto_applies_per_day=daily_cap.value,
        domain_budget_default=domain_budget.value,
        weakness_unstable_threshold=weakness_unstable.value,
        weakness_quarantine_threshold=weakness_quarantine.value,
        circuit_breaker_threshold=circuit_breaker.value,
        promote_confidence=promote_conf.value,
        evidence=evidence,
        details=details,
    )
