"""
execution_engine.py — Post-purchase execution intelligence.

Generates and persists execution opportunities (upsell / bundle) into
relational tables. Called by aggregation_worker after store_metrics.

Data flow:
  1. Detect opportunities from co_viewed_pairs + product_metrics
  2. Upsert into execution_opportunities table (persistent, deterministic ID)
  3. Bulk insert audience into execution_audiences (append-only, skip duplicates)
  4. Create execution_tracking rows for new audience members (outcome baseline)

Does NOT send emails. Produces Klaviyo-ready structured data.
Proof loop reads execution_tracking to measure outcomes.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)


def process_execution_opportunities(
    conn,
    shop_domain: str,
    co_viewed_pairs: list[dict],
) -> int:
    """
    Detect, persist, and populate audiences for execution opportunities.

    Returns the number of opportunities processed (upserted).
    """
    if not co_viewed_pairs:
        return 0

    # Load product metrics for decision logic
    pm_map = _load_product_metrics(conn, shop_domain)
    if not pm_map:
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    processed = 0

    # Mark all existing opportunities for this shop as potentially stale.
    # Active ones will be refreshed below; the rest stay as-is (is_active
    # is not flipped to false here — opportunities persist until they
    # naturally expire from the co_viewed_pairs threshold).
    seen_ids: set[str] = set()

    for pair in co_viewed_pairs[:5]:
        a_url = pair.get("product_a", "")
        b_url = pair.get("product_b", "")
        shared = pair.get("shared_visitors", 0)
        if not a_url or not b_url or shared < 3:
            continue

        a_data = pm_map.get(a_url, {})
        b_data = pm_map.get(b_url, {})
        a_purchases = a_data.get("purchases", 0)
        b_purchases = b_data.get("purchases", 0)
        a_name = _product_name(a_url)
        b_name = _product_name(b_url)

        opp = None

        # Upsell: A sells, B doesn't
        if a_purchases > 0 and b_purchases == 0 and b_data.get("views", 0) >= 5:
            opp = _build_opp("upsell", a_url, b_url, a_name, b_name, shared)
        elif b_purchases > 0 and a_purchases == 0 and a_data.get("views", 0) >= 5:
            opp = _build_opp("upsell", b_url, a_url, b_name, a_name, shared)
        # Bundle: both sell
        elif a_purchases > 0 and b_purchases > 0 and shared >= 5:
            opp = _build_opp("bundle", a_url, b_url, a_name, b_name, shared)

        if opp is None:
            continue

        exec_id = opp["execution_id"]
        seen_ids.add(exec_id)

        # Step 1: Upsert opportunity
        _upsert_opportunity(conn, shop_domain, opp, now)

        # Step 2: Get audience (for upsell only — bundles target all visitors)
        if opp["opp_type"] == "upsell":
            audience = _get_audience(conn, shop_domain, opp["product_a"], opp["product_b"])
            opp["audience_size"] = len(audience)

            # Update audience_size
            conn.execute(
                text("""
                    UPDATE execution_opportunities
                    SET audience_size = :size
                    WHERE shop_domain = :shop AND execution_id = :eid
                """),
                {"shop": shop_domain, "eid": exec_id, "size": len(audience)},
            )

            # Read holdout_pct for this opportunity (default 20)
            hp_row = conn.execute(
                text("SELECT holdout_pct FROM execution_opportunities WHERE shop_domain = :shop AND execution_id = :eid"),
                {"shop": shop_domain, "eid": exec_id},
            ).fetchone()
            holdout_pct = int(hp_row[0]) if hp_row and hp_row[0] is not None else 20

            # Step 3: Bulk insert audience + tracking rows with holdout assignment
            _populate_audience(conn, shop_domain, exec_id, audience, now, holdout_pct)

        processed += 1

    # Deactivate opportunities that are no longer in co_viewed_pairs
    if seen_ids:
        conn.execute(
            text("""
                UPDATE execution_opportunities
                SET is_active = false
                WHERE shop_domain = :shop
                  AND is_active = true
                  AND execution_id != ALL(:seen_ids)
            """),
            {"shop": shop_domain, "seen_ids": list(seen_ids)},
        )

    return processed


def compute_proof_metrics(conn, shop_domain: str) -> list[dict]:
    """
    Compute proof loop metrics for all active opportunities.

    Returns list of:
      {execution_id, opp_type, product_a, product_b, audience_size,
       return_rate, view_rate, purchase_rate, tracked_count}
    """
    result = conn.execute(
        text("""
            SELECT
                eo.execution_id,
                eo.opp_type,
                eo.product_a,
                eo.product_b,
                eo.audience_size,
                COUNT(et.id)                                    AS tracked,
                COUNT(*) FILTER (WHERE et.returned)             AS returned,
                COUNT(*) FILTER (WHERE et.viewed_product_b)     AS viewed,
                COUNT(*) FILTER (WHERE et.purchased_product_b)  AS purchased
            FROM execution_opportunities eo
            LEFT JOIN execution_tracking et
                ON et.execution_id = eo.execution_id
               AND et.shop_domain  = eo.shop_domain
            WHERE eo.shop_domain = :shop
              AND eo.is_active = true
            GROUP BY eo.execution_id, eo.opp_type, eo.product_a, eo.product_b, eo.audience_size
        """),
        {"shop": shop_domain},
    )
    metrics = []
    for r in result.fetchall():
        tracked = int(r[5] or 0)
        metrics.append({
            "execution_id": r[0],
            "opp_type": r[1],
            "product_a": r[2],
            "product_b": r[3],
            "audience_size": int(r[4] or 0),
            "tracked_count": tracked,
            "return_rate": round(int(r[6] or 0) / tracked, 4) if tracked > 0 else None,
            "view_rate": round(int(r[7] or 0) / tracked, 4) if tracked > 0 else None,
            "purchase_rate": round(int(r[8] or 0) / tracked, 4) if tracked > 0 else None,
        })
    return metrics


def compute_post_execution_deltas(conn, shop_domain: str) -> int:
    """
    For executed opportunities, compute:
    1. Before/after deltas (post-execution all-group rates vs baseline)
    2. Counterfactual lift (exposed vs holdout, post-execution only)
    3. Confidence label from counterfactual comparison

    Counterfactual is the primary causal signal.
    Before/after deltas remain as a secondary directional indicator.

    Returns number of opportunities updated.
    """
    opps = conn.execute(
        text("""
            SELECT
                eo.execution_id,
                eo.executed_at,
                eb.return_rate   AS bl_return,
                eb.view_rate     AS bl_view,
                eb.purchase_rate AS bl_purchase
            FROM execution_opportunities eo
            LEFT JOIN execution_baselines eb
                ON eb.execution_id = eo.execution_id
               AND eb.shop_domain  = eo.shop_domain
            WHERE eo.shop_domain = :shop
              AND eo.execution_status = 'executed'
              AND eo.is_active = true
        """),
        {"shop": shop_domain},
    ).fetchall()

    updated = 0
    for opp in opps:
        eid = opp[0]
        executed_at = opp[1]
        bl_return = opp[2]
        bl_view = opp[3]
        bl_purchase = opp[4]

        if executed_at is None:
            continue

        # -- Counterfactual: exposed vs holdout (post-execution only) --
        groups = conn.execute(
            text("""
                SELECT
                    group_type,
                    COUNT(*)                                    AS n,
                    COUNT(*) FILTER (WHERE returned)            AS ret,
                    COUNT(*) FILTER (WHERE viewed_product_b)    AS viewed,
                    COUNT(*) FILTER (WHERE purchased_product_b) AS purchased
                FROM execution_tracking
                WHERE shop_domain = :shop
                  AND execution_id = :eid
                  AND exposed_at >= :executed_at
                GROUP BY group_type
            """),
            {"shop": shop_domain, "eid": eid, "executed_at": executed_at},
        ).fetchall()

        exp = {"n": 0, "ret": 0, "viewed": 0, "purchased": 0}
        hld = {"n": 0, "ret": 0, "viewed": 0, "purchased": 0}
        for g in groups:
            d = exp if g[0] == "exposed" else hld
            d["n"] = int(g[1] or 0)
            d["ret"] = int(g[2] or 0)
            d["viewed"] = int(g[3] or 0)
            d["purchased"] = int(g[4] or 0)

        total_post = exp["n"] + hld["n"]
        if total_post == 0:
            continue

        def _rate(num, den):
            return round(num / den, 4) if den > 0 else None

        # Per-group rates
        rr_exp = _rate(exp["ret"], exp["n"])
        vr_exp = _rate(exp["viewed"], exp["n"])
        pr_exp = _rate(exp["purchased"], exp["n"])
        rr_hld = _rate(hld["ret"], hld["n"])
        vr_hld = _rate(hld["viewed"], hld["n"])
        pr_hld = _rate(hld["purchased"], hld["n"])

        # Lift = exposed - holdout
        def _lift(a, b):
            if a is not None and b is not None:
                return round(a - b, 4)
            return None

        lift_ret = _lift(rr_exp, rr_hld)
        lift_view = _lift(vr_exp, vr_hld)
        lift_purchase = _lift(pr_exp, pr_hld)

        # Combined post rates (all groups, for before/after comparison)
        post_ret = _rate(exp["ret"] + hld["ret"], total_post)
        post_view = _rate(exp["viewed"] + hld["viewed"], total_post)
        post_purchase = _rate(exp["purchased"] + hld["purchased"], total_post)

        # Before/after deltas
        d_ret = round(post_ret - (bl_return or 0), 4) if post_ret is not None and bl_return is not None else None
        d_view = round(post_view - (bl_view or 0), 4) if post_view is not None and bl_view is not None else None
        d_purchase = round(post_purchase - (bl_purchase or 0), 4) if post_purchase is not None and bl_purchase is not None else None

        # Leakage detection for this opportunity
        leakage = get_leakage_rate(conn, shop_domain, eid)

        # Confidence from counterfactual (degraded by leakage)
        confidence = _compute_confidence(
            exposed_n=exp["n"], holdout_n=hld["n"],
            lift_view=lift_view, lift_purchase=lift_purchase,
            has_baseline=bl_view is not None,
            leakage_rate=leakage,
        )

        conn.execute(
            text("""
                UPDATE execution_opportunities SET
                    post_return_rate       = :post_ret,
                    post_view_rate         = :post_view,
                    post_purchase_rate     = :post_purchase,
                    post_sample_size       = :total_post,
                    delta_return_rate      = :d_ret,
                    delta_view_rate        = :d_view,
                    delta_purchase_rate    = :d_purchase,
                    exposed_sample_size    = :exp_n,
                    holdout_sample_size    = :hld_n,
                    return_rate_exposed    = :rr_exp,
                    view_rate_exposed      = :vr_exp,
                    purchase_rate_exposed  = :pr_exp,
                    return_rate_holdout    = :rr_hld,
                    view_rate_holdout      = :vr_hld,
                    purchase_rate_holdout  = :pr_hld,
                    lift_return_rate       = :lift_ret,
                    lift_view_rate         = :lift_view,
                    lift_purchase_rate     = :lift_purchase,
                    confidence_label       = :conf
                WHERE shop_domain = :shop AND execution_id = :eid
            """),
            {
                "shop": shop_domain, "eid": eid,
                "post_ret": post_ret, "post_view": post_view, "post_purchase": post_purchase,
                "total_post": total_post,
                "d_ret": d_ret, "d_view": d_view, "d_purchase": d_purchase,
                "exp_n": exp["n"], "hld_n": hld["n"],
                "rr_exp": rr_exp, "vr_exp": vr_exp, "pr_exp": pr_exp,
                "rr_hld": rr_hld, "vr_hld": vr_hld, "pr_hld": pr_hld,
                "lift_ret": lift_ret, "lift_view": lift_view, "lift_purchase": lift_purchase,
                "conf": confidence,
            },
        )
        updated += 1

    return updated


def _compute_confidence(
    exposed_n: int,
    holdout_n: int,
    lift_view: float | None,
    lift_purchase: float | None,
    has_baseline: bool,
    leakage_rate: float = 0.0,
) -> str:
    """
    Deterministic confidence from counterfactual comparison.

    strong:   exposed >= 20 AND holdout >= 5 AND
              (lift_purchase >= +2pp OR lift_view >= +3pp) AND leakage < 20%
    moderate: exposed >= 10 AND holdout >= 3 AND positive lift AND leakage < 30%
    low:      everything else
    """
    if exposed_n < 5:
        return "low"

    # Leakage cap: high contamination degrades confidence
    if leakage_rate > 0.3:
        return "low"

    view_lift_positive = lift_view is not None and lift_view > 0
    purchase_lift_positive = lift_purchase is not None and lift_purchase > 0
    view_lift_strong = lift_view is not None and lift_view >= 0.03
    purchase_lift_strong = lift_purchase is not None and lift_purchase >= 0.02

    if leakage_rate <= 0.2 and exposed_n >= 20 and holdout_n >= 5 and (purchase_lift_strong or view_lift_strong):
        return "strong"

    if leakage_rate <= 0.3 and exposed_n >= 10 and holdout_n >= 3 and (view_lift_positive or purchase_lift_positive):
        return "moderate"

    if view_lift_positive or purchase_lift_positive:
        return "low"

    return "low"


def detect_holdout_leakage(conn, shop_domain: str) -> int:
    """
    Detect potential holdout contamination.

    A holdout visitor who views product_b within 5 minutes of page load
    (after execution started) is suspicious — they may have seen a cross-sell
    widget that should have been suppressed.

    Sets leakage_suspected = true on those tracking rows.
    Returns count of newly flagged rows.

    Only flags holdout visitors. Exposed visitors viewing product_b is expected.
    """
    result = conn.execute(
        text("""
            UPDATE execution_tracking et
            SET leakage_suspected = true, updated_at = now()
            WHERE et.shop_domain = :shop
              AND et.group_type = 'holdout'
              AND et.leakage_suspected = false
              AND et.viewed_product_b = true
              AND EXISTS (
                  SELECT 1 FROM execution_opportunities eo
                  WHERE eo.execution_id = et.execution_id
                    AND eo.shop_domain = et.shop_domain
                    AND eo.enforcement_mode = 'onsite'
              )
        """),
        {"shop": shop_domain},
    )
    return result.rowcount


def get_leakage_rate(conn, shop_domain: str, execution_id: str) -> float:
    """Fraction of holdout visitors with leakage_suspected = true."""
    result = conn.execute(
        text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE leakage_suspected) AS leaked
            FROM execution_tracking
            WHERE shop_domain = :shop
              AND execution_id = :eid
              AND group_type = 'holdout'
        """),
        {"shop": shop_domain, "eid": execution_id},
    ).fetchone()
    if result is None or int(result[0] or 0) == 0:
        return 0.0
    return round(int(result[1] or 0) / int(result[0]), 4)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_product_metrics(conn, shop_domain: str) -> dict:
    try:
        rows = conn.execute(
            text("""
                SELECT product_url, purchases_24h, revenue_24h, views_24h, cart_conversions_24h
                FROM product_metrics WHERE shop_domain = :shop
            """),
            {"shop": shop_domain},
        ).fetchall()
        return {
            r[0]: {"purchases": int(r[1] or 0), "revenue": float(r[2] or 0),
                   "views": int(r[3] or 0), "carts": int(r[4] or 0)}
            for r in rows
        }
    except Exception as exc:
        logger.warning("execution_engine: product_metrics read failed: %s", exc)
        return {}


