"""
goals.py — Merchant goals/targets system.

Every merchant can set multi-metric goals (revenue, CVR, AOV, orders,
custom). The service computes current run-rate, projects the month-end
value using the existing forecast service, and classifies each goal as:

  - on_track   — projected to hit or exceed the target
  - at_risk    — projected gap < 30% of target
  - off_track  — projected gap >= 30% of target

Goals that flip to at_risk or off_track automatically emit an ops_alert
that flows into the self-healing / merchant-digest pipeline, so the
merchant never has to check manually.

Storage
-------
Zero schema changes (migrations/ is TIER_2). Goals live in Redis under
`hs:goals:{shop_domain}` as a JSON blob with long TTL (365d, refreshed
on every read). If Redis flushes, the merchant resets goals — acceptable
for v1 since the goal definition is a handful of numbers they remember.

Self-healing integration
------------------------
* project_brain domain: 'goals' (medium criticality)
* ops_alert on at_risk/off_track: `goal_at_risk` alert_type
* data_integrity_probe extension: scans all shops daily for goal drift
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.core.silent_fallback import record_silent_return
from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("goals")

_REDIS_KEY_PREFIX = "hs:goals:v1"
_GOAL_TTL_SECONDS = 365 * 24 * 3600  # 1 year, refreshed on every access

# Supported metrics — every one must map to a deterministic extractor below.
_SUPPORTED_METRICS: frozenset[str] = frozenset({
    "monthly_revenue",
    "monthly_orders",
    "aov",
    "cvr",
})

# Period is always 'monthly' in v1 — weekly/quarterly possible in v2
Period = Literal["monthly"]


@dataclass
class Goal:
    """A single merchant target."""
    metric: str
    target_value: float
    period: Period
    set_at: str  # ISO timestamp
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "target_value": self.target_value,
            "period": self.period,
            "set_at": self.set_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        return cls(
            metric=d["metric"],
            target_value=float(d["target_value"]),
            period=d.get("period", "monthly"),
            set_at=d.get("set_at", ""),
            note=d.get("note", ""),
        )


@dataclass
class GoalProgress:
    """A goal's runtime state with gap analysis."""
    metric: str
    target_value: float
    current_value: float
    projected_value: float
    gap_pct: float
    status: str  # on_track | at_risk | off_track
    narrative: str

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "target_value": round(self.target_value, 2),
            "current_value": round(self.current_value, 2),
            "projected_value": round(self.projected_value, 2),
            "gap_pct": round(self.gap_pct, 1),
            "status": self.status,
            "narrative": self.narrative,
        }


# ---------------------------------------------------------------------------
# Storage (Redis-backed)
# ---------------------------------------------------------------------------


