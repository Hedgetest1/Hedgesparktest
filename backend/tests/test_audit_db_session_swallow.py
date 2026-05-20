"""Contract tests for `scripts/audit_db_session_swallow.py`.

Pins the Stage-1 forward-preventer for the write_no_rollback
DB-session-poison class. The audit catches: shared-session writes
(db.add/flush/commit/delete/merge/execute(<DML>)) inside try blocks
whose except handlers do NOT rollback and are NOT wrapped in
savepoint_scope/begin_nested.

These tests pin (in order of importance):
  1. **Non-vacuity** — the audit's self-test passes on a known-buggy
     set (3 buggy + 3 safe); production scan refuses if self-test
     regresses.
  2. **Detection** — each canonical bug shape is flagged.
  3. **Safety filters** — own-session pattern, savepoint_scope
     wrapping (outside or inside try), rollback handler, opt-out
     marker.
  4. **Read-only exclusion** — SELECT/WITH/EXPLAIN queries (literal
     OR dynamic) do not fire.
  5. **Production scan**: report-mode returns 0 (Stage 1 is info-
     only; Stage 2 will flip to strict).
"""
from __future__ import annotations

import pathlib
import sys

_SCRIPTS = pathlib.Path("/opt/wishspark/backend/scripts")
sys.path.insert(0, str(_SCRIPTS))

from audit_db_session_swallow import (  # noqa: E402
    main,
    run_self_test,
    _scan_snippet,
)


# ──────────────────────────────────────────────────────────────────────
# 1. Non-vacuity — the matcher must catch its own canonical buggy set.
# ──────────────────────────────────────────────────────────────────────
def test_self_test_passes() -> None:
    """If this regresses, the audit is silently vacuous — the smoke-
    fiction class CLAUDE.md §19 warns about. The production scan
    refuses to run when the self-test fails."""
    assert run_self_test(verbose=False) == 0


# ──────────────────────────────────────────────────────────────────────
# 2. Detection — each canonical bug shape gets flagged.
# ──────────────────────────────────────────────────────────────────────
def test_detects_flush_swallow() -> None:
    src = '''
def handler(db):
    try:
        db.add(Foo()); db.flush()
    except Exception:
        log.warning("oops")
'''
    findings = _scan_snippet(src)
    assert len(findings) == 1


def test_detects_commit_swallow() -> None:
    src = '''
def handler(db):
    try:
        db.commit()
    except SQLAlchemyError as e:
        log.error("nope")
'''
    findings = _scan_snippet(src)
    assert len(findings) == 1


def test_detects_execute_update_literal() -> None:
    src = '''
def handler(db):
    try:
        db.execute(text("UPDATE merchants SET x=1"))
    except Exception:
        pass
'''
    findings = _scan_snippet(src)
    assert len(findings) == 1


def test_detects_execute_insert_via_fstring() -> None:
    """f-string SQL with INSERT prefix should be flagged."""
    src = '''
def handler(db, val):
    try:
        db.execute(text(f"INSERT INTO foo (v) VALUES ({val})"))
    except Exception:
        pass
'''
    findings = _scan_snippet(src)
    assert len(findings) == 1


def test_detects_delete_swallow() -> None:
    src = '''
def handler(db, obj):
    try:
        db.delete(obj); db.flush()
    except Exception:
        pass
'''
    findings = _scan_snippet(src)
    assert len(findings) == 1


# ──────────────────────────────────────────────────────────────────────
# 3. Safety filters — known-safe shapes do NOT fire.
# ──────────────────────────────────────────────────────────────────────
def test_handler_rollback_silences() -> None:
    """A handler that calls db.rollback() is safe."""
    src = '''
def handler(db):
    try:
        db.add(Foo()); db.flush()
    except Exception:
        db.rollback()
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_rollback_quiet_helper_silences() -> None:
    """rollback_quiet(db) helper counts as rollback."""
    src = '''