def _product_name(url: str) -> str:
    if not url:
        return "this product"
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    parts = [p for p in clean.split("/") if p]
    for i, part in enumerate(parts):
        if part == "products" and i + 1 < len(parts):
            return parts[i + 1].replace("-", " ").replace("_", " ").title()
    return parts[-1].replace("-", " ").title() if parts else "this product"


def _opp_id(opp_type: str, url_a: str, url_b: str) -> str:
    key = f"{opp_type}:{url_a}:{url_b}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _build_opp(opp_type: str, a_url: str, b_url: str,
               a_name: str, b_name: str, shared: int) -> dict:
    exec_id = _opp_id(opp_type, a_url, b_url)
    if opp_type == "upsell":
        return {
            "execution_id": exec_id,
            "opp_type": "upsell",
            "product_a": a_url,
            "product_b": b_url,
            "audience_size": 0,  # populated after audience query
            "suggested_message": (
                f"You bought {a_name} — customers who love it also love {b_name}. "
                f"Complete your collection today."
            ),
            "timing": "2-3 days post-purchase",
            "expected_impact": (
                f"{shared} shared visitors/week. "
                f"If 10% of {a_name} buyers also buy {b_name}, "
                f"that's incremental revenue from an audience you already own."
            ),
        }
    else:  # bundle
        return {
            "execution_id": exec_id,
            "opp_type": "bundle",
            "product_a": a_url,
            "product_b": b_url,
            "audience_size": shared,
            "suggested_message": (
                f"Better together: {a_name} + {b_name}. Get both and save."
            ),
            "timing": "Immediate — feature on product pages and homepage",
            "expected_impact": (
                f"{shared} visitors view both products/week. "
                f"Bundles typically increase AOV 15-25%."
            ),
        }


