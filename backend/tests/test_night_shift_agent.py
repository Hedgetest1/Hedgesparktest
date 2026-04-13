"""Tests for the Phase Ω⁵ Night Shift Agent."""
from __future__ import annotations

from unittest.mock import patch

from app.services import night_shift_agent as nsa


def _base_rars(total=0.0, comps=None):
    return {
        "total_at_risk_eur": total,
        "components": comps or [],
        "prevented_eur_this_month": 0.0,
    }


def _base_fusion(alerts=None):
    return {"alerts": alerts or []}


def _base_causal(hyps=None):
    return {"hypotheses": hyps or []}


def test_sleep_confidence_all_clear_capped_uncalibrated(monkeypatch):
    """All signals green → raw 100 but capped at 85 pre-calibration."""
    from app.services import night_shift_calibration as nsc
    monkeypatch.setattr(nsc, "is_calibrated", lambda shop: False)
    monkeypatch.setattr(nsc, "observation_count", lambda shop: 5)

    score, label, prov = nsa._compute_sleep_confidence(
        fusion_alert_count=0,
        critical_alerts=0,
        warning_alerts=0,
        prevented_eur_24h=5.0,
        rars_total=0.0,
        has_causal_hypothesis=True,
        shop_domain="test.myshopify.com",
    )
    assert prov["raw_score"] == 100
    assert score == 85  # capped
    assert "uncalibrated" in label
    assert not prov["calibrated"]
    # Every named contribution is present
    names = {c["name"] for c in prov["contributions"]}
    assert {"baseline", "no_critical_alerts", "low_warning_load", "low_rars_total",
            "prevention_evidence", "causal_known", "data_fresh"}.issubset(names)


def test_sleep_confidence_critical_drops_hard(monkeypatch):
    from app.services import night_shift_calibration as nsc
    monkeypatch.setattr(nsc, "is_calibrated", lambda shop: False)
    monkeypatch.setattr(nsc, "observation_count", lambda shop: 0)

    score, label, prov = nsa._compute_sleep_confidence(
        fusion_alert_count=4,
        critical_alerts=2,
        warning_alerts=2,
        prevented_eur_24h=0.0,
        rars_total=1200.0,
        has_causal_hypothesis=True,
        shop_domain="test.myshopify.com",
    )
    # baseline 30 + causal 10 + data_fresh 10 + low_warn 10 = 60  (critical, rars, prevention = 0)
    assert score == 60
    assert "guided autonomy" in label or "uncalibrated" in label


def test_sleep_confidence_calibrated_path_uncapped(monkeypatch):
    """Once calibrated, the cap lifts and full autonomy label is reachable."""
    from app.services import night_shift_calibration as nsc
    monkeypatch.setattr(nsc, "is_calibrated", lambda shop: True)
    monkeypatch.setattr(nsc, "observation_count", lambda shop: 100)

    score, label, prov = nsa._compute_sleep_confidence(
        fusion_alert_count=0,
        critical_alerts=0,
        warning_alerts=0,
        prevented_eur_24h=5.0,
        rars_total=0.0,
        has_causal_hypothesis=True,
        shop_domain="test.myshopify.com",
    )
    assert score == 100
    assert label == "full autonomy"
    assert prov["calibrated"] is True


def test_pick_top_action_prefers_rars_largest():
    rars = _base_rars(
        total=500,
        comps=[
            {"source": "refund_spike", "loss_eur": 200, "recommendation": "review refunds"},
            {"source": "abandoned_intent", "loss_eur": 300, "recommendation": "fire retarget"},
        ],
    )
    action, journal = nsa._pick_top_action(rars=rars, causal=_base_causal(), fusion=_base_fusion())
    assert action is not None
    assert action["kind"] == "rars_component"
    assert action["estimated_impact_eur"] == 300
    assert any(j.verdict == "kept" for j in journal)
    assert any(j.verdict == "watched" for j in journal)


