"""Sprint 3 #3 — cross-shop pattern aggregator tests.

Covers:
  - one_sample_t_test correctness (mean/std/p_value edge cases)
  - _compute_lift_pct (None, near-zero baseline, normal, negative)
  - _confidence_label boundary buckets
  - k-anonymity floor (n_shops < 3 → no row, n_shops >= 3 → row written)
  - delete-below-k sweep (row exists but recompute drops below 3)
  - filter: no_action_* decisions and unmeasured (baseline/measured null)
  - GDPR invariant: no shop_domain ever lands in cross_shop_patterns
  - _read_cross_shop_priors returns empty list for unknown vertical
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.stats import one_sample_t_test
from app.services import cross_shop_aggregator as csa


# ---------------------------------------------------------------------------
# stats helpers — pure functions, no DB
# ---------------------------------------------------------------------------


def test_one_sample_t_test_zero_mean_high_p():
    """Sample centered at 0 → p_value ~ 1.0 (cannot reject H0)."""
    mean, std, p = one_sample_t_test([-1.0, 0.0, 1.0])
    assert mean == 0.0
    assert std > 0
    assert p > 0.9


def test_one_sample_t_test_strong_signal_low_p():
    """Tight cluster around +5 → p_value << 0.05 (reject H0=0)."""
    mean, std, p = one_sample_t_test([4.9, 5.0, 5.1, 5.0, 4.95])
    assert 4.9 < mean < 5.1
    assert p < 0.001


def test_one_sample_t_test_single_value_returns_1():
    """n<2 → cannot compute t-statistic; p=1.0 by convention."""
    mean, std, p = one_sample_t_test([3.0])
    assert mean == 3.0
    assert std == 0.0
    assert p == 1.0


def test_one_sample_t_test_zero_variance_nonzero_mean():
    """Identical constant samples != 0 → infinitely-significant by t-stat.
    Our helper clamps to p=0.0 (caller should still respect n_shops>=3)."""
    mean, std, p = one_sample_t_test([7.0, 7.0, 7.0])
    assert mean == 7.0
    assert std == 0.0
    assert p == 0.0


# ---------------------------------------------------------------------------
# _compute_lift_pct
# ---------------------------------------------------------------------------


def test_compute_lift_pct_positive():
    """baseline 100, measured 110 → +10% lift."""
    assert csa._compute_lift_pct(100.0, 110.0) == pytest.approx(10.0)


def test_compute_lift_pct_negative():
    """baseline 100, measured 80 → -20% lift."""
    assert csa._compute_lift_pct(100.0, 80.0) == pytest.approx(-20.0)


def test_compute_lift_pct_none_inputs():
    assert csa._compute_lift_pct(None, 10.0) is None
    assert csa._compute_lift_pct(10.0, None) is None


def test_compute_lift_pct_near_zero_baseline():
    """A 1e-12 baseline would produce a millions-of-percent lift — guarded."""
    assert csa._compute_lift_pct(1e-12, 1.0) is None
    assert csa._compute_lift_pct(0.0, 1.0) is None


# ---------------------------------------------------------------------------
# _confidence_label
# ---------------------------------------------------------------------------


def test_confidence_high_requires_10_shops_and_low_p_and_effect_size():
    # lift_pct_avg above MIN_EFFECT_SIZE_PCT (0.5%) is required
    assert csa._confidence_label(10, 0.01, lift_pct_avg=2.0) == "high"
    assert csa._confidence_label(20, 0.04, lift_pct_avg=-3.5) == "high"


def test_confidence_medium_requires_5_shops_and_p_under_01_and_effect_size():
    assert csa._confidence_label(5, 0.05, lift_pct_avg=1.0) == "medium"
    assert csa._confidence_label(9, 0.09, lift_pct_avg=-2.0) == "medium"


def test_confidence_low_floor():
    """n_shops=3 (k-anonymity floor) without strong signal → low."""
    assert csa._confidence_label(3, 0.50, lift_pct_avg=5.0) == "low"
    assert csa._confidence_label(4, 0.20, lift_pct_avg=5.0) == "low"


def test_confidence_high_demotes_to_low_when_p_too_high():
    """10 shops but p=0.40 → high tier rejected, medium also rejected
    (medium requires p<0.10) → falls through to low."""
    assert csa._confidence_label(10, 0.40, lift_pct_avg=5.0) == "low"


def test_confidence_demoted_to_low_below_effect_size_floor(monkeypatch):
    """Born 2026-05-11 competitor-CTO audit: statistically-significant
    micro-lifts (e.g., 10 shops × +0.001% lift with p<0.001) are
    demoted to "low" confidence even when p_value and n_shops would
    qualify for "high". Otherwise merchant_brain._apply_cross_shop_prior
    would demote a paying merchant's action based on noise.
    """
    # +0.001% lift with strong stats → demoted to low (effect too small)
    assert csa._confidence_label(10, 0.001, lift_pct_avg=0.001) == "low"
    # -0.4% lift (just below 0.5% floor) → demoted to low
    assert csa._confidence_label(20, 0.001, lift_pct_avg=-0.4) == "low"
    # Exact floor — 0.5% qualifies
    assert csa._confidence_label(10, 0.01, lift_pct_avg=0.5) == "high"
    assert csa._confidence_label(10, 0.01, lift_pct_avg=-0.5) == "high"

    # Default lift_pct_avg=0.0 → always low (zero-effect)
    assert csa._confidence_label(10, 0.001) == "low"


# ---------------------------------------------------------------------------
# Aggregator end-to-end against the test DB
# ---------------------------------------------------------------------------


def _insert_decision(
    db, shop_domain: str, action_kind: str, metric_kind: str,
    baseline: float, measured: float, status: str = "effective",
):
    db.execute(text("""
        INSERT INTO brain_decisions
          (decision_at, shop_domain, action_kind,
           expected_outcome_metric, outcome_window_hours,
           baseline_value, measured_value, outcome_status,
           outcome_evaluated_at)
        VALUES
          (now() - interval '1 day', :s, :a, :m, 24, :b, :v, :st, now())
    """), {"s": shop_domain, "a": action_kind, "m": metric_kind,
           "b": baseline, "v": measured, "st": status})


def _patch_vertical(monkeypatch, mapping: dict[str, str]):
    """Override vertical_classifier.get_vertical so tests don't depend on
    shop_categories data being populated for synthetic shops."""
    def fake(db, shop_domain):
        return mapping.get(shop_domain, "")
    monkeypatch.setattr(csa, "get_vertical", fake)


def test_aggregator_skips_k_anon_under_3_shops(db, monkeypatch):
    """2 shops with the same signal → groups=1, written=0, skipped_k_anon=1."""
    _patch_vertical(monkeypatch, {
        "s1.myshopify.com": "apparel",
        "s2.myshopify.com": "apparel",
    })
    _insert_decision(db, "s1.myshopify.com",
                     "retention_outreach_email", "rars_delta_7d", 100.0, 110.0)
    _insert_decision(db, "s2.myshopify.com",
                     "retention_outreach_email", "rars_delta_7d", 100.0, 108.0)
    report = csa._run_aggregation(db)
    assert report["groups"] == 1
    assert report["written"] == 0
    assert report["skipped_k_anon"] == 1
    row = db.execute(text("SELECT COUNT(*) FROM cross_shop_patterns")).scalar()
    assert row == 0


def test_aggregator_writes_when_3_shops_reached(db, monkeypatch):
    """3 distinct shops with same signal → row written, k-anon satisfied."""
    _patch_vertical(monkeypatch, {
        f"s{i}.myshopify.com": "apparel" for i in range(1, 4)
    })
    for shop, measured in [
        ("s1.myshopify.com", 110.0),
        ("s2.myshopify.com", 108.0),
        ("s3.myshopify.com", 112.0),
    ]:
        _insert_decision(db, shop, "retention_outreach_email",
                         "rars_delta_7d", 100.0, measured)
    report = csa._run_aggregation(db)
    assert report["written"] == 1
    row = db.execute(text("""
        SELECT vertical, action_kind, metric_kind,
               lift_pct_avg, n_shops, n_decisions, confidence
        FROM cross_shop_patterns
    """)).fetchone()
    assert row.vertical == "apparel"
    assert row.action_kind == "retention_outreach_email"
    assert row.metric_kind == "rars_delta_7d"
    assert row.n_shops == 3
    assert row.n_decisions == 3
    assert 8.0 < row.lift_pct_avg < 12.0  # mean of 10, 8, 12 = 10
    assert row.confidence == "low"  # n=3, low confidence floor


def test_aggregator_filters_no_action_decisions(db, monkeypatch):
    """no_action_* decisions must be filtered out by the SQL WHERE clause."""
    _patch_vertical(monkeypatch, {
        f"s{i}.myshopify.com": "apparel" for i in range(1, 4)
    })
    for i in range(1, 4):
        # 3 shops with no_action — should NOT aggregate
        _insert_decision(db, f"s{i}.myshopify.com",
                         "no_action_cooldown", "cooldown_pending",
                         100.0, 100.0)
    report = csa._run_aggregation(db)
    assert report["groups"] == 0
    assert report["written"] == 0


def test_aggregator_filters_missing_baseline(db, monkeypatch):
    """Decisions without baseline OR measured are skipped."""
    _patch_vertical(monkeypatch, {
        f"s{i}.myshopify.com": "apparel" for i in range(1, 4)
    })
    # 2 valid + 1 with null baseline (NOT a side-effect of partial measurement)
    _insert_decision(db, "s1.myshopify.com",
                     "retention_outreach_email", "rars_delta_7d", 100.0, 110.0)
    _insert_decision(db, "s2.myshopify.com",
                     "retention_outreach_email", "rars_delta_7d", 100.0, 108.0)
    db.execute(text("""
        INSERT INTO brain_decisions
          (decision_at, shop_domain, action_kind,
           expected_outcome_metric, outcome_window_hours,
           outcome_status, outcome_evaluated_at)
        VALUES (now(), 's3.myshopify.com', 'retention_outreach_email',
                'rars_delta_7d', 24, 'effective', now())
    """))
    report = csa._run_aggregation(db)
    # Only 2 valid rows → still under k-anonymity, no row written
    assert report["written"] == 0


def test_aggregator_deletes_when_falls_below_k(db, monkeypatch):
    """Existing aggregate row → recompute drops below 3 → row deleted."""
    # Seed an existing row manually
    db.execute(text("""
        INSERT INTO cross_shop_patterns
          (vertical, action_kind, metric_kind, lift_pct_avg,
           n_shops, n_decisions, confidence)
        VALUES ('apparel', 'retention_outreach_email', 'rars_delta_7d',
                10.0, 4, 4, 'low')
    """))
    # New recompute only has 2 shops with this signal
    _patch_vertical(monkeypatch, {
        "s1.myshopify.com": "apparel",
        "s2.myshopify.com": "apparel",
    })
    _insert_decision(db, "s1.myshopify.com",
                     "retention_outreach_email", "rars_delta_7d", 100.0, 110.0)
    _insert_decision(db, "s2.myshopify.com",
                     "retention_outreach_email", "rars_delta_7d", 100.0, 108.0)
    report = csa._run_aggregation(db)
    assert report["deleted_below_k"] == 1
    row = db.execute(text("""
        SELECT COUNT(*) FROM cross_shop_patterns
    """)).scalar()
    assert row == 0


def test_aggregator_excludes_opt_out_shops(db, monkeypatch):
    """GDPR Art. 21 — shops with opt-out flag must NOT contribute to
    cross-shop aggregates. Stronger than k>=3 (which only hides via
    sample-size threshold): a single opted-out shop is excluded entirely."""
    _patch_vertical(monkeypatch, {
        f"s{i}.myshopify.com": "apparel" for i in range(1, 5)
    })

    # 4 shops with measured lifts; one of them (s2) is opted-out.
    for shop, measured in [
        ("s1.myshopify.com", 110.0),
        ("s2.myshopify.com", 108.0),  # opted-out, must be excluded
        ("s3.myshopify.com", 112.0),
        ("s4.myshopify.com", 109.0),
    ]:
        _insert_decision(db, shop, "retention_outreach_email",
                         "rars_delta_7d", 100.0, measured)

    # Patch the opt-out check so s2 is opted out (Redis path bypassed)
    def fake_opted_out(shop_domain):
        return shop_domain == "s2.myshopify.com"
    monkeypatch.setattr(csa, "is_merchant_opted_out", fake_opted_out)

    report = csa._run_aggregation(db)
    assert report["shops_excluded_opt_out"] == 1
    # 4 shops total - 1 opt-out = 3 contributing → k-anonymity OK, row written
    assert report["written"] == 1
    row = db.execute(text("""
        SELECT n_shops, n_decisions FROM cross_shop_patterns
    """)).fetchone()
    assert row.n_shops == 3
    assert row.n_decisions == 3


def test_aggregator_drops_below_k_when_opt_out_pushes_under_3(db, monkeypatch):
    """3 shops total but 1 opts out → only 2 contributing → k-anonymity
    floor not met → no row written."""
    _patch_vertical(monkeypatch, {
        f"s{i}.myshopify.com": "apparel" for i in range(1, 4)
    })
    for shop, measured in [
        ("s1.myshopify.com", 110.0),
        ("s2.myshopify.com", 108.0),
        ("s3.myshopify.com", 112.0),
    ]:
        _insert_decision(db, shop, "retention_outreach_email",
                         "rars_delta_7d", 100.0, measured)
    monkeypatch.setattr(
        csa, "is_merchant_opted_out",
        lambda s: s == "s3.myshopify.com",
    )
    report = csa._run_aggregation(db)
    assert report["shops_excluded_opt_out"] == 1
    assert report["written"] == 0
    assert report["skipped_k_anon"] == 1


def test_gdpr_no_shop_domain_in_aggregate_table(db, monkeypatch):
    """Hard GDPR invariant: cross_shop_patterns table has no shop_domain
    column. Tested by inspecting information_schema."""
    cols = db.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'cross_shop_patterns'
    """)).fetchall()
    names = {c.column_name for c in cols}
    assert "shop_domain" not in names
    # All other identity-like fields must be absent too
    for forbidden in ("shop_id", "merchant_id", "email", "ip"):
        assert forbidden not in names


