"""Tests for M4 — governed TIER_1 auto-apply.

Locks the gate stack: every gate must short-circuit cleanly, only a
candidate that passes ALL gates simultaneously may auto-apply, and
the kill-switch / daily cap halt the loop.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services import bugfix_pipeline as bp


def _fake_candidate(
    *,
    cid: int = 1,
    confidence: int = 90,
    domain: str = "evolution",
    source_type: str = "ops_alert",
    patch_files: str = '["app/services/evolution_engine.py"]',
    patch_diff: str | None = None,
    patch_risk_tier: int = 1,
    fingerprint_match: bool = False,
):
    if patch_diff is None:
        # Small valid-looking diff with 5 added prod lines
        patch_diff = (
            "--- a/app/services/evolution_engine.py\n"
            "+++ b/app/services/evolution_engine.py\n"
            "@@ -1,1 +1,6 @@\n"
            " pass\n"
            + "\n".join(f"+    line{i} = {i}" for i in range(5))
            + "\n"
        )
    c = MagicMock()
    c.id = cid
    c.title = f"fix #{cid}"
    c.fix_confidence = confidence
    c.affected_domain = domain
    c.source_type = source_type
    c.patch_files = patch_files
    c.patch_diff = patch_diff
    c.patch_risk_tier = patch_risk_tier
    c.status = "patch_proposed"
    c.priority_score = 100
    c.created_at = datetime.now()
    c.reviewer_assessment_id = None
    c.failure_reason = None
    return c


def _query_returning(candidates):
    chain = MagicMock()
    chain.filter.return_value.order_by.return_value.limit.return_value.all.return_value = candidates
    return chain


def _fake_db(candidates, *, daily_cap_hit: bool = False, domain_wins: int = 5):
    db = MagicMock()
    db.query.return_value = _query_returning(candidates)
    # daily cap query (executed via text())
    cap_count = 99 if daily_cap_hit else 0
    db.execute.return_value.fetchone.return_value = (cap_count if daily_cap_hit else domain_wins,)
    return db


def _passes_predictive_history(p=0.9, n=10):
    return patch.object(bp, "predict_outcome_probability", return_value=(p, n))


def _no_fingerprint_match():
    return patch.object(bp, "_check_patch_fingerprint", return_value=None)


def _no_self_healing_touch():
    return patch.object(bp, "touches_self_healing_pipeline", return_value=(False, []))


def _passing_reviewer():
    rev = MagicMock(verdict="approve", auto_approvable=True, risk_level="low", id=42)
    return patch("app.services.reviewer_layer.review_entity", return_value=rev)


# ---- Kill switch + daily cap ----

def test_kill_switch_blocks_run(monkeypatch):
    monkeypatch.setenv("AUTO_APPLY_TIER1", "0")
    db = _fake_db([_fake_candidate()])
    summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["skipped_disabled"] == 1
    assert summary["applied"] == 0


def test_daily_cap_blocks_run(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=True):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["skipped_daily_cap"] == 1
    assert summary["applied"] == 0


# ---- Per-gate failures ----

def test_low_confidence_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate(confidence=70)])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["applied"] == 0
    assert summary["gate_failures"].get("confidence") == 1


def test_low_predictive_probability_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(p=0.30, n=20):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("predictive_probability") == 1


def test_insufficient_predictive_samples_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(p=0.95, n=2):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("predictive_samples") == 1


def test_patch_too_large_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    big_diff = (
        "--- a/app/services/evolution_engine.py\n"
        "+++ b/app/services/evolution_engine.py\n"
        "@@ -1,1 +1,60 @@\n"
        + "\n".join(f"+    line{i} = {i}" for i in range(80))
        + "\n"
    )
    db = _fake_db([_fake_candidate(patch_diff=big_diff)])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history():
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("patch_too_large") == 1


def test_multi_file_patch_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate(
        patch_files='["app/services/a.py", "app/services/b.py"]',
    )])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history():
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("not_single_file") == 1


def test_low_domain_track_record_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(), \
         patch.object(bp, "_gov_tier1_domain_wins", return_value=1):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("domain_track_record") == 1


def test_fingerprint_match_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(), \
         patch.object(bp, "_gov_tier1_domain_wins", return_value=5), \
         patch.object(bp, "_check_patch_fingerprint", return_value={"candidate_id": 33, "outcome": "rolled_back"}):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("fingerprint_match") == 1


def test_self_healing_touch_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(), \
         patch.object(bp, "_gov_tier1_domain_wins", return_value=5), \
         _no_fingerprint_match(), \
         patch.object(bp, "touches_self_healing_pipeline", return_value=(True, ["app/services/bugfix_pipeline.py"])):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("self_healing_touch") == 1


def test_reviewer_reject_skipped(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    db = _fake_db([_fake_candidate()])
    rejected = MagicMock(verdict="reject", auto_approvable=False, risk_level="medium", id=99)
    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(), \
         patch.object(bp, "_gov_tier1_domain_wins", return_value=5), \
         _no_fingerprint_match(), \
         _no_self_healing_touch(), \
         patch("app.services.reviewer_layer.review_entity", return_value=rejected):
        summary = bp.run_governed_tier1_auto_apply(db)
    assert summary["gate_failures"].get("reviewer_verdict") == 1


# ---- Happy path: every gate passes → patch applies ----

def test_full_pass_applies_patch(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    cand = _fake_candidate(cid=777)
    db = _fake_db([cand])

    apply_result = MagicMock(status="applied", failure_reason=None)

    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(p=0.85, n=12), \
         patch.object(bp, "_gov_tier1_domain_wins", return_value=5), \
         _no_fingerprint_match(), \
         _no_self_healing_touch(), \
         _passing_reviewer(), \
         patch.object(bp, "apply_bugfix_candidate", return_value=apply_result), \
         patch("app.services.audit.write_audit_log"), \
         patch("app.services.alerting.write_alert"):
        summary = bp.run_governed_tier1_auto_apply(db)

    assert summary["attempted"] == 1
    assert summary["applied"] == 1
    assert summary["failed"] == 0
    assert cand.decided_by == "auto_tier_1_governed"
    assert cand.status == "approved"  # set before apply, apply mock doesn't update it


def test_apply_failure_halts_loop(monkeypatch):
    monkeypatch.delenv("AUTO_APPLY_TIER1", raising=False)
    cand = _fake_candidate(cid=778)
    db = _fake_db([cand])

    apply_result = MagicMock(status="apply_failed", failure_reason="git apply rejected")

    with patch.object(bp, "_gov_tier1_check_daily_cap", return_value=False), \
         _passes_predictive_history(p=0.85, n=12), \
         patch.object(bp, "_gov_tier1_domain_wins", return_value=5), \
         _no_fingerprint_match(), \
         _no_self_healing_touch(), \
         _passing_reviewer(), \
         patch.object(bp, "apply_bugfix_candidate", return_value=apply_result), \
         patch("app.services.audit.write_audit_log"), \
         patch("app.services.alerting.write_alert"):
        summary = bp.run_governed_tier1_auto_apply(db)

    assert summary["attempted"] == 1
    assert summary["applied"] == 0
    assert summary["failed"] == 1
