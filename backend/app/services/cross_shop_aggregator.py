"""cross_shop_aggregator.py — Sprint 3 #3 vertical-level pattern aggregator.

Reads measured outcomes from brain_decisions (Sprint 1 #5 outcome ledger
+ Sprint 1 #6 closed-loop trigger) and aggregates lifts per
(vertical, action_kind, metric_kind) signal. Writes the aggregate to
cross_shop_patterns where n_shops >= 3 (k-anonymity hard floor).

Network-effect deterministic: each new shop of vertical V inherits a
prior derived from N>=3 other shops of V that have already measured
this signal — beyond the static industry baselines wired by Sprint 2 #4.

GDPR-clean invariants (audited by scripts/audit_cross_shop_anonymity.py):
  - No shop_domain stored in cross_shop_patterns.
  - n_shops >= 3 enforced by SQL CHECK constraint + recomputed in code.
  - Recompute deletes rows that fall below 3 distinct shops.
  - Only outcome-evaluated decisions are eligible (outcome_status set).

Scheduling: gated by Redis key `hs:cross_shop_aggregator:next_run` with
a 6h TTL. The aggregation_worker (5min cycle) calls run_if_due() each
tick; the helper returns immediately unless the claim has expired.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.redis_client import _client as _redis_client
from app.core.stats import one_sample_t_test
from app.models.cross_shop_pattern import CrossShopPattern
from app.services.merchant_privacy import is_merchant_opted_out
from app.services.vertical_classifier import get_vertical


logger = logging.getLogger(__name__)


# k-anonymity hard floor. Mirrors the SQL CHECK constraint.
K_ANONYMITY_MIN_SHOPS = 3

# Decisions older than this are not aggregated (matches brain_decisions
# retention horizon — older rows may be pruned by retention_task).
LOOKBACK_DAYS = 90

# 6h cadence per founder roadmap memo.
RUN_INTERVAL_SECONDS = 6 * 60 * 60

# Redis claim key — TTL-gates the aggregator across the 5min worker cycle.
NEXT_RUN_KEY = "hs:cross_shop_aggregator:next_run"

# Only aggregate decisions where the brain actually dispatched a limb +
# the outcome was measured (not still pending, not evaluation-failed).
EVALUATED_STATUSES = ("effective", "ineffective", "neutral")


def run_if_due(db: Session | None = None) -> dict:
    """Aggregator entry-point for periodic workers.

    Returns a dict report so the worker can log a one-line summary:
      - skipped: claim held by another tick or not yet expired
      - completed: aggregation ran; report contains counts.
    """
    r = _redis_client()
    # SETNX claim — if key exists, another tick already ran in the last 6h.
    if r is not None:
        try:
            claimed = bool(r.set(
                NEXT_RUN_KEY, "1",
                ex=RUN_INTERVAL_SECONDS, nx=True,
            ))
        except Exception:
            # Redis transiently unavailable — fall through to run (lock-
            # free degradation, acceptable for a 6h periodic).
            claimed = True
        if not claimed:
            return {"status": "skipped", "reason": "claim_held"}

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        return _run_aggregation(db)
    finally:
        if own_session:
            db.close()


def _run_aggregation(db: Session) -> dict:
    """Full aggregation pass over brain_decisions in the lookback window.

    Read pattern (single SQL query): all evaluated decisions in the
    window with shop_domain + action_kind + expected_outcome_metric +
    baseline + measured. Then group in Python per (vertical, ak, mk)
    via the vertical_classifier (24h Redis cache → near-zero cost).

    Per group: compute lift_pct list, run one-sample t-test, derive
    confidence. Skip groups with n_shops < 3.

    Upsert: delete-then-insert per signal (simpler than ON CONFLICT
    given non-trivial constraint name); wrap in a single transaction.
    """
    horizon = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)

    # SQL-side aggregation: GROUP BY (shop_domain, action_kind, metric_kind)
    # returning an ARRAY of lift% values per group. At 10k merchants × ~4
    # action_kinds × ~5 metric_kinds = at most ~200k rows returned, each
    # carrying a small float array. Burst memory ~20MB vs the old per-row
    # fetch ~100MB at scale. lift% math moves into PG (computed inline +
    # zero baseline filtered SQL-side; mirrors _compute_lift_pct logic).
    rows = db.execute(text("""
        SELECT shop_domain, action_kind, expected_outcome_metric AS metric_kind,
               ARRAY_AGG(
                 ((measured_value - baseline_value) / baseline_value) * 100.0
               ) AS lift_array
        FROM brain_decisions
        WHERE outcome_status = ANY(:statuses)
          AND decision_at >= :horizon
          AND baseline_value IS NOT NULL
          AND measured_value IS NOT NULL
          AND expected_outcome_metric IS NOT NULL
          AND ABS(baseline_value) >= 1e-9
          AND action_kind NOT LIKE 'no_action_%'
        GROUP BY shop_domain, action_kind, expected_outcome_metric
    """), {
        "statuses": list(EVALUATED_STATUSES),
        "horizon": horizon,
    }).fetchall()

    # GDPR Art. 21 opt-out filter — shops that have objected to
    # automated processing must not contribute to cross-shop aggregates,
    # even if their brain_decisions still sit in the DB.
    distinct_shops = {r.shop_domain for r in rows}
    opted_out_shops = {
        s for s in distinct_shops if is_merchant_opted_out(s)
    }
    rows = [r for r in rows if r.shop_domain not in opted_out_shops]

    # vertical_classifier.get_vertical is Redis-cached 24h (~zero cost
    # after first call per shop). Single lookup per distinct shop.
    vertical_by_shop: dict[str, str] = {}
    for r in rows:
        if r.shop_domain not in vertical_by_shop:
            vertical_by_shop[r.shop_domain] = get_vertical(db, r.shop_domain)

    # Group: (vertical, action_kind, metric_kind) -> list of (shop, lift_pct).
    # Each SQL row contributes its lift_array to the (vertical, action,
    # metric) bucket, preserving the per-decision-sample resolution the
    # old in-memory groupby produced — without loading every decision row.
    groups: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    for r in rows:
        vertical = vertical_by_shop[r.shop_domain]
        if not vertical:
            continue
        key = (vertical, r.action_kind, r.metric_kind)
        for lift in (r.lift_array or []):
            if lift is None:
                continue
            groups.setdefault(key, []).append((r.shop_domain, float(lift)))

    # Aggregate
    written = 0
    skipped_k_anon = 0
    deleted_below_k = 0

    # First pass: track which signals we WILL write (n_shops >= 3).
    # Anything currently in cross_shop_patterns but not in this set
    # falls below k-anonymity and must be deleted.
    surviving_signals: set[tuple[str, str, str]] = set()

    for (vertical, action_kind, metric_kind), pairs in groups.items():
        distinct_shops = {shop for shop, _ in pairs}
        n_shops = len(distinct_shops)
        n_decisions = len(pairs)
        if n_shops < K_ANONYMITY_MIN_SHOPS:
            skipped_k_anon += 1
            continue

        lifts = [lift for _, lift in pairs]
        mean, std, p_value = one_sample_t_test(lifts)
        confidence = _confidence_label(n_shops, p_value)

        # Upsert via delete-then-insert in same transaction
        db.execute(text("""
            DELETE FROM cross_shop_patterns
            WHERE vertical = :v AND action_kind = :a AND metric_kind = :m
        """), {"v": vertical, "a": action_kind, "m": metric_kind})

        db.execute(text("""
            INSERT INTO cross_shop_patterns
              (vertical, action_kind, metric_kind,
               lift_pct_avg, lift_pct_std, n_shops, n_decisions,
               p_value, confidence, last_aggregated_at, created_at)
            VALUES
              (:v, :a, :m, :avg, :std, :ns, :nd, :p, :c, now(), now())
        """), {
            "v": vertical, "a": action_kind, "m": metric_kind,
            "avg": mean, "std": std if std > 0 else None,
            "ns": n_shops, "nd": n_decisions,
            "p": p_value if 0.0 < p_value < 1.0 else None,
            "c": confidence,
        })

        surviving_signals.add((vertical, action_kind, metric_kind))
        written += 1

    # Sweep: delete any stored signal that no longer has >=3 shops.
    existing = db.execute(text("""
        SELECT vertical, action_kind, metric_kind
        FROM cross_shop_patterns
    """)).fetchall()
    for e in existing:
        key = (e.vertical, e.action_kind, e.metric_kind)
        if key not in surviving_signals:
            db.execute(text("""
                DELETE FROM cross_shop_patterns
                WHERE vertical = :v
                  AND action_kind = :a
                  AND metric_kind = :m
            """), {"v": e.vertical, "a": e.action_kind, "m": e.metric_kind})
            deleted_below_k += 1

    db.commit()

    report = {
        "status": "completed",
        "rows_read": len(rows),
        "shops_classified": len(vertical_by_shop),
        "shops_excluded_opt_out": len(opted_out_shops),
        "groups": len(groups),
        "written": written,
        "skipped_k_anon": skipped_k_anon,
        "deleted_below_k": deleted_below_k,
    }
    logger.info("cross_shop_aggregator: %s", report)
    return report


def _compute_lift_pct(baseline: float, measured: float) -> float | None:
    """Lift% = (measured - baseline) / baseline * 100.

    Returns None if baseline is too close to zero to define a percentage
    (avoid division explosions where a 0.0001 baseline can produce
    millions-of-percent "lift"). The 1e-9 floor is well below any real
    metric we measure (rars in cents, cvr in decimals).
    """
    if baseline is None or measured is None:
        return None
    if abs(baseline) < 1e-9:
        return None
    return ((measured - baseline) / baseline) * 100.0


def _confidence_label(n_shops: int, p_value: float) -> str:
    """Derive a 3-level confidence label from sample size + p-value.

    - high: n_shops >= 10 AND p < 0.05
    - medium: n_shops >= 5 AND p < 0.10
    - low: otherwise (n_shops >= 3 guaranteed by k-anonymity floor)
    """
    if n_shops >= 10 and p_value < 0.05:
        return "high"
    if n_shops >= 5 and p_value < 0.10:
        return "medium"
    return "low"


def get_pattern_for_vertical(
    db: Session,
    vertical: str,
    action_kind: str,
    metric_kind: str,
) -> CrossShopPattern | None:
    """Read API for SIP / merchant_brain. Returns None when no aggregate
    yet meets k-anonymity for this signal."""
    return (
        db.query(CrossShopPattern)
        .filter(
            CrossShopPattern.vertical == vertical,
            CrossShopPattern.action_kind == action_kind,
            CrossShopPattern.metric_kind == metric_kind,
        )
        .one_or_none()
    )


def list_patterns_for_vertical(
    db: Session, vertical: str,
) -> list[CrossShopPattern]:
    """Read API for /pro/store-profile endpoint. Returns all aggregates
    for a vertical so the dashboard can surface what the shop is
    inheriting from its peers."""
    return (
        db.query(CrossShopPattern)
        .filter(CrossShopPattern.vertical == vertical)
        .order_by(CrossShopPattern.confidence.desc(), CrossShopPattern.n_shops.desc())
        .all()
    )
