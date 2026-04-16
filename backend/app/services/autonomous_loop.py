"""
autonomous_loop.py — Hardened autonomous revenue recovery loop.

Architecture:
  1. Measurement health validation BEFORE any outcome acceptance
  2. Multi-dimensional trust profile (execution, measurement, outcome, stability)
  3. Earned autonomy levels (0–5) replacing static risk classification
  4. Contradiction detection preventing model poisoning
  5. Nudge interaction intelligence for multi-nudge stores
  6. Global + per-merchant fail-safes
  7. Temporal decay in learning (recent outcomes weighted higher)

Safety invariant: the system NEVER learns from unvalidated data.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.active_nudge import ActiveNudge
from app.models.autonomous_action import AutonomousAction
from app.models.store_intelligence_profile import StoreIntelligenceProfile
from app.services.measurement_health import (
    HealthState,
    check_measurement_health,
    update_sip_measurement_health,
)

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════

_MAX_ACTIONS_PER_SHOP_PER_DAY = 3
_DEFAULT_HOLDOUT_PCT = 20

# Rollback
_MIN_VISITORS_EARLY_ROLLBACK = 50
_EARLY_ROLLBACK_CVR_DEGRADATION = 0.20
_INTERACTION_NUDGE_CAP = 4

# Trust dynamics
_TRUST_INITIAL = 0.5
_TRUST_GAIN_POSITIVE = 0.04
_TRUST_LOSS_ROLLBACK = 0.15
_TRUST_LOSS_NEGATIVE = 0.10
_TRUST_GAIN_NEUTRAL = 0.01
_TRUST_FLOOR = 0.0
_TRUST_CEILING = 1.0

# Autonomy level thresholds
# Level: (min_trust, min_sip_confidence, min_successful_experiments)
_AUTONOMY_LEVELS = {
    0: (0.0,  "none",   0),   # observe only
    1: (0.0,  "low",    0),   # suggest
    2: (0.3,  "medium", 0),   # assisted (merchant approval)
    3: (0.6,  "medium", 3),   # semi-auto (low risk auto-deploy)
    4: (0.75, "high",   8),   # full auto (low + medium auto-deploy)
    5: (0.9,  "high",   15),  # aggressive (parallel experiments)
}

# Cooldowns
_COOLDOWN_DAYS_NEGATIVE = 14
_COOLDOWN_DAYS_ROLLBACK = 30
_MAX_FAILURES_BEFORE_COOLDOWN = 2

# Learning gates
_MIN_VISITORS_FOR_LEARNING = 200
_MIN_P_VALUE_FOR_LEARNING = 0.10
_MIN_P_VALUE_FOR_STRONG_LEARNING = 0.05
_MAX_LEARNING_WEIGHT = 0.25
_DECAY_HALFLIFE_DAYS = 60

# Global
_GLOBAL_ROLLBACK_THRESHOLD = 3


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════

def run_autonomous_cycle(db: Session, shop_domain: str) -> int:
    """Run one autonomous loop cycle for a merchant."""
    sip = _load_sip(db, shop_domain)

    # Kill switch
    if sip and sip.autonomous_paused:
        return 0

    # Measurement health gate
    if sip and sip.measurement_health == "broken":
        return 0

    # Global fail-safe
    if _is_global_anomaly(db):
        log.warning("autonomous_loop: GLOBAL PAUSE — anomaly detected")
        return 0

    # Recompute autonomy level
    if sip:
        _recompute_autonomy_level(db, sip)

    actions_taken = 0
    actions_taken += _evaluate_existing_actions(db, shop_domain, sip)
    actions_taken += _evaluate_new_signals(db, shop_domain, sip)
    return actions_taken


# ══════════════════════════════════════════════════════════════════════════
# Phase 1 — Evaluate existing actions
# ══════════════════════════════════════════════════════════════════════════

def _evaluate_existing_actions(db: Session, shop_domain: str, sip: StoreIntelligenceProfile | None) -> int:
    actions = (
        db.query(AutonomousAction)
        .filter(
            AutonomousAction.shop_domain == shop_domain,
            AutonomousAction.status.in_(["deployed", "measuring"]),
        )
        .all()
    )
    count = 0
    for action in actions:
        try:
            if _check_rollback(db, action):
                count += 1
                continue
            if _check_completion(db, action, sip):
                count += 1
        except Exception as exc:
            log.warning("autonomous_loop: eval error id=%d: %s", action.id, exc)
            db.rollback()
    return count


def _check_rollback(db: Session, action: AutonomousAction) -> bool:
    if not action.nudge_id:
        return False

    nudge = db.query(ActiveNudge).filter(ActiveNudge.id == action.nudge_id).first()
    if not nudge or nudge.status != "active":
        action.status = "rolled_back"
        action.rollback_reason = "Nudge no longer active"
        action.updated_at = _now()
        db.commit()
        _update_trust(db, action.shop_domain, "rollback")
        return True

    rank_data = _get_rank_data(db, action.shop_domain, nudge)
    if not rank_data:
        return False

    exposed = rank_data.get("exposed_count", 0)
    holdout = rank_data.get("holdout_count", 0)
    exposed_cvr = rank_data.get("post_exposure_cvr", 0)
    holdout_cvr = rank_data.get("holdout_cvr") or 0

    # Early rollback: treatment materially worse
    if exposed >= _MIN_VISITORS_EARLY_ROLLBACK and holdout >= 10:
        if holdout_cvr > 0 and exposed_cvr < holdout_cvr * (1 - _EARLY_ROLLBACK_CVR_DEGRADATION):
            _execute_rollback(db, action, nudge,
                              f"Treatment CVR {exposed_cvr:.4f} >{_EARLY_ROLLBACK_CVR_DEGRADATION:.0%} "
                              f"worse than control {holdout_cvr:.4f} (n={exposed + holdout})")
            return True

    rec = rank_data.get("recommendation", "")
    if rec in ("investigate_negative_lift", "deactivate_low_value"):
        _execute_rollback(db, action, nudge, f"Nudge rank: {rec}")
        return True

    return False


def _check_completion(db: Session, action: AutonomousAction, sip: StoreIntelligenceProfile | None) -> bool:
    if not action.nudge_id:
        return False

    nudge = db.query(ActiveNudge).filter(ActiveNudge.id == action.nudge_id).first()
    if not nudge:
        return False

    rank_data = _get_rank_data(db, action.shop_domain, nudge)
    if not rank_data:
        return False

    if not rank_data.get("sufficient_sample"):
        if action.status == "deployed":
            action.status = "measuring"
            action.measurement_start = action.measurement_start or _now()
            action.updated_at = _now()
            db.commit()
        return False

    exposed_count = rank_data.get("exposed_count", 0)
    holdout_count = rank_data.get("holdout_count", 0)
    exposed_cvr = rank_data.get("post_exposure_cvr", 0)
    holdout_cvr = rank_data.get("holdout_cvr") or 0
    cvr_lift = rank_data.get("cvr_lift_pct")
    p_value = rank_data.get("p_value")

    # ── MEASUREMENT HEALTH CHECK (critical gate) ──
    health, health_detail = check_measurement_health(
        db, action.shop_domain, action.nudge_id,
        exposed_count, holdout_count, cvr_lift,
    )
    update_sip_measurement_health(db, action.shop_domain, health, health_detail)

    if health == HealthState.BROKEN:
        # Freeze: do not classify outcome, do not update trust or SIP
        action.rollback_reason = f"Measurement broken: {health_detail}"
        action.status = "rolled_back"
        action.updated_at = _now()
        from app.services.nudge_engine import deactivate_nudge
        deactivate_nudge(db, nudge.id, action.shop_domain)
        db.commit()
        log.warning("autonomous_loop: FROZEN id=%d — measurement broken: %s", action.id, health_detail)
        return True

    if health == HealthState.DEGRADED:
        # Continue measuring but do NOT classify outcome yet
        log.info("autonomous_loop: DEGRADED measurement id=%d — %s", action.id, health_detail)
        return False

    # ── Outcome classification (only on HEALTHY measurement) ──
    if p_value is not None and p_value < 0.05 and cvr_lift is not None and cvr_lift > 0:
        outcome = "positive"
    elif p_value is not None and p_value < 0.05 and cvr_lift is not None and cvr_lift < 0:
        outcome = "negative"
    else:
        outcome = "neutral"

    action.measurement_end = _now()
    action.treatment_cvr = exposed_cvr
    action.control_cvr = holdout_cvr
    action.lift_pct = cvr_lift
    action.p_value = p_value
    action.visitors_measured = exposed_count + holdout_count
    action.outcome = outcome
    action.updated_at = _now()

    if outcome == "positive":
        action.status = "completed"
    else:
        action.status = "suppressed"
        from app.services.nudge_engine import deactivate_nudge
        deactivate_nudge(db, nudge.id, action.shop_domain)
        action.rollback_reason = f"Outcome: {outcome}. Lift: {cvr_lift}%, p={p_value}"

    db.commit()

    # Trust + SIP updates (only after healthy measurement, not bootstrap)
    if not getattr(action, "is_bootstrap", False):
        _update_trust(db, action.shop_domain, outcome)
        _update_sip_from_outcome(db, action)
        _check_contradiction(db, action)
    else:
        log.info("autonomous_loop: bootstrap experiment id=%d — outcome recorded but excluded from learning", action.id)

    if outcome == "negative":
        _maybe_add_cooldown(db, action)

    log.info("autonomous_loop: id=%d outcome=%s lift=%.1f%% p=%.4f n=%d shop=%s",
             action.id, outcome, cvr_lift or 0, p_value or 1,
             action.visitors_measured or 0, action.shop_domain)
    return True


# ══════════════════════════════════════════════════════════════════════════
# Phase 2 — New signal deployment
# ══════════════════════════════════════════════════════════════════════════

def _evaluate_new_signals(db: Session, shop_domain: str, sip: StoreIntelligenceProfile | None) -> int:
    autonomy = sip.autonomy_level if sip else 0
    if autonomy < 3:
        # Below level 3: no auto-deployment
        return 0

    today_count = _count_actions_today(db, shop_domain)
    if today_count >= _MAX_ACTIONS_PER_SHOP_PER_DAY:
        return 0

    eligible = _find_eligible_signals(db, shop_domain)
    if not eligible:
        return 0

    remaining = _MAX_ACTIONS_PER_SHOP_PER_DAY - today_count
    count = 0

    for signal in eligible[:remaining]:
        try:
            action = _decide_and_maybe_deploy(db, shop_domain, signal, sip)
            if action:
                count += 1
        except Exception as exc:
            log.warning("autonomous_loop: deploy error %s/%s: %s",
                        shop_domain, signal["product_url"], exc)
            db.rollback()

    return count


def _find_eligible_signals(db: Session, shop_domain: str) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT os.product_url, os.signal_type, os.signal_strength
            FROM opportunity_signals os
            WHERE os.shop_domain = :shop
              AND os.expires_at > NOW()
              AND os.signal_strength >= 0.5
              AND NOT EXISTS (
                  SELECT 1 FROM active_nudges an
                  WHERE an.shop_domain = os.shop_domain
                    AND an.product_url = os.product_url
                    AND an.status = 'active'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM autonomous_actions aa
                  WHERE aa.shop_domain = os.shop_domain
                    AND aa.product_url = os.product_url
                    AND aa.status IN ('proposed', 'deployed', 'measuring')
              )
            ORDER BY os.signal_strength DESC
            LIMIT 5
        """),
        {"shop": shop_domain},
    ).fetchall()

    return [{"product_url": r[0], "signal_type": r[1], "signal_strength": float(r[2])} for r in rows]


