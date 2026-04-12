"""Tests for D6 — operator answer prediction.

Contract:
  1. File pattern is derived from the first directory segment pair.
  2. Historical approvals on the same pattern → "approve" recommendation.
  3. Historical rejections on the same pattern → "reject" recommendation.
  4. Below _MIN_SAMPLE evidence → recommendation == "unknown".
  5. Missing file pattern falls back to affected_domain query.
  6. Beta posterior stays in [0, 1] and scales confidence with n.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.models.audit_log import AuditLog
from app.models.bugfix_candidate import BugFixCandidate
from app.services import operator_prediction as op


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_tier2(
    db,
    *,
    files: list[str] | None = None,
    affected_domain: str | None = "pipeline",
    decision: str | None = None,
) -> BugFixCandidate:
    c = BugFixCandidate(
        source_type="manual",
        source_ref=f"d6_{uuid.uuid4().hex[:10]}",
        title=f"t2 {uuid.uuid4().hex[:6]}",
        summary="x",
        status="patch_proposed",
        patch_risk_tier=2,
        affected_domain=affected_domain,
        patch_files=json.dumps(files) if files else None,
    )
    db.add(c)
    db.flush()
    if decision in ("approved", "rejected"):
        db.add(AuditLog(
            actor_type="admin",
            actor_name="test_operator",
            action_type=f"bugfix_{decision}",
            target_type="bugfix",
            target_id=str(c.id),
            status="completed",
        ))
        db.flush()
    return c


# ---------- _file_pattern ----------

def test_file_pattern_extracts_two_level_prefix():
    c = BugFixCandidate(patch_files=json.dumps(["app/services/foo.py"]))
    assert op._file_pattern(c) == "app/services/"


def test_file_pattern_single_segment():
    c = BugFixCandidate(patch_files=json.dumps(["README.md"]))
    assert op._file_pattern(c) == "README.md"


def test_file_pattern_none_when_empty():
    assert op._file_pattern(BugFixCandidate(patch_files=None)) is None
    assert op._file_pattern(BugFixCandidate(patch_files="[]")) is None


def test_file_pattern_handles_bad_json():
    assert op._file_pattern(BugFixCandidate(patch_files="not json")) is None


# ---------- Beta posterior ----------

def test_beta_posterior_mean_uniform_prior():
    # Beta(1, 1) → mean 0.5
    assert op._beta_posterior_mean(0, 0) == 0.5


def test_beta_posterior_mean_all_approved():
    # Beta(11, 1) → mean 11/12 ≈ 0.917
    assert abs(op._beta_posterior_mean(10, 0) - (11 / 12)) < 1e-9


def test_beta_posterior_mean_all_rejected():
    assert abs(op._beta_posterior_mean(0, 10) - (1 / 12)) < 1e-9


# ---------- Classification ----------

def test_classify_approve_high_posterior():
    rec, conf = op._classify(0.85, 10)
    assert rec == "approve"
    assert 0 <= conf <= 1


def test_classify_reject_low_posterior():
    rec, conf = op._classify(0.15, 10)
    assert rec == "reject"


def test_classify_unknown_neutral():
    rec, _ = op._classify(0.52, 10)
    assert rec == "unknown"


def test_classify_unknown_small_sample():
    rec, _ = op._classify(0.9, 1)
    assert rec == "unknown"


# ---------- End-to-end on the DB ----------

def test_predicts_approve_when_file_pattern_mostly_approved(db):
    suffix = uuid.uuid4().hex[:10]
    pattern_file = f"app/services/predict_approve_{suffix}.py"

    for _ in range(6):
        _make_tier2(db, files=[pattern_file], decision="approved")
    _make_tier2(db, files=[pattern_file], decision="rejected")

    victim = _make_tier2(db, files=[pattern_file])
    pred = op.predict_decision_for_candidate(db, victim)
    assert pred["recommendation"] == "approve"
    assert pred["signal"].startswith("file_pattern:")
    assert pred["sample_size"] >= op._MIN_SAMPLE


def test_predicts_reject_when_file_pattern_mostly_rejected(db):
    suffix = uuid.uuid4().hex[:10]
    pattern_file = f"app/api/predict_reject_{suffix}.py"

    for _ in range(6):
        _make_tier2(db, files=[pattern_file], decision="rejected")
    _make_tier2(db, files=[pattern_file], decision="approved")

    victim = _make_tier2(db, files=[pattern_file])
    pred = op.predict_decision_for_candidate(db, victim)
    assert pred["recommendation"] == "reject"


def test_predicts_unknown_when_no_history(db):
    victim = _make_tier2(
        db,
        files=[f"tests/cold_start_{uuid.uuid4().hex[:12]}.py"],
        affected_domain=f"cold_domain_{uuid.uuid4().hex[:8]}",
    )
    pred = op.predict_decision_for_candidate(db, victim)
    assert pred["recommendation"] == "unknown"
    assert pred["signal"] == "prior"
    assert pred["sample_size"] == 0


def test_falls_back_to_affected_domain(db):
    suffix = uuid.uuid4().hex[:8]
    domain = f"fallback_domain_{suffix}"
    for _ in range(6):
        _make_tier2(db, files=None, affected_domain=domain, decision="approved")

    victim = _make_tier2(db, files=None, affected_domain=domain)
    pred = op.predict_decision_for_candidate(db, victim)
    assert pred["recommendation"] == "approve"
    assert pred["signal"].startswith("domain:")
