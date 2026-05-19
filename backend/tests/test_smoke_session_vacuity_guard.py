"""Contract test for the smoke-harness vacuity guard (born 2026-05-19h).

GROUND TRUTH: `scripts/smoke_endpoints.py` minted a forged session
token with hardcoded `session_version=0` while the prod smoke merchant
had drifted to `sv=19` → `deps.py:190 token_sv < db_sv` → 401 on EVERY
authed route → all counted as `skipped_auth` (ok=True) → the harness
reported `passed 137` (`--strict`-wired in preflight) while genuinely
testing ~1/137 routes, for an unknown number of commits, undetected.

`passed` counts skipped(401/403/404) rows as ok=True, so the honest
signal is genuinely-2xx = passed - skipped_auth. This locks the
structural guard that mechanizes the §1.4 "sanity-check the
implausible green" lesson: a green run where the forged session
authenticates almost nothing is FICTION and must fail in --strict.
Measured-healthy baseline after the fix = 118/137 (86%) genuine.
"""
from __future__ import annotations

import importlib

_se = importlib.import_module("scripts.smoke_endpoints")


def test_pre_fix_fiction_is_flagged_vacuous():
    """The EXACT 2026-05-19 finding: 137 'passed', 136 auth-skipped =
    ~1 genuine. Must be flagged vacuous (a green run here = fiction)."""
    assert _se._smoke_session_vacuous(
        {"total": 137, "passed": 137, "skipped_auth": 136, "failed": 0}
    ) is True


def test_post_fix_healthy_is_NOT_vacuous():
    """The measured post-fix reality: 137 passed, 19 auth-skipped →
    118 genuine (86%). Must NOT false-fail (the 50% floor has a
    36-point margin so normal 404/operator variation never flakes)."""
    assert _se._smoke_session_vacuous(
        {"total": 137, "passed": 137, "skipped_auth": 19, "failed": 0}
    ) is False


def test_boundary_just_below_half_is_vacuous():
    # 68/137 genuine = 49.6% < 50% → vacuous (session degrading).
    assert _se._smoke_session_vacuous(
        {"total": 137, "passed": 137, "skipped_auth": 69, "failed": 0}
    ) is True


def test_boundary_just_above_half_is_ok():
    # 69/137 = 50.4% ≥ 50% → not vacuous.
    assert _se._smoke_session_vacuous(
        {"total": 137, "passed": 137, "skipped_auth": 68, "failed": 0}
    ) is False


def test_empty_run_is_not_vacuous_no_div_by_zero():
    assert _se._smoke_session_vacuous(
        {"total": 0, "passed": 0, "skipped_auth": 0, "failed": 0}
    ) is False


def test_failures_counted_separately_still_vacuous_if_session_dead():
    # All-skipped except failures: genuine = passed - skipped_auth.
    assert _se._smoke_session_vacuous(
        {"total": 100, "passed": 80, "skipped_auth": 80, "failed": 20}
    ) is True