def _decide_and_maybe_deploy(
    db: Session, shop_domain: str, signal: dict, sip: StoreIntelligenceProfile | None,
) -> AutonomousAction | None:
    nudge_type, nudge_score, sel_reason = _select_nudge_type(signal["signal_type"], sip, db)
    if _is_cooled_down(sip, nudge_type):
        return None

    risk, risk_reason = _classify_risk(signal, sip, nudge_type, nudge_score, shop_domain, db)
    autonomy = sip.autonomy_level if sip else 0
    trust = sip.trust_score if sip else _TRUST_INITIAL

    # Autonomy gate: level 3 can auto-deploy low risk only, level 4+ can deploy medium
    can_deploy = (
        (risk == "low" and autonomy >= 3) or
        (risk == "medium" and autonomy >= 4)
    )

    action = AutonomousAction(
        shop_domain=shop_domain,
        signal_type=signal["signal_type"],
        product_url=signal["product_url"],
        action_type="nudge_deploy",
        nudge_type=nudge_type,
        risk_level=risk,
        decision_reason=f"{sel_reason}. Risk: {risk_reason}. Trust: {trust:.2f}. Autonomy: L{autonomy}",
        sip_confidence=sip.confidence_level if sip else "none",
        sip_nudge_score=nudge_score,
        status="proposed",
        holdout_pct=_DEFAULT_HOLDOUT_PCT,
    )
    db.add(action)
    db.flush()

    if can_deploy:
        nudge = _deploy_nudge(db, action, shop_domain, signal["product_url"], nudge_type)
        if nudge:
            action.status = "deployed"
            action.nudge_id = nudge.id
            action.deployed_at = _now()
            action.measurement_start = _now()
            log.info("autonomous_loop: DEPLOYED id=%d L%d risk=%s trust=%.2f %s shop=%s",
                     action.id, autonomy, risk, trust, nudge_type, shop_domain)

    db.commit()
    return action


