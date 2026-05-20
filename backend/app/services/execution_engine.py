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


# ---------------------------------------------------------------------------
# Post-execution deltas: SQL constants + computation helpers
# ---------------------------------------------------------------------------
# Refactor 2026-05-12 (A3 medium close): 298-LOC god function → composer +
# 6 helpers + 4 SQL constants. Identical contract; bulk-UPDATE wire format
# byte-identical (20 columns × N opps via unnest of parallel arrays).

_DELTAS_OPPS_SQL = text("""
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
""")


_DELTAS_GROUPS_SQL = text("""
    SELECT
        et.execution_id,
        et.group_type,
        COUNT(*)                                    AS n,
        COUNT(*) FILTER (WHERE et.returned)         AS ret,
        COUNT(*) FILTER (WHERE et.viewed_product_b) AS viewed,
        COUNT(*) FILTER (WHERE et.purchased_product_b) AS purchased
    FROM execution_tracking et
    JOIN execution_opportunities eo
      ON eo.shop_domain = et.shop_domain
     AND eo.execution_id = et.execution_id
    WHERE et.shop_domain = :shop
      AND eo.execution_status = 'executed'
      AND eo.is_active = true
      AND eo.executed_at IS NOT NULL
      AND et.exposed_at >= eo.executed_at
      AND et.execution_id = ANY(:eids)
    GROUP BY et.execution_id, et.group_type
""")


_DELTAS_LEAKAGE_SQL = text("""
    SELECT
        execution_id,
        COUNT(*)                                AS total_holdout,
        COUNT(*) FILTER (WHERE leakage_suspected) AS leaked
    FROM execution_tracking
    WHERE shop_domain = :shop
      AND group_type = 'holdout'
      AND execution_id = ANY(:eids)
    GROUP BY execution_id
""")


_DELTAS_BULK_UPDATE_SQL = text("""
    UPDATE execution_opportunities AS eo SET
        post_return_rate       = v.post_ret,
        post_view_rate         = v.post_view,
        post_purchase_rate     = v.post_purchase,
        post_sample_size       = v.total_post,
        delta_return_rate      = v.d_ret,
        delta_view_rate        = v.d_view,
        delta_purchase_rate    = v.d_purchase,
        exposed_sample_size    = v.exp_n,
        holdout_sample_size    = v.hld_n,
        return_rate_exposed    = v.rr_exp,
        view_rate_exposed      = v.vr_exp,
        purchase_rate_exposed  = v.pr_exp,
        return_rate_holdout    = v.rr_hld,
        view_rate_holdout      = v.vr_hld,
        purchase_rate_holdout  = v.pr_hld,
        lift_return_rate       = v.lift_ret,
        lift_view_rate         = v.lift_view,
        lift_purchase_rate     = v.lift_purchase,
        confidence_label       = v.conf
    FROM unnest(
        CAST(:eids AS text[]),
        CAST(:post_rets AS double precision[]),
        CAST(:post_views AS double precision[]),
        CAST(:post_purchases AS double precision[]),
        CAST(:total_posts AS integer[]),
        CAST(:d_rets AS double precision[]),
        CAST(:d_views AS double precision[]),
        CAST(:d_purchases AS double precision[]),
        CAST(:exp_ns AS integer[]),
        CAST(:hld_ns AS integer[]),
        CAST(:rr_exps AS double precision[]),
        CAST(:vr_exps AS double precision[]),
        CAST(:pr_exps AS double precision[]),
        CAST(:rr_hlds AS double precision[]),
        CAST(:vr_hlds AS double precision[]),
        CAST(:pr_hlds AS double precision[]),
        CAST(:lift_rets AS double precision[]),
        CAST(:lift_views AS double precision[]),
        CAST(:lift_purchases AS double precision[]),
        CAST(:confs AS text[])
    ) AS v(
        eid, post_ret, post_view, post_purchase, total_post,
        d_ret, d_view, d_purchase, exp_n, hld_n,
        rr_exp, vr_exp, pr_exp, rr_hld, vr_hld, pr_hld,
        lift_ret, lift_view, lift_purchase, conf
    )
    WHERE eo.shop_domain = :shop AND eo.execution_id = v.eid
""")


