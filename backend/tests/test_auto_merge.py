"""
Tests for the Phase-2 gated auto-merge loop.

run_auto_merge() is the final step that closes the self-healing cycle for
truly safe TIER_0 fixes. The tests verify every gate individually so a
regression in any one of them is caught before it reaches production.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.autofix_promotion import AutoFixPromotion
from app.models.bugfix_candidate import BugFixCandidate
from app.services.promotion_pipeline import (
    _candidate_touches_forbidden_path,
    _is_auto_merge_enabled,
    _AUTO_MERGE_FORBIDDEN_PREFIXES,
    run_auto_merge,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_applied_candidate(db, patch_files=None, risk_tier=0) -> BugFixCandidate:
    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref=f"auto_merge_test_{id(patch_files)}",
        title="Test candidate",
        status="applied",
        applied_at=_now(),
        patch_files=json.dumps(patch_files or ["app/services/some_safe.py"]),
        patch_risk_tier=risk_tier,
        created_at=_now(),
    )
    db.add(c)
    db.flush()
    return c


def _make_promo(db, candidate: BugFixCandidate, *, status="pushed",
                ci="passed", pr_url="https://gh/fake/pr/1", pr_number=1) -> AutoFixPromotion:
    promo = AutoFixPromotion(
        bugfix_candidate_id=candidate.id,
        git_commit_sha="abc123def456",
        status=status,
        pr_url=pr_url,
        pr_number=pr_number,
        remote_ci_status=ci,
        branch_name=f"autofix/candidate-{candidate.id}",
    )
    db.add(promo)
    db.flush()
    return promo


# ---------------------------------------------------------------------------
# Env kill-switch
# ---------------------------------------------------------------------------

def test_auto_merge_kill_switch_disables(db, monkeypatch):
    """Default is now ON (M3 sprint, 2026-04-11). Set AUTO_MERGE_TIER0=0
    to halt every future auto-merge."""
    monkeypatch.setenv("AUTO_MERGE_TIER0", "0")
    assert _is_auto_merge_enabled() is False
    c = _make_applied_candidate(db)
    _make_promo(db, c)
    result = run_auto_merge(db)
    assert result["merged"] == 0
    assert result["skipped_disabled"] == 1


def test_auto_merge_default_on(monkeypatch):
    """Default-ON contract — env var unset means enabled."""
    monkeypatch.delenv("AUTO_MERGE_TIER0", raising=False)
    assert _is_auto_merge_enabled() is True


def test_auto_merge_enabled_when_flag_set(monkeypatch):
    monkeypatch.setenv("AUTO_MERGE_TIER0", "1")
    assert _is_auto_merge_enabled() is True


# ---------------------------------------------------------------------------
# Forbidden paths
# ---------------------------------------------------------------------------

def test_forbidden_path_rejects_billing_frontend():
    hit = _candidate_touches_forbidden_path(json.dumps([
        "app/services/safe.py",
        "dashboard/src/app/components/billing/PlanCard.tsx",
    ]))
    assert hit == "dashboard/src/app/components/billing"


def test_forbidden_path_rejects_onboarding():
    hit = _candidate_touches_forbidden_path(json.dumps([
        "dashboard/src/app/install/page.tsx",
    ]))
    assert hit == "dashboard/src/app/install"


def test_forbidden_path_rejects_webhooks_backend():
    hit = _candidate_touches_forbidden_path(json.dumps([
        "app/api/webhooks.py",
    ]))
    assert hit == "app/api/webhooks.py"


def test_forbidden_path_rejects_migrations():
    hit = _candidate_touches_forbidden_path(json.dumps([
        "migrations/versions/20260411_add_column.py",
    ]))
    assert hit == "migrations/"


def test_forbidden_path_allows_safe_service_file():
    assert _candidate_touches_forbidden_path(json.dumps([
        "app/services/nudge_engine.py",
        "app/services/opportunity_engine.py",
    ])) is None


def test_forbidden_path_rejects_missing_patch_files():
    # Unknown patch surface → unsafe by design (fail-closed).
    assert _candidate_touches_forbidden_path(None) == "unknown_patch_files"
    assert _candidate_touches_forbidden_path("") == "unknown_patch_files"


def test_forbidden_path_rejects_unparseable():
    assert _candidate_touches_forbidden_path("not json!") == "patch_files_unparseable"


def test_forbidden_prefixes_include_critical_surfaces():
    """Defense-in-depth: the forbidden list must cover billing, auth, migrations."""
    joined = "|".join(_AUTO_MERGE_FORBIDDEN_PREFIXES)
    assert "billing" in joined
    assert "onboarding" in joined
    assert "migrations" in joined
    assert "webhooks" in joined


# ---------------------------------------------------------------------------
# Gated auto-merge full flow
# ---------------------------------------------------------------------------

def test_auto_merge_skips_when_candidate_touches_forbidden_path(db, monkeypatch):
    monkeypatch.setenv("AUTO_MERGE_TIER0", "1")
    # Reset cooldown so the test is not blocked by a prior run
    import app.services.promotion_pipeline as pp
    pp._auto_merge_last = None

    c = _make_applied_candidate(
        db,
        patch_files=["dashboard/src/app/components/billing/PlanCard.tsx"],
    )
    _make_promo(db, c)

    result = run_auto_merge(db)
    assert result["merged"] == 0
    assert result["skipped_gate"] >= 1
    assert any("forbidden_path" in r for r in result["reasons"])


def test_auto_merge_skips_when_merge_recommendation_blocks(db, monkeypatch):
    """Candidate passes pre-filter (ci=passed, pr_url set) but is non-TIER_0 →
    compute_merge_recommendation() returns recommend=False and the auto-merge
    path must short-circuit with skipped_gate + a reason including merge_rec_blocked."""
    monkeypatch.setenv("AUTO_MERGE_TIER0", "1")
    import app.services.promotion_pipeline as pp
    pp._auto_merge_last = None

    # Non-TIER_0 → gate 5 of compute_merge_recommendation blocks the merge.
    c = _make_applied_candidate(
        db,
        patch_files=["app/services/nudge_engine.py"],
        risk_tier=1,
    )
    _make_promo(db, c)  # ci=passed, pr_url set → passes pre-filter

    result = run_auto_merge(db)
    assert result["merged"] == 0
    assert result["skipped_gate"] >= 1
    assert any("merge_rec_blocked" in r for r in result["reasons"])


def test_auto_merge_calls_merge_promotion_when_all_gates_green(db, monkeypatch):
    monkeypatch.setenv("AUTO_MERGE_TIER0", "1")
    import app.services.promotion_pipeline as pp
    pp._auto_merge_last = None

    c = _make_applied_candidate(db, patch_files=["app/services/nudge_engine.py"], risk_tier=0)
    _make_promo(db, c)

    # Patch merge_promotion so we do not actually invoke gh cli in the test.
    with patch(
        "app.services.promotion_pipeline.merge_promotion",
        return_value="merged",
    ) as mocked_merge:
        result = run_auto_merge(db)

    assert mocked_merge.called
    assert result["merged"] == 1
    # Cooldown should now be armed
    assert pp._is_auto_merge_on_cooldown() is True


def test_auto_merge_cooldown_blocks_second_run(db, monkeypatch):
    monkeypatch.setenv("AUTO_MERGE_TIER0", "1")
    import app.services.promotion_pipeline as pp
    pp._auto_merge_last = None

    c1 = _make_applied_candidate(db, patch_files=["app/services/nudge_engine.py"])
    _make_promo(db, c1)

    with patch(
        "app.services.promotion_pipeline.merge_promotion",
        return_value="merged",
    ):
        run_auto_merge(db)

    # Second invocation should be blocked by cooldown
    c2 = _make_applied_candidate(db, patch_files=["app/services/other_safe.py"])
    _make_promo(db, c2)
    result = run_auto_merge(db)
    assert result["merged"] == 0
    assert result["skipped_cooldown"] == 1


def test_auto_merge_returns_zero_when_no_candidates(db, monkeypatch):
    monkeypatch.setenv("AUTO_MERGE_TIER0", "1")
    import app.services.promotion_pipeline as pp
    pp._auto_merge_last = None
    result = run_auto_merge(db)
    assert result["merged"] == 0
    assert result["considered"] == 0
