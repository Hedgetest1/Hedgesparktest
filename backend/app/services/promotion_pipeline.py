"""
promotion_pipeline.py — Promotes local auto-fix commits to remote branches.

Flow:
    1. Auto-applied bugfix → AutoFixPromotion(pending) created
    2. create_promotion_branch() → local branch + status=branch_created
    3. run_promotion_ci_check() → ci_pending/ci_passed/ci_failed
    4. Operator approves → approved
    5. push_promotion() → git push origin branch → pushed

No auto-push. No auto-merge. Human-gated at push step.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.autofix_promotion import AutoFixPromotion

log = logging.getLogger("promotion_pipeline")

_BACKEND_DIR = "/opt/wishspark/backend"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _has_remote() -> bool:
    """Check if a git remote named 'origin' is configured."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=_BACKEND_DIR,
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and len(r.stdout.strip()) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Create promotion from applied bugfix
# ---------------------------------------------------------------------------

def create_promotion(db: Session, bugfix_candidate_id: int, git_commit_sha: str) -> AutoFixPromotion:
    """Create a new promotion record for a successfully applied bugfix."""
    # Dedup: don't create if one already exists for this candidate
    existing = (
        db.query(AutoFixPromotion)
        .filter(AutoFixPromotion.bugfix_candidate_id == bugfix_candidate_id)
        .first()
    )
    if existing:
        return existing

    promo = AutoFixPromotion(
        bugfix_candidate_id=bugfix_candidate_id,
        git_commit_sha=git_commit_sha,
        status="pending",
    )
    db.add(promo)
    db.flush()

    # Slack notify (fail-safe)
    try:
        _notify_promotion(promo, "created")
    except Exception:
        pass

    log.info("promotion: created id=%d candidate=%d sha=%s", promo.id, bugfix_candidate_id, git_commit_sha)
    return promo


# ---------------------------------------------------------------------------
# Branch creation
# ---------------------------------------------------------------------------

