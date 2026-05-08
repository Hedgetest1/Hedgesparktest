"""Lock the 2026-05-08 MerchantBrain v0.2 contract — limb dispatch.

v0.2 adds COORDINATE → email_orchestrator wiring for re_engagement_check.
The 3 other action_kinds (retention_outreach_email, recovery_digest,
proactive_nudge_compose) stay deferred to v0.3 because they need
founder-approved copy or a different limb (nudge_composer).

These tests pin the contract so a future refactor:
  - Cannot accidentally widen the dispatch map (silent firing of
    unmapped action_kinds with the wrong template).
  - Cannot remove the adversarial-review-before-dispatch gate.
  - Cannot dispatch when brain is disabled (defense in depth on top
    of tick()'s own gate).
  - Cannot fire when merchant has no contact email or paused emails.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text as _sql_text

from app.services import merchant_brain
from app.services.merchant_brain import (
    BrainDecisionDraft,
    MerchantState,
    _adversarial_review,
    _coordinate,
    _ACTION_EMAIL_MAP,
)


def _state(**kw) -> MerchantState:
    base = dict(
        shop_domain="t.myshopify.com",
        rars_total_eur=0.0,
        churn_risk_level="unknown",
        recent_orders_7d=0,
        recent_events_24h=0,
        hours_since_install=200.0,
        last_action_age_hours=None,
        last_chat_age_hours=None,
        last_brain_decision_age_hours=None,
        has_email_in_queue=False,
    )
    base.update(kw)
    return MerchantState(**base)


def _draft(action_kind: str = "re_engagement_check") -> BrainDecisionDraft:
    return BrainDecisionDraft(
        action_kind=action_kind,
        action_payload={},
        rationale="test",
        expected_outcome_metric="events_24h_resumed",
        outcome_window_hours=24,
        baseline_value=0.0,
    )


def _seed_merchant(db, shop: str, *, email: str | None = "merchant@test.com",
                   paused: bool = False) -> None:
    """Insert (or upsert) a Merchant row with the contact_email shape used
    by _adversarial_review's lookup. Relies on the table's defaults
    (installed_at, plan, etc.) so the test stays independent of schema
    columns it doesn't care about."""
    db.execute(
        _sql_text(
            "INSERT INTO merchants (shop_domain, contact_email, email_paused) "
            "VALUES (:s, :e, :p) "
            "ON CONFLICT (shop_domain) DO UPDATE SET "
            "  contact_email = EXCLUDED.contact_email, "
            "  email_paused = EXCLUDED.email_paused"
        ),
        {"s": shop, "e": email, "p": paused},
    )
    db.flush()


# -------------------------------------------------------------------------
# Action map invariants
# -------------------------------------------------------------------------

def test_action_email_map_only_re_engagement_wired():
    """v0.2 wires exactly 1 of 4 action_kinds. Adding a new wiring
    without copy review = silent merchant impact = test must catch it.
    """
    wired = {k for k, v in _ACTION_EMAIL_MAP.items() if v is not None}
    assert wired == {"re_engagement_check"}, (
        f"v0.2 must wire ONLY re_engagement_check (got {wired}). "
        "retention_outreach_email/recovery_digest/proactive_nudge_compose "
        "need founder-approved copy or a different limb — see merchant_brain "
        "_coordinate docstring + CLAUDE.md §1.5."
    )


def test_action_email_map_re_engagement_uses_existing_template():
    """re_engagement_check must dispatch via existing reengagement_drift —
    NOT a parallel template. Anti-fork guard: if someone adds a new
    'brain_re_engagement' email_type, the audit_email_registry would
    flag it but THIS test catches it earlier."""
    assert _ACTION_EMAIL_MAP["re_engagement_check"] == "reengagement_drift"


# -------------------------------------------------------------------------
# Adversarial-review-before-dispatch gate
# -------------------------------------------------------------------------

def test_adversarial_review_blocks_when_brain_disabled(db, monkeypatch):
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    blocked = _adversarial_review(
        db, _state(), _draft(), email_type="reengagement_drift"
    )
    assert blocked == "brain_disabled"


