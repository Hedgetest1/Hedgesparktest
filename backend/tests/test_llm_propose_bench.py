"""
LLM propose_patch benchmark suite — L6 of the prompt-engineering sprint.

Deterministic regression suite covering every observed hallucination
mode from the 2026-04-11 audit. Uses fake LLM stubs (no real API calls,
no LLM budget consumed) so it runs in CI on every commit.

Pass criteria: every named failure mode is rejected before reaching
disk; every well-formed diff is accepted. The grounding helpers inject
the manifest + signatures + DO-NOT lines as expected.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.bugfix_pipeline import _validate_patch_semantics
from app.services.bugfix_prompt_grounding import (
    build_file_manifest,
    extract_signatures,
    preflight_ground_candidate,
)


# ---------------------------------------------------------------------------
# Direct semantic validator benchmarks (no DB needed)
# ---------------------------------------------------------------------------


def test_bench_phantom_path_rejected():
    """Rule A: +++ b/<nonexistent> with no /dev/null marker."""
    diff = (
        "--- a/app/services/totally_fake_module.py\n"
        "+++ b/app/services/totally_fake_module.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+x = 1\n"
    )
    files = json.dumps(["app/services/totally_fake_module.py"])
    ok, reason = _validate_patch_semantics(diff, files)
    assert ok is False
    assert "phantom_path" in reason or "file_not_found" in reason


def test_bench_new_file_with_dev_null_accepted():
    """Phantom rule must NOT fire for new files introduced via /dev/null."""
    diff = (
        "--- /dev/null\n"
        "+++ b/tests/test_brand_new_bench_file.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def test_x():\n"
        "+    assert True\n"
    )
    files = json.dumps(["tests/test_brand_new_bench_file.py"])
    ok, _ = _validate_patch_semantics(diff, files)
    assert ok is True


def test_bench_duplicate_symbol_rejected():
    """Rule B: adding a def whose name already exists in the target file."""
    # risk_forecast.py defines `record_rars_snapshot` — re-adding it must fail
    diff = (
        "--- a/app/services/risk_forecast.py\n"
        "+++ b/app/services/risk_forecast.py\n"
        "@@ -50,0 +51,3 @@\n"
        "+def record_rars_snapshot(shop_domain, total_at_risk_eur):\n"
        "+    print('duplicated')\n"
        "+    return None\n"
    )
    files = json.dumps(["app/services/risk_forecast.py"])
    ok, reason = _validate_patch_semantics(diff, files)
    assert ok is False
    assert "duplicate_symbol" in reason


def test_bench_hallucinated_import_rejected():
    """Rule C: importing a symbol that doesn't exist in the target module."""
    diff = (
        "--- a/app/services/risk_forecast.py\n"
        "+++ b/app/services/risk_forecast.py\n"
        "@@ -1,1 +1,2 @@\n"
        " from __future__ import annotations\n"
        "+from app.services.refund_loss import nonexistent_ghost_function\n"
    )
    files = json.dumps(["app/services/risk_forecast.py"])
    ok, reason = _validate_patch_semantics(diff, files)
    assert ok is False
    assert "hallucinated_import" in reason


def test_bench_hallucinated_module_rejected():
    """Rule C extension: importing from a module that doesn't exist."""
    diff = (
        "--- a/app/services/risk_forecast.py\n"
        "+++ b/app/services/risk_forecast.py\n"
        "@@ -1,1 +1,2 @@\n"
        " from __future__ import annotations\n"
        "+from app.services.totally_invented_ghost_module import anything\n"
    )
    files = json.dumps(["app/services/risk_forecast.py"])
    ok, reason = _validate_patch_semantics(diff, files)
    assert ok is False
    assert "hallucinated_import" in reason


def test_bench_untested_significant_change_rejected():
    """Rule D: large app/ change with no test file in the patch."""
    body_lines = "\n".join(f"+    line_{i} = {i}" for i in range(25))
    diff = (
        "--- a/app/services/risk_forecast.py\n"
        "+++ b/app/services/risk_forecast.py\n"
        "@@ -1,1 +1,26 @@\n"
        " from __future__ import annotations\n"
        f"{body_lines}\n"
    )
    files = json.dumps(["app/services/risk_forecast.py"])
    ok, reason = _validate_patch_semantics(diff, files)
    assert ok is False
    assert "untested_significant_change" in reason


def test_bench_significant_change_with_test_accepted():
    """Rule D: same large change passes if a test file is co-committed."""
    body_lines = "\n".join(f"+    line_{i} = {i}" for i in range(25))
    diff = (
        "--- a/app/services/risk_forecast.py\n"
        "+++ b/app/services/risk_forecast.py\n"
        "@@ -1,1 +1,26 @@\n"
        " from __future__ import annotations\n"
        f"{body_lines}\n"
        "--- /dev/null\n"
        "+++ b/tests/test_bench_companion.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def test_companion():\n"
        "+    assert True\n"
    )
    files = json.dumps([
        "app/services/risk_forecast.py",
        "tests/test_bench_companion.py",
    ])
    ok, _ = _validate_patch_semantics(diff, files)
    assert ok is True


