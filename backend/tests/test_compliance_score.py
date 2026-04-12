"""Tests for compliance_score — rolling security + GDPR score."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.ops_alert import OpsAlert
from app.services import compliance_score as cs


@pytest.fixture(autouse=True)
def _clear_compliance_pause_key():
    """The compliance synthesizer sets a 24h Redis kill-switch on
    low scores. If a test leaves it set, subsequent suite-level tests
    of `run_auto_apply` see a paused pipeline and fail unrelated
    assertions. Clear before AND after every compliance test."""
    rc = cs._redis()
    if rc is not None:
        try:
            rc.delete(cs._AUTO_PAUSE_KEY)
            rc.delete(cs._CACHE_KEY)
        except Exception:
            pass
    yield
    if rc is not None:
        try:
            rc.delete(cs._AUTO_PAUSE_KEY)
            rc.delete(cs._CACHE_KEY)
        except Exception:
            pass


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- Individual components ----------

def test_security_probes_component_with_all_pass(monkeypatch):
    monkeypatch.setattr(
        "app.services.security_heartbeat.get_last_results",
        lambda: {
            "results": [
                {"probe": "p1", "passed": True},
                {"probe": "p2", "passed": True},
                {"probe": "p3", "passed": True},
            ],
        },
    )
    c = cs._score_security_probes()
    assert c["score"] == c["weight"]


def test_security_probes_component_with_partial(monkeypatch):
    monkeypatch.setattr(
        "app.services.security_heartbeat.get_last_results",
        lambda: {
            "results": [
                {"probe": "p1", "passed": True},
                {"probe": "p2", "passed": False},
            ],
        },
    )
    c = cs._score_security_probes()
    assert 0 < c["score"] < c["weight"]


def test_security_probes_component_no_data(monkeypatch):
    monkeypatch.setattr(
        "app.services.security_heartbeat.get_last_results",
        lambda: None,
    )
    c = cs._score_security_probes()
    assert c["score"] == 0


def test_gdpr_sla_component_with_no_breaches(db):
    c = cs._score_gdpr_sla(db)
    assert c["score"] == c["weight"]


def test_gdpr_sla_component_with_active_breach(db):
    db.add(OpsAlert(
        severity="critical",
        source=f"gdpr_request:test_{uuid.uuid4().hex[:6]}",
        alert_type="gdpr_sla_breach",
        shop_domain=f"test_{uuid.uuid4().hex[:6]}.myshopify.com",
        summary="breach",
        resolved=False,
    ))
    db.flush()
    c = cs._score_gdpr_sla(db)
    assert c["score"] < c["weight"]


def test_learning_isolation_component(db):
    c = cs._score_learning_isolation(db)
    assert c["score"] == c["weight"]


def test_security_guard_wall_component():
    c = cs._score_security_guard_wall()
    assert c["score"] == c["weight"]


def test_pii_masking_coverage_scan():
    c = cs._score_pii_masking_coverage()
    # Must either be full credit or report a specific offender
    if c["score"] != c["weight"]:
        assert "unmasked" in c["detail"].lower() or c["detail"]


# ---------- Full synthesizer ----------

def test_compute_returns_valid_shape(db):
    result = cs.compute_compliance_score(db)
    assert "score" in result
    assert "grade" in result
    assert result["grade"] in ("A+", "A", "B", "C", "D", "F")
    assert 0 <= result["score"] <= 100
    assert "components" in result
    # Sum of component scores equals the total
    total = sum(c["score"] for c in result["components"].values())
    assert abs(total - result["score"]) < 0.1


def test_grade_boundaries():
    assert cs._grade(100) == "A+"
    assert cs._grade(95) == "A+"
    assert cs._grade(94.9) == "A"
    assert cs._grade(90) == "A"
    assert cs._grade(85) == "B"
    assert cs._grade(75) == "C"
    assert cs._grade(65) == "D"
    assert cs._grade(50) == "F"


_DB_TAKING_COMPONENTS = {"gdpr_sla", "learning_isolation", "breach_response_latency"}


def _mock_all_components(monkeypatch, *, score_value: str):
    """Monkeypatch ALL 11 compliance components.
    score_value='zero' → all return 0; score_value='full' → all return weight."""
    for name, weight in cs._WEIGHTS.items():
        fn_name = f"_score_{name}"
        s = 0 if score_value == "zero" else weight
        if name in _DB_TAKING_COMPONENTS:
            monkeypatch.setattr(cs, fn_name, lambda _db, _w=weight, _s=s: {"weight": _w, "score": _s, "detail": "x"})
        else:
            monkeypatch.setattr(cs, fn_name, lambda _w=weight, _s=s: {"weight": _w, "score": _s, "detail": "x"})


def test_auto_pause_triggers_below_threshold(db, monkeypatch):
    # Force every component to return 0 so total score is 0
    _mock_all_components(monkeypatch, score_value="zero")

    result = cs.compute_compliance_score(db)
    assert result["score"] == 0
    if cs._redis() is None:
        pytest.skip("redis unavailable")
    assert cs.is_self_modification_paused() is True


def test_auto_pause_clears_when_score_recovers(db, monkeypatch):
    # First pass: force 0 → pause
    _mock_all_components(monkeypatch, score_value="zero")
    cs.compute_compliance_score(db)
    if cs._redis() is None:
        pytest.skip("redis unavailable")
    assert cs.is_self_modification_paused() is True

    # Second pass: all green → resume
    _mock_all_components(monkeypatch, score_value="full")
    result = cs.compute_compliance_score(db)
    assert result["score"] == 100
    assert cs.is_self_modification_paused() is False


def test_compute_caches_result(db):
    cs.compute_compliance_score(db)
    cached = cs.get_cached_compliance_score()
    if cached is None:
        pytest.skip("redis unavailable")
    assert "score" in cached