def test_adversarial_review_no_op_for_no_action_kinds(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    for kind in ("no_action_cooldown", "no_action_no_signal"):
        blocked = _adversarial_review(
            db, _state(), _draft(action_kind=kind),
            email_type="reengagement_drift",
        )
        assert blocked is None, f"{kind} must skip review (caller skips dispatch)"


def test_adversarial_review_blocks_when_email_type_missing(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    blocked = _adversarial_review(db, _state(), _draft(), email_type=None)
    assert blocked == "no_email_type_for_action_kind"


def test_adversarial_review_blocks_when_no_contact(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-no-contact.myshopify.com"
    _seed_merchant(db, shop, email=None)
    blocked = _adversarial_review(
        db, _state(shop_domain=shop), _draft(),
        email_type="reengagement_drift",
    )
    assert blocked == "no_contact_email_or_paused"


def test_adversarial_review_blocks_when_paused(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-paused.myshopify.com"
    _seed_merchant(db, shop, email="paused@test.com", paused=True)
    blocked = _adversarial_review(
        db, _state(shop_domain=shop), _draft(),
        email_type="reengagement_drift",
    )
    assert blocked == "no_contact_email_or_paused"


def test_adversarial_review_blocks_recent_brain_dispatch(db, monkeypatch):
    """Brain has dispatched same (shop, email_type) within the cooldown
    window — defense in depth on top of orchestrator dedup."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-recent-dispatch.myshopify.com"
    _seed_merchant(db, shop)

    # Insert a recent brain_decisions row with limb_dispatched + matching
    # email_type — this represents a successful dispatch within the
    # cooldown window.
    db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'prior', 're_engagement_check', '{}', "
            " 'prior', 'email_orchestrator', "
            " '{\"intent_id\":\"prior123\",\"email_type\":\"reengagement_drift\"}', "
            " 'events_24h_resumed', 24, 0.0, NOW() - INTERVAL '1 hour')"
        ),
        {"s": shop},
    )
    db.flush()

    blocked = _adversarial_review(
        db, _state(shop_domain=shop), _draft(),
        email_type="reengagement_drift",
    )
    assert blocked is not None
    assert blocked.startswith("brain_dispatch_cooldown_"), blocked


def test_adversarial_review_passes_after_cooldown(db, monkeypatch):
    """A 25h-old dispatch is past the 24h cooldown — review approves."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-past-cooldown.myshopify.com"
    _seed_merchant(db, shop)
    db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'old', 're_engagement_check', '{}', "
            " 'old', 'email_orchestrator', "
            " '{\"intent_id\":\"old123\",\"email_type\":\"reengagement_drift\"}', "
            " 'events_24h_resumed', 24, 0.0, NOW() - INTERVAL '25 hours')"
        ),
        {"s": shop},
    )
    db.flush()
    blocked = _adversarial_review(
        db, _state(shop_domain=shop), _draft(),
        email_type="reengagement_drift",
    )
    assert blocked is None


# -------------------------------------------------------------------------
# COORDINATE — dispatch wiring
# -------------------------------------------------------------------------

def test_coordinate_no_action_kinds_skip(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    for kind in ("no_action_cooldown", "no_action_no_signal"):
        limb, resp = _coordinate(db, _state(), _draft(action_kind=kind))
        assert limb is None
        assert resp == {}


def test_coordinate_unwired_kinds_defer_to_v03(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    for kind in ("retention_outreach_email", "recovery_digest",
                 "proactive_nudge_compose"):
        limb, resp = _coordinate(db, _state(), _draft(action_kind=kind))
        assert limb is None
        assert resp.get("deferred_to") == "v0.3_copy_or_limb_pending"
        assert resp.get("action_kind") == kind


def test_coordinate_re_engagement_dispatches_via_orchestrator(
    db, monkeypatch
):
    """Wired path: re_engagement_check → submit_intent(reengagement_drift).

    Mocks email_orchestrator.submit_intent so the test doesn't actually
    queue an email. Verifies the limb response captures intent_id +
    email_type so the brain_decisions ledger is honest."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-dispatch.myshopify.com"
    _seed_merchant(db, shop)

    captured: dict = {}

    def _fake_submit(_db, intent):
        captured["shop"] = intent.shop_domain
        captured["email_type"] = intent.email_type
        captured["producer"] = intent.producer
        captured["to_email"] = intent.to_email
        return "intent_xyz_123"

    monkeypatch.setattr(
        "app.services.email_orchestrator.submit_intent", _fake_submit
    )

    state = _state(shop_domain=shop, hours_since_install=72)
    limb, resp = _coordinate(db, state, _draft())

    assert limb == "email_orchestrator"
    assert resp == {
        "intent_id": "intent_xyz_123",
        "email_type": "reengagement_drift",
    }
    assert captured["shop"] == shop
    assert captured["email_type"] == "reengagement_drift"
    assert captured["producer"] == "merchant_brain"
    assert captured["to_email"] == "merchant@test.com"


def test_coordinate_re_engagement_blocked_when_no_email(db, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-no-email.myshopify.com"
    _seed_merchant(db, shop, email=None)
    limb, resp = _coordinate(db, _state(shop_domain=shop), _draft())
    assert limb is None
    assert resp.get("blocked_by_review") == "no_contact_email_or_paused"


def test_coordinate_re_engagement_blocked_when_brain_disabled(
    db, monkeypatch
):
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    shop = "brain-disabled.myshopify.com"
    _seed_merchant(db, shop)
    limb, resp = _coordinate(db, _state(shop_domain=shop), _draft())
    assert limb is None
    assert resp.get("blocked_by_review") == "brain_disabled"


def test_coordinate_re_engagement_records_orchestrator_failure(
    db, monkeypatch
):
    """Limb crash records as structured response, not silent failure."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-orch-crash.myshopify.com"
    _seed_merchant(db, shop)

    def _fake_submit(_db, _intent):
        raise RuntimeError("simulated orchestrator failure")

    monkeypatch.setattr(
        "app.services.email_orchestrator.submit_intent", _fake_submit
    )

    limb, resp = _coordinate(db, _state(shop_domain=shop), _draft())
    assert limb is None
    assert resp.get("error", "").startswith("submit_failed:")