# ---------------------------------------------------------------------------
# SIP read API
# ---------------------------------------------------------------------------


def test_sip_read_cross_shop_priors_empty_for_unknown_vertical(db):
    from app.services.sip_engine import _read_cross_shop_priors
    # Pass the underlying conn (db is a Session with a bound connection)
    conn = db.connection()
    assert _read_cross_shop_priors(conn, None) == []
    assert _read_cross_shop_priors(conn, "") == []
    # Vertical with no rows
    assert _read_cross_shop_priors(conn, "nonexistent_vertical") == []


def test_sip_read_cross_shop_priors_returns_high_confidence_first(db):
    from app.services.sip_engine import _read_cross_shop_priors
    db.execute(text("""
        INSERT INTO cross_shop_patterns
          (vertical, action_kind, metric_kind, lift_pct_avg,
           n_shops, n_decisions, confidence)
        VALUES
          ('beauty', 'retention_outreach_email', 'rars_delta_7d',
           5.0, 12, 30, 'high'),
          ('beauty', 'recovery_digest', 'rars_delta_7d',
           3.0, 4, 8, 'low')
    """))
    conn = db.connection()
    rows = _read_cross_shop_priors(conn, "beauty")
    assert len(rows) == 2
    assert rows[0]["confidence"] == "high"
    assert rows[0]["action_kind"] == "retention_outreach_email"
    assert rows[1]["confidence"] == "low"


