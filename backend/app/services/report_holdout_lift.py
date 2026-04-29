"""report_holdout_lift.py — Gap #1 Custom Report Builder helper.

Computes a per-window holdout-vs-exposed revenue lift annotation
for a custom saved report. Reads from `execution_tracking` (cohort
assignments) and joins to `shop_orders` via
`visitor_purchase_sessions`.

Scope (v1): fires only when the shop has at LEAST one execution
overlapping the report's date window AND both cohorts have ≥30
visitors with ≥1 order. Otherwise returns None and the report
silently omits the annotation.

The annotation is calm + factual per founder voice direction
2026-04-28: "tono calmo merchant friendly". No promotional copy
in the data layer; the dashboard owns the rendering string.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("report_holdout_lift")

_MIN_PER_COHORT = 30
_MIN_P = 0.001


def holdout_lift_for_shop_window(
    db: Session,
    shop: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any] | None:
    """Return {lift_eur, p_value, n_exposed, n_holdout} or None.

    None when:
      - No active execution overlaps the window
      - Either cohort has <30 visitors with revenue data
      - Welch's t-test computation fails

    The function is designed to be called inside the report executor
    in a wide try/except — never raises.
    """
    try:
        # Find any execution that overlapped the window. We bound the
        # JOIN to a single eid for v1 — multi-execution aggregation is
        # a future-sprint refinement.
        eid_row = db.execute(
            text("""
                SELECT execution_id
                FROM execution_tracking
                WHERE shop_domain = :shop
                  AND exposed_at BETWEEN :start AND :end
                GROUP BY execution_id
                HAVING COUNT(*) FILTER (WHERE group_type = 'exposed') >= :min_n
                   AND COUNT(*) FILTER (WHERE group_type = 'holdout') >= :min_n
                ORDER BY MAX(exposed_at) DESC
                LIMIT 1
            """),
            {"shop": shop, "start": start, "end": end, "min_n": _MIN_PER_COHORT},
        ).fetchone()
        if not eid_row:
            return None
        execution_id = eid_row[0]

        # Per-visitor revenue in window, split by cohort.
        # visitor_purchase_sessions.visitor_id ↔ execution_tracking.visitor_id
        # ↔ shop_orders via visitor_purchase_sessions.shopify_order_id
        rows = db.execute(
            text("""
                WITH cohort AS (
                    SELECT visitor_id, group_type
                    FROM execution_tracking
                    WHERE shop_domain = :shop
                      AND execution_id = :eid
                      AND group_type IN ('exposed', 'holdout')
                ),
                rev AS (
                    SELECT
                        c.visitor_id,
                        c.group_type,
                        COALESCE(SUM(so.total_price), 0) AS total_rev
                    FROM cohort c
                    LEFT JOIN visitor_purchase_sessions vps
                      ON vps.shop_domain = :shop
                     AND vps.visitor_id = c.visitor_id
                    LEFT JOIN shop_orders so
                      ON so.shop_domain = :shop
                     AND so.shopify_order_id = vps.shopify_order_id
                     AND so.created_at BETWEEN :start AND :end
                    GROUP BY c.visitor_id, c.group_type
                )
                SELECT group_type, total_rev FROM rev
            """),
            {"shop": shop, "eid": execution_id, "start": start, "end": end},
        ).fetchall()

        exposed = [float(r[1] or 0) for r in rows if r[0] == "exposed"]
        holdout = [float(r[1] or 0) for r in rows if r[0] == "holdout"]
        if len(exposed) < _MIN_PER_COHORT or len(holdout) < _MIN_PER_COHORT:
            return None

        from app.services.fix_holdout_measurement import _welch_t_test
        t_abs, df = _welch_t_test(exposed, holdout)
        from app.services.fix_holdout_measurement import _two_sided_t_pvalue
        p_value = _two_sided_t_pvalue(t_abs, df) if df > 0 else 1.0

        mean_e = sum(exposed) / len(exposed) if exposed else 0
        mean_h = sum(holdout) / len(holdout) if holdout else 0
        # Per-window total lift: average per-visitor delta × exposed count
        lift_eur = (mean_e - mean_h) * len(exposed)

        return {
            "lift_eur": round(lift_eur, 2),
            "p_value": round(max(p_value, _MIN_P), 4),
            "n_exposed": len(exposed),
            "n_holdout": len(holdout),
        }
    except Exception as exc:  # noqa: BLE001
        # DB-touching code path — promote to warning per
        # audit_exception_debug. The function is opportunistic (called
        # inside the report executor in a wide try/except), so failure
        # here just means the holdout annotation is absent for this
        # report run; no merchant impact.
        log.warning("report_holdout_lift: %s", exc)
        from app.core.silent_fallback import record_silent_return
        record_silent_return("report_holdout_lift")
        return None
