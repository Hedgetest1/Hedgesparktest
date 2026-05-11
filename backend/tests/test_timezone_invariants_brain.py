"""Timezone invariant tests for brain_decisions + cross_shop_aggregator.

Born 2026-05-11 Senior+++ close. Schema: `brain_decisions.decision_at`
is `TIMESTAMP without TZ`; code constructs comparison datetimes via
`datetime.now(timezone.utc).replace(tzinfo=None)`. The invariant: code-
side naive UTC arithmetic must agree with PG `now()` semantics so a
horizon comparison `decision_at >= :horizon` doesn't drift.

Failure modes guarded:
  - Server timezone misconfigured (PG `now()` returns local time, code
    compares with UTC naive → 1-N hour drift).
  - Code accidentally compares a TZ-aware datetime with a TZ-naive
    column (Python raises in strict mode; SQLAlchemy strips silently).
  - Code uses `datetime.utcnow()` (deprecated) which returns naive
    local-as-UTC and breaks if server is non-UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def test_pg_now_aligns_with_code_side_naive_utc(db):
    """PG `now()` cast to TIMESTAMP without TZ must equal code-side
    `datetime.now(timezone.utc).replace(tzinfo=None)` within 5 seconds.
    Any larger drift = server TZ misconfigured."""
    code_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    pg_naive = db.execute(text(
        "SELECT (now() AT TIME ZONE 'UTC')::timestamp AS pg_utc"
    )).scalar()

    delta_seconds = abs((code_utc_naive - pg_naive).total_seconds())
    assert delta_seconds < 5.0, (
        f"PG now() and code-side UTC drift = {delta_seconds:.2f}s — "
        f"timezone misconfiguration. PG: {pg_naive}, Code: {code_utc_naive}"
    )


def test_brain_decisions_decision_at_horizon_query_consistent(db):
    """Insert a decision at "5 minutes ago" using the same horizon
    construction the aggregator uses. Verify it's selected by the
    aggregator-style WHERE clause."""
    horizon = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    )
    five_min_ago = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    )

    db.execute(text("""
        INSERT INTO brain_decisions
          (decision_at, shop_domain, action_kind,
           expected_outcome_metric, outcome_window_hours,
           baseline_value, measured_value, outcome_status,
           outcome_evaluated_at)
        VALUES
          (:t, '_tz_test_.myshopify.com', 'test_action',
           'rars_delta_7d', 24, 100.0, 110.0, 'effective',
           :t)
    """), {"t": five_min_ago})
    db.flush()

    # Aggregator-style query: should return the row inserted at
    # five_min_ago with horizon at ten_min_ago.
    rows = db.execute(text("""
        SELECT shop_domain
          FROM brain_decisions
         WHERE shop_domain = '_tz_test_.myshopify.com'
           AND decision_at >= :horizon
           AND outcome_status = 'effective'
    """), {"horizon": horizon}).fetchall()

    assert len(rows) == 1, (
        "Inserted decision at t-5min must be selected by horizon at "
        "t-10min — if not, timezone arithmetic drifted."
    )


def test_aggregator_horizon_construction_matches_module(db):
    """The aggregator constructs `horizon` as
    `datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=N)`.
    Verify a row at "N-1 days old" is selected and at "N+1 days old"
    is not."""
    from app.services.cross_shop_aggregator import LOOKBACK_DAYS

    in_window = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(days=LOOKBACK_DAYS - 1)
    )
    out_window = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(days=LOOKBACK_DAYS + 1)
    )
    horizon = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(days=LOOKBACK_DAYS)
    )

    db.execute(text("""
        INSERT INTO brain_decisions
          (decision_at, shop_domain, action_kind,
           expected_outcome_metric, outcome_window_hours,
           baseline_value, measured_value, outcome_status,
           outcome_evaluated_at)
        VALUES
          (:in_t, '_tz_window_in_.myshopify.com', 'test_action',
           'rars_delta_7d', 24, 100.0, 110.0, 'effective', :in_t),
          (:out_t, '_tz_window_out_.myshopify.com', 'test_action',
           'rars_delta_7d', 24, 100.0, 110.0, 'effective', :out_t)
    """), {"in_t": in_window, "out_t": out_window})
    db.flush()

    rows = db.execute(text("""
        SELECT shop_domain
          FROM brain_decisions
         WHERE shop_domain LIKE '_tz_window_%'
           AND decision_at >= :horizon
    """), {"horizon": horizon}).fetchall()

    shops = {r.shop_domain for r in rows}
    assert "_tz_window_in_.myshopify.com" in shops, \
        "in-window decision missing — horizon math drifted"
    assert "_tz_window_out_.myshopify.com" not in shops, \
        "out-of-window decision included — horizon math drifted"


def test_no_naive_utcnow_in_brain_or_aggregator_code():
    """`datetime.utcnow()` is deprecated and returns naive local-as-UTC
    on misconfigured servers. Brain + aggregator code must use
    `datetime.now(timezone.utc).replace(tzinfo=None)` instead."""
    import pathlib
    here = pathlib.Path(__file__).parent.parent / "app" / "services"
    targets = [
        here / "merchant_brain.py",
        here / "cross_shop_aggregator.py",
        here / "sip_engine.py",
    ]
    violations = []
    for path in targets:
        if not path.exists():
            continue
        text_content = path.read_text()
        # Match `datetime.utcnow(` (the deprecated naive UTC). Allow
        # `datetime.now(timezone.utc)` which is the correct pattern.
        for lineno, line in enumerate(text_content.splitlines(), 1):
            if "datetime.utcnow(" in line and not line.strip().startswith("#"):
                violations.append(f"{path.name}:{lineno}: {line.strip()}")
    assert violations == [], (
        "Deprecated datetime.utcnow() found in brain code:\n"
        + "\n".join(violations)
    )
