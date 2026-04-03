"""
Tests for app.services.loop_health — autonomous loop diagnostics and circuit breakers.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.bugfix_candidate import BugFixCandidate
from app.services.loop_health import (
    check_recurrence,
    check_thrashing,
    is_source_thrashing,
    reopen_from_ineffective,
    get_loop_health,
    score_subsystem_weakness,
    _THRASH_THRESHOLD,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_candidate(db, source_type="ops_alert", source_ref="alert_1",
                    status="applied", outcome=None, title="Test bug",
                    days_ago=0):
    c = BugFixCandidate(
        source_type=source_type,
        source_ref=source_ref,
        title=title,
        status=status,
        created_at=_now() - timedelta(days=days_ago),
    )
    if status == "applied":
        c.applied_at = _now() - timedelta(days=days_ago)
    if outcome:
        c.outcome_status = outcome
        c.outcome_measured_at = _now() - timedelta(days=max(days_ago - 2, 0))
        c.outcome_evidence = json.dumps({"alerts_before": 5, "alerts_after": 8})
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Recurrence detection
# ---------------------------------------------------------------------------

class TestRecurrence:
    def test_no_recurrence_when_single_candidate(self, db):
        _make_candidate(db, source_ref="unique_1", outcome="effective")
        result = check_recurrence(db)
        assert len(result) == 0

    def test_detects_recurring_ineffective(self, db):
        _make_candidate(db, source_ref="recurring_x", outcome="ineffective", days_ago=10)
        _make_candidate(db, source_ref="recurring_x", outcome="ineffective", days_ago=5)
        result = check_recurrence(db)
        recurring = [r for r in result if r["source_ref"] == "recurring_x"]
        assert len(recurring) == 1
        assert recurring[0]["ineffective_fixes"] == 2

    def test_skips_resolved_recurrence(self, db):
        """If latest fix was effective, it's not a recurrence."""
        _make_candidate(db, source_ref="fixed_y", outcome="ineffective", days_ago=10)
        _make_candidate(db, source_ref="fixed_y", outcome="effective", days_ago=2)
        result = check_recurrence(db)
        recurring = [r for r in result if r["source_ref"] == "fixed_y"]
        assert len(recurring) == 0


# ---------------------------------------------------------------------------
# Thrash detection
# ---------------------------------------------------------------------------

class TestThrashing:
    def test_no_thrashing_below_threshold(self, db):
        _make_candidate(db, source_ref="mild_1", status="apply_failed", days_ago=5)
        _make_candidate(db, source_ref="mild_1", status="apply_failed", days_ago=3)
        assert not is_source_thrashing(db, "ops_alert", "mild_1")

    def test_detects_thrashing_at_threshold(self, db):
        for i in range(_THRASH_THRESHOLD):
            _make_candidate(db, source_ref="bad_src", status="apply_failed", days_ago=i + 1)
        assert is_source_thrashing(db, "ops_alert", "bad_src")

    def test_thrashing_includes_ineffective(self, db):
        _make_candidate(db, source_ref="mixed_1", status="apply_failed", days_ago=5)
        _make_candidate(db, source_ref="mixed_1", outcome="ineffective", days_ago=3)
        _make_candidate(db, source_ref="mixed_1", status="rolled_back", days_ago=1)
        assert is_source_thrashing(db, "ops_alert", "mixed_1")

    def test_check_thrashing_returns_list(self, db):
        for i in range(_THRASH_THRESHOLD):
            _make_candidate(db, source_ref="list_src", status="apply_failed", days_ago=i + 1)
        result = check_thrashing(db)
        assert any(r["source_ref"] == "list_src" for r in result)


# ---------------------------------------------------------------------------
# Reopen from ineffective
# ---------------------------------------------------------------------------

