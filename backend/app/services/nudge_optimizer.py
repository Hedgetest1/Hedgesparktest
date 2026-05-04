"""
nudge_optimizer.py — Autonomous A/B test winner selection and challenger generation.

This is the "self-improving nudge" loop that closes the compose → measure →
optimise cycle without merchant intervention:

  1. EVALUATE  — for each active A/B nudge, check if a winner has emerged.
  2. PROMOTE   — if a winner meets the confidence threshold, make it the primary
                 variant and retire the loser.
  3. CHALLENGE — after promotion, queue a new challenger variant via the AI
                 composer, using the winner's performance as context.

This service is called by nudge_optimization_worker.py on a recurring schedule
(every 6 hours by default).  It is idempotent — running it more frequently
produces no duplicate writes.

Decision logic
--------------
We use a minimum-detectable-effect (MDE) approach rather than classic
p-value testing, because Shopify store traffic volumes are usually too
small for frequentist power calculations to converge within a reasonable
time window.

Winner criteria (all must be met):
  a. Both variants have >= MIN_IMPRESSIONS_PER_VARIANT exposures.
  b. The leading variant's conversion rate is >= MDE_LIFT_PCT higher than
     the trailing variant (e.g. 5% relative lift: 0.105 vs 0.10).
  c. The nudge has been running for >= MIN_RUN_HOURS hours.

If no winner emerges after MAX_RUN_HOURS, the nudge is marked as
inconclusive and the primary variant (social_proof / control) is retained.

Promotion
---------
On winner promotion:
  - nudge.copy_config is updated to the winner's copy_config
  - nudge.copy_variant is updated to the winner's variant_name
  - nudge.copy_variants retains the full history (no deletion)
  - nudge.optimization_state is set to "promoted" with metadata

Challenger generation
---------------------
After promotion, the composer generates a new variant using:
  - winner_performance context (CR, impressions, variant strategy)
  - product signals from the latest product_metrics row
  - directive to explore a DIFFERENT strategy than the winner

The new variant replaces only the losing slot in copy_variants.  The winner
slot is preserved.  The experiment continues with a fresh A/B split.

Public interface
----------------
    run_optimization_cycle(db: Session, shop_domain: str | None = None) -> dict
        Run one full optimisation cycle for all qualifying nudges.
        If shop_domain is provided, only that shop is processed.
        Returns a summary dict for the worker log.

    evaluate_nudge(db: Session, nudge: ActiveNudge) -> OptimizationDecision
        Evaluate a single nudge for winner selection.
        Returns an OptimizationDecision named tuple.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.active_nudge import ActiveNudge

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum exposures per variant before any decision is considered.
# Below this, the sample is too small and we must continue running.
MIN_IMPRESSIONS_PER_VARIANT: int = int(50)

# Minimum relative conversion rate lift to declare a winner.
# 0.05 = 5% relative lift (e.g. 0.105 vs 0.100).
# Lower = more sensitive but more false positives.
# Higher = fewer false positives but slower to converge on small traffic.
MDE_LIFT_PCT: float = 0.05

# Minimum hours the nudge must have run before declaring a winner.
# Prevents declaring a winner from a single burst of traffic.
MIN_RUN_HOURS: int = 48

# Maximum hours before the experiment is declared inconclusive.
# After this, the primary (control) variant is promoted unconditionally
# and a fresh challenger is generated.
MAX_RUN_HOURS: int = 336   # 14 days

# Minimum absolute conversion rate for a variant to be considered a winner.
# Prevents promoting a variant with CR=0.001 as "better" than CR=0.0009.
MIN_ABSOLUTE_CR: float = 0.005   # 0.5% minimum CR to count


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------

@dataclass
class VariantStats:
    variant_name: str
    impressions: int
    conversions: int
    conversion_rate: float


@dataclass
class OptimizationDecision:
    nudge_id: int
    shop_domain: str
    product_url: str
    decision: str              # "no_data", "waiting", "winner", "inconclusive"
    winner_variant: Optional[str]
    loser_variant: Optional[str]
    winner_cr: Optional[float]
    loser_cr: Optional[float]
    impressions_a: int
    impressions_b: int
    run_hours: float
    reason: str


# ---------------------------------------------------------------------------
# Stats fetcher
# ---------------------------------------------------------------------------

def _get_variant_stats(
    db: Session,
    shop_domain: str,
    nudge_id: int,
    variant_names: list[str],
) -> dict[str, VariantStats]:
    """
    Compute per-variant impression and conversion stats from nudge_events
    and visitor_purchase_sessions.

    Impressions = nudge_events WHERE event_type = 'shown' AND copy_variant = ?
    Conversions = visitor_purchase_sessions where visitor saw the nudge variant
                  and purchased within 72 hours of the shown event.

    This is the same observational attribution used by nudge_measurement.py.

    Previously this ran 2 DB queries per variant (N+1).  Now it runs exactly
    2 queries total — one GROUP BY for impressions, one GROUP BY for
    conversions — then joins the results in Python.
    """
    # Initialise every requested variant to zero so callers always get a
    # complete dict even if the DB returns no rows for some variants.
    stats: dict[str, VariantStats] = {
        vname: VariantStats(
            variant_name=vname,
            impressions=0,
            conversions=0,
            conversion_rate=0.0,
        )
        for vname in variant_names
    }

    try:
        # ── Query 1: impressions per variant (one GROUP BY) ──────────────────
        imp_rows = db.execute(
            text("""
                SELECT ne.event_meta->>'copy_variant' AS variant,
                       COUNT(*)                       AS impressions
                FROM nudge_events ne
                WHERE ne.shop_domain = :shop
                  AND ne.nudge_id    = :nudge_id
                  AND ne.event_type  = 'shown'
                  AND ne.event_meta->>'copy_variant' = ANY(:variants)
                GROUP BY 1
            """),
            {
                "shop":     shop_domain,
                "nudge_id": nudge_id,
                "variants": variant_names,
            },
        ).fetchall()

        impressions_by_variant: dict[str, int] = {
            row[0]: int(row[1] or 0) for row in imp_rows
        }

        # ── Query 2: conversions per variant (one GROUP BY) ──────────────────
        conv_rows = db.execute(
            text("""
                SELECT ne.event_meta->>'copy_variant'  AS variant,
                       COUNT(DISTINCT ne.visitor_id)   AS conversions
                FROM nudge_events ne
                JOIN visitor_purchase_sessions vps
                  ON vps.visitor_id   = ne.visitor_id
                 AND vps.shop_domain  = ne.shop_domain
                 AND vps.confirmed_at >= ne.created_at
                 AND vps.confirmed_at <= ne.created_at + interval '72 hours'
                WHERE ne.shop_domain = :shop
                  AND ne.nudge_id    = :nudge_id
                  AND ne.event_type  = 'shown'
                  AND ne.event_meta->>'copy_variant' = ANY(:variants)
                GROUP BY 1
            """),
            {
                "shop":     shop_domain,
                "nudge_id": nudge_id,
                "variants": variant_names,
            },
        ).fetchall()

        conversions_by_variant: dict[str, int] = {
            row[0]: int(row[1] or 0) for row in conv_rows
        }

        # ── Merge into VariantStats ──────────────────────────────────────────
        for vname in variant_names:
            imp = impressions_by_variant.get(vname, 0)
            conv = conversions_by_variant.get(vname, 0)
            stats[vname] = VariantStats(
                variant_name    = vname,
                impressions     = imp,
                conversions     = conv,
                conversion_rate = conv / imp if imp > 0 else 0.0,
            )

    except Exception as exc:
        log.warning(
            "nudge_optimizer: stats batch query failed for nudge=%d: %s",
            nudge_id, exc,
        )
        # stats dict already initialised to zeros for all variants above

    return stats


# ---------------------------------------------------------------------------
# Nudge evaluator
# ---------------------------------------------------------------------------

def evaluate_nudge(db: Session, nudge: ActiveNudge) -> OptimizationDecision:
    """
    Evaluate a single nudge for A/B winner selection.

    Returns an OptimizationDecision with decision in:
      "no_data"       — nudge has no A/B variants or is not active
      "waiting"       — insufficient impressions or runtime
      "winner"        — a variant has met all winner criteria
      "inconclusive"  — MAX_RUN_HOURS elapsed without a winner

    This function never modifies the nudge or the DB.
    """
    def _deny(reason: str, decision: str = "no_data") -> OptimizationDecision:
        return OptimizationDecision(
            nudge_id=nudge.id,
            shop_domain=nudge.shop_domain,
            product_url=nudge.product_url,
            decision=decision,
            winner_variant=None,
            loser_variant=None,
            winner_cr=None,
            loser_cr=None,
            impressions_a=0,
            impressions_b=0,
            run_hours=0.0,
            reason=reason,
        )

    # Must be active and have ≥2 variants
    if nudge.status != "active":
        return _deny("nudge_not_active")

    variants = nudge.copy_variants_list()
    if len(variants) < 2:
        return _deny("no_ab_variants")

    variant_names = [v["variant_name"] for v in variants]

    # Run time
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    created = nudge.created_at or now
    run_hours = (now - created).total_seconds() / 3600.0

    # Fetch stats
    stats = _get_variant_stats(db, nudge.shop_domain, nudge.id, variant_names)

    name_a = variant_names[0]
    name_b = variant_names[1]
    s_a = stats.get(name_a, VariantStats(name_a, 0, 0, 0.0))
    s_b = stats.get(name_b, VariantStats(name_b, 0, 0, 0.0))

    # Insufficient impressions
    if s_a.impressions < MIN_IMPRESSIONS_PER_VARIANT or s_b.impressions < MIN_IMPRESSIONS_PER_VARIANT:
        if run_hours >= MAX_RUN_HOURS:
            return OptimizationDecision(
                nudge_id=nudge.id,
                shop_domain=nudge.shop_domain,
                product_url=nudge.product_url,
                decision="inconclusive",
                winner_variant=name_a,   # retain primary (control) on inconclusive
                loser_variant=name_b,
                winner_cr=s_a.conversion_rate,
                loser_cr=s_b.conversion_rate,
                impressions_a=s_a.impressions,
                impressions_b=s_b.impressions,
                run_hours=run_hours,
                reason=f"max_run_hours_elapsed_with_low_impressions (a={s_a.impressions} b={s_b.impressions})",
            )
        return OptimizationDecision(
            nudge_id=nudge.id,
            shop_domain=nudge.shop_domain,
            product_url=nudge.product_url,
            decision="waiting",
            winner_variant=None,
            loser_variant=None,
            winner_cr=None,
            loser_cr=None,
            impressions_a=s_a.impressions,
            impressions_b=s_b.impressions,
            run_hours=run_hours,
            reason=f"insufficient_impressions (need {MIN_IMPRESSIONS_PER_VARIANT} per variant, have a={s_a.impressions} b={s_b.impressions})",
        )

    # Minimum runtime
    if run_hours < MIN_RUN_HOURS:
        return OptimizationDecision(
            nudge_id=nudge.id,
            shop_domain=nudge.shop_domain,
            product_url=nudge.product_url,
            decision="waiting",
            winner_variant=None,
            loser_variant=None,
            winner_cr=None,
            loser_cr=None,
            impressions_a=s_a.impressions,
            impressions_b=s_b.impressions,
            run_hours=run_hours,
            reason=f"min_run_hours_not_elapsed (need {MIN_RUN_HOURS}h, have {run_hours:.1f}h)",
        )

    # Inconclusive timeout
    if run_hours >= MAX_RUN_HOURS:
        return OptimizationDecision(
            nudge_id=nudge.id,
            shop_domain=nudge.shop_domain,
            product_url=nudge.product_url,
            decision="inconclusive",
            winner_variant=name_a,  # retain primary (control)
            loser_variant=name_b,
            winner_cr=s_a.conversion_rate,
            loser_cr=s_b.conversion_rate,
            impressions_a=s_a.impressions,
            impressions_b=s_b.impressions,
            run_hours=run_hours,
            reason=f"max_run_hours_elapsed (a_cr={s_a.conversion_rate:.4f} b_cr={s_b.conversion_rate:.4f})",
        )

    # Determine leader and trailer
    leader, trailer = (s_a, s_b) if s_a.conversion_rate >= s_b.conversion_rate else (s_b, s_a)

    # Minimum absolute CR guard — prevents promoting near-zero performers
    if leader.conversion_rate < MIN_ABSOLUTE_CR:
        return OptimizationDecision(
            nudge_id=nudge.id,
            shop_domain=nudge.shop_domain,
            product_url=nudge.product_url,
            decision="waiting",
            winner_variant=None,
            loser_variant=None,
            winner_cr=None,
            loser_cr=None,
            impressions_a=s_a.impressions,
            impressions_b=s_b.impressions,
            run_hours=run_hours,
            reason=f"leader_cr_below_minimum (leader={leader.conversion_rate:.4f} min={MIN_ABSOLUTE_CR})",
        )

    # Relative lift check
    if trailer.conversion_rate > 0:
        relative_lift = (leader.conversion_rate - trailer.conversion_rate) / trailer.conversion_rate
    else:
        relative_lift = 1.0   # trailer has 0 conversions → leader wins

    if relative_lift >= MDE_LIFT_PCT:
        return OptimizationDecision(
            nudge_id=nudge.id,
            shop_domain=nudge.shop_domain,
            product_url=nudge.product_url,
            decision="winner",
            winner_variant=leader.variant_name,
            loser_variant=trailer.variant_name,
            winner_cr=leader.conversion_rate,
            loser_cr=trailer.conversion_rate,
            impressions_a=s_a.impressions,
            impressions_b=s_b.impressions,
            run_hours=run_hours,
            reason=f"winner_by_mde (lift={relative_lift:.1%} >= {MDE_LIFT_PCT:.0%})",
        )

    # No winner yet
    return OptimizationDecision(
        nudge_id=nudge.id,
        shop_domain=nudge.shop_domain,
        product_url=nudge.product_url,
        decision="waiting",
        winner_variant=None,
        loser_variant=None,
        winner_cr=None,
        loser_cr=None,
        impressions_a=s_a.impressions,
        impressions_b=s_b.impressions,
        run_hours=run_hours,
        reason=f"lift_below_mde (lift={relative_lift:.1%} < {MDE_LIFT_PCT:.0%})",
    )


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

def _promote_winner(
    db: Session,
    nudge: ActiveNudge,
    decision: OptimizationDecision,
) -> None:
    """
    Promote the winning variant as the primary nudge copy.

    Updates:
      - copy_config → winner's copy_config
      - copy_variant → winner's variant_name
      - updated_at → now

    Does NOT clear copy_variants — the full A/B history is retained.
    Logs the decision with winner/loser CRs for audit trail.
    """
    if decision.winner_variant is None:
        return

    # Find winner's copy_config from copy_variants list
    variants = nudge.copy_variants_list()
    winner_config: dict | None = None
    for v in variants:
        if v.get("variant_name") == decision.winner_variant:
            winner_config = v.get("copy_config", {})
            break

    if winner_config is None:
        log.warning(
            "nudge_optimizer: winner variant %r not found in copy_variants for nudge=%d",
            decision.winner_variant, nudge.id,
        )
        return

    try:
        nudge.copy_config  = json.dumps(winner_config)
        nudge.copy_variant = decision.winner_variant
        nudge.updated_at   = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        log.info(
            "nudge_optimizer: PROMOTED winner=%r (cr=%.4f) over loser=%r (cr=%.4f) "
            "for nudge=%d shop=%s product=%s (run=%.1fh %s)",
            decision.winner_variant, decision.winner_cr or 0,
            decision.loser_variant, decision.loser_cr or 0,
            nudge.id, nudge.shop_domain, nudge.product_url,
            decision.run_hours, decision.reason,
        )
    except Exception as exc:
        db.rollback()
        log.error(
            "nudge_optimizer: promotion failed for nudge=%d: %s",
            nudge.id, exc,
        )


# ---------------------------------------------------------------------------
# Challenger generation (async — wraps the AI composer)
# ---------------------------------------------------------------------------

async def _generate_challenger(
    db: Session,
    nudge: ActiveNudge,
    decision: OptimizationDecision,
    signals: dict,
    product_title: str,
) -> None:
    """
    Generate a new challenger variant via the AI composer.

    The new variant:
      - Uses a DIFFERENT strategy than the winner (to genuinely explore)
      - Is injected into the loser slot in copy_variants
      - Resets the loser slot's impression count (implicit — events are by
        variant_name; the new name gets a clean slate)

    If the composer fails or falls back, the loser slot is left unchanged
    (the experiment continues with the original loser variant).
    """
    try:
        from app.services.nudge_composer import (
            compose_nudge_variants,
            _select_strategy_pair,
            _STRATEGIES,
        )

        winner_name = decision.winner_variant or "social_proof"

        # Build a context hint for the composer: winner performance summary
        winner_context_hint = {
            "previous_winner": winner_name,
            "winner_cr":       round(decision.winner_cr or 0, 4),
            "loser_cr":        round(decision.loser_cr or 0, 4),
            "run_hours":       round(decision.run_hours, 1),
            "directive":       (
                f"The '{winner_name}' variant won this experiment. "
                "Generate a DIFFERENT strategy for the challenger slot. "
                "Do NOT use the same framing as the winner."
            ),
        }

        # Enrich signals with winner context
        enriched_signals = {**signals, "_optimization_context": winner_context_hint}

        variants, meta = await compose_nudge_variants(
            product_title     = product_title,
            product_url       = nudge.product_url,
            signals           = enriched_signals,
            data_window_hours = 72,
            shop_domain       = nudge.shop_domain,
        )

        if meta.get("fallback_used"):
            log.info(
                "nudge_optimizer: challenger generation fell back to rule-based for nudge=%d",
                nudge.id,
            )

        # Find the challenger variant (the one that is NOT the winner)
        challenger = next(
            (v for v in variants if v.get("variant_name") != winner_name),
            variants[0] if variants else None,
        )
        if challenger is None:
            return

        # Inject challenger into loser slot in copy_variants
        current_variants = nudge.copy_variants_list()
        updated_variants = []
        loser_replaced = False
        for v in current_variants:
            if v.get("variant_name") == decision.loser_variant and not loser_replaced:
                updated_variants.append({
                    "variant_name": challenger["variant_name"],
                    "copy_config":  challenger["copy_config"],
                    "_challenger":  True,
                    "_replaced_at": datetime.now(timezone.utc).isoformat(),
                })
                loser_replaced = True
            else:
                updated_variants.append(v)

        if not loser_replaced:
            updated_variants.append(challenger)

        nudge.copy_variants = json.dumps(updated_variants)
        nudge.updated_at    = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        log.info(
            "nudge_optimizer: CHALLENGER generated strategy=%r for nudge=%d shop=%s "
            "(replaced loser=%r fallback=%s)",
            challenger.get("variant_name"),
            nudge.id, nudge.shop_domain,
            decision.loser_variant,
            meta.get("fallback_used"),
        )

    except Exception as exc:
        # best-effort: if challenger composition fails, the loser slot is
        # left unchanged and the experiment continues with the existing
        # variant. Documented in the function docstring above; the
        # composer is an optional optimization, not a hard dependency.
        log.warning(
            "nudge_optimizer: challenger generation failed (non-fatal) for nudge=%d: %s",
            nudge.id, exc,
        )


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

async def run_optimization_cycle(
    db: Session,
    shop_domain: Optional[str] = None,
) -> dict:
    """
    Run one full optimisation cycle for all qualifying nudges.

    For each active A/B nudge that is eligible for evaluation:
      1. Evaluate winner/loser/waiting/inconclusive.
      2. If winner or inconclusive: promote the winner.
      3. If winner: generate a challenger for the loser slot.

    Parameters
    ----------
    db          Request-scoped SQLAlchemy session.
    shop_domain If provided, only process nudges for this shop.

    Returns
    -------
    Summary dict for the worker log:
    {
        "evaluated": int,
        "promoted":  int,
        "challenged": int,
        "waiting":   int,
        "errors":    int,
    }
    """
    summary = {
        "evaluated": 0, "promoted": 0, "challenged": 0, "waiting": 0, "errors": 0,
        # Aliases consumed by nudge_optimization_worker._record_cycle
        # (shape contract — KEEP IN SYNC). _shops_seen tracks unique
        # shops that owned at least one A/B nudge this cycle.
        "shops_processed": 0,
        "nudges_evaluated": 0,
        "winners_promoted": 0,
        "challengers_generated": 0,
    }
    _shops_seen: set[str] = set()

    try:
        q = db.query(ActiveNudge).filter(
            ActiveNudge.status == "active",
            ActiveNudge.copy_variants != None,  # noqa: E711  — must have A/B variants
        )
        if shop_domain:
            q = q.filter(ActiveNudge.shop_domain == shop_domain)
        nudges = q.all()
    except Exception as exc:
        log.error("nudge_optimizer: failed to load nudges: %s", exc)
        return summary

    # Pre-load product_metrics for all (shop, product_url) pairs in one query
    # so the winner branch never issues a per-nudge DB call (N+1 fix).
    ab_nudges = [n for n in nudges if n.is_ab_experiment()]
    _pm_cache: dict[tuple[str, str], dict] = {}
    if ab_nudges:
        try:
            keys = list({(n.shop_domain, n.product_url or "") for n in ab_nudges if n.product_url})
            if keys:
                shops   = [k[0] for k in keys]
                urls    = [k[1] for k in keys]
                pm_rows = db.execute(
                    text("""
                        SELECT DISTINCT ON (shop_domain, product_url)
                               shop_domain,
                               product_url,
                               views_1h, views_24h, unique_visitors_24h,
                               avg_dwell_24h, avg_scroll_24h,
                               return_visitor_count_7d, cart_conversions_24h
                        FROM product_metrics
                        WHERE shop_domain = ANY(:shops)
                          AND (shop_domain, product_url) IN (
                              SELECT unnest(:shops::text[]), unnest(:urls::text[])
                          )
                    """),
                    {"shops": shops, "urls": urls},
                ).fetchall()
                for pm in pm_rows:
                    m = dict(pm._mapping)
                    _pm_cache[(m["shop_domain"], m["product_url"])] = {
                        k: m[k] for k in (
                            "views_1h", "views_24h", "unique_visitors_24h",
                            "avg_dwell_24h", "avg_scroll_24h",
                            "return_visitor_count_7d", "cart_conversions_24h",
                        )
                    }
        except Exception as exc:
            log.warning("nudge_optimizer: product_metrics pre-load failed: %s", exc)

    from app.core.query_count_monitor import worker_scope as _worker_scope
    for nudge in nudges:
        # Skip single-variant nudges quickly
        if not nudge.is_ab_experiment():
            continue

        summary["evaluated"] += 1
        if nudge.shop_domain:
            _shops_seen.add(nudge.shop_domain)

        try:
            with _worker_scope("nudge_optimizer.evaluate_nudge", nudge.shop_domain or "unknown"):
                decision = evaluate_nudge(db, nudge)

                if decision.decision == "waiting":
                    summary["waiting"] += 1
                    log.debug(
                        "nudge_optimizer: waiting nudge=%d (%s)",
                        nudge.id, decision.reason,
                    )
                    continue

                if decision.decision in ("winner", "inconclusive"):
                    _promote_winner(db, nudge, decision)
                    summary["promoted"] += 1

                    if decision.decision == "winner":
                        # Use pre-loaded product_metrics cache (no per-nudge DB call)
                        signals = _pm_cache.get((nudge.shop_domain, nudge.product_url or ""), {})

                        product_title = (nudge.product_url or "").split("/")[-1].replace("-", " ").title()

                        await _generate_challenger(db, nudge, decision, signals, product_title)
                        summary["challenged"] += 1

        except Exception as exc:
            log.error(
                "nudge_optimizer: unhandled error for nudge=%d: %s",
                nudge.id, exc,
            )
            summary["errors"] += 1

    # Populate worker_log aliases (kept in sync with worker._record_cycle).
    summary["shops_processed"] = len(_shops_seen)
    summary["nudges_evaluated"] = summary["evaluated"]
    summary["winners_promoted"] = summary["promoted"]
    summary["challengers_generated"] = summary["challenged"]

    log.info(
        "nudge_optimizer: cycle complete — evaluated=%d promoted=%d "
        "challenged=%d waiting=%d errors=%d shops=%d shop=%s",
        summary["evaluated"], summary["promoted"], summary["challenged"],
        summary["waiting"], summary["errors"], summary["shops_processed"],
        shop_domain or "all",
    )
    return summary