# ══════════════════════════════════════════════════════════════════════════
# Autonomy Level System
# ══════════════════════════════════════════════════════════════════════════

def _recompute_autonomy_level(db: Session, sip: StoreIntelligenceProfile) -> None:
    """Recompute autonomy level from trust, confidence, and experiment history."""
    trust = sip.trust_score or 0
    confidence = sip.confidence_level or "none"
    positives = sip.total_positive_outcomes or 0
    mhealth = sip.measurement_health or "healthy"

    conf_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    conf_rank = conf_order.get(confidence, 0)

    # Measurement health blocks progression
    if mhealth == "broken":
        new_level = 0
    elif mhealth == "degraded":
        new_level = min(sip.autonomy_level or 0, 2)  # cap at level 2
    else:
        new_level = 0
        for level in sorted(_AUTONOMY_LEVELS.keys(), reverse=True):
            min_trust, min_conf, min_exp = _AUTONOMY_LEVELS[level]
            min_conf_rank = conf_order.get(min_conf, 0)
            if trust >= min_trust and conf_rank >= min_conf_rank and positives >= min_exp:
                new_level = level
                break

    # Never jump more than 1 level per cycle (stability)
    current = sip.autonomy_level or 0
    if new_level > current + 1:
        new_level = current + 1

    if new_level != current:
        sip.autonomy_level = new_level
        sip.updated_at = _now()
        db.commit()
        log.info("autonomous_loop: autonomy L%d → L%d (trust=%.2f conf=%s exp=%d) shop=%s",
                 current, new_level, trust, confidence, positives, sip.shop_domain)