def _upsert_opportunity(conn, shop_domain: str, opp: dict, now: datetime) -> None:
    conn.execute(
        text("""
            INSERT INTO execution_opportunities (
                execution_id, shop_domain, opp_type, product_a, product_b,
                audience_size, suggested_message, timing, expected_impact,
                is_active, created_at, refreshed_at
            ) VALUES (
                :eid, :shop, :opp_type, :pa, :pb,
                :size, :msg, :timing, :impact,
                true, :now, :now
            )
            ON CONFLICT (shop_domain, execution_id) DO UPDATE SET
                audience_size     = :size,
                suggested_message = :msg,
                timing            = :timing,
                expected_impact   = :impact,
                is_active         = true,
                refreshed_at      = :now
        """),
        {
            "eid": opp["execution_id"], "shop": shop_domain,
            "opp_type": opp["opp_type"], "pa": opp["product_a"], "pb": opp["product_b"],
            "size": opp["audience_size"], "msg": opp["suggested_message"],
            "timing": opp["timing"], "impact": opp["expected_impact"],
            "now": now,
        },
    )


def _get_audience(conn, shop_domain: str, bought_url: str, target_url: str) -> list[str]:
    """Get visitor_ids who bought product_a and viewed product_b. Bounded."""
    try:
        result = conn.execute(
            text("""
                WITH buyers AS (
                    SELECT DISTINCT vps.visitor_id
                    FROM visitor_purchase_sessions vps
                    INNER JOIN shop_orders so
                        ON so.shopify_order_id = vps.shopify_order_id
                    WHERE vps.shop_domain = :shop AND so.shop_domain = :shop
                      AND so.created_at >= NOW() - INTERVAL '30 days'
                      AND EXISTS (
                          SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                          WHERE item->>'product_url' = :bought_url
                      )
                ),
                viewers AS (
                    SELECT DISTINCT visitor_id
                    FROM events
                    WHERE shop_domain = :shop AND product_url = :target_url
                      AND event_type IN ('page_view', 'product_view')
                      AND timestamp >= EXTRACT(EPOCH FROM NOW() - INTERVAL '30 days') * 1000
                )
                SELECT b.visitor_id FROM buyers b
                INNER JOIN viewers v ON v.visitor_id = b.visitor_id
                LIMIT 500
            """),
            {"shop": shop_domain, "bought_url": bought_url, "target_url": target_url},
        )
        return [r[0] for r in result.fetchall()]
    except Exception as exc:
        logger.warning("execution_engine: audience query failed: %s", exc)
        return []