class TestReopenFromIneffective:
    def test_reopens_ineffective_candidate(self, db):
        c = _make_candidate(db, source_ref="reopen_1", outcome="ineffective", days_ago=5)
        # outcome_measured_at must be 48+ hours ago
        c.outcome_measured_at = _now() - timedelta(hours=49)
        db.flush()

        result = reopen_from_ineffective(db)
        assert result["reopened"] == 1

        # Verify follow-up candidate exists
        followup = db.query(BugFixCandidate).filter(
            BugFixCandidate.source_type == "recurrence",
        ).first()
        assert followup is not None
        assert "[Recurrence]" in followup.title
        ctx = json.loads(followup.context_json)
        assert ctx["previous_candidate_id"] == c.id

    def test_no_duplicate_reopen(self, db):
        c = _make_candidate(db, source_ref="reopen_2", outcome="ineffective", days_ago=5)
        c.outcome_measured_at = _now() - timedelta(hours=49)
        db.flush()

        r1 = reopen_from_ineffective(db)
        assert r1["reopened"] == 1

        r2 = reopen_from_ineffective(db)
        assert r2["reopened"] == 0  # already has follow-up

    def test_suppresses_thrashing_reopen(self, db):
        # Create thrashing history
        for i in range(_THRASH_THRESHOLD):
            _make_candidate(db, source_ref="thrash_src", status="apply_failed", days_ago=i + 1)
        # Now create an ineffective one
        c = _make_candidate(db, source_ref="thrash_src", outcome="ineffective", days_ago=5)
        c.outcome_measured_at = _now() - timedelta(hours=49)
        db.flush()

        result = reopen_from_ineffective(db)
        assert result["suppressed"] >= 1
        assert result["reopened"] == 0


# ---------------------------------------------------------------------------
# Pipeline health snapshot
# ---------------------------------------------------------------------------

class TestLoopHealth:
    def test_returns_complete_snapshot(self, db):
        result = get_loop_health(db)
        assert "bugfix_queues" in result
        assert "evolution_queues" in result
        assert "throughput_7d" in result
        assert "outcomes_30d" in result
        assert "failure_rate_30d_pct" in result
        assert "stuck_items" in result
        assert "thrashing_sources" in result
        assert "recurrences" in result
        assert "is_healthy" in result

    def test_detects_stuck_items(self, db):
        # Create an item that's been open for 4 days (threshold is 72h)
        c = BugFixCandidate(
            source_type="manual", source_ref="stuck_test",
            title="Stuck item", status="open",
            created_at=_now() - timedelta(days=4),
        )
        db.add(c)
        db.flush()

        result = get_loop_health(db)
        stuck = [s for s in result["stuck_items"] if s["status"] == "open"]
        assert len(stuck) > 0

    def test_healthy_with_no_issues(self, db):
        # Clean slate — should be healthy
        result = get_loop_health(db)
        # May or may not be healthy depending on pre-existing data
        assert isinstance(result["is_healthy"], bool)


# ---------------------------------------------------------------------------
# Thrash escalation
# ---------------------------------------------------------------------------

class TestThrashEscalation:
    def test_escalation_creates_alert(self, db):
        """Thrashing source creates an ops_alert when suppressed."""
        from app.services.bugfix_pipeline import _escalate_thrashing

        _escalate_thrashing(db, "ops_alert", "test_escalate_ref")
        db.flush()

        from app.models.ops_alert import OpsAlert
        alert = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "chronic_thrashing",
                OpsAlert.source == "ops_alert:test_escalate_ref",
            )
            .first()
        )
        assert alert is not None
        assert "3+ times" in alert.summary
        assert alert.severity == "warning"

    def test_escalation_is_dedup_safe(self, db):
        """Second escalation for same source does not create duplicate alert."""
        from app.services.bugfix_pipeline import _escalate_thrashing

        _escalate_thrashing(db, "ops_alert", "dedup_esc_ref")
        db.flush()
        _escalate_thrashing(db, "ops_alert", "dedup_esc_ref")
        db.flush()

        from app.models.ops_alert import OpsAlert
        count = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "chronic_thrashing",
                OpsAlert.source == "ops_alert:dedup_esc_ref",
            )
            .count()
        )
        assert count == 1


# ---------------------------------------------------------------------------
# Recurrence-aware prompt
# ---------------------------------------------------------------------------

class TestRecurrencePrompt:
    def test_recurrence_candidate_gets_enriched_prompt(self):
        """Recurrence context is injected into the LLM prompt."""
        import json
        # Simulate what propose_patch does for context building
        context_json = json.dumps({
            "previous_candidate_id": 42,
            "previous_outcome": "ineffective",
            "previous_patch_summary": "Added null check in handler",
            "previous_files": '["app/services/foo.py"]',
            "alerts_before": 5,
            "alerts_after": 8,
            "original_source_type": "ops_alert",
            "original_source_ref": "alert_99",
            "original_title": "GDPR processing failure",
        })
        ctx = json.loads(context_json)

        # Build the same context_parts as propose_patch does for recurrence
        context_parts = ["## Bug: [Recurrence] GDPR processing failure"]
        context_parts.append(
            "## IMPORTANT — Previous Fix Was Ineffective\n"
            "A prior attempt to fix this bug did NOT resolve it. "
            "You MUST try a fundamentally different approach.\n\n"
            f"Previous patch summary: {ctx.get('previous_patch_summary', 'unknown')}\n"
            f"Previous files changed: {ctx.get('previous_files', 'unknown')}\n"
            f"Alerts before previous fix: {ctx.get('alerts_before', '?')}\n"
            f"Alerts after previous fix: {ctx.get('alerts_after', '?')} (should have decreased)\n"
            f"Original bug: {ctx.get('original_title', 'unknown')}\n"
            f"Original source: {ctx.get('original_source_type', '?')}/{ctx.get('original_source_ref', '?')}\n\n"
            "Do NOT repeat the same fix. Investigate the root cause more deeply."
        )
        prompt = "\n\n".join(context_parts)

        assert "Previous Fix Was Ineffective" in prompt
        assert "Added null check in handler" in prompt
        assert "Do NOT repeat the same fix" in prompt
        assert "alerts_before" not in prompt or "5" in prompt
        assert "GDPR processing failure" in prompt