# ══════════════════════════════════════════════════════════════════════════
# Trust Profile (multi-dimensional)
# ══════════════════════════════════════════════════════════════════════════

def _update_trust(db: Session, shop_domain: str, outcome: str) -> None:
    sip = _load_sip(db, shop_domain)
    if not sip:
        return

    old_trust = sip.trust_score or _TRUST_INITIAL
    profile = dict(sip.trust_profile or {
        "execution_reliability": 0.5,
        "measurement_integrity": 0.5,
        "outcome_quality": 0.5,
        "stability": 0.5,
    })

    # Update scalar trust
    deltas = {"positive": _TRUST_GAIN_POSITIVE, "neutral": _TRUST_GAIN_NEUTRAL,
              "rollback": -_TRUST_LOSS_ROLLBACK, "negative": -_TRUST_LOSS_NEGATIVE}
    delta = deltas.get(outcome, 0)
    new_trust = max(_TRUST_FLOOR, min(_TRUST_CEILING, old_trust + delta))
    sip.trust_score = round(new_trust, 4)

    # Update multi-dimensional profile
    total = (sip.total_autonomous_actions or 0) + 1
    positives = (sip.total_positive_outcomes or 0) + (1 if outcome == "positive" else 0)
    rollbacks = (sip.total_rollbacks or 0) + (1 if outcome in ("rollback", "negative") else 0)

    # Execution reliability: % not rolled back
    profile["execution_reliability"] = round(1.0 - (rollbacks / max(total, 1)), 4)

    # Outcome quality: % positive among completed
    profile["outcome_quality"] = round(positives / max(total, 1), 4)

    # Measurement integrity: stays high unless measurement health degrades
    mhealth = sip.measurement_health or "healthy"
    if mhealth == "healthy":
        profile["measurement_integrity"] = min(1.0, profile.get("measurement_integrity", 0.5) + 0.02)
    elif mhealth == "degraded":
        profile["measurement_integrity"] = max(0.0, profile.get("measurement_integrity", 0.5) - 0.1)
    else:
        profile["measurement_integrity"] = max(0.0, profile.get("measurement_integrity", 0.5) - 0.3)

    # Stability: low contradiction count + consistent outcomes = high
    contradictions = sip.contradiction_count or 0
    profile["stability"] = round(max(0.0, 1.0 - contradictions * 0.1), 4)

    # Overall: weighted average (measurement_integrity has highest weight)
    profile["overall"] = round(
        profile["execution_reliability"] * 0.2 +
        profile["measurement_integrity"] * 0.35 +
        profile["outcome_quality"] * 0.25 +
        profile["stability"] * 0.2,
        4,
    )

    # Counters
    if outcome == "positive":
        sip.total_positive_outcomes = positives
    if outcome in ("rollback", "negative"):
        sip.total_rollbacks = rollbacks
    sip.total_autonomous_actions = total
    sip.last_outcome_at = _now()
    sip.updated_at = _now()

    # Auto-pause at very low trust
    if new_trust < 0.1 and not sip.autonomous_paused:
        sip.autonomous_paused = True
        sip.pause_reason = f"Trust {new_trust:.2f} — auto-paused"
        log.warning("autonomous_loop: AUTO-PAUSED %s (trust=%.2f)", shop_domain, new_trust)

    # Persist trust_profile as JSONB
    db.execute(
        text("UPDATE store_intelligence_profiles SET trust_profile = :tp WHERE shop_domain = :shop"),
        {"tp": json.dumps(profile), "shop": shop_domain},
    )
    db.commit()


