"""
lesson_gc.py — Lesson lifecycle management: GC, confidence decay, contradiction detection.

Runs daily from agent_worker. Maintains lesson quality by:
  1. Decaying confidence on unreinforced lessons (0.05/week)
  2. Retiring stale lessons (confidence < 0.2 and age > 30d)
  3. Detecting contradictions (same domain, opposite lesson types)
  4. Promoting high-evidence lessons (evidence_count >= 5, confidence >= 0.9)

Public interface:
    run_lesson_gc(db) -> dict
    should_run_gc() -> bool
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

log = logging.getLogger("lesson_gc")

# Cooldown: run at most once per 24 hours
_GC_COOLDOWN_SECONDS = 24 * 3600
_last_gc_run: float | None = None

# Confidence decay: 0.05 per week for unreinforced lessons
_DECAY_PER_WEEK = 0.05

# Retirement threshold: below this confidence AND older than 30d → retire
_RETIRE_CONFIDENCE = 0.2
_RETIRE_MIN_AGE_DAYS = 30

# Promotion threshold: above this confidence AND evidence count → promote
_PROMOTE_CONFIDENCE = 0.9
_PROMOTE_MIN_EVIDENCE = 5

# Auto-confirm pending promotions after this many days without human rejection
_AUTO_CONFIRM_DAYS = 7


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def should_run_gc() -> bool:
    global _last_gc_run
    if _last_gc_run is None:
        return True
    return (time.monotonic() - _last_gc_run) >= _GC_COOLDOWN_SECONDS


def mark_gc_run():
    global _last_gc_run
    _last_gc_run = time.monotonic()


def run_lesson_gc(db: Session) -> dict:
    """
    Run lesson garbage collection cycle.

    Returns summary dict with counts.
    """
    from app.models.system_lesson import SystemLesson

    summary = {"decayed": 0, "retired": 0, "contradictions": 0, "promoted": 0}
    now = _now()

    # -----------------------------------------------------------------------
    # Phase 1: Confidence decay for unreinforced lessons
    # -----------------------------------------------------------------------
    # Lessons not reinforced in the last 7 days get confidence decayed
    decay_cutoff = now - timedelta(days=7)

    active_lessons = (
        db.query(SystemLesson)
        .filter(
            SystemLesson.status == "active",
        )
        .all()
    )

    for lesson in active_lessons:
        # Calculate weeks since last reinforcement (or creation)
        last_activity = lesson.last_reinforced_at or lesson.created_at
        if last_activity < decay_cutoff:
            weeks_stale = (now - last_activity).days / 7
            decay = min(_DECAY_PER_WEEK * weeks_stale, 0.3)  # cap decay per GC cycle
            old_conf = lesson.confidence
            lesson.confidence = max(0.0, lesson.confidence - decay)
            if lesson.confidence < old_conf:
                summary["decayed"] += 1

    # -----------------------------------------------------------------------
    # Phase 2: Retire stale lessons
    # -----------------------------------------------------------------------
    retire_cutoff = now - timedelta(days=_RETIRE_MIN_AGE_DAYS)

    stale = (
        db.query(SystemLesson)
        .filter(
            SystemLesson.status == "active",
            SystemLesson.confidence < _RETIRE_CONFIDENCE,
            SystemLesson.created_at < retire_cutoff,
        )
        .all()
    )

    for lesson in stale:
        lesson.status = "retired"
        summary["retired"] += 1
        log.info("lesson_gc: retired lesson #%d domain=%s (confidence=%.2f)", lesson.id, lesson.domain, lesson.confidence)

    # -----------------------------------------------------------------------
    # Phase 3: Contradiction detection
    # -----------------------------------------------------------------------
    # Find domains with both effective_pattern AND ineffective_pattern active lessons
    domain_types = (
        db.query(SystemLesson.domain, SystemLesson.lesson_type, func.count(SystemLesson.id))
        .filter(
            SystemLesson.status == "active",
            SystemLesson.confidence >= 0.3,
            SystemLesson.lesson_type.in_(["effective_pattern", "ineffective_pattern"]),
        )
        .group_by(SystemLesson.domain, SystemLesson.lesson_type)
        .all()
    )

    # Build domain → {type: count} map
    domain_map: dict[str, dict[str, int]] = {}
    for domain, ltype, count in domain_types:
        if domain not in domain_map:
            domain_map[domain] = {}
        domain_map[domain][ltype] = count

    for domain, types in domain_map.items():
        effective_count = types.get("effective_pattern", 0)
        ineffective_count = types.get("ineffective_pattern", 0)

        if effective_count > 0 and ineffective_count > 0:
            summary["contradictions"] += 1
            log.warning(
                "lesson_gc: CONTRADICTION in domain=%s — %d effective vs %d ineffective lessons",
                domain, effective_count, ineffective_count,
            )
            # Downgrade confidence of the weaker side
            if effective_count < ineffective_count:
                # More failures than successes → downgrade effective lessons
                _downgrade_weaker_lessons(db, domain, "effective_pattern")
            elif ineffective_count < effective_count:
                _downgrade_weaker_lessons(db, domain, "ineffective_pattern")
            # If equal, downgrade both (domain is truly ambiguous)
            else:
                _downgrade_weaker_lessons(db, domain, "effective_pattern")
                _downgrade_weaker_lessons(db, domain, "ineffective_pattern")

    # -----------------------------------------------------------------------
    # Phase 4: Promotion — two-stage with human validation
    # -----------------------------------------------------------------------
    # Get adaptive promotion threshold (bounded, evidence-aware)
    try:
        from app.services.adaptive_governance import get_adaptive_thresholds
        promote_conf = get_adaptive_thresholds(db).promote_confidence
    except Exception as exc:
        log.warning("lesson_gc: run_lesson_gc failed: %s", exc)
        promote_conf = _PROMOTE_CONFIDENCE

    # Stage 4a: New promotions → pending_promotion (not yet hard-blocking)
    # ISOLATION GATE: Only real_merchant lessons may be promoted to
    # regression_warning. Pre-merchant/test/sandbox lessons must never
    # become permanent product dogma.
    promotable = (
        db.query(SystemLesson)
        .filter(
            SystemLesson.status == "active",
            SystemLesson.confidence >= promote_conf,
            SystemLesson.evidence_count >= _PROMOTE_MIN_EVIDENCE,
            SystemLesson.lesson_type == "ineffective_pattern",
            SystemLesson.promotion_status.is_(None),  # not already promoted/pending
            SystemLesson.evidence_source == "real_merchant",
        )
        .all()
    )

    for lesson in promotable:
        lesson.promotion_status = "pending_promotion"
        summary["promoted"] += 1
        log.info(
            "lesson_gc: PENDING PROMOTION lesson #%d domain=%s "
            "(confidence=%.2f, evidence=%d) — awaiting human review",
            lesson.id, lesson.domain, lesson.confidence, lesson.evidence_count,
        )
        # Create ops_alert for operator visibility
        try:
            from app.services.alerting import write_alert
            # heal-detection: GC operation event log — fires once per pass
            write_alert(
                db, severity="info", source="lesson_gc",
                alert_type="lesson_pending_promotion",
                summary=(
                    f"Lesson #{lesson.id} in domain '{lesson.domain}' eligible for promotion "
                    f"to regression_warning (conf={lesson.confidence:.2f}, evidence={lesson.evidence_count}). "
                    f"Review via /ops/lessons/{lesson.id}/promote or /ops/lessons/{lesson.id}/reject"
                ),
                detail={
                    "lesson_id": lesson.id,
                    "domain": lesson.domain,
                    "summary": lesson.summary[:200],
                    "confidence": lesson.confidence,
                    "evidence_count": lesson.evidence_count,
                },
            )
        except Exception as exc:
            log.warning("lesson_gc: run_lesson_gc failed: %s", exc)

    # Stage 4b: Auto-confirm pending promotions after 7 days without human rejection
    auto_confirm_cutoff = now - timedelta(days=_AUTO_CONFIRM_DAYS)
    pending = (
        db.query(SystemLesson)
        .filter(
            SystemLesson.promotion_status == "pending_promotion",
            SystemLesson.created_at <= auto_confirm_cutoff,
            # Must still meet quality thresholds (confidence could have decayed)
            SystemLesson.confidence >= _PROMOTE_CONFIDENCE * 0.8,
            SystemLesson.evidence_count >= _PROMOTE_MIN_EVIDENCE,
        )
        .all()
    )

    for lesson in pending:
        lesson.lesson_type = "regression_warning"
        lesson.promotion_status = "promoted"
        lesson.promoted_at = now
        lesson.promotion_decided_by = "auto_confirm"
        summary["auto_confirmed"] = summary.get("auto_confirmed", 0) + 1
        log.info(
            "lesson_gc: AUTO-CONFIRMED promotion lesson #%d domain=%s → regression_warning "
            "(7d without rejection)",
            lesson.id, lesson.domain,
        )

    if any(v > 0 for v in summary.values()):
        db.flush()
        log.info(
            "lesson_gc: decayed=%d retired=%d contradictions=%d promoted=%d auto_confirmed=%d",
            summary["decayed"], summary["retired"], summary["contradictions"],
            summary["promoted"], summary.get("auto_confirmed", 0),
        )

    return summary


def _downgrade_weaker_lessons(db: Session, domain: str, lesson_type: str) -> None:
    """Downgrade confidence of the weaker lesson type in a contradicted domain."""
    from app.models.system_lesson import SystemLesson

    lessons = (
        db.query(SystemLesson)
        .filter(
            SystemLesson.domain == domain,
            SystemLesson.lesson_type == lesson_type,
            SystemLesson.status == "active",
        )
        .all()
    )

    for l in lessons:
        l.confidence = max(0.0, l.confidence - 0.15)
