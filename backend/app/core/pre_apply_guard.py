"""
pre_apply_guard.py — Unified enforcement gate for all patch/change operations.

Every code-modifying pipeline (bugfix_pipeline, evolution_converter, future agents)
must call guard_pre_apply() before writing changes to disk.

This module composes:
    - tier_check: file and diff tier classification
    - file_lock: concurrent edit prevention
    - frontend build requirement detection
    - tracker version bump requirement detection
    - test count verification

Public interface:
    guard_pre_apply(files, patch_diff, owner) -> GuardResult
    release_guard(files, owner) -> None
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger("pre_apply_guard")

_BACKEND_DIR = "/opt/wishspark/backend"
_MIN_TEST_COUNT = 631


@dataclass
class GuardResult:
    """Result of the pre-apply guard check."""
    allowed: bool
    tier: int
    label: str
    reasons: list[str] = field(default_factory=list)
    files_locked: bool = False
    locked_files: list[str] = field(default_factory=list)
    lock_conflicts: list[dict] = field(default_factory=list)
    requires_frontend_build: bool = False
    requires_tracker_bump: bool = False
    blocked: bool = False
    block_reason: str | None = None


def guard_pre_apply(
    files: list[str],
    patch_diff: str | None = None,
    owner: str = "unknown",
) -> GuardResult:
    """
    Full enforcement gate. Must be called before any patch is applied.

    Sequence:
        1. Tier check (file paths + diff content)
        2. TIER_2 → blocked immediately
        3. File lock acquisition
        4. Return result with tier, requirements, and lock status

    Args:
        files: File paths the patch will modify
        patch_diff: Diff content for content scanning
        owner: Agent identifier for file locking (e.g., "bugfix_pipeline")

    Returns:
        GuardResult. If allowed=True and tier=TIER_0, agent may proceed.
        If tier=TIER_1, agent must submit proposal and wait for approval.
        If blocked=True, agent must stop immediately.
    """
    from app.core.tier_check import enforce_pre_apply, TIER_0, TIER_2, TIER_LABELS

    # Step 1: Tier enforcement
    enforce = enforce_pre_apply(files, patch_diff)

    result = GuardResult(
        allowed=False,
        tier=enforce.tier,
        label=enforce.label,
        reasons=list(enforce.reasons),
        requires_frontend_build=enforce.requires_frontend_build,
        requires_tracker_bump=enforce.requires_tracker_bump,
    )

    # Step 2: TIER_2 → hard block
    if enforce.blocked:
        result.blocked = True
        result.block_reason = enforce.block_reason
        log.warning(
            "pre_apply_guard: BLOCKED owner=%s tier=TIER_2 reason=%s",
            owner, enforce.block_reason,
        )
        return result

    # Step 3: File lock acquisition
    if files:
        from app.core.file_lock import try_lock_files
        lock_result = try_lock_files(files, owner)
        result.files_locked = lock_result.acquired
        result.locked_files = lock_result.locked_files
        result.lock_conflicts = lock_result.conflicts

        if not lock_result.acquired:
            result.blocked = True
            conflict_desc = ", ".join(
                f"{c['file']} (held by {c['held_by']})"
                for c in lock_result.conflicts[:3]
            )
            result.block_reason = f"File lock conflict: {conflict_desc}"
            result.reasons.append(f"BLOCKED: concurrent edit conflict — {conflict_desc}")
            log.warning("pre_apply_guard: LOCK CONFLICT owner=%s conflicts=%s", owner, conflict_desc)
            return result

    # Step 4: Set allowed based on tier
    result.allowed = enforce.tier == TIER_0

    tier_label = TIER_LABELS.get(enforce.tier, f"TIER_{enforce.tier}")
    if result.allowed:
        log.info("pre_apply_guard: ALLOWED owner=%s tier=%s files=%d", owner, tier_label, len(files))
    else:
        log.info(
            "pre_apply_guard: APPROVAL_REQUIRED owner=%s tier=%s files=%d",
            owner, tier_label, len(files),
        )

    return result


def release_guard(files: list[str], owner: str) -> None:
    """
    Release file locks acquired by guard_pre_apply.
    Call this after patch is applied (success or failure) or after proposal is submitted.
    """
    if files:
        from app.core.file_lock import release_file_locks
        release_file_locks(files, owner)


def verify_test_count() -> tuple[bool, int, str]:
    """
    Run pytest and verify test count meets minimum threshold.

    Returns:
        (passed, count, output) — passed is True if tests pass AND count >= 631.
    """
    try:
        result = subprocess.run(
            [
                f"{_BACKEND_DIR}/venv/bin/python", "-m", "pytest",
                "tests/", "--ignore=tests/test_scaling_intelligence.py", "-q",
            ],
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = result.stdout[-1000:] + "\n" + result.stderr[-500:]

        # Parse test count from pytest output (e.g., "631 passed")
        import re
        match = re.search(r"(\d+) passed", output)
        count = int(match.group(1)) if match else 0

        passed = result.returncode == 0 and count >= _MIN_TEST_COUNT
        return passed, count, output.strip()
    except subprocess.TimeoutExpired:
        return False, 0, "pytest timed out after 180s"
    except Exception as exc:
        return False, 0, f"pytest execution failed: {exc}"


def verify_frontend_build() -> tuple[bool, str]:
    """
    Run Next.js build and verify it completes successfully.

    Returns:
        (passed, output)
    """
    try:
        result = subprocess.run(
            ["npx", "next", "build"],
            cwd="/opt/wishspark/dashboard",
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout[-500:] + "\n" + result.stderr[-500:]
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "next build timed out after 120s"
    except Exception as exc:
        return False, f"next build execution failed: {exc}"