# ══════════════════════════════════════════════════════════════════════════
# Risk Classification
# ══════════════════════════════════════════════════════════════════════════

_DEFAULT_NUDGE_BY_SIGNAL = {
    "HIGH_TRAFFIC_NO_CART": "social_proof",
    "HIGH_ENGAGEMENT_NO_ACTION": "high_interest",
    "DEAD_TRAFFIC": "social_proof",
    "HIGH_RETURN_LOW_CONVERSION": "return_visitor",
    "LOW_CONVERSION_ATTENTION": "social_proof",
    "TRAFFIC_SPIKE": "high_interest",
    "SCROLL_HIGH_NO_CLICK": "engagement_depth",
    "RETURN_VISITOR_INTEREST": "return_visitor",
}


def _select_nudge_type(signal_type: str, sip: StoreIntelligenceProfile | None, db: Session | None = None) -> tuple[str, float | None, str]:
    """
    Nudge selection priority: SIP → CIG → default.
    CIG fills the gap when SIP has no learned data.
    """
    # Priority 1: SIP learned mapping
    if sip and sip.best_nudge_by_signal:
        learned = sip.best_nudge_by_signal.get(signal_type)
        if learned and sip.nudge_type_scores:
            score = sip.nudge_type_scores.get(learned, 0)
            if score > 0.3:
                return learned, score, f"SIP: {learned} ({score:.2f})"

    # Priority 1b: SIP best overall
    if sip and sip.nudge_type_scores:
        best = max(sip.nudge_type_scores.items(), key=lambda x: x[1], default=None)
        if best and best[1] > 0.5:
            return best[0], best[1], f"SIP best: {best[0]} ({best[1]:.2f})"

    # Priority 2: CIG recommendation (when SIP has no data)
    if db and sip:
        try:
            from app.services.cig_engine import get_cig_nudge_recommendation
            cig_nudge, cig_score, cig_reason = get_cig_nudge_recommendation(db, sip.shop_domain, signal_type)
            if cig_nudge and cig_score and cig_score > 0:
                return cig_nudge, cig_score, f"CIG: {cig_reason}"
        except Exception as exc:
            log.warning("autonomous_loop: CIG nudge recommendation failed: %s", exc)

    # Priority 3: Default mapping
    default = _DEFAULT_NUDGE_BY_SIGNAL.get(signal_type, "social_proof")
    return default, None, f"Default: {default}"


