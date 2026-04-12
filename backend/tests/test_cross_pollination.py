"""Tests for D2 — cross-pollinated preventive candidates.

Contract:
  1. `cross_pollinate_from_proven_fix` only fires when the candidate has
     a `proven_effective` holdout measurement.
  2. Matching unresolved alerts on OTHER shops produce inherited
     BugFixCandidate rows tagged `source_type='cross_pollinated'`.
  3. Shops already covered by a prior pollination are skipped
     (idempotency).
  4. Pollination is capped at `_MAX_POLLINATIONS_PER_FIX`.
  5. Candidates with no matching alerts return a noop report.
  6. Candidates without a recoverable alert_type return a noop report.
  7. The inherited candidate's diff/files/test_command are copied
     verbatim from the proven fix (no LLM call).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.models.ops_alert import OpsAlert
from app.services import cross_pollination as xp
from app.services import fix_holdout_measurement as hm


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_proven_candidate(db, alert_type: str) -> tuple[BugFixCandidate, OpsAlert]:
    """Create a proven candidate bound to a fresh ops_alert."""
    seed_alert = OpsAlert(
        severity="warning",
        source="xp_test",
        alert_type=alert_type,
        shop_domain=f"seed_{uuid.uuid4().hex[:8]}.myshopify.com",
        summary="seed alert",
        resolved=True,
    )
    db.add(seed_alert)
    db.flush()

    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref=str(seed_alert.id),
        title=f"Proven fix for {alert_type}",
        summary="proven fix",
        status="applied",
        affected_domain="pipeline",
        patch_summary="proven summary",
        patch_diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
        patch_files='["app/x.py"]',
        test_command="pytest tests/test_x.py",
        patch_risk_tier=0,
        fix_confidence=95,
    )
    db.add(c)
    db.flush()
    return c, seed_alert


def _seed_alert_for_shop(db, alert_type: str, shop: str) -> OpsAlert:
    a = OpsAlert(
        severity="warning",
        source="xp_test",
        alert_type=alert_type,
        shop_domain=shop,
        summary=f"precondition on {shop}",
        resolved=False,
    )
    db.add(a)
    db.flush()
    return a


def _mark_proven(candidate_id: int, lift_eur: float = 50.0):
    """Patch get_measurement to return a proven_effective verdict."""
    return patch.object(
        xp,
        "_load_proven_verdict",
        return_value={
            "status": "proven_effective",
            "lift_eur": lift_eur,
            "p_value": 0.01,
            "n_treatment": 10,
            "n_control": 10,
        },
    )


# ---------- Happy path ----------

def test_pollinates_matching_shops(db):
    alert_type = f"fleet_bug_{uuid.uuid4().hex[:8]}"
    proven, _ = _make_proven_candidate(db, alert_type)
    shops = [
        f"shop_a_{uuid.uuid4().hex[:6]}.myshopify.com",
        f"shop_b_{uuid.uuid4().hex[:6]}.myshopify.com",
        f"shop_c_{uuid.uuid4().hex[:6]}.myshopify.com",
    ]
    for s in shops:
        _seed_alert_for_shop(db, alert_type, s)

    with _mark_proven(proven.id):
        report = xp.cross_pollinate_from_proven_fix(db, proven.id)

    assert report["status"] == "pollinated"
    assert report["created"] == 3
    assert report["alert_type"] == alert_type

    created = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.id.in_(report["created_ids"]))
        .all()
    )
    assert len(created) == 3
    for c in created:
        assert c.source_type == "cross_pollinated"
        assert c.patch_diff == proven.patch_diff
        assert c.patch_files == proven.patch_files
        assert c.test_command == proven.test_command
        ctx = json.loads(c.context_json)
        assert ctx["inherited_from"] == proven.id
        assert ctx["target_shop"] in shops


def test_skips_when_not_proven_effective(db):
    alert_type = f"unproven_{uuid.uuid4().hex[:8]}"
    proven, _ = _make_proven_candidate(db, alert_type)
    _seed_alert_for_shop(db, alert_type, f"victim_{uuid.uuid4().hex[:6]}.myshopify.com")

    # No patching → measurement is None → not_proven_effective
    report = xp.cross_pollinate_from_proven_fix(db, proven.id)
    assert report["status"] == "noop"
    assert report["skipped_reason"] == "not_proven_effective"
    assert report["created"] == 0


def test_idempotent_second_call(db):
    alert_type = f"idempotent_{uuid.uuid4().hex[:8]}"
    proven, _ = _make_proven_candidate(db, alert_type)
    shop = f"idem_{uuid.uuid4().hex[:6]}.myshopify.com"
    _seed_alert_for_shop(db, alert_type, shop)

    with _mark_proven(proven.id):
        first = xp.cross_pollinate_from_proven_fix(db, proven.id)
        second = xp.cross_pollinate_from_proven_fix(db, proven.id)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_reason"] == "no_matching_alerts"


def test_respects_max_cap(db):
    alert_type = f"capped_{uuid.uuid4().hex[:8]}"
    proven, _ = _make_proven_candidate(db, alert_type)
    # Create well above the cap
    for _ in range(xp._MAX_POLLINATIONS_PER_FIX + 5):
        _seed_alert_for_shop(
            db, alert_type,
            f"cap_{uuid.uuid4().hex[:8]}.myshopify.com",
        )

    with _mark_proven(proven.id):
        report = xp.cross_pollinate_from_proven_fix(db, proven.id)

    assert report["created"] == xp._MAX_POLLINATIONS_PER_FIX


def test_noop_when_no_matching_alerts(db):
    alert_type = f"lonely_{uuid.uuid4().hex[:8]}"
    proven, _ = _make_proven_candidate(db, alert_type)
    # No other shops have this alert_type

    with _mark_proven(proven.id):
        report = xp.cross_pollinate_from_proven_fix(db, proven.id)
    assert report["status"] == "noop"
    assert report["skipped_reason"] == "no_matching_alerts"


def test_noop_when_alert_type_unknown(db):
    """Candidate with opaque source_ref that can't be mapped to an
    alert_type returns alert_type_unknown."""
    c = BugFixCandidate(
        source_type="manual",
        source_ref="hand-filed",
        title="Manual candidate",
        summary="x",
        status="applied",
        affected_domain="pipeline",
        patch_diff="--- a/x\n+++ b/x\n@@\n-a\n+b\n",
        patch_files='["x"]',
    )
    db.add(c)
    db.flush()

    with _mark_proven(c.id):
        report = xp.cross_pollinate_from_proven_fix(db, c.id)
    assert report["skipped_reason"] == "alert_type_unknown"


def test_noop_when_candidate_missing(db):
    report = xp.cross_pollinate_from_proven_fix(db, 9_999_999_9)
    assert report["skipped_reason"] == "candidate_not_found"


def test_measure_outcome_invokes_pollination_when_db_passed(db, monkeypatch):
    """End-to-end: measure_outcome with db= passes through to cross-
    pollination when the verdict graduates to proven_effective."""
    alert_type = f"e2e_{uuid.uuid4().hex[:8]}"
    proven, _ = _make_proven_candidate(db, alert_type)
    _seed_alert_for_shop(
        db, alert_type, f"e2e_{uuid.uuid4().hex[:6]}.myshopify.com",
    )

    calls: list[int] = []

    def fake_pollinate(db_, cid):
        calls.append(cid)
        return {"status": "pollinated", "created": 1}

    monkeypatch.setattr(xp, "cross_pollinate_from_proven_fix", fake_pollinate)
    monkeypatch.setattr(
        "app.services.cross_pollination.cross_pollinate_from_proven_fix",
        fake_pollinate,
    )

    # Clearly significant treatment vs control → proven_effective verdict
    result = hm.measure_outcome(
        candidate_id=proven.id,
        treatment_outcomes=[-100, -110, -95, -105, -98, -102, -108, -96, -100, -104],
        control_outcomes=[0, 5, -2, 3, 1, -1, 4, 2, 0, -3],
        metric_name="rars_delta_eur",
        bigger_is_better=False,
        db=db,
    )
    assert result["status"] == "proven_effective"
    assert calls == [proven.id]


def test_measure_outcome_no_pollination_without_db():
    """Pure-math path (no db=) must not attempt pollination."""
    result = hm.measure_outcome(
        candidate_id=999_999_999,
        treatment_outcomes=[-100, -110, -95, -105, -98, -102, -108, -96, -100, -104],
        control_outcomes=[0, 5, -2, 3, 1, -1, 4, 2, 0, -3],
        metric_name="rars_delta_eur",
        bigger_is_better=False,
    )
    # No crash, verdict present
    assert result["status"] in ("proven_effective", "ineffective", "inconclusive", "measuring")
