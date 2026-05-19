#!/usr/bin/env python3
# invariant-eligible: false — static AST/source audit over SOURCE.
# Commit-stage-only: guards against a SOURCE edit that removes a
# write_no_rollback-class guard. Meaningless to re-run every 15-min
# runtime cycle against unchanged source.
"""audit_db_session_rollback.py — regression preventer for the
write_no_rollback DB-session-poison class (born 2026-05-19).

The class: a try/except catches a DB error, logs it, but never
rolls back → the SHARED SQLAlchemy session stays poisoned
(InFailedSqlTransaction / PendingRollbackError) → every subsequent
op reusing it that cycle fails spuriously and the real work (or the
alert that would flag it) is silently lost. Found by the 2026-05-19
Sentry deep-DA (sentry #239 + 6 invariant_monitor incidents).

Two canonical fixes (app/core/database.py):
  - `savepoint_scope(db)`  — BATCH loops (per-row flush + a single
    post-loop / per-shop commit): a failing iteration rolls back only
    its own SAVEPOINT; the good rows still commit.
  - `rollback_quiet(db)`   — commit-per-iteration / read-only loops:
    the committed row is durable; just un-poison for the next one.

This audit LOCKS the 11 sites root-fixed across commits 2dd99d1 /
b2e641c / this sprint so a future refactor cannot silently strip the
guard and re-open the class. It is the file/site-scoped regression
gate; the GENERIC "any new shared-session per-iteration loop without a
guard" AST detector is specced in
project_db_session_rollback_class_sweep_2026_05_19 (the Agent's
refined heuristic) — deliberately separate because a sloppy
whole-codebase AST detector over-fires (~270 raw candidates) and
bricking commits on false positives is the inverse of the fix.

Non-vacuity: asserts it verified ≥ _MIN_SITES and that EACH expected
(file, guard-token) pair is present. If a refactor renames/removes a
guard the audit fails loudly with the exact site.

Exit 0 = all guards intact, exit 1 = a guard was stripped (regression).
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent

# (relative path, must-contain-token, human site description). The token
# is the canonical guard the site MUST retain. Multiple rows per file
# where a file has >1 guarded loop.
_SITES: list[tuple[str, str, str]] = [
    # --- canonical primitives must exist ---
    ("app/core/database.py", "def rollback_quiet(", "canonical rollback_quiet helper"),
    ("app/core/database.py", "def savepoint_scope(", "canonical savepoint_scope ctx-mgr"),
    # --- §0 revenue + safety-net (2dd99d1) ---
    ("app/services/revenue_metrics.py", "rollback_quiet(db)", "get_shop_aov/currency/timezone fallback heal (#239)"),
    ("app/services/invariant_monitor.py", "_safe_check(_check, db, summary)", "invariant runtime-check dispatch wrapper"),
    ("app/services/invariant_monitor.py", "_rollback_quiet(db)", "invariant write_alert handlers"),
    # --- SAVEPOINT batch loops (this sprint) ---
    ("app/services/action_learning.py", "savepoint_scope(db)", "evaluate_pending_outcomes per-outcome loop"),
    ("app/services/prediction_log.py", "savepoint_scope(db)", "run_mature_predictions per-row UPDATE loop"),
    ("app/services/uninstall_erasure.py", "savepoint_scope(db)", "GDPR Art.17 watchdog per-merchant loop"),
    ("app/workers/tasks/nudge_compose_task.py", "savepoint_scope(db)", "nudge compose per-nudge loop"),
    ("app/services/action_proof.py", "savepoint_scope(db)", "compute_pending_deltas per-snap loop"),
    # --- rollback_quiet commit-per-iter / read loops ---
    # intelligence_worker: helper update_product_opportunity commits
    # per-pair (CORRECTED 2026-05-19b — d15ada0 wrongly used
    # savepoint_scope; the helper's commit dissolved the SAVEPOINT).
    ("app/workers/intelligence_worker.py", "rollback_quiet(db)", "update_opportunity per-pair loop (helper commits per-pair)"),
    ("app/services/merchant_churn_predictor.py", "rollback_quiet(db)", "compute_churn_report per-merchant scoring loop"),
    ("app/workers/segment_monitor_worker.py", "rollback_quiet(db)", "_process_product handlers (create_task commits per-call)"),
    ("app/services/contextual_bandit.py", "rollback_quiet(db)", "event-replay purchase-probe loop (conn-death class)"),
    # regulatory_watch INTENTIONALLY ABSENT: d15ada0 mis-fixed it with
    # rollback_quiet on a BATCH loop (post-loop commit @719) which would
    # discard prior rules' flushed alerts + append-only compliance
    # audit-log rows. Reverted to pre-d15ada0; re-opened as
    # R-blocker:sprint (needs savepoint_scope per-rule via a careful
    # ~120-line restructure) — project_db_session_rollback_class_sweep.
]

# Floor: this many distinct (file,token) checks must run & pass. If the
# list is gutted the audit fails rather than vacuously passing.
_MIN_SITES = 14


def main() -> int:
    failures: list[str] = []
    checked = 0
    for rel, token, desc in _SITES:
        p = _BACKEND / rel
        if not p.is_file():
            failures.append(f"MISSING FILE {rel} — site '{desc}' cannot be verified")
            continue
        src = p.read_text()
        checked += 1
        if token not in src:
            failures.append(
                f"{rel}: guard token {token!r} absent — the "
                f"write_no_rollback guard for '{desc}' was removed/renamed. "
                f"Re-add savepoint_scope/rollback_quiet (see "
                f"project_db_session_rollback_class_sweep_2026_05_19)."
            )

    if failures:
        print("FAIL: write_no_rollback-class regression(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    if checked < _MIN_SITES:
        print(
            f"FAIL (non-vacuity): only {checked} site(s) verified, "
            f"expected ≥{_MIN_SITES}. The site list may be gutted — "
            f"verify the fix is intact."
        )
        return 1

    print(
        f"OK: {checked} write_no_rollback-class guard(s) intact "
        f"(savepoint_scope / rollback_quiet across §0 revenue + "
        f"safety-net + 9 worker-loop siblings)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