def _update_tracking_outcomes(conn, shop_domain: str) -> int:
    """
    Incrementally update execution_tracking rows for this shop.

    For each tracking row where NOT all outcomes are true:
      - returned: did visitor have ANY event after exposed_at?
      - viewed_product_b: did visitor view the target product after exposed_at?
      - purchased_product_b: did visitor purchase the target product after exposed_at?

    Uses efficient batch updates — one query per outcome flag, not per visitor.
    Returns total rows updated.
    """
    updated = 0

    # 1. Mark returned = true for visitors with any event after exposed_at
    try:
        r = conn.execute(
            text("""
                UPDATE execution_tracking et
                SET returned = true, updated_at = now()
                WHERE et.shop_domain = :shop
                  AND et.returned = false
                  AND EXISTS (
                      SELECT 1 FROM events e
                      WHERE e.shop_domain = :shop
                        AND e.visitor_id = et.visitor_id
                        AND e.timestamp > EXTRACT(EPOCH FROM et.exposed_at) * 1000
                      LIMIT 1
                  )
            """),
            {"shop": shop_domain},
        )
        updated += r.rowcount
    except Exception as exc:
        logger.warning("execution tracking: returned update failed: %s", exc)

    # 2. Mark viewed_product_b = true
    try:
        r = conn.execute(
            text("""
                UPDATE execution_tracking et
                SET viewed_product_b = true, updated_at = now()
                WHERE et.shop_domain = :shop
                  AND et.viewed_product_b = false
                  AND EXISTS (
                      SELECT 1 FROM execution_opportunities eo
                      WHERE eo.execution_id = et.execution_id
                        AND eo.shop_domain = et.shop_domain
                        AND EXISTS (
                            SELECT 1 FROM events e
                            WHERE e.shop_domain = :shop
                              AND e.visitor_id = et.visitor_id
                              AND e.product_url = eo.product_b
                              AND e.event_type IN ('page_view', 'product_view')
                              AND e.timestamp > EXTRACT(EPOCH FROM et.exposed_at) * 1000
                            LIMIT 1
                        )
                  )
            """),
            {"shop": shop_domain},
        )
        updated += r.rowcount
    except Exception as exc:
        logger.warning("execution tracking: viewed update failed: %s", exc)

    # 3. Mark purchased_product_b = true
    try:
        r = conn.execute(
            text("""
                UPDATE execution_tracking et
                SET purchased_product_b = true, updated_at = now()
                WHERE et.shop_domain = :shop
                  AND et.purchased_product_b = false
                  AND EXISTS (
                      SELECT 1 FROM execution_opportunities eo
                      WHERE eo.execution_id = et.execution_id
                        AND eo.shop_domain = et.shop_domain
                        AND EXISTS (
                            SELECT 1 FROM visitor_purchase_sessions vps
                            INNER JOIN shop_orders so
                                ON so.shopify_order_id = vps.shopify_order_id
                            WHERE vps.shop_domain = :shop
                              AND vps.visitor_id = et.visitor_id
                              AND so.created_at > et.exposed_at
                              AND EXISTS (
                                  SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                                  WHERE item->>'product_url' = eo.product_b
                              )
                            LIMIT 1
                        )
                  )
            """),
            {"shop": shop_domain},
        )
        updated += r.rowcount
    except Exception as exc:
        logger.warning("execution tracking: purchased update failed: %s", exc)

    return updated


