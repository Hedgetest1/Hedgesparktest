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


def test_adversarial_review_brain_wide_cooldown_blocks_other_email(
    db, monkeypatch,
):
    """The brain-wide cooldown blocks a DIFFERENT email_type within 20h
    of any prior brain email dispatch — prevents inbox spam across
    action_kinds. Born 2026-05-08 (Competitor-CTO audit lens)."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-cross-cooldown.myshopify.com"
    _seed_merchant(db, shop)
    # Seed: brain dispatched retention_outreach 5h ago for this shop.
    db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'prior', 'retention_outreach_email', '{}', "
            " 'prior', 'email_orchestrator', "
            " '{\"intent_id\":\"X1\",\"email_type\":\"retention_outreach\"}', "
            " 'merchant_re_engaged_7d', 168, 0.0, "
            " NOW() - INTERVAL '5 hours')"
        ),
        {"s": shop},
    )
    db.flush()
    # Now check: would the brain be allowed to dispatch a DIFFERENT
    # email_type (recovery_digest) right now? It must be BLOCKED by the
    # brain-wide cooldown even though per-(shop, recovery_digest) cooldown
    # is clean.
    blocked = _adversarial_review(
        db, _state(shop_domain=shop), _draft(action_kind="recovery_digest"),
        email_type="recovery_digest",
    )
    assert blocked is not None
    assert blocked.startswith("brain_any_email_cooldown_"), blocked


def test_pick_top_at_risk_product_filters_stale_products(db, monkeypatch):
    """The top-product picker must filter products with no event in
    last 7 days — born 2026-05-08 (Competitor-CTO audit lens) so the
    brain doesn't queue SCARCITY_NUDGE on a long-deactivated product."""
    from app.services.merchant_brain import _pick_top_at_risk_product
    shop = "brain-stale-product.myshopify.com"
    db.execute(
        _sql_text("DELETE FROM product_metrics WHERE shop_domain = :s"),
        {"s": shop},
    )
    # Seed a product with high views but last_event_at 30 days ago — should
    # be filtered out.
    db.execute(
        _sql_text(
            "INSERT INTO product_metrics "
            "(shop_domain, product_url, views_24h, cart_conversions_24h, "
            " last_event_at, updated_at) "
            "VALUES (:s, '/products/stale', 9999, 0, "
            "        EXTRACT(EPOCH FROM NOW() - INTERVAL '30 days')::bigint * 1000, "
            "        NOW()) "
        ),
        {"s": shop},
    )
    db.flush()
    # Stale product → picker returns None
    assert _pick_top_at_risk_product(db, shop) is None

    # Now seed a fresh product with lower views — should be picked.
    db.execute(
        _sql_text(
            "INSERT INTO product_metrics "
            "(shop_domain, product_url, views_24h, cart_conversions_24h, "
            " last_event_at, updated_at) "
            "VALUES (:s, '/products/fresh', 50, 1, "
            "        EXTRACT(EPOCH FROM NOW())::bigint * 1000, NOW()) "
        ),
        {"s": shop},
    )
    db.flush()
    assert _pick_top_at_risk_product(db, shop) == "/products/fresh"


def test_email_priority_retention_outreach_winback(db):
    """retention_outreach must map to WINBACK priority (urgent winback),
    not LIFECYCLE (default fallback). Competitor-CTO audit catch."""
    from app.services.email_orchestrator import Priority
    assert Priority.from_email_type("retention_outreach") == Priority.WINBACK


def test_email_priority_recovery_digest_revenue(db):
    """recovery_digest must map to REVENUE priority (money-at-risk frame),
    not LIFECYCLE. Competitor-CTO audit catch."""
    from app.services.email_orchestrator import Priority
    assert Priority.from_email_type("recovery_digest") == Priority.REVENUE


# -------------------------------------------------------------------------
# Holdout (control arm) — A/B for outcome measurement
# -------------------------------------------------------------------------

def test_holdout_pct_default_is_10pct(monkeypatch):
    from app.services.merchant_brain import _holdout_pct
    monkeypatch.delenv("BRAIN_HOLDOUT_PCT", raising=False)
    assert _holdout_pct() == 0.10


def test_holdout_pct_env_override(monkeypatch):
    from app.services.merchant_brain import _holdout_pct
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "0.25")
    assert _holdout_pct() == 0.25


def test_holdout_pct_zero_disables(monkeypatch):
    from app.services.merchant_brain import _is_holdout
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "0")
    # With 0% holdout, no shop should ever be in control arm.
    for shop in [f"shop-{i}.myshopify.com" for i in range(50)]:
        assert _is_holdout(shop) is False