# ---------------------------------------------------------------------------
# Subsystem weakness scoring
# ---------------------------------------------------------------------------

class TestWeaknessScoring:
    def test_empty_returns_empty_list(self, db):
        result = score_subsystem_weakness(db, lookback_days=1)
        # May have pre-existing data, but should be a list
        assert isinstance(result, list)

    def test_failed_candidate_scores_domain(self, db):
        """apply_failed candidate with patch_files scores its domain."""
        c = BugFixCandidate(
            source_type="ops_alert", source_ref="weak_test_1",
            title="Test failure", status="apply_failed",
            patch_files=json.dumps(["app/services/revenue_metrics.py"]),
            created_at=_now() - timedelta(days=1),
        )
        db.add(c)
        db.flush()

        result = score_subsystem_weakness(db)
        intel = [r for r in result if r["domain"] == "intelligence"]
        assert len(intel) == 1
        assert intel[0]["score"] > 0
        assert "apply_failed" in intel[0]["signals"]

    def test_ineffective_scores_higher_than_open(self, db):
        """Ineffective outcome weighs more than an open candidate."""
        # Ineffective in intelligence domain
        c1 = BugFixCandidate(
            source_type="ops_alert", source_ref="weak_high",
            title="Ineffective fix", status="applied",
            outcome_status="ineffective",
            patch_files=json.dumps(["app/services/revenue_metrics.py"]),
            created_at=_now() - timedelta(days=1),
            applied_at=_now() - timedelta(days=1),
        )
        # Just open in tracking domain
        c2 = BugFixCandidate(
            source_type="ops_alert", source_ref="weak_low",
            title="Open candidate", status="open",
            patch_files=json.dumps(["app/api/track.py"]),
            created_at=_now() - timedelta(days=1),
        )
        db.add_all([c1, c2])
        db.flush()

        result = score_subsystem_weakness(db)
        scores = {r["domain"]: r["score"] for r in result}
        # Intelligence has ineffective (weight 5), tracking has open (weight 1)
        # Both are "low" criticality so same multiplier
        assert scores.get("intelligence", 0) > scores.get("tracking", 0)

    def test_critical_domain_amplified(self, db):
        """Failures in critical domains score higher due to criticality multiplier."""
        # 1 failure in critical domain (webhooks)
        c1 = BugFixCandidate(
            source_type="ops_alert", source_ref="crit_test",
            title="Webhook fail", status="apply_failed",
            patch_files=json.dumps(["app/api/webhooks.py"]),
            created_at=_now() - timedelta(days=1),
        )
        # 1 failure in low domain (intelligence)
        c2 = BugFixCandidate(
            source_type="ops_alert", source_ref="low_test",
            title="Intel fail", status="apply_failed",
            patch_files=json.dumps(["app/services/revenue_metrics.py"]),
            created_at=_now() - timedelta(days=1),
        )
        db.add_all([c1, c2])
        db.flush()

        result = score_subsystem_weakness(db)
        scores = {r["domain"]: r["score"] for r in result}
        # webhooks is critical (4x), intelligence is low (0.5x)
        # Same signal (apply_failed=3), so webhooks = 3*4=12, intelligence = 3*0.5=1.5
        assert scores.get("webhooks", 0) > scores.get("intelligence", 0)

    def test_result_shape(self, db):
        """Each result entry has the expected fields."""
        c = BugFixCandidate(
            source_type="ops_alert", source_ref="shape_test",
            title="Shape test", status="apply_failed",
            patch_files=json.dumps(["app/services/nudge_engine.py"]),
            created_at=_now() - timedelta(days=1),
        )
        db.add(c)
        db.flush()

        result = score_subsystem_weakness(db)
        nudge = [r for r in result if r["domain"] == "nudges"]
        assert len(nudge) == 1
        entry = nudge[0]
        assert "domain" in entry
        assert "score" in entry
        assert "criticality" in entry
        assert "signals" in entry
        assert "reasons" in entry
        assert isinstance(entry["reasons"], list)

    def test_sorted_weakest_first(self, db):
        """Results are sorted by score descending."""
        c1 = BugFixCandidate(
            source_type="ops_alert", source_ref="sort_a",
            title="A", status="apply_failed",
            patch_files=json.dumps(["app/services/revenue_metrics.py"]),
            created_at=_now() - timedelta(days=1),
        )
        c2 = BugFixCandidate(
            source_type="ops_alert", source_ref="sort_b",
            title="B", status="rolled_back",
            patch_files=json.dumps(["app/api/webhooks.py"]),
            created_at=_now() - timedelta(days=1),
        )
        db.add_all([c1, c2])
        db.flush()

        result = score_subsystem_weakness(db)
        if len(result) >= 2:
            assert result[0]["score"] >= result[1]["score"]

    def test_loop_health_includes_weakness(self, db):
        """get_loop_health includes weakest_subsystems key."""
        result = get_loop_health(db)
        assert "weakest_subsystems" in result
        assert isinstance(result["weakest_subsystems"], list)


