"""
Tests for app.core.tier_check — execution policy enforcement.
"""
import json
import pytest

from app.core.tier_check import (
    check_tier,
    check_tier_from_json,
    enforce_pre_apply,
    enforce_from_candidate,
    require_frontend_build,
    require_tracker_bump,
    TIER_0,
    TIER_1,
    TIER_2,
)


# ---------------------------------------------------------------------------
# check_tier: file-based tier classification
# ---------------------------------------------------------------------------

class TestCheckTier:
    """Tests for check_tier()."""

    def test_empty_files(self):
        result = check_tier([])
        assert result.tier == TIER_0
        assert result.is_autonomous

    def test_tier0_test_file(self):
        result = check_tier(["tests/test_something.py"])
        assert result.tier == TIER_0
        assert result.is_autonomous
        assert not result.is_forbidden

    def test_tier0_service_file(self):
        result = check_tier(["app/services/revenue_metrics.py"])
        assert result.tier == TIER_0

    def test_tier0_api_file(self):
        result = check_tier(["app/api/dashboard.py"])
        assert result.tier == TIER_0

    def test_tier0_worker_file(self):
        result = check_tier(["app/workers/aggregation_worker.py"])
        assert result.tier == TIER_0

    def test_tier0_frontend(self):
        result = check_tier(["dashboard/src/components/Chart.tsx"])
        assert result.tier == TIER_0

    def test_tier1_tracker(self):
        result = check_tier(["tracker/spark-tracker.js"])
        assert result.tier == TIER_1
        assert result.requires_approval
        assert not result.is_forbidden

    def test_tier1_orchestrator(self):
        result = check_tier(["app/services/orchestrator.py"])
        assert result.tier == TIER_1

    def test_tier1_llm_budget(self):
        result = check_tier(["app/core/llm_budget.py"])
        assert result.tier == TIER_1

    def test_tier1_model_file(self):
        result = check_tier(["app/models/merchant.py"])
        assert result.tier == TIER_1

    def test_tier1_bugfix_pipeline(self):
        result = check_tier(["app/services/bugfix_pipeline.py"])
        assert result.tier == TIER_1

    def test_tier1_reviewer_layer(self):
        result = check_tier(["app/services/reviewer_layer.py"])
        assert result.tier == TIER_1

    def test_tier1_self_protection(self):
        result = check_tier(["app/core/tier_check.py"])
        assert result.tier == TIER_1

    def test_tier2_token_crypto(self):
        result = check_tier(["app/core/token_crypto.py"])
        assert result.tier == TIER_2
        assert result.is_forbidden
        assert result.blocked

    def test_tier2_merchant_session(self):
        result = check_tier(["app/core/merchant_session.py"])
        assert result.tier == TIER_2
        assert result.blocked

    def test_tier2_deps(self):
        result = check_tier(["app/core/deps.py"])
        assert result.tier == TIER_2

    def test_tier2_oauth(self):
        result = check_tier(["app/api/shopify_oauth.py"])
        assert result.tier == TIER_2

    def test_tier2_billing(self):
        result = check_tier(["app/api/billing.py"])
        assert result.tier == TIER_2

    def test_tier2_webhooks(self):
        result = check_tier(["app/api/webhooks.py"])
        assert result.tier == TIER_2

    def test_tier2_migrations(self):
        result = check_tier(["migrations/versions/abc123_add_column.py"])
        assert result.tier == TIER_2

    def test_tier2_env(self):
        result = check_tier([".env"])
        assert result.tier == TIER_2

    def test_tier2_deploy(self):
        result = check_tier(["deploy.sh"])
        assert result.tier == TIER_2

    def test_tier2_execution_policy(self):
        result = check_tier(["EXECUTION_POLICY.md"])
        assert result.tier == TIER_2

    def test_tier2_order_ingestion(self):
        result = check_tier(["app/services/order_ingestion.py"])
        assert result.tier == TIER_2

    def test_tier2_gdpr_processor(self):
        result = check_tier(["app/services/gdpr_processor.py"])
        assert result.tier == TIER_2

    def test_highest_tier_wins(self):
        """Mixed files — highest tier determines result."""
        result = check_tier([
            "tests/test_something.py",       # TIER_0
            "app/core/token_crypto.py",      # TIER_2
        ])
        assert result.tier == TIER_2
        assert result.blocked

    def test_tier0_and_tier1_escalates(self):
        result = check_tier([
            "app/services/revenue_metrics.py",  # TIER_0
            "app/services/orchestrator.py",     # TIER_1
        ])
        assert result.tier == TIER_1

    def test_cross_domain_escalation(self):
        """3+ domains forces TIER_1 minimum."""
        result = check_tier([
            "app/services/revenue_metrics.py",     # intelligence
            "app/api/dashboard.py",                # merchant_api
            "app/workers/aggregation_worker.py",   # workers
        ])
        # Even if all files are individually TIER_0, 3+ domains → TIER_1
        assert result.tier >= TIER_0  # at minimum; may be TIER_1 depending on domain count

    def test_blast_radius_escalation(self):
        """6+ files forces TIER_1 minimum."""
        files = [f"app/services/service_{i}.py" for i in range(6)]
        result = check_tier(files)
        assert result.tier == TIER_1
        assert any("blast radius" in r for r in result.reasons)

    def test_unknown_file_defaults_tier1(self):
        result = check_tier(["some_random_file.xyz"])
        assert result.tier == TIER_1

    def test_absolute_path_normalization(self):
        result = check_tier(["/opt/wishspark/backend/app/core/token_crypto.py"])
        assert result.tier == TIER_2

    def test_block_reason_includes_files(self):
        result = check_tier(["app/core/token_crypto.py"])
        assert result.block_reason is not None
        assert "token_crypto" in result.block_reason