def handler(db):
    try:
        db.add(Foo())
    except Exception:
        rollback_quiet(db)
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_own_session_pattern_excluded() -> None:
    """`db = SessionLocal()` + `db.close()` = own session, not shared."""
    src = '''
def own_handler():
    db = SessionLocal()
    try:
        db.add(Foo()); db.flush()
    except Exception:
        log.error("ok")
    finally:
        db.close()
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_outer_savepoint_scope_silences() -> None:
    """`with savepoint_scope(db):` wrapping a try silences."""
    src = '''
def wrapped(db):
    with savepoint_scope(db):
        try:
            db.add(Foo())
        except Exception:
            raise
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_inner_begin_nested_silences() -> None:
    """`try: with db.begin_nested(): ...` — the SAVEPOINT inside the try
    body protects the writes; outer except handles release failure."""
    src = '''
def wrapped(db):
    try:
        with db.begin_nested():
            db.execute(text("UPDATE x SET y=1"))
    except Exception:
        log.warning("ok")
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_opt_out_comment_silences() -> None:
    """A `# session-rollback: ok — <reason>` comment on or near the
    try silences the finding."""
    src = '''
def wrapped(db):
    # session-rollback: ok — caller wraps in worker_scope
    try:
        db.add(Foo())
    except Exception:
        log.warning("ok")
'''
    findings = _scan_snippet(src)
    assert findings == []


# ──────────────────────────────────────────────────────────────────────
# 4. Read-only exclusion — SELECT queries do not fire.
# ──────────────────────────────────────────────────────────────────────
def test_select_literal_excluded() -> None:
    """Literal SELECT in execute does not fire."""
    src = '''
def reader(db):
    try:
        db.execute(text("SELECT * FROM merchants"))
    except Exception:
        log.warning("ok")
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_dynamic_sql_assumes_read() -> None:
    """Dynamic SQL (variable reference) defaults to read — the audit
    deliberately trades exhaustiveness for signal: most dynamic SQL
    is SELECT analytics. Real writes are caught via db.add/flush/
    commit/delete/merge and literal/f-string INSERT/UPDATE/DELETE
    detection. Documented heuristic, not a bug."""
    src = '''
def builder(db, sql):
    try:
        db.execute(text(sql))
    except Exception:
        log.warning("ok")
'''
    findings = _scan_snippet(src)
    assert findings == []


def test_with_explain_excluded() -> None:
    """CTE / EXPLAIN start prefixes are read-only."""
    src = '''
def reader(db):
    try:
        db.execute(text("WITH t AS (SELECT 1) SELECT * FROM t"))
    except Exception:
        log.warning("ok")
'''
    findings = _scan_snippet(src)
    assert findings == []


# ──────────────────────────────────────────────────────────────────────
# 5. Production scan — Stage 1 = REPORT mode, exit 0.
# ──────────────────────────────────────────────────────────────────────
def test_production_scan_report_returns_zero() -> None:
    """Stage 1: report mode returns 0 regardless of finding count.
    Stage 2 will flip preflight to --strict after all current
    candidates are fixed or annotated."""
    rc = main(["--report"])
    assert rc == 0


def test_production_scan_quiet_report_returns_zero() -> None:
    rc = main(["--report", "--quiet"])
    assert rc == 0


# ──────────────────────────────────────────────────────────────────────
# 6. Strict mode — backlog cleared, strict returns 0 (Stage 2 close).
# ──────────────────────────────────────────────────────────────────────
def test_strict_mode_returns_zero_after_stage2_close() -> None:
    """Stage 2 (2026-05-20) cleared the 27-candidate backlog (26
    annotations + 1 structural fix via savepoint_scope on alerting.py
    Step-2 delivery_status flush). Strict mode now gates the preflight
    and MUST return 0; a regression (new un-annotated shared-session
    swallow-no-rollback site) would re-fire and block the commit."""
    rc = main(["--strict", "--quiet"])
    assert rc == 0