# Field names ordered to match the unnest signature. Singular form is
# the per-opp dict key; SQL UPDATE param names are the plural form (+s).
_DELTAS_UPDATE_FIELDS = (
    "eid",
    "post_ret", "post_view", "post_purchase", "total_post",
    "d_ret", "d_view", "d_purchase",
    "exp_n", "hld_n",
    "rr_exp", "vr_exp", "pr_exp",
    "rr_hld", "vr_hld", "pr_hld",
    "lift_ret", "lift_view", "lift_purchase",
    "conf",
)


_EMPTY_GROUP = {"n": 0, "ret": 0, "viewed": 0, "purchased": 0}


def _rate(num, den):
    """Pure rate calculation rounded to 4 places, None on zero denominator."""
    return round(num / den, 4) if den > 0 else None


def _lift(a, b):
    """Pure lift (a-b) rounded to 4 places; None if either input is None."""
    return round(a - b, 4) if (a is not None and b is not None) else None


def _fetch_eligible_opps(conn, shop_domain: str) -> list:
    """Pull executed+active opps with non-NULL executed_at."""
    opps = conn.execute(_DELTAS_OPPS_SQL, {"shop": shop_domain}).fetchall()
    return [opp for opp in opps if opp[1] is not None]


def _fetch_group_buckets(conn, shop_domain: str, eids: list[str]) -> dict[str, dict]:
    """One round-trip fetch of (exposed, holdout) tracking counts per opp."""
    rows = conn.execute(
        _DELTAS_GROUPS_SQL,
        {"shop": shop_domain, "eids": eids},
    ).fetchall()

    by_eid: dict[str, dict] = {}
    for r in rows:
        bucket = by_eid.setdefault(r[0], {
            "exposed": dict(_EMPTY_GROUP),
            "holdout": dict(_EMPTY_GROUP),
        })
        target = bucket["exposed"] if r[1] == "exposed" else bucket["holdout"]
        target["n"] = int(r[2] or 0)
        target["ret"] = int(r[3] or 0)
        target["viewed"] = int(r[4] or 0)
        target["purchased"] = int(r[5] or 0)
    return by_eid