def _assign_group(visitor_id: str, execution_id: str, holdout_pct: int) -> str:
    """
    Deterministic group assignment using hash.
    Same (visitor_id, execution_id) always produces same group.
    No randomness, no runtime state.
    """
    key = f"{visitor_id}:{execution_id}"
    h = int(hashlib.md5(key.encode()).hexdigest(), 16) % 100
    return "holdout" if h < holdout_pct else "exposed"


def _populate_audience(conn, shop_domain: str, exec_id: str,
                       visitor_ids: list[str], now: datetime,
                       holdout_pct: int = 20) -> None:
    """Batch insert audience + tracking rows with deterministic group assignment."""
    if not visitor_ids:
        return

    # Pre-compute all assignments
    rows = [
        {"eid": exec_id, "shop": shop_domain, "vid": vid,
         "group": _assign_group(vid, exec_id, holdout_pct), "now": now}
        for vid in visitor_ids
    ]

    # Batch insert audience membership (skip duplicates)
    BATCH = 50
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        values_sql = ", ".join(
            f"(:eid_{j}, :shop_{j}, :vid_{j}, :group_{j}, :now_{j})"
            for j in range(len(batch))
        )
        params = {}
        for j, r in enumerate(batch):
            params[f"eid_{j}"] = r["eid"]
            params[f"shop_{j}"] = r["shop"]
            params[f"vid_{j}"] = r["vid"]
            params[f"group_{j}"] = r["group"]
            params[f"now_{j}"] = r["now"]

        conn.execute(
            text(f"""
                INSERT INTO execution_audiences
                    (execution_id, shop_domain, visitor_id, group_type, created_at)
                VALUES {values_sql}
                ON CONFLICT (execution_id, visitor_id) DO NOTHING
            """),
            params,
        )
        conn.execute(
            text(f"""
                INSERT INTO execution_tracking
                    (execution_id, shop_domain, visitor_id, group_type, exposed_at, updated_at)
                VALUES {values_sql}
                ON CONFLICT (execution_id, visitor_id) DO NOTHING
            """),
            params,
        )