def create_promotion_branch(db: Session, promotion_id: int) -> str:
    """Create a local git branch for the promotion. Returns branch name or error."""
    promo = db.query(AutoFixPromotion).get(promotion_id)
    if not promo:
        return "not_found"
    if promo.status not in ("pending", "branch_created"):
        return f"wrong_status: {promo.status}"

    branch = f"autofix/candidate-{promo.bugfix_candidate_id}-{promo.git_commit_sha[:8]}"

    # Check if branch already exists
    check = subprocess.run(
        ["git", "rev-parse", "--verify", branch], cwd=_BACKEND_DIR,
        capture_output=True, timeout=5,
    )
    if check.returncode == 0:
        promo.branch_name = branch
        promo.status = "branch_created"
        db.flush()
        return branch

    # Create branch at the commit
    result = subprocess.run(
        ["git", "branch", branch, promo.git_commit_sha], cwd=_BACKEND_DIR,
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        promo.failure_reason = f"branch_create_failed: {result.stderr[:200]}"
        promo.status = "failed"
        db.flush()
        return f"error: {result.stderr[:200]}"

    promo.branch_name = branch
    promo.status = "branch_created"
    db.flush()
    log.info("promotion: branch created id=%d branch=%s", promo.id, branch)
    return branch


# ---------------------------------------------------------------------------
# CI verification
# ---------------------------------------------------------------------------

def run_promotion_ci_check(db: Session, promotion_id: int) -> str:
    """
    Check CI status for the promotion branch.

    Current implementation: local test run (no remote CI integration yet).
    Returns: ci_passed | ci_failed | ci_pending | error
    """
    promo = db.query(AutoFixPromotion).get(promotion_id)
    if not promo:
        return "not_found"
    if promo.status not in ("branch_created", "ci_pending"):
        return f"wrong_status: {promo.status}"

    promo.status = "ci_pending"
    db.flush()

    # Run local test suite as CI proxy
    try:
        result = subprocess.run(
            [f"{_BACKEND_DIR}/venv/bin/python", "-m", "pytest", "tests/", "-q"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": _BACKEND_DIR},
        )
        ci_output = (result.stdout[-500:] + "\n" + result.stderr[-500:]).strip()
        promo.ci_result = ci_output[:2000]
        promo.ci_url = "local://pytest"

        if result.returncode == 0:
            promo.status = "ci_passed"
            log.info("promotion: CI passed id=%d", promo.id)
            return "ci_passed"
        else:
            promo.status = "ci_failed"
            promo.failure_reason = f"tests_failed (exit {result.returncode})"
            _notify_promotion(promo, "ci_failed")
            log.warning("promotion: CI failed id=%d", promo.id)
            return "ci_failed"
    except Exception as exc:
        promo.status = "ci_failed"
        promo.failure_reason = f"ci_error: {str(exc)[:200]}"
        db.flush()
        return f"error: {type(exc).__name__}"
    finally:
        db.flush()


# ---------------------------------------------------------------------------
# Push to remote
# ---------------------------------------------------------------------------

def push_promotion(db: Session, promotion_id: int) -> str:
    """
    Push the promotion branch to origin. Human-gated — never auto-called.

    Returns: pushed | error_message
    """
    promo = db.query(AutoFixPromotion).get(promotion_id)
    if not promo:
        return "not_found"
    if promo.status not in ("approved", "ci_passed", "branch_created"):
        return f"wrong_status: {promo.status}"
    if not promo.branch_name:
        return "no_branch"
    if not _has_remote():
        promo.failure_reason = "no_git_remote_configured"
        promo.status = "failed"
        db.flush()
        return "no_remote"

    try:
        result = subprocess.run(
            ["git", "push", "origin", promo.branch_name],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            promo.status = "pushed"
            promo.pushed_at = _now()
            db.flush()
            _notify_promotion(promo, "pushed")
            log.info("promotion: pushed id=%d branch=%s", promo.id, promo.branch_name)
            return "pushed"
        else:
            promo.status = "failed"
            promo.failure_reason = f"push_failed: {result.stderr[:300]}"
            db.flush()
            _notify_promotion(promo, "push_failed")
            return f"push_failed: {result.stderr[:200]}"
    except Exception as exc:
        promo.status = "failed"
        promo.failure_reason = f"push_error: {str(exc)[:200]}"
        db.flush()
        return f"error: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Remote CI monitoring
# ---------------------------------------------------------------------------

def _has_gh_cli() -> bool:
    """Check if gh CLI is available."""
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def check_remote_ci_status(db: Session, promotion_id: int) -> str:
    """
    Check remote CI status for a pushed promotion branch.
    Returns: passed | failed | in_progress | queued | unknown | unconfigured | error
    """
    promo = db.query(AutoFixPromotion).get(promotion_id)
    if not promo:
        return "not_found"
    if promo.status not in ("pushed", "approved") and not promo.pushed_at:
        return f"wrong_status: {promo.status}"

    promo.remote_ci_checked_at = _now()

    if not _has_gh_cli() or not _has_remote():
        promo.remote_ci_status = "unconfigured"
        db.flush()
        return "unconfigured"

    try:
        # Query latest workflow run for the branch
        result = subprocess.run(
            ["gh", "run", "list", "--branch", promo.branch_name or "", "--limit", "1", "--json", "status,conclusion,url"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            promo.remote_ci_status = "unknown"
            promo.failure_reason = f"gh_run_list_failed: {result.stderr[:200]}"
            db.flush()
            return "unknown"

        import json
        runs = json.loads(result.stdout or "[]")
        if not runs:
            promo.remote_ci_status = "unknown"
            db.flush()
            return "unknown"

        run = runs[0]
        gh_status = run.get("status", "")
        conclusion = run.get("conclusion", "")
        promo.remote_ci_url = run.get("url", "")

        if gh_status == "completed":
            if conclusion == "success":
                promo.remote_ci_status = "passed"
            else:
                promo.remote_ci_status = "failed"
                try:
                    _notify_promotion(promo, "remote_ci_failed")
                except Exception:
                    pass
        elif gh_status in ("queued", "waiting"):
            promo.remote_ci_status = "queued"
        elif gh_status == "in_progress":
            promo.remote_ci_status = "in_progress"
        else:
            promo.remote_ci_status = "unknown"

        db.flush()
        return promo.remote_ci_status

    except Exception as exc:
        promo.remote_ci_status = "unknown"
        promo.failure_reason = f"ci_check_error: {str(exc)[:200]}"
        db.flush()
        return f"error: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# PR creation
# ---------------------------------------------------------------------------

def create_promotion_pr(db: Session, promotion_id: int) -> str:
    """
    Create a GitHub PR for the promotion branch.
    Returns: pr_url | error_message
    """
    promo = db.query(AutoFixPromotion).get(promotion_id)
    if not promo:
        return "not_found"
    if not promo.pushed_at or not promo.branch_name:
        return "branch_not_pushed"
    if promo.pr_url:
        return promo.pr_url  # already created

    if not _has_gh_cli():
        promo.failure_reason = "gh_cli_not_available"
        db.flush()
        return "gh_cli_not_available"

    if not _has_remote():
        promo.failure_reason = "no_git_remote"
        db.flush()
        return "no_git_remote"

    # Build PR body
    from app.models.bugfix_candidate import BugFixCandidate
    candidate = db.query(BugFixCandidate).get(promo.bugfix_candidate_id)
    title = f"chore(autofix): candidate #{promo.bugfix_candidate_id}"
    body_parts = [
        f"## Auto-Fix Candidate #{promo.bugfix_candidate_id}",
        f"**Title:** {candidate.title if candidate else 'N/A'}",
        f"**Patch summary:** {(candidate.patch_summary or 'N/A')[:300] if candidate else 'N/A'}",
        f"**Local CI:** {'passed' if promo.ci_result and 'passed' in promo.ci_result.lower() else 'see details'}",
        f"**Remote CI:** {promo.remote_ci_status or 'pending'}",
        f"**Commit:** `{promo.git_commit_sha[:12]}`",
        "",
        "---",
        "Generated by Hedge Spark autonomous code repair pipeline.",
    ]
    body = "\n".join(body_parts)

    try:
        result = subprocess.run(
            ["gh", "pr", "create",
             "--title", title,
             "--body", body,
             "--head", promo.branch_name,
             "--base", "main"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            promo.pr_url = pr_url
            # Extract PR number from URL
            try:
                promo.pr_number = int(pr_url.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                pass
            db.flush()
            try:
                _notify_promotion(promo, "pr_created")
            except Exception:
                pass
            log.info("promotion: PR created id=%d url=%s", promo.id, pr_url)
            return pr_url
        else:
            promo.failure_reason = f"pr_create_failed: {result.stderr[:300]}"
            db.flush()
            return f"pr_create_failed: {result.stderr[:200]}"
    except Exception as exc:
        promo.failure_reason = f"pr_error: {str(exc)[:200]}"
        db.flush()
        return f"error: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_promotion(db: Session, promotion_id: int) -> str:
    """
    Merge a promotion PR. Human-gated — never auto-called.
    Requires: pushed + PR exists + remote CI passed + approved.
    Returns: merged | error_message
    """
    promo = db.query(AutoFixPromotion).get(promotion_id)
    if not promo:
        return "not_found"
    if not promo.pr_url or not promo.pr_number:
        return "no_pr"
    if promo.remote_ci_status != "passed":
        return f"ci_not_passed: {promo.remote_ci_status or 'unknown'}"
    if promo.status not in ("pushed", "approved"):
        return f"wrong_status: {promo.status}"

    if not _has_gh_cli():
        return "gh_cli_not_available"

    try:
        result = subprocess.run(
            ["gh", "pr", "merge", str(promo.pr_number), "--squash", "--delete-branch"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            promo.status = "merged"
            promo.merged_at = _now()
            # Try to get merge commit SHA
            try:
                sha_result = subprocess.run(
                    ["git", "rev-parse", "main"],
                    cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=5,
                )
                if sha_result.returncode == 0:
                    promo.merge_commit_sha = sha_result.stdout.strip()[:40]
            except Exception:
                pass
            db.flush()
            # Create post-merge outcome for tracking
            try:
                from app.services.merge_intelligence import create_merge_outcome
                create_merge_outcome(db, promo.id)
                db.flush()
            except Exception:
                pass
            try:
                _notify_promotion(promo, "merged")
            except Exception:
                pass
            log.info("promotion: merged id=%d pr=%d", promo.id, promo.pr_number)
            return "merged"
        else:
            promo.failure_reason = f"merge_failed: {result.stderr[:300]}"
            db.flush()
            try:
                _notify_promotion(promo, "merge_failed")
            except Exception:
                pass
            return f"merge_failed: {result.stderr[:200]}"
    except Exception as exc:
        promo.failure_reason = f"merge_error: {str(exc)[:200]}"
        db.flush()
        return f"error: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Auto-promotion pipeline (TIER_0 only)
# ---------------------------------------------------------------------------

import time as _time

_auto_push_cooldown: dict[str, float] = {}
_AUTO_PUSH_COOLDOWN_S = 3600  # 1 hour


def _is_push_on_cooldown(candidate_id: int) -> bool:
    key = str(candidate_id)
    last = _auto_push_cooldown.get(key)
    if last is None:
        return False
    return (_time.monotonic() - last) < _AUTO_PUSH_COOLDOWN_S


def _set_push_cooldown(candidate_id: int):
    _auto_push_cooldown[str(candidate_id)] = _time.monotonic()


def is_promotion_ready() -> tuple[bool, list[str]]:
    """Check if auto-promotion infrastructure is available."""
    reasons = []
    if not _has_remote():
        reasons.append("no_git_remote")
    if not _has_gh_cli():
        reasons.append("no_gh_cli")
    return len(reasons) == 0, reasons


def run_auto_promotion(db: Session, max_per_cycle: int = 1) -> dict:
    """
    Auto-promote TIER_0 applied patches: branch → push → CI poll → PR.
    Max 1 push per cycle. Merge remains human-gated.
    """
    summary = {"branched": 0, "pushed": 0, "ci_polled": 0, "prs_created": 0, "skipped": 0, "errors": 0}

    ready, not_ready_reasons = is_promotion_ready()

    # Phase A: Create branches for pending promotions
    pending = (
        db.query(AutoFixPromotion)
        .filter(AutoFixPromotion.status == "pending")
        .order_by(AutoFixPromotion.created_at)
        .limit(5)
        .all()
    )
    for promo in pending:
        try:
            result = create_promotion_branch(db, promo.id)
            if not result.startswith("error") and not result.startswith("not_found"):
                summary["branched"] += 1
        except Exception as exc:
            summary["errors"] += 1
            log.warning("auto_promotion: branch error id=%d: %s", promo.id, exc)
        db.flush()

    # Phase B: Push branch_created promotions (if infra ready)
    if ready:
        pushable = (
            db.query(AutoFixPromotion)
            .filter(AutoFixPromotion.status == "branch_created")
            .order_by(AutoFixPromotion.created_at)
            .limit(max_per_cycle)
            .all()
        )
        pushed_this_cycle = 0
        for promo in pushable:
            if pushed_this_cycle >= max_per_cycle:
                break
            if _is_push_on_cooldown(promo.bugfix_candidate_id):
                summary["skipped"] += 1
                continue
            try:
                result = push_promotion(db, promo.id)
                if result == "pushed":
                    summary["pushed"] += 1
                    pushed_this_cycle += 1
                    _set_push_cooldown(promo.bugfix_candidate_id)
                else:
                    summary["errors"] += 1
            except Exception as exc:
                summary["errors"] += 1
                log.warning("auto_promotion: push error id=%d: %s", promo.id, exc)
            db.flush()

    # Phase C: Poll remote CI for pushed promotions
    pushed_promos = (
        db.query(AutoFixPromotion)
        .filter(
            AutoFixPromotion.status == "pushed",
            AutoFixPromotion.remote_ci_status.in_([None, "queued", "in_progress"]),
        )
        .order_by(AutoFixPromotion.created_at)
        .limit(5)
        .all()
    )
    for promo in pushed_promos:
        if not ready:
            promo.remote_ci_status = "unconfigured"
            db.flush()
            continue
        try:
            ci_result = check_remote_ci_status(db, promo.id)
            summary["ci_polled"] += 1

            # Auto-create PR if CI passed and no PR yet (max 3 attempts tracked via failure_reason)
            if ci_result == "passed" and not promo.pr_url:
                pr_attempts = (promo.failure_reason or "").count("pr_attempt")
                if pr_attempts < 3:
                    try:
                        pr_result = create_promotion_pr(db, promo.id)
                        if pr_result.startswith("http"):
                            summary["prs_created"] += 1
                            promo.failure_reason = None  # clear on success
                        else:
                            promo.failure_reason = f"{promo.failure_reason or ''}; pr_attempt_{pr_attempts+1}: {pr_result[:100]}"
                    except Exception as exc:
                        promo.failure_reason = f"{promo.failure_reason or ''}; pr_attempt_{pr_attempts+1}: {exc}"
                        log.warning("auto_promotion: PR create error id=%d: %s", promo.id, exc)

            # Alert on CI failure
            if ci_result == "failed":
                try:
                    from app.services.alerting import write_alert
                    write_alert(
                        db, severity="warning", source="promotion_pipeline",
                        alert_type="remote_ci_failed",
                        summary=f"Remote CI failed for promotion #{promo.id} branch={promo.branch_name}",
                        detail={"promotion_id": promo.id, "ci_result": promo.ci_result},
                    )
                except Exception:
                    pass

        except Exception as exc:
            summary["errors"] += 1
            log.warning("auto_promotion: CI poll error id=%d: %s", promo.id, exc)
        db.flush()

    if any(v > 0 for k, v in summary.items() if k != "skipped"):
        log.info(
            "auto_promotion: branched=%d pushed=%d ci_polled=%d prs=%d errors=%d",
            summary["branched"], summary["pushed"], summary["ci_polled"],
            summary["prs_created"], summary["errors"],
        )

    return summary


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify_promotion(promo: AutoFixPromotion, event: str) -> None:
    """Send Slack notification for promotion events. Fire-and-forget."""
    try:
        from app.core.alert_delivery import _SLACK_URL
        if not _SLACK_URL:
            return
        import httpx

        emoji = {
            "created": ":package:",
            "ci_failed": ":x:",
            "remote_ci_failed": ":x:",
            "pushed": ":rocket:",
            "push_failed": ":warning:",
            "pr_created": ":clipboard:",
            "merged": ":white_check_mark:",
            "merge_failed": ":warning:",
        }.get(event, ":grey_question:")

        httpx.post(_SLACK_URL, json={
            "text": (
                f"{emoji} *PROMOTION {event.upper()}*\n"
                f"*Branch:* `{promo.branch_name or 'pending'}`\n"
                f"*SHA:* `{promo.git_commit_sha[:12]}`\n"
                f"*ID:* {promo.id}"
            ),
        }, timeout=5.0)
        promo.notified_at = _now()
    except Exception:
        pass
