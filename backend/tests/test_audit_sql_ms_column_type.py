"""Contract tests for ``scripts/audit_sql_ms_column_type.py``.

Pins the pg-ms-type class preventer (born 2026-05-20 after commit
``35796ae`` hand-fixed the canonical instance in
``merchant_brain.evaluate_pending_outcomes``). The audit guards the
class where BIGINT epoch-ms columns (``events.timestamp``,
``analytics_event.ts_ms``, ``product_metrics.last_event_at``) are
compared against Postgres-timestamp expressions (``now()``,
``current_timestamp``, ``:datetime`` binds, ISO literals) — a
comparison that fails at runtime with::

  psycopg2.errors.UndefinedFunction:
    operator does not exist: bigint >= timestamp without time zone

These tests pin (in order of importance):
  1. **Non-vacuity** — the audit's self-test passes on a known-buggy
     set; the audit refuses to scan production if the self-test
     regresses. Defends against the smoke-fiction class.
  2. **Detection** — each canonical bug shape is flagged.
  3. **Opt-out** — ``# sql-ms-type: ok — <reason>`` silences the
     finding when the comparison is provably safe.
  4. **Repo-wide GREEN** — the live production scan returns 0
     findings (every site is either fixed or annotated). This is the
     "did the remediation actually land" pin; a regression would mean
     someone introduced a new pg-ms-type instance.
  5. **SQL-type-name exclusion** — ``AS timestamp`` / ``timestamp
     WITHOUT TIME ZONE`` are NOT flagged.
"""
from __future__ import annotations

import pathlib
import sys

_SCRIPTS = pathlib.Path("/opt/wishspark/backend/scripts")
sys.path.insert(0, str(_SCRIPTS))

from audit_sql_ms_column_type import (  # noqa: E402
    APP_ROOT,
    find_violations_in_sql,
    main,
    run_self_test,
    scan_file,
)


# ──────────────────────────────────────────────────────────────────────
# 1. Non-vacuity — the matcher must catch its own canonical buggy set.
# ──────────────────────────────────────────────────────────────────────
def test_self_test_passes() -> None:
    """If this regresses, the audit is silently vacuous (smoke-fiction
    class). The production scan refuses to run when the self-test
    fails."""
    assert run_self_test(verbose=False) == 0


# ──────────────────────────────────────────────────────────────────────
# 2. Detection — each canonical bug shape gets flagged.
# ──────────────────────────────────────────────────────────────────────
def test_detects_canonical_35796ae_bug_shape() -> None:
    """The exact SQL form that fired in production on 2026-05-08."""
    sql = (
        "SELECT COUNT(*) FROM events WHERE shop_domain=:s "
        "AND timestamp >= :c"
    )
    findings = find_violations_in_sql(sql)
    assert len(findings) >= 1
    assert "timestamp" in findings[0][0]


def test_detects_events_timestamp_vs_now() -> None:
    findings = find_violations_in_sql(
        "SELECT * FROM events WHERE events.timestamp >= now()"
    )
    assert len(findings) >= 1


def test_detects_ts_ms_vs_datetime_bind() -> None:
    findings = find_violations_in_sql(
        "SELECT 1 FROM analytics_event WHERE ts_ms < :cutoff_dt"
    )
    assert len(findings) >= 1
    assert findings[0][0] == "ts_ms"


def test_detects_last_event_at_between() -> None:
    findings = find_violations_in_sql(
        "SELECT * FROM product_metrics WHERE last_event_at BETWEEN :a AND :b"
    )
    assert len(findings) >= 1
    assert findings[0][0] == "last_event_at"


def test_detects_current_timestamp() -> None:
    findings = find_violations_in_sql(
        "SELECT 1 FROM events WHERE events.timestamp <= current_timestamp"
    )
    assert len(findings) >= 1


def test_detects_iso_literal_compare() -> None:
    findings = find_violations_in_sql(
        "SELECT 1 FROM events WHERE events.timestamp >= '2026-05-08'"
    )
    assert len(findings) >= 1


# ──────────────────────────────────────────────────────────────────────
# 3. Safe-bind name heuristic — `_ms` suffix should NOT fire.
# ──────────────────────────────────────────────────────────────────────
def test_ms_suffix_bind_not_flagged() -> None:
    """Bind names containing ``_ms``/``_epoch``/``_millis`` are
    treated as already-in-epoch-ms (safe-by-convention).
    """
    findings = find_violations_in_sql(
        "SELECT 1 FROM events WHERE events.timestamp >= :cutoff_ms"
    )
    assert findings == []


def test_extract_epoch_rhs_not_flagged() -> None:
    """SQL-level cast via ``EXTRACT(EPOCH FROM ...)`` is safe."""
    findings = find_violations_in_sql(
        "SELECT 1 FROM events WHERE events.timestamp >= "
        "EXTRACT(EPOCH FROM NOW()) * 1000"
    )
    assert findings == []


# ──────────────────────────────────────────────────────────────────────
# 4. Opt-out comment silences the audit at the call site.
# ──────────────────────────────────────────────────────────────────────
def test_opt_out_comment_silences_audit(tmp_path: pathlib.Path) -> None:
    """A ``# sql-ms-type: ok — ...`` comment near the ``text(...)`` call
    silences the finding, even when the matcher would otherwise fire."""
    src = '''
from sqlalchemy import text

def query(db, dt):
    # sql-ms-type: ok — bind is int ms, this is a test
    return db.execute(
        text("SELECT 1 FROM events WHERE events.timestamp >= :c"),
        {"c": int(dt.timestamp() * 1000)},
    ).scalar()
'''
    f = tmp_path / "fake.py"
    f.write_text(src)
    assert scan_file(f) == []


