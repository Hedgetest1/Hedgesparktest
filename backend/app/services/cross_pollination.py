# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
cross_pollination.py — D2: preventive candidates inherited from proven fixes.

Once a BugFixCandidate has graduated to `proven_effective` via the B1
holdout measurement service, we know its diff has statistically significant
lift + a p-value below the threshold. At that point the same strategy is a
free win for every other shop exhibiting the same precondition but not yet
remediated. D2 scans `ops_alerts` for matching unresolved alerts on other
shops and creates preventive BugFixCandidate rows that inherit the proven
fix's diff/files/test_command — no LLM spend, applied straight through the
existing approval/governance gates.

Design constraints (from the north-star roadmap):
  * Shops' DATA never leaves the shop. We share the FIX, not the data.
  * The inherited candidate is a new row, not a mutation of the original.
  * Precondition = same `alert_type` on an `OpsAlert` row, `resolved=false`,
    `shop_domain` distinct from the shops already covered by the original
    fix's cohort.
  * Hard cap per proven fix (`_MAX_POLLINATIONS_PER_FIX`) so one
    fleet-wide fix can't open hundreds of rows in one tick.
  * Idempotent: a second call with the same proven_fix_id creates
    nothing new (no duplicates).
  * Zero LLM, pure deterministic SQL + metadata copy.

Public API
----------
    cross_pollinate_from_proven_fix(db, candidate_id) -> dict
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

log = logging.getLogger("cross_pollination")

_MAX_POLLINATIONS_PER_FIX = 20
_ALERT_LOOKBACK_HOURS = 72


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_proven_verdict(candidate_id: int) -> dict | None:
    """Return the holdout measurement dict for a candidate if — and
    ONLY if — it is marked 'proven_effective'. Otherwise None."""
    try:
        from app.services.fix_holdout_measurement import get_measurement
        m = get_measurement(candidate_id)
    except Exception as exc:
        log.debug("cross_pollination: measurement lookup failed: %s", exc)
        return None
    if not isinstance(m, dict):
        return None
    if m.get("status") != "proven_effective":
        return None
    return m


def _infer_alert_type(db: Session, candidate) -> str | None:
    """Return the alert_type that originally produced this candidate.

    Two paths:
      1. If source_type == 'ops_alert' and source_ref looks like an id,
         join OpsAlert by id and read alert_type directly.
      2. Otherwise fall back to source_ref prefix (matching the fleet-wide
         normalization used in B2). None if we can't identify a stable
         alert family.
    """
    from app.models.ops_alert import OpsAlert

    source_ref = getattr(candidate, "source_ref", None) or ""
    source_type = getattr(candidate, "source_type", None) or ""

    if source_type == "ops_alert" and source_ref.isdigit():
        try:
            row = db.get(OpsAlert, int(source_ref))
            if row and row.alert_type:
                return row.alert_type
        except Exception as exc:
            log.warning("cross_pollination: id lookup failed: %s", exc)

    # Fallback: source_ref often encodes the alert_type as a prefix like
    # "webhook_drift:shop_a" — pull the left side if it matches a known
    # alert_type column value.
    if ":" in source_ref:
        candidate_alert_type = source_ref.split(":", 1)[0]
        try:
            exists = (
                db.query(OpsAlert.alert_type)
                .filter(OpsAlert.alert_type == candidate_alert_type)
                .limit(1)
                .first()
            )
            if exists:
                return candidate_alert_type
        except Exception as exc:
            log.warning("cross_pollination: _infer_alert_type failed: %s", exc)

    return None


def _already_pollinated_shops(
    db: Session, proven_candidate_id: int,
) -> set[str]:
    """Return the set of shops that already have a cross-pollinated
    candidate inherited from this proven fix. Used to make the
    pollination step idempotent across multiple invocations."""
    from app.models.bugfix_candidate import BugFixCandidate

    covered: set[str] = set()
    needle = f'"inherited_from": {proven_candidate_id}'
    try:
        rows = (
            db.query(BugFixCandidate.context_json)
            .filter(
                BugFixCandidate.source_type == "cross_pollinated",
                BugFixCandidate.context_json.isnot(None),
                BugFixCandidate.context_json.like(f"%{needle}%"),
            )
            .all()
        )
    except Exception as exc:
        log.warning("cross_pollination: covered shops query failed: %s", exc)
        return covered

    for (ctx_json,) in rows:
        try:
            ctx = json.loads(ctx_json or "{}")
        except Exception as exc:
            log.warning("cross_pollination: _already_pollinated_shops failed: %s", exc)
            continue
        if isinstance(ctx, dict) and ctx.get("inherited_from") == proven_candidate_id:
            shop = ctx.get("target_shop")
            if isinstance(shop, str) and shop:
                covered.add(shop)
    return covered


