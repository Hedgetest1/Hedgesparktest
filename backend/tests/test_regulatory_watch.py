"""Tests for the regulatory watch pipeline."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def test_registry_has_rules():
    """Registry must have a non-trivial set of rules."""
    from app.services.regulatory_watch import REGULATORY_RULES
    assert len(REGULATORY_RULES) >= 20


def test_all_rules_have_required_fields():
    """Every rule must have all required fields."""
    from app.services.regulatory_watch import REGULATORY_RULES
    for rule in REGULATORY_RULES:
        assert rule.rule_id, f"Rule missing rule_id"
        assert rule.regulation, f"Rule {rule.rule_id} missing regulation"
        assert rule.article, f"Rule {rule.rule_id} missing article"
        assert rule.description, f"Rule {rule.rule_id} missing description"
        assert callable(rule.check_fn), f"Rule {rule.rule_id} check_fn not callable"
        assert rule.version >= 1, f"Rule {rule.rule_id} version < 1"


def test_rule_ids_are_unique():
    """Every rule must have a unique rule_id."""
    from app.services.regulatory_watch import REGULATORY_RULES
    ids = [r.rule_id for r in REGULATORY_RULES]
    assert len(ids) == len(set(ids)), "Duplicate rule IDs found"


def test_regulations_covered():
    """Registry must cover key regulations."""
    from app.services.regulatory_watch import REGULATORY_RULES
    regulations = {r.regulation for r in REGULATORY_RULES}
    assert "GDPR" in regulations
    assert "CCPA/CPRA" in regulations
    assert "OWASP" in regulations


def test_gdpr_articles_covered():
    """GDPR rules must cover core data-subject right articles."""
    from app.services.regulatory_watch import REGULATORY_RULES
    gdpr_articles = {r.article for r in REGULATORY_RULES if r.regulation == "GDPR"}
    for required in ["Art. 6/7", "Art. 15/20", "Art. 16", "Art. 17", "Art. 21", "Art. 33/34"]:
        assert required in gdpr_articles, f"Missing GDPR {required}"


def test_all_checks_pass_in_current_codebase(db):
    """In the current codebase, all enabled rules should pass."""
    from app.services.regulatory_watch import REGULATORY_RULES
    failures = []
    for rule in REGULATORY_RULES:
        if not rule.enabled:
            continue
        try:
            passed = rule.check_fn(db)
            if not passed:
                failures.append(rule.rule_id)
        except Exception as exc:
            failures.append(f"{rule.rule_id} (raised {type(exc).__name__})")
    assert failures == [], f"Regulatory rules failed: {failures}"


def test_run_regulatory_audit_returns_report(db):
    """Audit run produces a valid report dict."""
    from app.services.regulatory_watch import run_regulatory_audit
    # Bypass cooldown
    with patch("app.services.regulatory_watch._redis", return_value=None):
        report = run_regulatory_audit(db)
    assert "total_rules" in report
    assert "passed" in report
    assert "failed" in report
    assert report["total_rules"] >= 20


def test_run_regulatory_audit_cooldown():
    """Audit respects 6h cooldown."""
    from app.services.regulatory_watch import run_regulatory_audit
    mock_rc = MagicMock()
    now_ts = datetime.now(timezone.utc).timestamp()
    mock_rc.get.return_value = str(now_ts).encode()
    with patch("app.services.regulatory_watch._redis", return_value=mock_rc):
        report = run_regulatory_audit(MagicMock())
    assert report.get("skipped") is True


def test_failing_rule_emits_alert(db):
    """A failing rule must create a compliance_gap ops_alert."""
    from app.services.regulatory_watch import run_regulatory_audit, REGULATORY_RULES, RegRule
    from app.models.ops_alert import OpsAlert

    # Inject a fake failing rule
    fake_rule = RegRule(
        rule_id="TEST-fail-rule",
        regulation="TEST",
        article="T.1",
        description="This rule always fails",
        check_fn=lambda db: False,
        version=99,
    )
    original_rules = REGULATORY_RULES.copy()
    REGULATORY_RULES.clear()
    REGULATORY_RULES.append(fake_rule)

    try:
        with patch("app.services.regulatory_watch._redis", return_value=None):
            report = run_regulatory_audit(db)
        assert report["failed"] == 1
        assert report["new_alerts"] == 1

        alert = (
            db.query(OpsAlert)
            .filter(OpsAlert.alert_type == "compliance_gap")
            .filter(OpsAlert.source == "regulatory:TEST-fail-rule:v99")
            .first()
        )
        assert alert is not None
        assert "COMPLIANCE GAP" in alert.summary
    finally:
        REGULATORY_RULES.clear()
        REGULATORY_RULES.extend(original_rules)


def test_passing_rule_auto_resolves_alert(db):
    """A rule that now passes should auto-resolve its prior alert."""
    from app.services.regulatory_watch import run_regulatory_audit, REGULATORY_RULES, RegRule
    from app.models.ops_alert import OpsAlert

    # First create an unresolved alert
    alert = OpsAlert(
        severity="critical",
        source="regulatory:TEST-auto-resolve:v1",
        alert_type="compliance_gap",
        summary="test gap",
        resolved=False,
    )
    db.add(alert)
    db.flush()

    # Inject a now-passing rule with matching id
    fake_rule = RegRule(
        rule_id="TEST-auto-resolve",
        regulation="TEST",
        article="T.2",
        description="This rule now passes",
        check_fn=lambda db: True,
        version=1,
    )
    original_rules = REGULATORY_RULES.copy()
    REGULATORY_RULES.clear()
    REGULATORY_RULES.append(fake_rule)

    try:
        with patch("app.services.regulatory_watch._redis", return_value=None):
            report = run_regulatory_audit(db)
        assert report["auto_resolved"] == 1

        db.refresh(alert)
        assert alert.resolved is True
    finally:
        REGULATORY_RULES.clear()
        REGULATORY_RULES.extend(original_rules)


def test_dedup_does_not_double_alert(db):
    """Same failing rule on consecutive runs should not create duplicate alerts."""
    from app.services.regulatory_watch import run_regulatory_audit, REGULATORY_RULES, RegRule
    from app.models.ops_alert import OpsAlert

    fake_rule = RegRule(
        rule_id="TEST-dedup",
        regulation="TEST",
        article="T.3",
        description="dedup test",
        check_fn=lambda db: False,
        version=1,
    )
    original_rules = REGULATORY_RULES.copy()
    REGULATORY_RULES.clear()
    REGULATORY_RULES.append(fake_rule)

    try:
        with patch("app.services.regulatory_watch._redis", return_value=None):
            run_regulatory_audit(db)
            report2 = run_regulatory_audit(db)

        # Second run should detect existing alert and not create a new one
        assert report2["new_alerts"] == 0

        count = (
            db.query(OpsAlert)
            .filter(OpsAlert.source == "regulatory:TEST-dedup:v1")
            .count()
        )
        assert count == 1
    finally:
        REGULATORY_RULES.clear()
        REGULATORY_RULES.extend(original_rules)


def test_get_regulatory_summary():
    """Summary endpoint returns a valid structure."""
    from app.services.regulatory_watch import get_regulatory_summary
    summary = get_regulatory_summary()
    assert summary["total_rules"] >= 20
    assert "GDPR" in summary["regulations_covered"]
    assert isinstance(summary["rules_per_regulation"], dict)


def test_regulatory_watch_audit_log_raise_no_phantom_alert(db):
    """write_no_rollback class close 2026-05-19c — Finding-2 residual
    (Agent ab6d901947b397f8a). When write_audit_log raises AFTER the
    OpsAlert flush (its own un-guarded db.flush — a transient DB
    error), the per-rule savepoint rolls back the OpsAlert. The report
    MUST be truthful: the compliance gap is recorded (never lost —
    Finding-2 original concern) but new_alerts stays 0 (no alert
    persisted). This is the INTENTIONAL §0 correction of the HEAD
    original, which counted new_alerts=+1 then full-rolled-back the
    OpsAlert (a report claiming an alert its own rollback destroyed) —
    truthful > bug-compatible. Also proves no cascade (session clean).
    """
    from sqlalchemy import text
    from app.services.regulatory_watch import (
        run_regulatory_audit, REGULATORY_RULES, RegRule,
    )
    from app.models.ops_alert import OpsAlert

    fake_rule = RegRule(
        rule_id="TEST-F2-AUDITLOG-RAISE",
        regulation="GDPR",
        article="Art.5",
        description="synthetic non-compliant rule — write_audit_log-raise drift path",
        check_fn=lambda _db: False,  # non-compliant → new_alert branch
        suggested_action="code_change",
        version=1,
    )
    source_tag = "regulatory:TEST-F2-AUDITLOG-RAISE:v1"
    # Hermetic: clear any stale row for this synthetic source first so
    # the later `is None` assertion cannot flip on a re-run or a
    # concurrent writer (audit_test_hermeticity contract).
    db.query(OpsAlert).filter(OpsAlert.source == source_tag).delete()
    db.flush()
    original_rules = REGULATORY_RULES.copy()
    REGULATORY_RULES.clear()
    REGULATORY_RULES.append(fake_rule)
    try:
        with patch("app.services.regulatory_watch._redis", return_value=None), \
             patch("app.services.audit.write_audit_log",
                   side_effect=RuntimeError("simulated audit-row flush failure")):
            report = run_regulatory_audit(db)

        assert report.get("skipped") is not True
        # Gap is NEVER lost — recorded unconditionally before the
        # savepoint (Finding-2 original drift, fixed):
        assert report["failed"] == 1
        assert any(
            g["rule_id"] == "TEST-F2-AUDITLOG-RAISE" for g in report["gaps"]
        )
        # TRUTHFUL count: write_audit_log raised → savepoint rolled
        # back the OpsAlert → it did NOT persist → new_alerts == 0
        # (the HEAD original lied here with +1):
        assert report["new_alerts"] == 0
        # The OpsAlert was genuinely rolled back (savepoint isolation):
        assert (
            db.query(OpsAlert)
            .filter(OpsAlert.source == source_tag)
            .first()
        ) is None
        # No cascade — session clean for continued work:
        assert db.execute(text("SELECT 1")).scalar() == 1
    finally:
        REGULATORY_RULES.clear()
        REGULATORY_RULES.extend(original_rules)