def test_no_opt_out_means_flagged(tmp_path: pathlib.Path) -> None:
    """Without the opt-out comment, the same SQL DOES get flagged."""
    src = '''
from sqlalchemy import text

def query(db, dt):
    return db.execute(
        text("SELECT 1 FROM events WHERE events.timestamp >= :c"),
        {"c": int(dt.timestamp() * 1000)},
    ).scalar()
'''
    f = tmp_path / "fake.py"
    f.write_text(src)
    findings = scan_file(f)
    assert len(findings) >= 1


# ──────────────────────────────────────────────────────────────────────
# 5. SQL-type-name exclusion — `AS timestamp` is NOT a column.
# ──────────────────────────────────────────────────────────────────────
def test_as_timestamp_cast_not_flagged() -> None:
    """``CAST(... AS timestamp)`` is a SQL type, not a column ref."""
    findings = find_violations_in_sql(
        "SELECT * FROM events WHERE shop_domain = :shop "
        "AND created_at >= CAST(:dt AS timestamp)"
    )
    assert findings == []


def test_timestamp_without_time_zone_not_flagged() -> None:
    """``timestamp WITHOUT TIME ZONE`` is a SQL type name (the very
    error string the bug class produces)."""
    findings = find_violations_in_sql(
        "SELECT (now() AT TIME ZONE 'UTC')::timestamp WITHOUT TIME ZONE FROM events"
    )
    assert findings == []


def test_order_by_timestamp_not_flagged() -> None:
    """A bare ``timestamp`` used outside a comparison context is OK."""
    findings = find_violations_in_sql(
        "SELECT * FROM events WHERE shop_domain = :s ORDER BY timestamp DESC LIMIT 1"
    )
    assert findings == []


# ──────────────────────────────────────────────────────────────────────
# 6. Repo-wide GREEN — every live site is fixed or annotated.
# ──────────────────────────────────────────────────────────────────────
def test_production_scan_green() -> None:
    """The full ``app/`` scan returns 0 findings. A regression here
    means someone introduced an un-annotated bigint-ms ↔ timestamp
    comparison (canonical 35796ae shape)."""
    assert main([]) == 0


def test_production_scan_quiet_green() -> None:
    """The ``--quiet`` flag suppresses informational output but returns
    the same exit code on GREEN."""
    assert main(["--quiet"]) == 0


# ──────────────────────────────────────────────────────────────────────
# 7. Aliased-events known gap — documented behaviour, not a bug.
# ──────────────────────────────────────────────────────────────────────
def test_aliased_events_table_now_covered() -> None:
    """``FROM events e WHERE e.timestamp >= :c`` IS now covered by
    Pass 3 (alias resolution). Born 2026-05-20 closing the "accepted
    limits aren't 11/10" gap — extracted from the founder pushback
    that documented tradeoffs aren't acceptable for a top-1 project.

    The audit now parses `FROM events <alias>` / `FROM events AS <alias>`
    from the SQL body and treats `<alias>.timestamp` as the canonical
    epoch-ms column.
    """
    findings = find_violations_in_sql(
        "SELECT * FROM events e WHERE e.timestamp >= :c"
    )
    assert len(findings) >= 1
    assert "e.timestamp" in findings[0][0]


def test_aliased_events_with_AS_keyword() -> None:
    """`FROM events AS e WHERE e.timestamp >= :c` also detected."""
    findings = find_violations_in_sql(
        "SELECT * FROM events AS e WHERE e.timestamp >= :c"
    )
    assert len(findings) >= 1


def test_aliased_join_events() -> None:
    """`JOIN events ev ON ... WHERE ev.timestamp >= :c` detected."""
    findings = find_violations_in_sql(
        "SELECT * FROM shop_orders s JOIN events ev "
        "ON s.shop_domain = ev.shop_domain "
        "WHERE ev.timestamp >= :c"
    )
    assert len(findings) >= 1


def test_aliased_safe_rhs_not_flagged() -> None:
    """Aliased ref against int-ms bind name is safe."""
    findings = find_violations_in_sql(
        "SELECT * FROM events e WHERE e.timestamp >= :cutoff_ms"
    )
    assert findings == []


def test_aliased_to_non_epoch_table_not_flagged() -> None:
    """`FROM shop_orders s WHERE s.timestamp >= :c` is NOT flagged
    (shop_orders.timestamp would be a PG timestamp, not bigint ms).
    The audit only triggers Pass 3 when the alias maps to an
    epoch-ms table (events / analytics_event / product_metrics)."""
    findings = find_violations_in_sql(
        "SELECT * FROM shop_orders s WHERE s.created_at >= :c"
    )
    assert findings == []


# ──────────────────────────────────────────────────────────────────────
# 8. Repo scope — APP_ROOT actually exists (catches misconfigured paths).
# ──────────────────────────────────────────────────────────────────────
def test_app_root_exists() -> None:
    assert APP_ROOT.is_dir()