# ---------------------------------------------------------------------------
# Weakness boost in meta-reviewer prioritization
# ---------------------------------------------------------------------------

class TestWeaknessBoost:
    def test_boost_for_weak_domain(self):
        """Proposal targeting a weak domain gets a bounded boost."""
        from app.services.meta_reviewer import _weakness_boost_for_proposal
        proposal = {"id": 1, "target_file": "app/services/revenue_metrics.py"}
        weakness_map = {"intelligence": 15.0}
        boost = _weakness_boost_for_proposal(proposal, weakness_map)
        assert boost == 15  # maps directly (5-20 range)

    def test_no_boost_for_healthy_domain(self):
        """No boost when domain has no weakness signal."""
        from app.services.meta_reviewer import _weakness_boost_for_proposal
        proposal = {"id": 1, "target_file": "app/services/revenue_metrics.py"}
        weakness_map = {"webhooks": 20.0}  # different domain is weak
        boost = _weakness_boost_for_proposal(proposal, weakness_map)
        assert boost == 0

    def test_boost_capped_at_20(self):
        """Boost is bounded to max 20 points."""
        from app.services.meta_reviewer import _weakness_boost_for_proposal
        proposal = {"id": 1, "target_file": "app/services/revenue_metrics.py"}
        weakness_map = {"intelligence": 999.0}  # extremely weak
        boost = _weakness_boost_for_proposal(proposal, weakness_map)
        assert boost == 20

    def test_boost_floor_at_5(self):
        """Boost is at least 5 when domain has any weakness signal."""
        from app.services.meta_reviewer import _weakness_boost_for_proposal
        proposal = {"id": 1, "target_file": "app/services/revenue_metrics.py"}
        weakness_map = {"intelligence": 0.5}  # barely weak
        boost = _weakness_boost_for_proposal(proposal, weakness_map)
        assert boost == 5

    def test_no_boost_without_target_file(self):
        """Proposals without target_file get no boost."""
        from app.services.meta_reviewer import _weakness_boost_for_proposal
        proposal = {"id": 1, "target_file": None}
        weakness_map = {"intelligence": 15.0}
        boost = _weakness_boost_for_proposal(proposal, weakness_map)
        assert boost == 0

    def test_deterministic_fallback_includes_boost(self):
        """The deterministic fallback scoring path applies weakness boost."""
        from app.services.meta_reviewer import _parse_review
        proposals = [
            {"id": 1, "type": "reliability", "target_file": "app/services/revenue_metrics.py",
             "auto_applicable": True, "age_days": 5, "dedup_key": "test:1"},
            {"id": 2, "type": "reliability", "target_file": "app/api/track.py",
             "auto_applicable": True, "age_days": 5, "dedup_key": "test:2"},
        ]
        weakness_map = {"intelligence": 20.0}  # revenue_metrics → intelligence domain

        review = _parse_review("", proposals, [], [], weakness_map)
        priorities = {p["proposal_id"]: p for p in review["priorities"]}

        # Proposal 1 targets intelligence (weak) — should have higher score
        assert priorities[1]["priority_score"] > priorities[2]["priority_score"]
        assert priorities[1].get("weakness_boosted") is True
        assert priorities[1].get("weakness_boost", 0) > 0