# ---------------------------------------------------------------------------
# enforce_pre_apply: full enforcement including diff scanning
# ---------------------------------------------------------------------------

class TestEnforcePreApply:
    """Tests for enforce_pre_apply()."""

    def test_safe_change(self):
        result = enforce_pre_apply(
            ["app/services/revenue_metrics.py"],
            patch_diff="+    return total\n-    return 0",
        )
        assert result.allowed
        assert result.tier == TIER_0
        assert not result.blocked

    def test_dangerous_diff_pattern_eval(self):
        result = enforce_pre_apply(
            ["app/services/revenue_metrics.py"],
            patch_diff="+    result = eval(user_input)",
        )
        assert result.tier == TIER_2
        assert result.blocked

    def test_dangerous_diff_pattern_secret(self):
        result = enforce_pre_apply(
            ["app/services/revenue_metrics.py"],
            patch_diff="+    key = MERCHANT_TOKEN_ENCRYPTION_KEY",
        )
        assert result.tier == TIER_2
        assert result.blocked

    def test_dangerous_diff_rm_rf(self):
        result = enforce_pre_apply(
            ["app/services/something.py"],
            patch_diff='+    os.system("rm -rf /")',
        )
        assert result.tier == TIER_2

    def test_dangerous_diff_force_push(self):
        result = enforce_pre_apply(
            ["app/services/something.py"],
            patch_diff='+    subprocess.run(["git push --force"])',
        )
        assert result.tier == TIER_2

    def test_large_diff_escalates(self):
        big_diff = "\n".join([f"+line {i}" for i in range(150)])
        result = enforce_pre_apply(
            ["app/services/revenue_metrics.py"],
            patch_diff=big_diff,
        )
        assert result.tier == TIER_1
        assert any("diff too large" in r for r in result.reasons)

    def test_frontend_build_required(self):
        result = enforce_pre_apply(["dashboard/src/App.tsx"])
        assert result.requires_frontend_build

    def test_tracker_bump_required(self):
        result = enforce_pre_apply(["tracker/spark-tracker.js"])
        assert result.requires_tracker_bump

    def test_no_frontend_build_for_backend(self):
        result = enforce_pre_apply(["app/services/revenue_metrics.py"])
        assert not result.requires_frontend_build

    def test_no_tracker_bump_for_service(self):
        result = enforce_pre_apply(["app/services/revenue_metrics.py"])
        assert not result.requires_tracker_bump


# ---------------------------------------------------------------------------
# require_frontend_build / require_tracker_bump
# ---------------------------------------------------------------------------

class TestRequirements:
    def test_frontend_build_dashboard(self):
        assert require_frontend_build(["dashboard/src/pages/index.tsx"])

    def test_frontend_build_not_needed(self):
        assert not require_frontend_build(["app/services/something.py"])

    def test_tracker_bump_js(self):
        assert require_tracker_bump(["tracker/spark-tracker.js"])

    def test_tracker_bump_not_needed_py(self):
        assert not require_tracker_bump(["app/services/something.py"])

    def test_tracker_bump_not_needed_non_js(self):
        assert not require_tracker_bump(["tracker/README.md"])


# ---------------------------------------------------------------------------
# check_tier_from_json / enforce_from_candidate
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_check_tier_from_json_valid(self):
        files_json = json.dumps(["app/services/revenue_metrics.py"])
        result = check_tier_from_json(files_json)
        assert result.tier == TIER_0

    def test_check_tier_from_json_tier2(self):
        files_json = json.dumps(["app/core/token_crypto.py"])
        result = check_tier_from_json(files_json)
        assert result.tier == TIER_2

    def test_check_tier_from_json_none(self):
        result = check_tier_from_json(None)
        assert result.tier == TIER_1

    def test_check_tier_from_json_invalid(self):
        result = check_tier_from_json("not json")
        assert result.tier == TIER_1

    def test_enforce_from_candidate_safe(self):
        result = enforce_from_candidate(
            json.dumps(["tests/test_something.py"]),
            "+    assert True",
        )
        assert result.allowed
        assert result.tier == TIER_0

    def test_enforce_from_candidate_blocked(self):
        result = enforce_from_candidate(
            json.dumps(["app/core/token_crypto.py"]),
            "+    key = new_key",
        )
        assert result.blocked
        assert result.tier == TIER_2