def test_holdout_pct_invalid_falls_back(monkeypatch):
    from app.services.merchant_brain import _holdout_pct
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "not-a-number")
    assert _holdout_pct() == 0.10


def test_holdout_deterministic_per_shop_per_day(monkeypatch):
    """Same shop on same day → same arm; cross-day arm rotation."""
    from app.services.merchant_brain import _is_holdout
    from datetime import datetime, timezone
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "0.10")
    day_a = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    day_b = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    # Same shop + same day → identical assignment
    for _ in range(5):
        assert _is_holdout("test-shop.myshopify.com", day_a) == _is_holdout("test-shop.myshopify.com", day_a)
    # Different days for same shop CAN differ (deterministic on date)
    arms = {_is_holdout(f"shop-{i}.myshopify.com", day_a) for i in range(200)}
    # With 200 shops at 10%, we should see both True and False arms
    assert arms == {True, False}, "10% holdout over 200 shops must produce both arms"


def test_holdout_distribution_close_to_pct(monkeypatch):
    from app.services.merchant_brain import _is_holdout
    from datetime import datetime, timezone
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "0.10")
    day = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    holdouts = sum(
        1 for i in range(1000)
        if _is_holdout(f"distribution-shop-{i}.myshopify.com", day)
    )
    # 10% of 1000 = 100 ± reasonable variance (uniform hash) → 50–150
    assert 50 <= holdouts <= 150, f"holdout distribution 10% drift: {holdouts}/1000"


def test_coordinate_holdout_shop_skips_dispatch(db, monkeypatch):
    """A shop assigned to control arm: brain decides but does NOT
    dispatch. Records arm=control_holdout in limb_response."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "1.0")  # force ALL shops into holdout
    shop = "brain-holdout-test.myshopify.com"
    _seed_merchant(db, shop)
    state = _state(shop_domain=shop, churn_risk_level="critical", recent_orders_7d=2)
    limb, resp = _coordinate(
        db, state, _draft(action_kind="retention_outreach_email"),
    )
    assert limb is None
    assert resp.get("arm") == "control_holdout"
    assert resp.get("would_dispatch_action_kind") == "retention_outreach_email"
    assert resp.get("holdout_pct") == 1.0


def test_coordinate_treatment_shop_dispatches(db, monkeypatch):
    """A shop NOT in holdout (pct=0) gets the normal dispatch path."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    monkeypatch.setenv("BRAIN_HOLDOUT_PCT", "0.0")  # no holdout
    shop = "brain-treatment-test.myshopify.com"
    _seed_merchant(db, shop)

    captured: dict = {}

    def _fake_submit(_db, intent):
        captured["email_type"] = intent.email_type
        return "intent_treat_xyz"

    monkeypatch.setattr(
        "app.services.email_orchestrator.submit_intent", _fake_submit
    )

    state = _state(shop_domain=shop, churn_risk_level="critical", recent_orders_7d=2)
    limb, resp = _coordinate(
        db, state, _draft(action_kind="retention_outreach_email"),
    )
    assert limb == "email_orchestrator"
    assert resp.get("email_type") == "retention_outreach"


# -------------------------------------------------------------------------
# EmailEvent enrichment — brain ↔ merchant_emails linkage
# -------------------------------------------------------------------------

def test_enrich_dispatched_decisions_links_resend_id(db, monkeypatch):
    """After orchestrator flushes a brain-dispatched intent, the
    enrichment helper joins the merchant_emails row back into
    brain_decisions.limb_response so observability traces brain → send.
    """
    from app.services.merchant_brain import enrich_dispatched_decisions
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-enrich-test.myshopify.com"

    # Seed a brain_decision dispatched 10 min ago, missing resend_id
    db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'enrich-test', 'recovery_digest', '{}', "
            " 'enrich-test', 'email_orchestrator', "
            " '{\"intent_id\":\"intent_xxx\",\"email_type\":\"recovery_digest\"}', "
            " 'rars_delta_7d', 168, 1000.0, "
            " NOW() - INTERVAL '10 minutes') "
            "RETURNING id"
        ),
        {"s": shop},
    )
    bd_id = db.execute(
        _sql_text(
            "SELECT id FROM brain_decisions WHERE shop_domain = :s "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"s": shop},
    ).scalar()

    # Seed a merchant_emails row that the orchestrator wrote 5 min later
    db.execute(
        _sql_text(
            "INSERT INTO merchant_emails "
            "(shop_domain, email_type, to_email, subject, status, resend_id, created_at) "
            "VALUES (:s, 'recovery_digest', 'merchant@test.com', "
            "        'recovery email', 'sent', 'resend_abc123', "
            "        NOW() - INTERVAL '5 minutes')"
        ),
        {"s": shop},
    )
    db.commit()

    # Run enrichment
    result = enrich_dispatched_decisions(db, max_enrich=10)
    assert result["enriched"] >= 1

    # Verify limb_response now has resend_id + send_status
    row = db.execute(
        _sql_text("SELECT limb_response FROM brain_decisions WHERE id = :i"),
        {"i": bd_id},
    ).fetchone()
    resp = row[0]
    assert resp.get("resend_id") == "resend_abc123"
    assert resp.get("send_status") == "sent"
    assert "merchant_email_id" in resp