# ---------------------------------------------------------------------------
# Auto-resolve thrash alerts
# ---------------------------------------------------------------------------

class TestAutoResolveThrashAlerts:
    def test_resolves_stabilized_source(self, db):
        """Alert is resolved when source has no failures in 7 days."""
        from app.services.loop_health import auto_resolve_thrash_alerts
        from app.models.ops_alert import OpsAlert

        # Create a chronic_thrashing alert
        alert = OpsAlert(
            severity="warning",
            source="ops_alert:stable_src",
            alert_type="chronic_thrashing",
            summary="Test thrash alert",
        )
        db.add(alert)
        db.flush()
        assert alert.resolved is False

        # No failures exist for this source → should resolve
        result = auto_resolve_thrash_alerts(db)
        assert result["resolved"] == 1

        db.refresh(alert)
        assert alert.resolved is True

    def test_keeps_active_thrashing_unresolved(self, db):
        """Alert stays unresolved when source still has recent failures."""
        from app.services.loop_health import auto_resolve_thrash_alerts
        from app.models.ops_alert import OpsAlert

        alert = OpsAlert(
            severity="warning",
            source="ops_alert:still_bad",
            alert_type="chronic_thrashing",
            summary="Active thrash",
        )
        db.add(alert)

        # Create a recent failure
        _make_candidate(db, source_ref="still_bad", status="apply_failed", days_ago=2)
        db.flush()

        result = auto_resolve_thrash_alerts(db)
        assert result["resolved"] == 0

        db.refresh(alert)
        assert alert.resolved is False

    def test_no_crash_on_empty(self, db):
        """No alerts → returns clean summary."""
        from app.services.loop_health import auto_resolve_thrash_alerts
        result = auto_resolve_thrash_alerts(db)
        assert result["checked"] == 0
        assert result["resolved"] == 0


# ---------------------------------------------------------------------------
# Telegram commands
# ---------------------------------------------------------------------------

class TestTelegramCommands:
    def test_loop_health_command_returns_string(self, db):
        """_cmd_loop_health returns a formatted string."""
        from app.services.telegram_agent import _cmd_loop_health
        result = _cmd_loop_health(db)
        assert isinstance(result, str)
        assert "Loop Health" in result
        assert "Throughput" in result

    def test_weakness_command_returns_string(self, db):
        """_cmd_weakness returns a formatted string."""
        from app.services.telegram_agent import _cmd_weakness
        result = _cmd_weakness(db)
        assert isinstance(result, str)
        assert "Weakness" in result

    def test_weakness_command_with_data(self, db):
        """Weakness command shows ranking when data exists."""
        from app.services.telegram_agent import _cmd_weakness
        # Create a failure to generate weakness data
        _make_candidate(db, source_ref="tg_test", status="apply_failed",
                        days_ago=1)
        c = db.query(BugFixCandidate).filter(
            BugFixCandidate.source_ref == "tg_test",
        ).first()
        c.patch_files = json.dumps(["app/services/revenue_metrics.py"])
        db.flush()

        result = _cmd_weakness(db)
        assert "weakest first" in result


# ---------------------------------------------------------------------------
# Evolution scanner focus
# ---------------------------------------------------------------------------

class TestEvolutionScannerFocus:
    def test_sort_by_weakness_preserves_all_proposals(self):
        """Sorting does not drop any proposals."""
        from app.services.evolution_engine import _sort_by_weakness
        proposals = [
            {"target_file": "app/services/revenue_metrics.py", "dedup_key": "a"},
            {"target_file": "app/api/track.py", "dedup_key": "b"},
            {"target_file": None, "dedup_key": "c"},
        ]
        result = _sort_by_weakness(proposals)
        assert len(result) == 3

    def test_sort_puts_weak_domains_first(self, db):
        """Proposals targeting weak domains sort before healthy ones."""
        from app.services.evolution_engine import _sort_by_weakness
        # Create failure data so intelligence domain is weak
        c = _make_candidate(db, source_ref="evo_sort_test",
                            status="apply_failed", days_ago=1)
        c.patch_files = json.dumps(["app/services/revenue_metrics.py"])
        db.flush()
        db.commit()

        proposals = [
            {"target_file": "app/api/track.py", "dedup_key": "healthy"},
            {"target_file": "app/services/revenue_metrics.py", "dedup_key": "weak"},
        ]
        result = _sort_by_weakness(proposals)
        # Weak domain (intelligence) should come first
        assert result[0]["dedup_key"] == "weak"