def _classify_risk(
    signal: dict, sip: StoreIntelligenceProfile | None, nudge_type: str,
    nudge_score: float | None, shop_domain: str, db: Session,
) -> tuple[str, str]:
    trust = sip.trust_score if sip else _TRUST_INITIAL
    confidence = sip.confidence_level if sip else "none"
    strength = signal.get("signal_strength", 0)

    if confidence in ("none", "low"):
        return "high", f"confidence={confidence}"

    prior_fail = db.execute(
        text("""SELECT COUNT(*) FROM autonomous_actions
                WHERE shop_domain = :s AND nudge_type = :n
                  AND outcome IN ('negative','rolled_back')
                  AND created_at > NOW() - INTERVAL '30 days'"""),
        {"s": shop_domain, "n": nudge_type},
    ).scalar() or 0
    if prior_fail > 0:
        return "high", f"Prior failures ({prior_fail}x/30d)"

    active = db.execute(
        text("SELECT COUNT(*) FROM active_nudges WHERE shop_domain=:s AND status='active'"),
        {"s": shop_domain},
    ).scalar() or 0
    if active >= _INTERACTION_NUDGE_CAP:
        return "high", f"{active} active nudges"

    if trust < 0.3:
        return "high", f"Trust {trust:.2f}"
    if trust < 0.6:
        return "medium", f"Trust {trust:.2f}"

    has_evidence = nudge_score is not None and nudge_score > 0.5
    if confidence in ("medium", "high") and (has_evidence or trust >= 0.8) and strength >= 0.6:
        return "low", f"Trust={trust:.2f} conf={confidence} score={nudge_score} str={strength:.2f}"

    return "medium", f"Trust={trust:.2f} conf={confidence}"


# ══════════════════════════════════════════════════════════════════════════
# Contradiction Detection
# ══════════════════════════════════════════════════════════════════════════