# ---------------------------------------------------------------------------
# Redis transient-down → skip (born 2026-05-11 competitor-CTO audit)
# ---------------------------------------------------------------------------


def test_run_if_due_skips_on_redis_transient_down(monkeypatch):
    """Redis raising on r.set() must result in a SKIPPED tick, not a
    no-lock aggregation run. Running without a TTL claim risks
    double-aggregation when Redis recovers (the 5min worker cycle
    re-ticks and sets the claim fresh, firing aggregation again on
    top of the no-lock run)."""
    class _BrokenRedis:
        def set(self, *args, **kwargs):
            raise ConnectionError("redis transient-down")

    monkeypatch.setattr(csa, "_redis_client", lambda: _BrokenRedis())

    # If the code fell through to run, this would raise (no DB session
    # provided, would try SessionLocal()). The skip-on-redis-down
    # behavior returns before any DB work.
    report = csa.run_if_due()
    assert report == {"status": "skipped", "reason": "redis_unavailable"}


def test_run_if_due_claim_held_skips(monkeypatch):
    """SETNX returning False (claim held by prior tick) → skipped."""
    class _ClaimedRedis:
        def set(self, *args, **kwargs):
            return False  # nx=True on existing key returns None/False
    monkeypatch.setattr(csa, "_redis_client", lambda: _ClaimedRedis())
    report = csa.run_if_due()
    assert report["status"] == "skipped"
    assert report["reason"] == "claim_held"
