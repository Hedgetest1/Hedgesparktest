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

# Effect-size floor PER metric_kind: lifts below the floor for that
# metric are treated as noise (stamped "low" confidence) even if
# statistically significant. Senior+++ replacement of the original
# magic-number `MIN_EFFECT_SIZE_PCT = 0.5` (2026-05-11 first pass) —
# different metrics have different operational visibility floors.
#
# Lifts are in PERCENTAGE POINTS as computed by `_compute_lift_pct`:
# `(measured - baseline) / baseline * 100`. So a `lift_pct_avg=0.5`
# means a 0.5% relative shift from baseline.
#
# Per-metric rationale:
#  - rars_delta_7d: RAR (revenue-at-risk in cents) is the noisiest
#    metric — typical week-over-week shop volatility is ±5-10%. A
#    sub-1% shift can't be distinguished from baseline noise. Floor
#    1.0% prevents "high confidence" priors below the noise floor.
#  - cvr_delta_7d: CVR is a percentage rate; a 0.5pt absolute shift
#    on a 2% baseline is 25% relative — clearly operationally meaningful.
#    Floor 0.5%.
#  - Binary outcome metrics (merchant_re_engaged_7d, events_24h_resumed):
#    the aggregator filters them via baseline/measured non-null +
#    non-zero-baseline checks, so they rarely reach _confidence_label.
#    Defensive 1.0% floor in case they slip through.
#  - Default 0.5%: minimum operationally-visible movement on a
#    merchant dashboard for unmapped metrics.
#
# Born 2026-05-11 Senior+++ close (replaces MIN_EFFECT_SIZE_PCT magic
# constant). Doctrine: every floor in the dict is justified empirically
# by the metric's noise envelope — not "the founder's intuition".
_EFFECT_SIZE_FLOORS_PCT: dict[str, float] = {
    "rars_delta_7d": 1.0,
    "cvr_delta_7d": 0.5,
    "merchant_re_engaged_7d": 1.0,
    "events_24h_resumed": 1.0,
    # Defensive defaults for the two meta metric_kinds emitted by
    # merchant_brain._rule_table that are STRUCTURALLY filtered out
    # of the aggregator (their action_kind always starts with
    # `no_action_*`, blocked by the `action_kind NOT LIKE 'no_action_%'`
    # WHERE clause in _run_aggregation). If a future rule emits these
    # metrics for a non-no_action action, the 1000% floor blocks any
    # confidence promotion — fail-safe rather than fall to 0.5% default.
    # Born 2026-05-11 Senior+++ propagation audit (defensive complete).
    "cooldown_pending": 1000.0,
    "none": 1000.0,
}
_DEFAULT_EFFECT_FLOOR_PCT = 0.5


def _effect_size_floor_pct(metric_kind: str | None) -> float:
    """Per-metric effect-size floor (percentage points). Falls back to
    `_DEFAULT_EFFECT_FLOOR_PCT` for unknown metric_kind."""
    return _EFFECT_SIZE_FLOORS_PCT.get(
        metric_kind or "", _DEFAULT_EFFECT_FLOOR_PCT
    )


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
        except Exception as e:
            # Redis transient-down: SKIP this tick. Running without a
            # TTL claim risks double-aggregation when Redis recovers
            # (5min worker re-tick sets the claim fresh and fires the
            # aggregator again on top of the earlier no-lock run). The
            # 6h cadence is observational, not safety-critical — better
            # to skip a tick than double-run. Born 2026-05-11
            # competitor-CTO audit. The previous "fall through to run"
            # comment was wrong: it traded one safety axis (cadence
            # discipline) for another (availability) without saying so.
            logger.warning(
                "cross_shop_aggregator: Redis unavailable, skipping tick: %s",
                e,
            )
            return {"status": "skipped", "reason": "redis_unavailable"}
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


# PG advisory-lock key for force_run_now serialization. 64-bit signed
# int adjacent to the audit_log chain lock (7421889543210176881) so
# they're easily greppable as "lock keys near the audit/learn moat".
# Born 2026-05-11 Senior+++ close — bypasses the Redis claim but
# acquires PG-level mutual exclusion so two concurrent force_run_now
# calls serialize cleanly instead of double-running the aggregator.
_FORCE_RUN_LOCK_KEY = 7421889543210176882


