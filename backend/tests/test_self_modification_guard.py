"""
Regression guard for the self-modification protection layer added 2026-04-11.

The self-healing pipeline must never auto-patch its own guts. If an LLM
proposes a change to any file in _SELF_MODIFICATION_PREFIXES (the bugfix
pipeline itself, loop_health, orchestrator, reviewer, etc.), the candidate
is force-downgraded to TIER_1 — human review required.

Rationale: a catastrophic feedback loop is possible if a buggy auto-fix
breaks the very code that would detect and revert it. This guard is the
last-line defense. TIER policy enforces the macro gates; this guard
enforces the micro gate on self-healing files specifically.
"""
from __future__ import annotations

import json

from app.services.bugfix_pipeline import (
    PATCH_TIER_0,
    PATCH_TIER_1,
    PATCH_TIER_2,
    classify_patch_risk,
    touches_self_healing_pipeline,
)


# ---------------------------------------------------------------------------
# Direct helper
# ---------------------------------------------------------------------------

def test_touches_self_healing_catches_bugfix_pipeline():
    hit, files = touches_self_healing_pipeline(
        json.dumps(["app/services/bugfix_pipeline.py"])
    )
    assert hit is True
    assert "app/services/bugfix_pipeline.py" in files


def test_touches_self_healing_catches_loop_health():
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["app/services/loop_health.py"])
    )
    assert hit is True


def test_touches_self_healing_catches_orchestrator():
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["app/services/orchestrator.py"])
    )
    assert hit is True


def test_touches_self_healing_catches_reviewer():
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["app/services/reviewer_layer.py"])
    )
    assert hit is True


def test_touches_self_healing_catches_deploy_gate():
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["scripts/deploy_gate.py"])
    )
    assert hit is True


def test_touches_self_healing_catches_adaptive_governance():
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["app/services/adaptive_governance.py"])
    )
    assert hit is True


def test_touches_self_healing_catches_agent_worker():
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["app/workers/agent_worker.py"])
    )
    assert hit is True


def test_touches_self_healing_ignores_normal_service():
    """A patch to a regular service file must not trigger the guard."""
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["app/services/nudge_engine.py"])
    )
    assert hit is False


def test_touches_self_healing_ignores_test_file():
    """Test files under tests/ are NOT the pipeline itself."""
    hit, _ = touches_self_healing_pipeline(
        json.dumps(["tests/test_bugfix_pipeline.py"])
    )
    assert hit is False


def test_touches_self_healing_mixed_patch_still_caught():
    """If ANY file in a multi-file patch touches the pipeline, it counts."""
    hit, files = touches_self_healing_pipeline(json.dumps([
        "app/services/nudge_engine.py",
        "app/services/orchestrator.py",  # <-- this is the pipeline
        "tests/test_stuff.py",
    ]))
    assert hit is True
    assert "app/services/orchestrator.py" in files


# ---------------------------------------------------------------------------
# Integration with classify_patch_risk
# ---------------------------------------------------------------------------

def test_classify_forces_tier_1_on_self_modification():
    """classify_patch_risk downgrades a self-modifying patch to TIER_1."""
    tier, reasons = classify_patch_risk(
        patch_files_json=json.dumps(["app/services/bugfix_pipeline.py"]),
        patch_diff="--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-old\n+new\n",
    )
    assert tier == PATCH_TIER_1, (
        f"self-modifying patch must be TIER_1, got {tier}"
    )
    assert any("self_modification" in r for r in reasons)


def test_classify_normal_test_patch_stays_tier_0():
    """A patch to a test file in an allowed prefix stays TIER_0."""
    tier, _ = classify_patch_risk(
        patch_files_json=json.dumps(["tests/test_nudge_engine.py"]),
        patch_diff="--- a/tests/test_nudge_engine.py\n+++ b/tests/test_nudge_engine.py\n@@ -1 +1 @@\n-old\n+new\n",
    )
    assert tier == PATCH_TIER_0


def test_classify_forbidden_path_still_tier_2_over_self_mod():
    """TIER_2 (billing, auth, migrations) wins over self-mod TIER_1
    because TIER_2 is the stronger ban."""
    tier, reasons = classify_patch_risk(
        patch_files_json=json.dumps(["app/api/billing.py"]),
        patch_diff="--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-x\n+y\n",
    )
    assert tier == PATCH_TIER_2  # forbidden path wins
    assert any("forbidden" in r for r in reasons)