def _check_contradiction(db: Session, action: AutonomousAction) -> None:
    """Detect conflicting outcomes for the same signal+nudge combination."""
    if not action.signal_type or not action.nudge_type or not action.outcome:
        return

    opposite = "negative" if action.outcome == "positive" else "positive"
    count = db.execute(
        text("""
            SELECT COUNT(*) FROM autonomous_actions
            WHERE shop_domain = :shop AND signal_type = :sig AND nudge_type = :ntype
              AND outcome = :opp AND created_at > NOW() - INTERVAL '90 days'
        """),
        {"shop": action.shop_domain, "sig": action.signal_type,
         "ntype": action.nudge_type, "opp": opposite},
    ).scalar() or 0

    if count > 0:
        db.execute(
            text("""
                UPDATE store_intelligence_profiles
                SET contradiction_count = COALESCE(contradiction_count, 0) + 1,
                    updated_at = NOW()
                WHERE shop_domain = :shop
            """),
            {"shop": action.shop_domain},
        )
        db.commit()
        log.warning("autonomous_loop: CONTRADICTION %s+%s has both positive and negative outcomes (shop=%s)",
                    action.signal_type, action.nudge_type, action.shop_domain)


# ══════════════════════════════════════════════════════════════════════════
# SIP Learning (hardened)
# ══════════════════════════════════════════════════════════════════════════

def _update_sip_from_outcome(db: Session, action: AutonomousAction) -> None:
    if not action.nudge_type or not action.outcome:
        return
    # Bootstrap experiments are excluded from learning to prevent model contamination
    if getattr(action, "is_bootstrap", False):
        log.info("autonomous_loop: skipping SIP learn for bootstrap experiment id=%d", action.id)
        return
    if (action.visitors_measured or 0) < _MIN_VISITORS_FOR_LEARNING:
        return
    if action.p_value is not None and action.p_value >= _MIN_P_VALUE_FOR_LEARNING:
        return

    sip = _load_sip(db, action.shop_domain)
    if not sip:
        return
    if (sip.measurement_health or "healthy") != "healthy":
        log.info("autonomous_loop: skipping SIP learn — measurement %s", sip.measurement_health)
        return

    scores = dict(sip.nudge_type_scores or {})
    old_score = scores.get(action.nudge_type, 0.5)

    # Outcome score
    if action.outcome == "positive":
        outcome_score = min(1.0, 0.7 + (action.lift_pct or 0) / 200)
    elif action.outcome == "negative":
        outcome_score = max(0.0, 0.3 - abs(action.lift_pct or 0) / 200)
    else:
        outcome_score = 0.4

    # Temporal decay: outcomes from weeks ago carry less weight
    age_days = max(0, (_now() - (action.measurement_end or _now())).total_seconds() / 86400)
    decay = math.exp(-0.693 * age_days / _DECAY_HALFLIFE_DAYS)  # half-life decay

    # Weight: sample × confidence × decay
    n_weight = min(1.0, (action.visitors_measured or 200) / 2000)
    p_weight = 1.0 if (action.p_value or 1) < _MIN_P_VALUE_FOR_STRONG_LEARNING else 0.5
    weight = min(_MAX_LEARNING_WEIGHT, n_weight * p_weight * decay)

    new_score = old_score * (1 - weight) + outcome_score * weight
    scores[action.nudge_type] = round(new_score, 4)

    # Update best mapping
    best_map = dict(sip.best_nudge_by_signal or {})
    if action.outcome == "positive" and action.signal_type:
        current_best = best_map.get(action.signal_type)
        current_score = scores.get(current_best, 0) if current_best else 0
        if scores.get(action.nudge_type, 0) > current_score:
            best_map[action.signal_type] = action.nudge_type

    db.execute(
        text("""UPDATE store_intelligence_profiles
                SET nudge_type_scores = :s, best_nudge_by_signal = :b, updated_at = NOW()
                WHERE shop_domain = :shop"""),
        {"s": json.dumps(scores), "b": json.dumps(best_map), "shop": action.shop_domain},
    )
    db.commit()


# ══════════════════════════════════════════════════════════════════════════
# Cooldowns
# ══════════════════════════════════════════════════════════════════════════

def _is_cooled_down(sip: StoreIntelligenceProfile | None, nudge_type: str) -> bool:
    if not sip or not sip.nudge_type_cooldowns:
        return False
    until = sip.nudge_type_cooldowns.get(nudge_type)
    if not until:
        return False
    try:
        return _now() < datetime.fromisoformat(until)
    except (ValueError, TypeError):
        return False