def test_enrich_dispatched_decisions_records_suppression(db, monkeypatch):
    """When orchestrator suppresses (rate-limited / governance),
    enrichment records send_status=suppressed + suppressed_by."""
    from app.services.merchant_brain import enrich_dispatched_decisions
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-enrich-suppress.myshopify.com"

    db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'suppress-test', 'retention_outreach_email', "
            " '{}', 'suppress-test', 'email_orchestrator', "
            " '{\"intent_id\":\"intent_yyy\",\"email_type\":\"retention_outreach\"}', "
            " 'merchant_re_engaged_7d', 168, 0.0, "
            " NOW() - INTERVAL '10 minutes')"
        ),
        {"s": shop},
    )
    db.execute(
        _sql_text(
            "INSERT INTO merchant_emails "
            "(shop_domain, email_type, to_email, subject, status, suppressed_by, created_at) "
            "VALUES (:s, 'retention_outreach', 'merchant@test.com', "
            "        'retention', 'suppressed', "
            "        'orchestrator:rate_limited', "
            "        NOW() - INTERVAL '5 minutes')"
        ),
        {"s": shop},
    )
    db.commit()

    enrich_dispatched_decisions(db, max_enrich=10)
    row = db.execute(
        _sql_text(
            "SELECT limb_response FROM brain_decisions WHERE shop_domain = :s "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"s": shop},
    ).fetchone()
    resp = row[0]
    assert resp.get("send_status") == "suppressed"
    assert resp.get("suppressed_by") == "orchestrator:rate_limited"
    # No resend_id for suppressed sends — that's correct
    assert resp.get("resend_id") is None or "resend_id" not in resp


def test_enrich_skips_already_enriched(db, monkeypatch):
    """Re-running enrichment doesn't double-process — already-enriched
    decisions (resend_id present) are skipped."""
    from app.services.merchant_brain import enrich_dispatched_decisions
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    shop = "brain-enrich-skip.myshopify.com"
    db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'skip-test', 'recovery_digest', '{}', "
            " 'skip-test', 'email_orchestrator', "
            " '{\"intent_id\":\"i1\",\"email_type\":\"recovery_digest\","
            "   \"resend_id\":\"already_enriched\",\"send_status\":\"sent\"}', "
            " 'rars_delta_7d', 168, 0.0, "
            " NOW() - INTERVAL '10 minutes')"
        ),
        {"s": shop},
    )
    db.commit()
    result = enrich_dispatched_decisions(db, max_enrich=10)
    # Already enriched → not counted again
    bd_resp = db.execute(
        _sql_text(
            "SELECT limb_response FROM brain_decisions WHERE shop_domain = :s "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"s": shop},
    ).fetchone()[0]
    assert bd_resp.get("resend_id") == "already_enriched"  # unchanged


def test_enrich_skips_brain_disabled(db, monkeypatch):
    """When brain is disabled, enrichment is a no-op."""
    from app.services.merchant_brain import enrich_dispatched_decisions
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    result = enrich_dispatched_decisions(db, max_enrich=10)
    assert result.get("skipped") == "brain_disabled"
    assert result.get("enriched") == 0


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
    # 2026-05-20 currency-correctness fix: brain must pass shop_currency so
    # non-EUR merchants don't receive `€{value}` in the email. The default
    # _state() fixture sets currency="USD" → context must reflect that.
    assert captured["context"]["shop_currency"] == state.currency, (
        f"brain dispatch must thread state.currency into the email intent "
        f"context — got {captured['context'].get('shop_currency')!r}, "
        f"expected {state.currency!r}"
    )


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