def _find_matching_alerts(
    db: Session, alert_type: str, exclude_shops: set[str],
) -> list[Any]:
    """Unresolved ops_alerts with the given alert_type on shops NOT yet
    covered by the proven fix's pollination set. Scoped to the last
    _ALERT_LOOKBACK_HOURS so we don't pollinate ancient stale alerts."""
    from app.models.ops_alert import OpsAlert

    cutoff = _now() - timedelta(hours=_ALERT_LOOKBACK_HOURS)
    try:
        q = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == alert_type,
                OpsAlert.resolved == False,  # noqa: E712
                OpsAlert.shop_domain.isnot(None),
                OpsAlert.created_at >= cutoff,
            )
            .order_by(OpsAlert.created_at.desc())
            .limit(_MAX_POLLINATIONS_PER_FIX * 3)
        )
        rows = q.all()
    except Exception as exc:
        log.warning("cross_pollination: alert scan failed: %s", exc)
        return []

    filtered: list[Any] = []
    seen_shops: set[str] = set()
    for r in rows:
        shop = r.shop_domain
        if not shop or shop in exclude_shops or shop in seen_shops:
            continue
        seen_shops.add(shop)
        filtered.append(r)
        if len(filtered) >= _MAX_POLLINATIONS_PER_FIX:
            break
    return filtered


def _build_inherited_candidate(
    proven,
    alert,
    *,
    alert_type: str,
    measurement: dict,
):
    """Construct (without adding) a new BugFixCandidate that inherits
    the proven fix's diff/files/test_command and is scoped to this shop."""
    from app.models.bugfix_candidate import BugFixCandidate

    inherited_ctx = {
        "inherited_from": proven.id,
        "target_shop": alert.shop_domain,
        "source_alert_id": alert.id,
        "source_alert_type": alert_type,
        "inherited_at": _now().isoformat(),
        "measurement_summary": {
            "lift_eur": measurement.get("lift_eur"),
            "p_value": measurement.get("p_value"),
            "n_treatment": measurement.get("n_treatment"),
            "n_control": measurement.get("n_control"),
        },
    }

    return BugFixCandidate(
        source_type="cross_pollinated",
        source_ref=f"inherited:{proven.id}:{alert.id}",
        title=f"Preventive: {proven.title}",
        summary=(
            f"Pre-empt {alert_type} on {alert.shop_domain} using proven fix from "
            f"candidate #{proven.id} "
            f"(lift {measurement.get('lift_eur')}, p={measurement.get('p_value')})"
        ),
        status="patch_proposed",
        affected_domain=proven.affected_domain,
        patch_summary=proven.patch_summary,
        patch_diff=proven.patch_diff,
        patch_files=proven.patch_files,
        test_command=proven.test_command,
        patch_risk_tier=proven.patch_risk_tier,
        fix_confidence=proven.fix_confidence,
        remediation_class=proven.remediation_class,
        context_json=json.dumps(inherited_ctx, default=str),
    )


def cross_pollinate_from_proven_fix(
    db: Session, candidate_id: int,
) -> dict[str, Any]:
    """Entry point. Given a candidate that MAY be proven_effective, scan
    for matching alerts on other shops and create preventive candidates.

    Returns a report dict: {status, created, skipped_reason?}.
    """
    from app.models.bugfix_candidate import BugFixCandidate

    report: dict[str, Any] = {
        "status": "noop",
        "created": 0,
        "proven_fix_id": candidate_id,
    }

    proven = db.get(BugFixCandidate, candidate_id)
    if not proven:
        report["skipped_reason"] = "candidate_not_found"
        return report
    if not proven.patch_diff:
        report["skipped_reason"] = "no_diff_to_inherit"
        return report

    measurement = _load_proven_verdict(candidate_id)
    if measurement is None:
        report["skipped_reason"] = "not_proven_effective"
        return report

    alert_type = _infer_alert_type(db, proven)
    if not alert_type:
        report["skipped_reason"] = "alert_type_unknown"
        return report

    covered = _already_pollinated_shops(db, candidate_id)
    alerts = _find_matching_alerts(db, alert_type, exclude_shops=covered)
    if not alerts:
        report["status"] = "noop"
        report["skipped_reason"] = "no_matching_alerts"
        return report

    created_ids: list[int] = []
    for alert in alerts:
        try:
            new_candidate = _build_inherited_candidate(
                proven, alert, alert_type=alert_type, measurement=measurement,
            )
            db.add(new_candidate)
            db.flush()
            created_ids.append(new_candidate.id)
        except Exception as exc:
            log.warning(
                "cross_pollination: failed to create inherited candidate "
                "for shop=%s: %s", alert.shop_domain, exc,
            )
            db.rollback()
            break

    report["status"] = "pollinated" if created_ids else "noop"
    report["created"] = len(created_ids)
    report["created_ids"] = created_ids
    report["alert_type"] = alert_type
    if created_ids:
        log.info(
            "cross_pollination: proven_fix=%d → %d preventive candidates "
            "(alert_type=%s)",
            candidate_id, len(created_ids), alert_type,
        )
    return report