def _maybe_add_cooldown(db: Session, action: AutonomousAction) -> None:
    if not action.nudge_type:
        return
    failures = db.execute(
        text("""SELECT COUNT(*) FROM autonomous_actions
                WHERE shop_domain=:s AND nudge_type=:n
                  AND outcome IN ('negative','rolled_back')
                  AND created_at > NOW() - INTERVAL '60 days'"""),
        {"s": action.shop_domain, "n": action.nudge_type},
    ).scalar() or 0
    if failures < _MAX_FAILURES_BEFORE_COOLDOWN:
        return
    sip = _load_sip(db, action.shop_domain)
    if not sip:
        return
    cooldowns = dict(sip.nudge_type_cooldowns or {})
    days = _COOLDOWN_DAYS_ROLLBACK if action.status == "rolled_back" else _COOLDOWN_DAYS_NEGATIVE
    cooldowns[action.nudge_type] = (_now() + timedelta(days=days)).isoformat()
    db.execute(
        text("UPDATE store_intelligence_profiles SET nudge_type_cooldowns=:c, updated_at=NOW() WHERE shop_domain=:s"),
        {"c": json.dumps(cooldowns), "s": action.shop_domain},
    )
    db.commit()
    log.warning("autonomous_loop: COOLDOWN %s for %dd (shop=%s)", action.nudge_type, days, action.shop_domain)


# ══════════════════════════════════════════════════════════════════════════
# Global Fail-Safe
# ══════════════════════════════════════════════════════════════════════════

def _is_global_anomaly(db: Session) -> bool:
    count = db.execute(
        text("""SELECT COUNT(DISTINCT shop_domain) FROM autonomous_actions
                WHERE status = 'rolled_back' AND updated_at > CURRENT_DATE"""),
    ).scalar() or 0
    return count >= _GLOBAL_ROLLBACK_THRESHOLD


# ══════════════════════════════════════════════════════════════════════════
# Deployment + Rollback
# ══════════════════════════════════════════════════════════════════════════

def _deploy_nudge(db, action, shop, product, nudge_type):
    from app.services.nudge_engine import create_or_refresh_nudge
    try:
        nudge, _ = create_or_refresh_nudge(
            db=db, shop_domain=shop, product_url=product,
            action_type="SCARCITY_NUDGE", trigger_source=f"auto:{action.id}",
            visitor_count=None, revenue_window=None,
            calibration_state=f"auto:{nudge_type}", holdout_pct=_DEFAULT_HOLDOUT_PCT,
        )
        return nudge
    except Exception as exc:
        log.warning("autonomous_loop: deploy failed id=%d: %s", action.id, exc)
        return None


def _execute_rollback(db, action, nudge, reason):
    from app.services.nudge_engine import deactivate_nudge
    deactivate_nudge(db, nudge.id, action.shop_domain)
    action.status = "rolled_back"
    action.rollback_reason = reason
    action.measurement_end = _now()
    action.updated_at = _now()
    db.commit()
    _update_trust(db, action.shop_domain, "rollback")
    log.info("autonomous_loop: ROLLBACK id=%d — %s", action.id, reason)


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _load_sip(db, shop):
    return db.query(StoreIntelligenceProfile).filter(StoreIntelligenceProfile.shop_domain == shop).first()

def _get_rank_data(db, shop, nudge):
    try:
        from app.services.nudge_rank import compute_nudge_rank
        r = compute_nudge_rank(db, shop, [nudge])
        return r[0] if r else None
    except Exception as exc:
        log.warning("autonomous_loop: nudge rank computation failed: %s", exc)
        return None

def _count_actions_today(db, shop):
    return db.execute(
        text("SELECT COUNT(*) FROM autonomous_actions WHERE shop_domain=:s AND created_at>CURRENT_DATE AND action_type='nudge_deploy'"),
        {"s": shop},
    ).scalar() or 0

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)
