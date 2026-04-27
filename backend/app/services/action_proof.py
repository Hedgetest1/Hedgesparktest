"""
action_proof.py — Closed-loop proof-of-impact engine.

Public interface:
    capture_baseline(db, shop_domain, product_url, action_type, ...) -> ActionSnapshot
    compute_pending_deltas(db) -> int  (number of deltas computed)
    get_proof_summary(db, shop_domain) -> dict  (merchant-facing proof report)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.action_snapshot import ActionSnapshot

log = logging.getLogger(__name__)

_COMPARE_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _product_metrics_now(db: Session, shop: str, product_url: str, days: int = 7) -> dict:
    """Compute current product metrics for baseline or comparison."""
    try:
        cutoff_ms = int((_now() - timedelta(days=days)).timestamp() * 1000)
        row = db.execute(
            text("""
                SELECT
                    COUNT(DISTINCT CASE WHEN event_type = 'product_view' THEN visitor_id END)::int AS visitors,
                    COUNT(DISTINCT CASE WHEN event_type = 'add_to_cart' THEN visitor_id END)::int AS atc_visitors
                FROM events
                WHERE shop_domain = :shop
                  AND product_url = :product_url
                  AND timestamp > :cutoff
            """),
            {"shop": shop, "product_url": product_url, "cutoff": cutoff_ms},
        ).fetchone()
        visitors = int(row[0] or 0)
        atc = int(row[1] or 0)

        # Orders for this product from line_items
        order_row = db.execute(
            text("""
                SELECT COUNT(DISTINCT so.shopify_order_id)::int AS orders,
                       COALESCE(SUM((item->>'price')::numeric * (item->>'quantity')::int), 0) AS revenue
                FROM shop_orders so,
                     jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                WHERE so.shop_domain = :shop
                  AND so.created_at >= NOW() - make_interval(days => :days)
                  AND item->>'product_id' IN (
                      SELECT DISTINCT product_id FROM events
                      WHERE shop_domain = :shop AND product_url = :product_url
                        AND product_id IS NOT NULL
                  )
            """),
            {"shop": shop, "product_url": product_url, "days": days},
        ).fetchone()
        orders = int(order_row[0] or 0) if order_row else 0
        revenue = float(order_row[1] or 0) if order_row else 0.0

        cvr = round(orders / visitors, 4) if visitors > 0 else 0.0
        atc_rate = round(atc / visitors, 4) if visitors > 0 else 0.0

        return {
            "visitors": visitors,
            "atc_visitors": atc,
            "orders": orders,
            "revenue": round(revenue, 2),
            "cvr": cvr,
            "atc_rate": atc_rate,
        }
    except Exception as exc:
        log.warning("action_proof: metrics query failed shop=%s product=%s: %s", shop, product_url, exc)
        return {"visitors": 0, "atc_visitors": 0, "orders": 0, "revenue": 0, "cvr": 0, "atc_rate": 0}


def capture_baseline(
    db: Session,
    shop_domain: str,
    product_url: str,
    action_type: str,
    action_task_id: int | None = None,
    signal_type: str | None = None,
    signal_strength: float | None = None,
) -> ActionSnapshot:
    """
    Capture baseline product metrics at the moment an action is created.
    Returns the created snapshot row.
    """
    now = _now()
    metrics = _product_metrics_now(db, shop_domain, product_url)

    snapshot = ActionSnapshot(
        shop_domain=shop_domain,
        product_url=product_url,
        action_type=action_type,
        action_task_id=action_task_id,
        baseline_cvr=metrics["cvr"],
        baseline_atc_rate=metrics["atc_rate"],
        baseline_revenue_7d=metrics["revenue"],
        baseline_visitors_7d=metrics["visitors"],
        baseline_orders_7d=metrics["orders"],
        signal_type=signal_type,
        signal_strength=signal_strength,
        snapshot_at=now,
        compare_after=now + timedelta(days=_COMPARE_DAYS),
        delta_computed=False,
    )
    db.add(snapshot)
    db.flush()
    log.info(
        "action_proof: baseline captured shop=%s product=%s action=%s task_id=%s",
        shop_domain, product_url, action_type, action_task_id,
    )
    return snapshot


def compute_pending_deltas(db: Session) -> int:
    """
    Find all snapshots past their compare_after date and compute deltas.
    Called from the aggregation worker. Returns the count of deltas computed.
    """
    now = _now()
    pending = (
        db.query(ActionSnapshot)
        .filter(
            ActionSnapshot.delta_computed == False,  # noqa: E712
            ActionSnapshot.compare_after <= now,
        )
        .limit(50)
        .all()
    )

    computed = 0
    for snap in pending:
        try:
            current = _product_metrics_now(db, snap.shop_domain, snap.product_url)

            snap.delta_cvr = round(current["cvr"] - (snap.baseline_cvr or 0), 4)
            snap.delta_atc_rate = round(current["atc_rate"] - (snap.baseline_atc_rate or 0), 4)
            snap.delta_revenue_7d = round(current["revenue"] - (snap.baseline_revenue_7d or 0), 2)
            snap.delta_visitors_7d = current["visitors"] - (snap.baseline_visitors_7d or 0)
            snap.delta_orders_7d = current["orders"] - (snap.baseline_orders_7d or 0)
            snap.delta_computed = True
            snap.delta_computed_at = now

            # Classify outcome
            if snap.delta_cvr > 0.005:  # >0.5pp improvement
                snap.outcome = "improved"
            elif snap.delta_cvr < -0.005:
                snap.outcome = "declined"
            else:
                snap.outcome = "stable"

            # Human summary
            if snap.outcome == "improved":
                base_pct = round((snap.baseline_cvr or 0) * 100, 1)
                curr_pct = round(current["cvr"] * 100, 1)
                rev_delta = snap.delta_revenue_7d or 0
                snap.summary = (
                    f"Conversion rate improved from {base_pct}% to {curr_pct}%."
                    + (f" Revenue +${rev_delta:,.2f} vs prior week." if rev_delta > 0 else "")
                )
            elif snap.outcome == "declined":
                base_pct = round((snap.baseline_cvr or 0) * 100, 1)
                curr_pct = round(current["cvr"] * 100, 1)
                snap.summary = f"Conversion rate changed from {base_pct}% to {curr_pct}%."
            else:
                snap.summary = "Metrics remained stable after the change."

            db.flush()
            computed += 1
            log.info(
                "action_proof: delta computed snap_id=%d shop=%s product=%s outcome=%s",
                snap.id, snap.shop_domain, snap.product_url, snap.outcome,
            )

            # Proof celebration emails removed — execution email flows
            # now go through Klaviyo (see sync_execution_to_klaviyo).
        except Exception as exc:
            log.error("action_proof: delta computation failed snap_id=%d: %s", snap.id, exc)

    if computed > 0:
        db.commit()
        log.info("action_proof: computed %d pending deltas", computed)

    return computed


def get_proof_summary(db: Session, shop_domain: str, days: int = 30) -> dict:
    """
    Return merchant-facing proof-of-impact summary.
    Used by digest and dashboard.
    """
    cutoff = _now() - timedelta(days=days)
    try:
        snapshots = (
            db.query(ActionSnapshot)
            .filter(
                ActionSnapshot.shop_domain == shop_domain,
                ActionSnapshot.delta_computed == True,  # noqa: E712
                ActionSnapshot.delta_computed_at >= cutoff,
            )
            .order_by(ActionSnapshot.delta_computed_at.desc())
            .limit(10)
            .all()
        )
    except Exception:
        return {
            "actions_measured": 0, "improvements": [],
            "total_revenue_delta": 0, "currency": "USD",
        }

    improvements = []
    total_rev_delta = 0.0
    for s in snapshots:
        if s.outcome == "improved":
            improvements.append({
                "product_url": s.product_url,
                "action_type": s.action_type,
                "summary": s.summary,
                "delta_cvr": s.delta_cvr,
                "delta_revenue": float(s.delta_revenue_7d) if s.delta_revenue_7d is not None else None,
                "measured_at": s.delta_computed_at.isoformat() + "Z" if s.delta_computed_at else None,
            })
        total_rev_delta += float(s.delta_revenue_7d or 0)

    try:
        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"
    return {
        "actions_measured": len(snapshots),
        "improvements": improvements,
        "total_revenue_delta": round(total_rev_delta, 2),
        "currency": currency,
    }


# ---------------------------------------------------------------------------
# Action effectiveness — closed-loop learning for action ranking
# ---------------------------------------------------------------------------

def get_action_effectiveness(db: Session, days: int = 90) -> dict[str, dict]:
    """
    Aggregate action_snapshot outcomes by action_type over the last N days.
    Returns: {"ACTION_TYPE": {"total": N, "improved": N, "declined": N, "stable": N, "effectiveness": 0.0-1.0}}

    Used by action_candidates_engine to boost/penalize action types based on
    historical performance. effectiveness = improved / total (0 if no data).
    """
    cutoff = _now() - timedelta(days=days)
    try:
        snapshots = (
            db.query(ActionSnapshot)
            .filter(
                ActionSnapshot.delta_computed == True,  # noqa: E712
                ActionSnapshot.delta_computed_at >= cutoff,
            )
            .all()
        )
    except Exception as exc:
        log.warning(
            "action_proof: snapshot query failed (%s): %s",
            type(exc).__name__, str(exc)[:200],
        )
        return {}

    by_type: dict[str, dict] = {}
    for s in snapshots:
        at = s.action_type or "unknown"
        if at not in by_type:
            by_type[at] = {"total": 0, "improved": 0, "declined": 0, "stable": 0}
        by_type[at]["total"] += 1
        outcome = s.outcome or "stable"
        by_type[at][outcome] = by_type[at].get(outcome, 0) + 1

    # Compute effectiveness ratio per action type
    for at, stats in by_type.items():
        stats["effectiveness"] = round(stats["improved"] / stats["total"], 3) if stats["total"] > 0 else 0.0

    return by_type