def test_bench_valid_minimal_diff_accepted():
    """A small, well-formed diff against a real file passes."""
    diff = (
        "--- a/app/services/risk_forecast.py\n"
        "+++ b/app/services/risk_forecast.py\n"
        "@@ -1,1 +1,2 @@\n"
        " from __future__ import annotations\n"
        "+# benign comment\n"
    )
    files = json.dumps(["app/services/risk_forecast.py"])
    ok, _ = _validate_patch_semantics(diff, files)
    assert ok is True


# ---------------------------------------------------------------------------
# Grounding helper benchmarks
# ---------------------------------------------------------------------------


def test_bench_manifest_contains_real_paths():
    """The manifest must list real files for the requested domain."""
    manifest = build_file_manifest("evolution")
    assert "Available file paths" in manifest
    assert "app/services/" in manifest
    # Every listed path must exist
    import os
    for line in manifest.splitlines():
        line = line.strip()
        if line.startswith("app/") and "(" in line:
            path = line.split("   ")[0].strip()
            assert os.path.isfile(f"/opt/wishspark/backend/{path}"), f"manifest listed missing path: {path}"


def test_bench_manifest_includes_extra_files():
    """extra_files override is honored even if domain mismatches."""
    manifest = build_file_manifest(
        "evolution", extra_files=["app/services/risk_forecast.py"],
    )
    assert "app/services/risk_forecast.py" in manifest


def test_bench_signatures_contain_real_function_names():
    """AST signatures must reflect actual defs in the file."""
    sigs = extract_signatures("app/services/risk_forecast.py")
    assert "def record_rars_snapshot" in sigs
    assert "def get_risk_forecast" in sigs
    # No function bodies leak
    assert "rc.setex" not in sigs
    assert "json.loads" not in sigs.split("```python")[1] if "```python" in sigs else True


def test_bench_signatures_returns_empty_for_missing_file():
    assert extract_signatures("app/services/no_such_module.py") == ""


# ---------------------------------------------------------------------------
# Pre-flight benchmarks
# ---------------------------------------------------------------------------


def _fake_candidate(*, context_json=None, patch_files=None):
    c = MagicMock()
    c.context_json = context_json
    c.patch_files = patch_files
    return c


def test_bench_preflight_rejects_phantom_target_file():
    c = _fake_candidate(
        context_json=json.dumps({"target_file": "app/services/ghost.py"}),
    )
    ok, reason = preflight_ground_candidate(c)
    assert ok is False
    assert "target_file_not_found" in reason


def test_bench_preflight_rejects_phantom_patch_file():
    c = _fake_candidate(
        patch_files=json.dumps(["app/services/ghost.py"]),
    )
    ok, reason = preflight_ground_candidate(c)
    assert ok is False
    assert "patch_file_not_found" in reason


def test_bench_preflight_allows_new_test_file():
    c = _fake_candidate(
        patch_files=json.dumps(["tests/test_brand_new_thing.py"]),
    )
    ok, _ = preflight_ground_candidate(c)
    assert ok is True


def test_bench_preflight_accepts_real_target_file():
    c = _fake_candidate(
        context_json=json.dumps({"target_file": "app/services/risk_forecast.py"}),
        patch_files=json.dumps(["app/services/risk_forecast.py"]),
    )
    ok, _ = preflight_ground_candidate(c)
    assert ok is True


# ---------------------------------------------------------------------------
# Integration: propose_patch with stubbed LLM
# ---------------------------------------------------------------------------


def test_bench_propose_patch_preflight_blocks_llm_call():
    """If preflight fails, _call_llm is NEVER invoked → zero budget spent."""
    from app.services import bugfix_pipeline as bp

    fake_candidate = MagicMock()
    fake_candidate.id = 99999
    fake_candidate.status = "open"
    fake_candidate.title = "ghost bug"
    fake_candidate.summary = "test"
    fake_candidate.affected_domain = None
    fake_candidate.patch_files = None
    fake_candidate.context_json = json.dumps({"target_file": "app/services/ghost.py"})
    fake_candidate.source_type = "manual"
    fake_candidate.failure_reason = None

    fake_db = MagicMock()
    fake_db.query.return_value.get.return_value = fake_candidate
    fake_db.get.return_value = fake_candidate

    with patch.object(bp, "_call_llm") as mock_llm, \
         patch.object(bp, "_check_patch_fingerprint", return_value=None), \
         patch.object(bp, "_classify_candidate_domain", return_value=None), \
         patch.object(bp, "recompute_priority_after_classification", return_value=None):
        result = bp.propose_patch(fake_db, 99999)

    assert result is False
    assert mock_llm.call_count == 0
    assert "prompt_ungrounded_preflight" in (fake_candidate.failure_reason or "")