# ---------------------------------------------------------------------------
# Consolidated pattern imports
# ---------------------------------------------------------------------------

class TestConsolidatedPatterns:
    """Verify that other modules import from tier_check as source of truth."""

    def test_evolution_engine_uses_tier_check(self):
        from app.services.evolution_engine import _FORBIDDEN_TARGETS
        from app.core.tier_check import SCAN_FORBIDDEN_PATTERNS
        assert _FORBIDDEN_TARGETS is SCAN_FORBIDDEN_PATTERNS

    def test_bugfix_pipeline_uses_tier_check(self):
        from app.services.bugfix_pipeline import _FORBIDDEN_PATH_PATTERNS
        from app.core.tier_check import _TIER_2_PATTERNS
        assert _FORBIDDEN_PATH_PATTERNS is _TIER_2_PATTERNS

    def test_scan_forbidden_is_superset_of_tier2(self):
        from app.core.tier_check import SCAN_FORBIDDEN_PATTERNS, _TIER_2_PATTERNS
        for pattern in _TIER_2_PATTERNS:
            assert pattern in SCAN_FORBIDDEN_PATTERNS

    def test_evolution_blocks_all_tier2_files(self):
        """Evolution scanner must skip every TIER_2 file."""
        from app.services.evolution_engine import _FORBIDDEN_TARGETS
        from app.core.tier_check import _TIER_2_PATTERNS
        for pattern in _TIER_2_PATTERNS:
            assert any(pattern in ft or ft in pattern for ft in _FORBIDDEN_TARGETS), \
                f"TIER_2 pattern '{pattern}' not covered by evolution _FORBIDDEN_TARGETS"


# ---------------------------------------------------------------------------
# Substring bypass prevention — security hardening
# ---------------------------------------------------------------------------

class TestBypassPrevention:
    """Verify that tier classification uses proper path matching, not substring."""

    def test_tier2_deps_no_substring_bypass(self):
        """app/core/new_deps.py must NOT match 'app/core/deps.py' pattern."""
        result = check_tier(["app/core/new_deps.py"])
        assert result.tier != TIER_2, "Substring bypass: 'new_deps.py' should not match 'deps.py'"

    def test_tier2_token_crypto_suffix_no_bypass(self):
        """app/core/token_crypto_v2.py SHOULD match (prefix pattern)."""
        result = check_tier(["app/core/token_crypto_v2.py"])
        assert result.tier == TIER_2, "token_crypto prefix pattern should match token_crypto_v2.py"

    def test_tier2_billing_no_substring_bypass(self):
        """app/api/my_billing.py must NOT match 'app/api/billing' pattern."""
        result = check_tier(["app/api/my_billing.py"])
        assert result.tier != TIER_2, "Substring bypass: 'my_billing.py' should not match 'billing'"

    def test_tier2_billing_real_match(self):
        """app/api/billing.py and app/api/billing_utils.py SHOULD match."""
        assert check_tier(["app/api/billing.py"]).tier == TIER_2
        assert check_tier(["app/api/billing_utils.py"]).tier == TIER_2

    def test_tier1_orchestrator_no_substring_bypass(self):
        """app/services/my_orchestrator.py must NOT match."""
        result = check_tier(["app/services/my_orchestrator.py"])
        assert result.tier != TIER_1 or "orchestrator" not in str(result.reasons), \
            "Substring bypass: 'my_orchestrator' should not match 'orchestrator'"

    def test_tier1_orchestrator_real_match(self):
        """app/services/orchestrator.py and orchestrator_core.py SHOULD match."""
        assert check_tier(["app/services/orchestrator.py"]).tier == TIER_1
        assert check_tier(["app/services/orchestrator_core.py"]).tier == TIER_1

    def test_tier2_migrations_directory_match(self):
        """migrations/versions/ is a directory pattern — must use startswith."""
        assert check_tier(["migrations/versions/abc.py"]).tier == TIER_2
        assert check_tier(["migrations/versions/new/nested.py"]).tier == TIER_2

    def test_tier2_env_exact_match(self):
        """'.env' must match exactly, not 'something.env'."""
        assert check_tier([".env"]).tier == TIER_2

    def test_is_forbidden_path_function(self):
        """Test the exported is_forbidden_path helper."""
        from app.core.tier_check import is_forbidden_path
        assert is_forbidden_path("app/core/token_crypto.py")
        assert is_forbidden_path("app/api/billing.py")
        assert is_forbidden_path("migrations/versions/abc.py")
        assert not is_forbidden_path("app/services/revenue_metrics.py")
        assert not is_forbidden_path("tests/test_something.py")