def test_pick_top_action_falls_back_to_causal():
    causal = _base_causal(hyps=[
        {"label": "paid_efficiency_collapse", "confidence": 0.7, "recommended_action": "pause worst campaigns"},
    ])
    action, journal = nsa._pick_top_action(rars=_base_rars(), causal=causal, fusion=_base_fusion())
    assert action is not None
    assert action["kind"] == "causal"
    assert action["label"] == "Paid Efficiency Collapse"


def test_pick_top_action_falls_back_to_fusion():
    fusion = _base_fusion(alerts=[
        {"pattern": "demand_softening", "fusion_score": 72, "severity": "warning", "recommended_action": "refresh creative"},
    ])
    action, journal = nsa._pick_top_action(rars=_base_rars(), causal=_base_causal(), fusion=fusion)
    assert action is not None
    assert action["kind"] == "fusion_alert"


def test_pick_top_action_none_when_all_clean():
    action, journal = nsa._pick_top_action(rars=_base_rars(), causal=_base_causal(), fusion=_base_fusion())
    assert action is None


def test_narrative_quiet_night():
    headline, narrative, status = nsa._build_narrative(
        shop_domain="x.myshopify.com",
        rars=_base_rars(),
        fusion=_base_fusion(),
        causal=_base_causal(),
        prevented_eur_24h=42.0,
        top_action=None,
    )
    assert status == "quiet"
    assert "Quiet night" in headline
    assert "€42" in headline


def test_narrative_alarm_on_critical_alerts():
    fusion = _base_fusion(alerts=[{"pattern": "p", "severity": "critical", "fusion_score": 90}])
    headline, narrative, status = nsa._build_narrative(
        shop_domain="x.myshopify.com",
        rars=_base_rars(),
        fusion=fusion,
        causal=_base_causal(),
        prevented_eur_24h=0.0,
        top_action={"kind": "fusion_alert", "label": "p", "detail": "d", "estimated_impact_eur": 0},
    )
    assert status == "alarm"
    assert "critical" in headline


def test_generate_for_shop_idempotent(monkeypatch):
    """Second call should return cached doc, not re-compute."""
    calls = {"n": 0}

    def fake_rars(db, shop):
        calls["n"] += 1
        return _base_rars()

    monkeypatch.setattr(nsa, "_gather_rars", fake_rars)
    monkeypatch.setattr(nsa, "_gather_fusion", lambda db, shop: _base_fusion())
    monkeypatch.setattr(nsa, "_gather_causal", lambda db, shop: _base_causal())
    monkeypatch.setattr(nsa, "_gather_prevented_today", lambda db, shop: 0.0)

    class FakeRedis:
        def __init__(self):
            self.store = {}
        def get(self, k):
            return self.store.get(k)
        def setex(self, k, ttl, v):
            self.store[k] = v

    fake_rc = FakeRedis()

    with patch("app.core.redis_client._client", return_value=fake_rc):
        doc1 = nsa.generate_for_shop(None, "idem.myshopify.com")
        doc2 = nsa.generate_for_shop(None, "idem.myshopify.com")

    assert doc1["day"] == doc2["day"]
    assert calls["n"] == 1  # second call hit the cache


def test_generate_for_shop_force_rebuilds(monkeypatch):
    monkeypatch.setattr(nsa, "_gather_rars", lambda db, shop: _base_rars())
    monkeypatch.setattr(nsa, "_gather_fusion", lambda db, shop: _base_fusion())
    monkeypatch.setattr(nsa, "_gather_causal", lambda db, shop: _base_causal())
    monkeypatch.setattr(nsa, "_gather_prevented_today", lambda db, shop: 0.0)

    class FakeRedis:
        def __init__(self): self.store = {}
        def get(self, k): return self.store.get(k)
        def setex(self, k, ttl, v): self.store[k] = v

    with patch("app.core.redis_client._client", return_value=FakeRedis()):
        doc = nsa.generate_for_shop(None, "force.myshopify.com", force=True)

    assert doc["shop_domain"] == "force.myshopify.com"
    assert doc["status"] == "quiet"
    assert doc["sleep_confidence"] > 0
    assert "journal" in doc
