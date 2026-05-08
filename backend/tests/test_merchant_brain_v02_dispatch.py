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

def test_action_email_map_v03_full_wiring():
    """v0.3 (2026-05-08) wires 3 of 4 action_kinds via email_orchestrator.
    The 4th (proactive_nudge_compose) dispatches via action_task_queue
    so its email_type entry is None by design — that's NOT a deferral.

    If a refactor unmaps any of the 3 email-driven action_kinds (or maps
    proactive_nudge_compose to an email_type), this test catches it.
    """
    wired_email = {k for k, v in _ACTION_EMAIL_MAP.items() if v is not None}
    assert wired_email == {
        "re_engagement_check",
        "retention_outreach_email",
        "recovery_digest",
    }, f"unexpected email-driven action_kinds: {wired_email}"
    # proactive_nudge_compose is intentionally non-email
    assert _ACTION_EMAIL_MAP["proactive_nudge_compose"] is None


def test_action_email_map_email_types_match_governance():
    """Each wired email_type must exist in TEMPLATE_REGISTRY (governance
    invariant — no ghost email types in the brain dispatch map)."""
    from app.services.email_governance import TEMPLATE_REGISTRY
    for action_kind, email_type in _ACTION_EMAIL_MAP.items():
        if email_type is None:
            continue
        assert email_type in TEMPLATE_REGISTRY, (
            f"{action_kind} maps to {email_type!r} but it's not registered "
            f"in TEMPLATE_REGISTRY — audit_email_registry would catch this "
            f"at preflight, but the brain dispatch lock catches it earlier."
        )


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


def test_coordinate_retention_outreach_dispatches(db, monkeypatch):
    """v0.3: retention_outreach_email dispatches via email_orchestrator
    using the new retention_outreach template."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-retention.myshopify.com"
    _seed_merchant(db, shop)

    captured: dict = {}

    def _fake_submit(_db, intent):
        captured["email_type"] = intent.email_type
        captured["producer"] = intent.producer
        captured["context"] = intent.context
        return "intent_retention_xyz"

    monkeypatch.setattr(
        "app.services.email_orchestrator.submit_intent", _fake_submit
    )

    state = _state(
        shop_domain=shop, churn_risk_level="critical", recent_orders_7d=2,
    )
    limb, resp = _coordinate(
        db, state, _draft(action_kind="retention_outreach_email"),
    )
    assert limb == "email_orchestrator"
    assert resp == {
        "intent_id": "intent_retention_xyz",
        "email_type": "retention_outreach",
    }
    assert captured["email_type"] == "retention_outreach"
    assert captured["producer"] == "merchant_brain"
    assert captured["context"]["churn_risk_level"] == "critical"


def test_coordinate_recovery_digest_dispatches(db, monkeypatch):
    """v0.3: recovery_digest dispatches via email_orchestrator using
    the new recovery_digest template."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-recovery.myshopify.com"
    _seed_merchant(db, shop)

    captured: dict = {}

    def _fake_submit(_db, intent):
        captured["email_type"] = intent.email_type
        captured["producer"] = intent.producer
        captured["context"] = intent.context
        return "intent_recovery_xyz"

    monkeypatch.setattr(
        "app.services.email_orchestrator.submit_intent", _fake_submit
    )

    state = _state(
        shop_domain=shop, rars_total_eur=5000, last_action_age_hours=80,
    )
    limb, resp = _coordinate(
        db, state, _draft(action_kind="recovery_digest"),
    )
    assert limb == "email_orchestrator"
    assert resp == {
        "intent_id": "intent_recovery_xyz",
        "email_type": "recovery_digest",
    }
    assert captured["email_type"] == "recovery_digest"
    assert captured["context"]["rars_eur"] == 5000


def test_coordinate_proactive_nudge_queues_action_task(db, monkeypatch):
    """v0.3: proactive_nudge_compose queues an ActionTask with
    action_type=SCARCITY_NUDGE for the top-at-risk product."""
    from sqlalchemy import text as _sql_text
    from app.models.action_task import ActionTask
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-nudge.myshopify.com"
    _seed_merchant(db, shop)

    # Seed product_metrics so _pick_top_at_risk_product returns something.
    # last_event_at is bigint epoch_ms (not timestamp).
    db.execute(
        _sql_text(
            "INSERT INTO product_metrics "
            "(shop_domain, product_url, views_24h, cart_conversions_24h, last_event_at, updated_at) "
            "VALUES (:s, :u, 100, 1, "
            "        EXTRACT(EPOCH FROM NOW())::bigint * 1000, NOW()) "
            "ON CONFLICT (shop_domain, product_url) DO UPDATE SET "
            "  views_24h = EXCLUDED.views_24h, "
            "  cart_conversions_24h = EXCLUDED.cart_conversions_24h"
        ),
        {"s": shop, "u": "/products/brain-test-target"},
    )
    db.flush()

    state = _state(
        shop_domain=shop, rars_total_eur=2000, recent_events_24h=120,
    )
    limb, resp = _coordinate(
        db, state, _draft(action_kind="proactive_nudge_compose"),
    )
    assert limb == "action_task_queue"
    assert resp.get("product_url") == "/products/brain-test-target"
    assert resp.get("action_type") == "SCARCITY_NUDGE"
    assert isinstance(resp.get("action_task_id"), int)

    # Verify ActionTask row landed
    task = db.get(ActionTask, resp["action_task_id"])
    assert task is not None
    assert task.shop_domain == shop
    assert task.action_type == "SCARCITY_NUDGE"
    assert task.triggered_by == "merchant_brain"


def test_coordinate_proactive_nudge_no_top_product_skips(db, monkeypatch):
    """When the shop has no product_metrics rows, the brain skips the
    dispatch with structured `skipped: no_top_product`. No ActionTask
    row is created."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-nudge-empty.myshopify.com"
    _seed_merchant(db, shop)
    state = _state(shop_domain=shop)
    limb, resp = _coordinate(
        db, state, _draft(action_kind="proactive_nudge_compose"),
    )
    assert limb is None
    assert resp.get("skipped") == "no_top_product"


def test_coordinate_proactive_nudge_brain_disabled_blocks(db, monkeypatch):
    """Brain disabled → proactive_nudge_compose blocks at the gate
    (defense in depth on top of tick()'s own check)."""
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    shop = "brain-nudge-disabled.myshopify.com"
    _seed_merchant(db, shop)
    state = _state(shop_domain=shop)
    limb, resp = _coordinate(
        db, state, _draft(action_kind="proactive_nudge_compose"),
    )
    assert limb is None
    assert resp.get("blocked_by_review") == "brain_disabled"


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