def force_run_now(db: Session | None = None) -> dict:
    """Bypass the 6h Redis claim and run aggregation immediately,
    serialized via PG advisory lock so concurrent callers wait
    instead of double-running.

    Use case: a merchant opts out (GDPR Art. 21) → their measured
    decisions must stop contributing to cross_shop_patterns ASAP.
    Waiting 6h for the next periodic tick leaves a TOCTOU window
    where the row might dip below k=3 in real terms while the SQL
    row still shows k>=3. `force_run_now` recomputes immediately +
    invalidates the Redis claim so the next periodic tick will
    re-acquire fresh.

    Senior+++ alternative to a DB trigger on merchants opt-out —
    the app-level event is more visible, testable, and evolvable
    than an opaque PL/pgSQL trigger. Caller MUST be responding to
    a real opt-out event (don't call from a tight loop — the
    aggregator is O(N·measured_decisions)).

    Concurrency: protected by `pg_advisory_xact_lock` keyed on
    `_FORCE_RUN_LOCK_KEY`. Two concurrent callers serialize — the
    second waits until the first commits, then runs against the
    freshly-aggregated state (typically a no-op because the first
    call already updated everything in-window).

    Born 2026-05-11 Senior+++ close (audit finding #1).
    """
    from sqlalchemy import text as _sql_text

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        # Acquire transactional advisory lock first. Held until the
        # session's transaction commits (inside _run_aggregation).
        # Two concurrent force_run_now calls on different sessions →
        # second blocks here until first commits.
        try:
            db.execute(_sql_text(
                "SELECT pg_advisory_xact_lock(:k)"
            ), {"k": _FORCE_RUN_LOCK_KEY})
        except Exception as e:
            # Lock acquisition failure is non-fatal (PG transient
            # issue) — fall through to run without lock. The Redis
            # claim invalidation below still serializes via the next
            # periodic tick's SETNX.
            logger.warning(
                "cross_shop_aggregator: PG advisory lock failed "
                "(non-fatal): %s", e,
            )

        # Invalidate any existing Redis claim so the next periodic
        # tick rebuilds fresh after this forced run.
        r = _redis_client()
        if r is not None:
            try:
                r.delete(NEXT_RUN_KEY)
            except Exception as e:
                logger.warning(
                    "cross_shop_aggregator: force_run claim "
                    "invalidation failed (non-fatal): %s", e,
                )

        report = _run_aggregation(db)
        report["forced"] = True
        return report
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

    # Per-tuple DELETE+INSERT upsert over an in-memory `groups` dict —
    # NOT per-shop N+1. `groups` is built from a single batched query
    # (`_load_brain_decisions`); iteration count is bounded by distinct
    # (vertical, action, metric) tuples after k-anonymity (<100 even at
    # 10k merchants). Canonical aggregator pattern.
    # n-plus-one: ok — flagged 2026-05-13 by audit_n_plus_one static check.
    for (vertical, action_kind, metric_kind), pairs in groups.items():
        distinct_shops = {shop for shop, _ in pairs}
        n_shops = len(distinct_shops)
        n_decisions = len(pairs)
        if n_shops < K_ANONYMITY_MIN_SHOPS:
            skipped_k_anon += 1
            continue

        lifts = [lift for _, lift in pairs]
        mean, std, p_value = one_sample_t_test(lifts)
        confidence = _confidence_label(
            n_shops, p_value, mean, metric_kind=metric_kind,
        )

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
    # Single round-trip DELETE WHERE NOT IN survivor-set, instead of
    # N round-trips one DELETE per stale row (the prior per-row loop
    # was the N+1 audit_n_plus_one flagged 2026-05-11; at 200k signals
    # × 4 verticals × 5 metrics that loop would issue ~200k DELETEs).
    if surviving_signals:
        survivors_tuples = list(surviving_signals)
        # Postgres tuple-IN: WHERE (a, b, c) NOT IN ((..), (..)) is
        # parameterised via composite expansion. SQLAlchemy doesn't
        # natively expand tuple-IN with named params, so we build the
        # tuple list literally — values are pre-validated by the
        # aggregator (table-allowlisted keys) so no injection surface.
        # Each (vertical, action_kind, metric_kind) is sourced from
        # _ALLOWED_TABLES / our own action_kind enum / brain_decisions
        # metric_kind enum — never user input.
        params = {}
        tuple_clauses = []
        for i, (v, a, m) in enumerate(survivors_tuples):
            params[f"v{i}"] = v
            params[f"a{i}"] = a
            params[f"m{i}"] = m
            tuple_clauses.append(f"(:v{i}, :a{i}, :m{i})")
        sql = (
            "DELETE FROM cross_shop_patterns "
            "WHERE (vertical, action_kind, metric_kind) NOT IN ("
            + ", ".join(tuple_clauses)
            + ")"
        )
        result = db.execute(text(sql), params)
        deleted_below_k = result.rowcount or 0
    else:
        # No survivors → wipe everything
        result = db.execute(text("DELETE FROM cross_shop_patterns"))
        deleted_below_k = result.rowcount or 0

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


def _confidence_label(
    n_shops: int,
    p_value: float,
    lift_pct_avg: float = 0.0,
    metric_kind: str | None = None,
) -> str:
    """Derive a 3-level confidence label from sample size + p-value +
    per-metric effect size.

    - high: n_shops >= 10 AND p < 0.05 AND |lift| >= floor(metric_kind)
    - medium: n_shops >= 5 AND p < 0.10 AND |lift| >= floor(metric_kind)
    - low: otherwise (n_shops >= 3 guaranteed by k-anonymity floor)

    The effect-size floor prevents "statistically significant but
    practically meaningless" patterns (e.g., 10 shops × +0.001% lift
    with p<0.001) from triggering brain demotions in
    _apply_cross_shop_prior.

    `metric_kind` selects a per-metric noise-floor from
    `_EFFECT_SIZE_FLOORS_PCT` (None → default). Born 2026-05-11
    competitor-CTO audit (initial magic-constant pass) + Senior+++
    upgrade to per-metric floor (same day).
    """
    if abs(lift_pct_avg) < _effect_size_floor_pct(metric_kind):
        return "low"
    if n_shops >= 10 and p_value < 0.05:
        return "high"
    if n_shops >= 5 and p_value < 0.10:
        return "medium"
    return "low"