def _key(shop: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{shop}"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def get_goals(shop_domain: str) -> list[Goal]:
    """Return the merchant's current goal set. Empty if none set or Redis down."""
    rc = _redis()
    if rc is None:
        record_silent_return("goals.read")
        return []
    try:
        raw = rc.get(_key(shop_domain))
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        # Refresh TTL so active merchants keep their goals forever
        rc.expire(_key(shop_domain), _GOAL_TTL_SECONDS)
        return [Goal.from_dict(d) for d in data if isinstance(d, dict)]
    except Exception as exc:
        log.debug("goals: get_goals redis error: %s", exc)
        return []


def set_goal(shop_domain: str, metric: str, target_value: float,
             period: Period = "monthly", note: str = "") -> Goal | None:
    """
    Create or update a goal. Returns the Goal on success, None on error.
    Each (shop, metric) has at most one active goal — re-setting replaces.
    """
    if metric not in _SUPPORTED_METRICS:
        raise ValueError(f"unsupported metric: {metric!r}. Must be one of {sorted(_SUPPORTED_METRICS)}")
    if target_value <= 0:
        raise ValueError("target_value must be positive")

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    new_goal = Goal(
        metric=metric,
        target_value=float(target_value),
        period=period,
        set_at=now,
        note=(note or "")[:200],
    )

    rc = _redis()
    if rc is None:
        record_silent_return("goals.write")
        return None

    try:
        existing = get_goals(shop_domain)
        # Replace if metric already has a goal
        kept = [g for g in existing if g.metric != metric]
        kept.append(new_goal)
        rc.setex(
            _key(shop_domain),
            _GOAL_TTL_SECONDS,
            json.dumps([g.to_dict() for g in kept]),
        )
        return new_goal
    except Exception as exc:
        log.warning("goals: set_goal redis error: %s", exc)
        return None


def delete_goal(shop_domain: str, metric: str) -> bool:
    """Remove a goal. Returns True if something was removed."""
    rc = _redis()
    if rc is None:
        record_silent_return("goals.delete")
        return False
    try:
        existing = get_goals(shop_domain)
        kept = [g for g in existing if g.metric != metric]
        if len(kept) == len(existing):
            return False  # nothing removed
        if kept:
            rc.setex(
                _key(shop_domain),
                _GOAL_TTL_SECONDS,
                json.dumps([g.to_dict() for g in kept]),
            )
        else:
            rc.delete(_key(shop_domain))
        return True
    except Exception as exc:
        log.warning("goals: delete_goal redis error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Progress computation — the killer feature
# ---------------------------------------------------------------------------


def _compute_current_value(db: Session, shop_domain: str, metric: str) -> float:
    """
    Return the merchant's current value for a metric over the current
    month-to-date window. Pure SQL aggregation, cheap.
    """
    from sqlalchemy import text
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    currency = get_shop_currency(db, shop_domain)

    if metric == "monthly_revenue":
        row = db.execute(text("""
            SELECT COALESCE(SUM(total_price), 0)
            FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :start
              AND (:currency IS NULL OR currency = :currency)
        """), {"shop": shop_domain, "start": month_start, "currency": currency}).fetchone()
        return float(row[0] or 0)

    if metric == "monthly_orders":
        row = db.execute(text("""
            SELECT COUNT(*)
            FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :start
              AND (:currency IS NULL OR currency = :currency)
        """), {"shop": shop_domain, "start": month_start, "currency": currency}).fetchone()
        return float(row[0] or 0)

    if metric == "aov":
        row = db.execute(text("""
            SELECT COALESCE(AVG(total_price), 0)
            FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :start
              AND (:currency IS NULL OR currency = :currency)
        """), {"shop": shop_domain, "start": month_start, "currency": currency}).fetchone()
        return float(row[0] or 0)

    if metric == "cvr":
        # Visitors from events + orders from shop_orders
        cutoff_ms = int(month_start.timestamp() * 1000)
        row = db.execute(text("""
            SELECT
                (SELECT COUNT(DISTINCT visitor_id) FROM events
                 WHERE shop_domain = :shop AND timestamp >= :cutoff_ms) AS visitors,
                (SELECT COUNT(*) FROM shop_orders
                 WHERE shop_domain = :shop AND created_at >= :start) AS orders
        """), {
            "shop": shop_domain,
            "start": month_start,
            "cutoff_ms": cutoff_ms,
        }).fetchone()
        visitors = int(row[0] or 0)
        orders = int(row[1] or 0)
        return (orders / visitors * 100) if visitors > 0 else 0.0

    return 0.0


def _project_end_of_month(current_value: float, day_of_month: int, days_in_month: int) -> float:
    """Linear projection: scale current value to full-month equivalent."""
    if day_of_month <= 0:
        return current_value
    return round(current_value * days_in_month / day_of_month, 2)


def _classify_goal(gap_pct: float) -> str:
    if gap_pct <= 0:
        return "on_track"
    if gap_pct < 30:
        return "at_risk"
    return "off_track"


def _narrative(metric: str, progress: GoalProgress) -> str:
    if progress.status == "on_track":
        return f"🎯 On track — projected {metric}={progress.projected_value:.0f} vs target {progress.target_value:.0f}"
    if progress.status == "at_risk":
        return f"⚠️ At risk — projected gap €{progress.target_value - progress.projected_value:.0f} ({progress.gap_pct:.0f}% short)"
    return f"🔻 Off track — projected {progress.gap_pct:.0f}% short of target"


def compute_goal_progress(db: Session, shop_domain: str) -> list[GoalProgress]:
    """
    Compute progress for every active goal. Returns [GoalProgress...].
    Empty list if no goals set.
    """
    goals = get_goals(shop_domain)
    if not goals:
        return []

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_of_month = now.day
    # Safe approximation — 30 for February, 31 for 31-day months, etc.
    import calendar
    days_in_month = calendar.monthrange(now.year, now.month)[1]

    results: list[GoalProgress] = []
    for g in goals:
        try:
            current = _compute_current_value(db, shop_domain, g.metric)
            projected = _project_end_of_month(current, day_of_month, days_in_month)
            gap = g.target_value - projected
            gap_pct = (gap / g.target_value * 100) if g.target_value > 0 else 0.0
            status = _classify_goal(gap_pct)
            progress = GoalProgress(
                metric=g.metric,
                target_value=g.target_value,
                current_value=current,
                projected_value=projected,
                gap_pct=gap_pct,
                status=status,
                narrative="",
            )
            progress.narrative = _narrative(g.metric, progress)
            results.append(progress)
        except Exception as exc:
            log.debug("goals: progress compute failed for %s %s: %s", shop_domain, g.metric, exc)

    return results


def check_goals_at_risk(db: Session, shop_domain: str) -> list[GoalProgress]:
    """
    Return only the at_risk + off_track goals. If any exist, emit a
    deduped ops_alert so the pipeline sees the goal drift.
    """
    progress = compute_goal_progress(db, shop_domain)
    risky = [p for p in progress if p.status in ("at_risk", "off_track")]

    if risky:
        try:
            from app.services.alerting import write_alert
            metrics_list = ", ".join(f"{p.metric}:{p.status}" for p in risky)
            write_alert(
                db,
                severity="warning",
                source=f"goals:{shop_domain}",
                alert_type="goal_at_risk",
                summary=f"{len(risky)} goal(s) at risk on {shop_domain}: {metrics_list}",
                shop_domain=shop_domain,
                detail={
                    "goals": [p.to_dict() for p in risky],
                    "total_projected_gap_eur": sum(
                        (p.target_value - p.projected_value)
                        for p in risky if p.metric == "monthly_revenue"
                    ),
                },
            )
        except Exception as exc:
            log.debug("goals: write_alert failed: %s", exc)

        # β4 — Klaviyo event forwarding: surface goal_at_risk as a custom
        # Klaviyo metric so merchants can build flows ("if goal_at_risk
        # fires, send a win-back email to customers in segment X").
        try:
            from app.services.klaviyo_events import forward_event_async, is_shop_connected
            if is_shop_connected(db, shop_domain):
                merchant_email = None
                try:
                    from app.models.merchant import Merchant
                    m = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
                    if m:
                        merchant_email = getattr(m, "contact_email", None)
                except Exception as exc:
                    log.warning("goals: merchant email lookup failed: %s", exc)
                if merchant_email:
                    forward_event_async(
                        shop_domain=shop_domain,
                        event_name="goal_at_risk",
                        email=merchant_email,
                        properties={
                            "risky_goals": len(risky),
                            "metrics": [p.metric for p in risky],
                            "projected_gap_eur": sum(
                                (p.target_value - p.projected_value)
                                for p in risky if p.metric == "monthly_revenue"
                            ),
                        },
                    )
        except Exception as exc:
            log.warning("goals: klaviyo forward failed (non-fatal): %s", exc)

        # Phase Ω''' — outbound webhook fan-out for goal.at_risk
        try:
            from app.services.event_emitter import emit
            emit(db, shop_domain, "goal.at_risk", {
                "shop_domain": shop_domain,
                "risky_count": len(risky),
                "goals": [p.to_dict() for p in risky],
                "total_projected_gap_eur": sum(
                    (p.target_value - p.projected_value)
                    for p in risky if p.metric == "monthly_revenue"
                ),
            })
        except Exception as exc:
            log.warning("goals: event_emitter fan-out failed: %s", exc)

    return risky