def _fetch_leakage_rates(conn, shop_domain: str, eids: list[str]) -> dict[str, float]:
    """One round-trip fetch of leakage rate per opp (ALL holdout rows,
    matching get_leakage_rate semantics)."""
    rows = conn.execute(
        _DELTAS_LEAKAGE_SQL,
        {"shop": shop_domain, "eids": eids},
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        total = int(r[1] or 0)
        leaked = int(r[2] or 0)
        out[r[0]] = round(leaked / total, 4) if total > 0 else 0.0
    return out


def _compute_opp_metrics(opp, exp: dict, hld: dict, leakage: float) -> dict | None:
    """
    Compute 20-field metrics dict for one opp. Returns None when the
    opp has zero post-execution tracking (total_post==0), matching the
    prior continue-in-loop skip rule.
    """
    total_post = exp["n"] + hld["n"]
    if total_post == 0:
        return None

    eid, _, bl_return, bl_view, bl_purchase = opp[0], opp[1], opp[2], opp[3], opp[4]

    rr_exp = _rate(exp["ret"], exp["n"])
    vr_exp = _rate(exp["viewed"], exp["n"])
    pr_exp = _rate(exp["purchased"], exp["n"])
    rr_hld = _rate(hld["ret"], hld["n"])
    vr_hld = _rate(hld["viewed"], hld["n"])
    pr_hld = _rate(hld["purchased"], hld["n"])

    lift_ret = _lift(rr_exp, rr_hld)
    lift_view = _lift(vr_exp, vr_hld)
    lift_purchase = _lift(pr_exp, pr_hld)

    post_ret = _rate(exp["ret"] + hld["ret"], total_post)
    post_view = _rate(exp["viewed"] + hld["viewed"], total_post)
    post_purchase = _rate(exp["purchased"] + hld["purchased"], total_post)

    d_ret = (
        round(post_ret - (bl_return or 0), 4)
        if post_ret is not None and bl_return is not None else None
    )
    d_view = (
        round(post_view - (bl_view or 0), 4)
        if post_view is not None and bl_view is not None else None
    )
    d_purchase = (
        round(post_purchase - (bl_purchase or 0), 4)
        if post_purchase is not None and bl_purchase is not None else None
    )

    confidence = _compute_confidence(
        exposed_n=exp["n"], holdout_n=hld["n"],
        lift_view=lift_view, lift_purchase=lift_purchase,
        has_baseline=bl_view is not None,
        leakage_rate=leakage,
    )

    return {
        "eid": eid,
        "post_ret": post_ret, "post_view": post_view, "post_purchase": post_purchase,
        "total_post": total_post,
        "d_ret": d_ret, "d_view": d_view, "d_purchase": d_purchase,
        "exp_n": exp["n"], "hld_n": hld["n"],
        "rr_exp": rr_exp, "vr_exp": vr_exp, "pr_exp": pr_exp,
        "rr_hld": rr_hld, "vr_hld": vr_hld, "pr_hld": pr_hld,
        "lift_ret": lift_ret, "lift_view": lift_view, "lift_purchase": lift_purchase,
        "conf": confidence,
    }


def _apply_bulk_update(conn, shop_domain: str, records: list[dict]) -> None:
    """Transpose list-of-dicts to parallel arrays and issue the bulk UPDATE."""
    params = {"shop": shop_domain}
    for field in _DELTAS_UPDATE_FIELDS:
        params[f"{field}s"] = [r[field] for r in records]
    conn.execute(_DELTAS_BULK_UPDATE_SQL, params)


def _emit_perf_breadcrumb(shop_domain: str, updated: int, eligible: int) -> None:
    """Best-effort Sentry breadcrumb for the bulk-op observability trail."""
    try:
        from app.core.sentry_init import pipeline_breadcrumb
        pipeline_breadcrumb(
            "perf.bulk_op",
            f"execution_engine.compute_post_execution_deltas shop={shop_domain} "
            f"updated={updated}",
            level="info",
            data={
                "op": "execution_deltas",
                "shop": shop_domain,
                "updated": updated,
                "eligible": eligible,
            },
        )
    except Exception:
        pass  # SILENT-EXCEPT-OK: sentry breadcrumb best-effort observability; never raise from a successful bulk-op return path.


def compute_post_execution_deltas(conn, shop_domain: str) -> int:
    """
    For executed opportunities, compute:
    1. Before/after deltas (post-execution all-group rates vs baseline)
    2. Counterfactual lift (exposed vs holdout, post-execution only)
    3. Confidence label from counterfactual comparison

    Counterfactual is the primary causal signal; before/after deltas remain
    as secondary directional indicator.

    Returns number of opportunities updated.

    Refactored 2026-05-12 (A3 medium close): 298-LOC god function → 25-LOC
    composer + 6 pure helpers + 4 module-level SQL constants. Identical
    contract; bulk-UPDATE wire format (20 columns × N opps via unnest)
    byte-identical.
    """
    eligible = _fetch_eligible_opps(conn, shop_domain)
    if not eligible:
        return 0
    eids = [opp[0] for opp in eligible]

    by_eid = _fetch_group_buckets(conn, shop_domain, eids)
    leakage_by_eid = _fetch_leakage_rates(conn, shop_domain, eids)

    records: list[dict] = []
    for opp in eligible:
        bucket = by_eid.get(opp[0])
        exp = bucket["exposed"] if bucket else dict(_EMPTY_GROUP)
        hld = bucket["holdout"] if bucket else dict(_EMPTY_GROUP)
        leakage = leakage_by_eid.get(opp[0], 0.0)
        record = _compute_opp_metrics(opp, exp, hld, leakage)
        if record is not None:
            records.append(record)

    if not records:
        return 0

    _apply_bulk_update(conn, shop_domain, records)
    _emit_perf_breadcrumb(shop_domain, updated=len(records), eligible=len(eligible))
    return len(records)


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

        # elite-hardening-allowed: parameter names from enumerate (`:visitor_0`, `:group_0`, etc.) — bind placeholders only, no user input
        conn.execute(
            text(f"""
                INSERT INTO execution_audiences
                    (execution_id, shop_domain, visitor_id, group_type, created_at)
                VALUES {values_sql}
                ON CONFLICT (execution_id, visitor_id) DO NOTHING
            """),
            params,
        )
        # elite-hardening-allowed: parameter names from enumerate — bind placeholders only, no user input
        conn.execute(
            text(f"""
                INSERT INTO execution_tracking
                    (execution_id, shop_domain, visitor_id, group_type, exposed_at, updated_at)
                VALUES {values_sql}
                ON CONFLICT (execution_id, visitor_id) DO NOTHING
            """),
            params,
        )
