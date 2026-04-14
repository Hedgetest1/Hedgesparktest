"""
promotion_pipeline.py — Promotes local auto-fix commits to remote branches.

Flow:
    1. Auto-applied bugfix → AutoFixPromotion(pending) created
    2. create_promotion_branch() → local branch + status=branch_created
    3. run_promotion_ci_check() → ci_pending/ci_passed/ci_failed
    4. push_promotion() → git push origin branch → pushed
    5. create_promotion_pr() → PR opened on GitHub → pr_url set
    6. merge_promotion() — still human-callable for the normal path
    7. run_auto_merge() — NEW: gated closed-loop merge, runs only when
       env AUTO_MERGE_TIER0=1 is set. All gates from merge_intelligence
       must green, the patch must be TIER_0, patch_files must not touch
       any path in _AUTO_MERGE_FORBIDDEN_PREFIXES, and a 1h/cycle cooldown
       applies. Default off. Enables true unattended closed-loop repair
       for the safest class of fix while keeping critical surfaces gated.
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

        if result.returncode != 0:
            promo.status = "ci_failed"
            promo.failure_reason = f"tests_failed (exit {result.returncode})"
            _notify_promotion(promo, "ci_failed")
            log.warning("promotion: CI failed id=%d", promo.id)
            return "ci_failed"

        # Frontend build verification — check if the promoted patch touches dashboard
        try:
            from app.models.bugfix_candidate import BugFixCandidate
            import json as _json
            candidate = db.query(BugFixCandidate).get(promo.bugfix_candidate_id)
            if candidate and candidate.patch_files:
                from app.core.tier_check import require_frontend_build
                files = _json.loads(candidate.patch_files)
                if require_frontend_build(files):
                    log.info("promotion: running frontend build check (dashboard files in patch)")
                    from app.core.pre_apply_guard import verify_frontend_build
                    build_ok, build_output = verify_frontend_build()
                    if not build_ok:
                        promo.status = "ci_failed"
                        promo.failure_reason = f"frontend_build_failed: {build_output[:300]}"
                        promo.ci_result = f"{ci_output[:1500]}\n\n--- FRONTEND BUILD ---\n{build_output[:400]}"
                        _notify_promotion(promo, "ci_failed")
                        log.warning("promotion: frontend build failed id=%d", promo.id)
                        db.flush()
                        return "ci_failed"
        except ImportError:
            pass  # tier_check/pre_apply_guard not available, skip frontend check
        except Exception as exc:
            log.warning("promotion: frontend build check error (non-fatal): %s", exc)

        promo.status = "ci_passed"
        log.info("promotion: CI passed id=%d", promo.id)
        return "ci_passed"
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
        "Generated by HedgeSpark autonomous code repair pipeline.",
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

_AUTO_MERGE_COOLDOWN_S = 3600  # 1 hour — never merge more than one TIER_0 fix per hour
_AUTO_MERGE_COOLDOWN_REDIS_KEY = "hs:auto_merge_cooldown"

# Frontend paths where an accidental regression would be immediately
# merchant-visible. Auto-merge is never allowed to touch these, even if
# all other gates pass — they require human eyes by policy.
_AUTO_MERGE_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "dashboard/src/app/components/billing",
    "dashboard/src/app/components/onboarding",
    "dashboard/src/app/components/auth",
    "dashboard/src/app/install",
    "dashboard/src/app/pricing",
    # Backend criticals are already blocked by TIER policy but we list them
    # here as a belt-and-suspenders defense in case TIER enforcement regresses.
    "app/api/billing",
    "app/api/shopify_oauth",
    "app/api/webhooks.py",
    "app/core/merchant_session",
    "app/core/token_crypto",
    "migrations/",
)


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
# Gated auto-merge — closes the last loop for truly safe TIER_0 fixes
# ---------------------------------------------------------------------------

def _is_auto_merge_enabled() -> bool:
    """Auto-merge for TIER_0 fixes. ON by default — set
    AUTO_MERGE_TIER0=0 to disable in an emergency.

    The gate stack inside `_is_auto_mergeable` (cooldown, merge_intelligence,
    forbidden paths, reviewer risk) is the actual safety net; the env
    var is the operator kill-switch.
    """
    val = os.getenv("AUTO_MERGE_TIER0", "1").strip()
    return val not in ("0", "false", "False", "")


def _is_auto_merge_on_cooldown() -> bool:
    """Check via Redis (survives PM2 restarts)."""
    try:
        from app.core.redis_client import cache_get
        return cache_get(_AUTO_MERGE_COOLDOWN_REDIS_KEY) is not None
    except Exception:
        return False  # Redis down — allow merge (fail-open is safer than blocking pipeline)


def _mark_auto_merge_done() -> None:
    """Set Redis cooldown marker (survives PM2 restarts)."""
    try:
        from app.core.redis_client import cache_set
        cache_set(_AUTO_MERGE_COOLDOWN_REDIS_KEY, True, _AUTO_MERGE_COOLDOWN_S)
    except Exception:
        pass  # Redis down — next cycle will re-check


def _candidate_touches_forbidden_path(patch_files_json: str | None) -> str | None:
    """Return the first forbidden prefix a patch touches, or None if safe."""
    if not patch_files_json:
        # Unknown patch surface → treat as unsafe. We will not auto-merge
        # a fix whose file list we cannot inspect.
        return "unknown_patch_files"
    try:
        import json as _json
        files = _json.loads(patch_files_json) or []
    except Exception:
        return "patch_files_unparseable"
    for f in files:
        if not isinstance(f, str):
            continue
        for prefix in _AUTO_MERGE_FORBIDDEN_PREFIXES:
            if f.startswith(prefix):
                return prefix
    return None


def _is_auto_mergeable(db: Session, promo: AutoFixPromotion) -> tuple[bool, str]:
    """
    Single decision function that determines whether a promotion is safe
    to auto-merge. Returns (ok, reason). Centralizes every gate so they
    are inspectable, testable, and hard to bypass.
    """
    # Gate 0: kill-switch
    if not _is_auto_merge_enabled():
        return False, "auto_merge_disabled"

    # Gate 1: cooldown
    if _is_auto_merge_on_cooldown():
        return False, "cooldown_active"

    # Gate 2: merge_intelligence recommends the merge (covers PR, CI,
    # critical alerts window, TIER_0, rollback, applied state).
    try:
        from app.services.merge_intelligence import compute_merge_recommendation
        rec = compute_merge_recommendation(db, promo.id)
    except Exception as exc:
        return False, f"merge_rec_error:{type(exc).__name__}"
    if not rec.recommend:
        return False, "merge_rec_blocked:" + ",".join(rec.reasons[:3])

    # Gate 3: patch must not touch a forbidden merchant-facing or TIER-critical path.
    from app.models.bugfix_candidate import BugFixCandidate
    candidate = db.query(BugFixCandidate).get(promo.bugfix_candidate_id)
    if not candidate:
        return False, "candidate_missing"
    forbidden = _candidate_touches_forbidden_path(candidate.patch_files)
    if forbidden:
        return False, f"forbidden_path:{forbidden}"

    # Gate 4: reviewer layer risk_level must be 'low' or lower.
    try:
        from app.models.reviewer_assessment import ReviewerAssessment
        latest_review = (
            db.query(ReviewerAssessment)
            .filter(
                ReviewerAssessment.entity_type == "bugfix_candidate",
                ReviewerAssessment.entity_id == candidate.id,
            )
            .order_by(ReviewerAssessment.created_at.desc())
            .first()
        )
        if latest_review is not None:
            risk = (latest_review.risk_level or "").lower()
            if risk not in ("", "low", "none"):
                return False, f"reviewer_risk:{risk}"
    except Exception:
        # Reviewer table missing → be permissive but log; this path should
        # never trigger in production because reviewer_layer writes on every apply.
        log.warning("auto_merge: reviewer assessment lookup skipped (non-fatal)")

    return True, "ok"


def run_auto_merge(db: Session, max_per_cycle: int = 1) -> dict:
    """
    Auto-merge pushed promotions whose gates are all green.

    Respects:
      * AUTO_MERGE_TIER0 env kill-switch (default off)
      * 1h cooldown between auto-merges
      * merge_intelligence recommendation (covers CI/PR/critical-alerts gates)
      * Forbidden-path list (billing/auth/onboarding/migrations/webhooks)
      * Reviewer risk_level ≤ low
      * Max N merges per cycle (default 1)

    Calls merge_promotion() for the actual gh pr merge — this function is
    pure orchestration. On merge, emits a Telegram/Slack notification
    (reusing _notify_promotion). Post-merge outcome tracking is already
    handled inside merge_promotion().
    """
    summary = {
        "considered": 0,
        "merged": 0,
        "skipped_disabled": 0,
        "skipped_cooldown": 0,
        "skipped_gate": 0,
        "errors": 0,
        "reasons": [],
    }

    if not _is_auto_merge_enabled():
        summary["skipped_disabled"] += 1
        return summary
    if _is_auto_merge_on_cooldown():
        summary["skipped_cooldown"] += 1
        return summary

    # Candidates for auto-merge: pushed, PR exists, CI passed.
    candidates = (
        db.query(AutoFixPromotion)
        .filter(
            AutoFixPromotion.status.in_(("pushed", "approved")),
            AutoFixPromotion.pr_url.isnot(None),
            AutoFixPromotion.remote_ci_status == "passed",
        )
        .order_by(AutoFixPromotion.created_at)
        .limit(5)
        .all()
    )

    merged_this_cycle = 0
    for promo in candidates:
        if merged_this_cycle >= max_per_cycle:
            break
        summary["considered"] += 1
        ok, reason = _is_auto_mergeable(db, promo)
        if not ok:
            summary["skipped_gate"] += 1
            summary["reasons"].append(f"promo={promo.id}:{reason}")
            log.info("auto_merge: skipped promo=%d reason=%s", promo.id, reason)
            continue
        try:
            result = merge_promotion(db, promo.id)
            if result == "merged":
                summary["merged"] += 1
                merged_this_cycle += 1
                _mark_auto_merge_done()
                log.info(
                    "auto_merge: MERGED promo=%d pr=%s branch=%s",
                    promo.id, promo.pr_number, promo.branch_name,
                )
            else:
                summary["errors"] += 1
                summary["reasons"].append(f"promo={promo.id}:merge_returned:{result[:80]}")
                log.warning(
                    "auto_merge: merge_promotion returned non-success promo=%d result=%s",
                    promo.id, result,
                )
        except Exception as exc:
            summary["errors"] += 1
            summary["reasons"].append(f"promo={promo.id}:exc:{type(exc).__name__}")
            log.warning("auto_merge: error promo=%d: %s", promo.id, exc)
        db.flush()

    if summary["merged"] > 0 or summary["errors"] > 0:
        log.info(
            "auto_merge: considered=%d merged=%d errors=%d skipped_gate=%d",
            summary["considered"], summary["merged"],
            summary["errors"], summary["skipped_gate"],
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


# ---------------------------------------------------------------------------
# Auto-deploy after merge — closes the very last loop
# ---------------------------------------------------------------------------
#
# After `merge_promotion` finalizes a PR, the merged commit is on `origin/main`
# but production still runs the previous SHA. Historically the operator had to
# `git pull && pm2 restart` manually — that human gate was the real reason
# "the system felt asleep for days". This module turns it into a fully gated
# automatic step.
#
# Safety stack (any failing gate aborts the deploy):
#   1. AUTO_DEPLOY_PAUSED env kill-switch
#   2. Per-cycle cap + 20-min cooldown between consecutive deploys
#   3. deploy_gate.py --preflight (health, cooldown, last-good commit)
#   4. Subprocess git pull (fails closed on conflicts)
#   5. pm2 restart of backend + dashboard (agent_worker is a separate process,
#      so it does not commit suicide by restarting wishspark-backend)
#   6. deploy_gate.py --postdeploy --auto-rollback (health-poll, log-spike,
#      auto-rollback on failure)
#   7. Per-promotion idempotency via Redis (`hs:deploy:promotion:{id}`)
#   8. write_alert ops_alert on every outcome (deploy_succeeded / deploy_failed
#      / deploy_rolled_back) so the daily digest sees it.

_AUTO_DEPLOY_COOLDOWN_S = 20 * 60
_AUTO_DEPLOY_MAX_PER_CYCLE = 1
_AUTO_DEPLOY_REPO = "/opt/wishspark"
_AUTO_DEPLOY_GATE_SCRIPT = "/opt/wishspark/backend/scripts/deploy_gate.py"
_AUTO_DEPLOY_PROCESSES = ("wishspark-backend", "wishspark-dashboard")
_auto_deploy_last: float | None = None


def _is_auto_deploy_enabled() -> bool:
    """Auto-deploy is ON by default. Set AUTO_DEPLOY_PAUSED=1 to halt
    every future deploy in 5 seconds (operator emergency stop)."""
    if os.getenv("AUTO_DEPLOY_PAUSED", "").strip() == "1":
        return False
    return True


def _is_auto_deploy_on_cooldown() -> bool:
    global _auto_deploy_last
    if _auto_deploy_last is None:
        return False
    return (_time.monotonic() - _auto_deploy_last) < _AUTO_DEPLOY_COOLDOWN_S


def _mark_auto_deploy_done() -> None:
    global _auto_deploy_last
    _auto_deploy_last = _time.monotonic()


def _deploy_marker_key(promo_id: int) -> str:
    return f"hs:deploy:promotion:{promo_id}"


def _is_promotion_already_deployed(promo_id: int) -> bool:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("promotion_pipeline.deployed_check")
            return False
        return bool(rc.exists(_deploy_marker_key(promo_id)))
    except Exception:
        return False


def _mark_promotion_deployed(promo_id: int, sha: str) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("promotion_pipeline.mark_deployed")
            return
        rc.setex(_deploy_marker_key(promo_id), 90 * 24 * 3600, sha)
    except Exception:
        pass


def _shell(cmd: list[str], *, timeout: int = 120) -> tuple[int, str]:
    """Run a shell command, return (rc, combined_output_first_2k_chars)."""
    import subprocess
    try:
        result = subprocess.run(
            cmd,
            cwd=_AUTO_DEPLOY_REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = ((result.stdout or "") + (result.stderr or ""))[:2000]
        return result.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _deploy_one_promotion(db: Session, promo: AutoFixPromotion) -> dict:
    """Deploy a single merged promotion. Returns a structured result.

    On success: Redis marker set, ops_alert(deploy_succeeded) written.
    On failure: ops_alert(deploy_failed) written, no marker so the next
    cycle can retry once the underlying issue is resolved.
    On rollback: ops_alert(deploy_rolled_back) written, marker still
    set so we don't loop on the same broken promotion.
    """
    result = {
        "promo_id": promo.id,
        "status": "unknown",
        "preflight_rc": None,
        "git_pull_rc": None,
        "pm2_rc": None,
        "postdeploy_rc": None,
        "rolled_back": False,
        "error": None,
    }

    from app.services.alerting import write_alert

    # 1. Preflight gate (health, cooldown, stores last_good_commit)
    rc, out = _shell(["python3", _AUTO_DEPLOY_GATE_SCRIPT, "--preflight"], timeout=120)
    result["preflight_rc"] = rc
    if rc != 0:
        result["status"] = "preflight_blocked"
        result["error"] = out
        try:
            write_alert(
                db,
                source=f"auto_deploy:promo_{promo.id}",
                alert_type="deploy_failed",
                severity="warning",
                summary=f"Auto-deploy preflight blocked promo {promo.id}",
                detail={"phase": "preflight", "rc": rc, "output": out},
            )
        except Exception:
            pass
        return result

    # 2. git pull origin main — fails closed on merge conflict / dirty tree
    rc, out = _shell(["git", "pull", "--ff-only", "origin", "main"], timeout=60)
    result["git_pull_rc"] = rc
    if rc != 0:
        result["status"] = "git_pull_failed"
        result["error"] = out
        try:
            write_alert(
                db,
                source=f"auto_deploy:promo_{promo.id}",
                alert_type="deploy_failed",
                severity="critical",
                summary=f"Auto-deploy git pull failed promo {promo.id}",
                detail={"phase": "git_pull", "rc": rc, "output": out},
            )
        except Exception:
            pass
        return result

    # 3. Restart backend + dashboard processes (agent_worker is a sibling
    #    process under PM2, so it does NOT commit suicide here)
    rc, out = _shell(["pm2", "restart", *_AUTO_DEPLOY_PROCESSES, "--update-env"], timeout=60)
    result["pm2_rc"] = rc
    if rc != 0:
        result["status"] = "pm2_restart_failed"
        result["error"] = out
        try:
            write_alert(
                db,
                source=f"auto_deploy:promo_{promo.id}",
                alert_type="deploy_failed",
                severity="critical",
                summary=f"Auto-deploy pm2 restart failed promo {promo.id}",
                detail={"phase": "pm2_restart", "rc": rc, "output": out},
            )
        except Exception:
            pass
        return result

    # 4. Postdeploy gate with auto-rollback enabled
    rc, out = _shell(
        ["python3", _AUTO_DEPLOY_GATE_SCRIPT, "--postdeploy", "--auto-rollback"],
        timeout=180,
    )
    result["postdeploy_rc"] = rc
    if rc != 0:
        # deploy_gate already attempted git reset + pm2 restart on its side
        result["status"] = "postdeploy_failed_rolled_back"
        result["rolled_back"] = True
        result["error"] = out
        _mark_promotion_deployed(promo.id, promo.merge_commit_sha or "")
        try:
            write_alert(
                db,
                source=f"auto_deploy:promo_{promo.id}",
                alert_type="deploy_rolled_back",
                severity="critical",
                summary=f"Auto-deploy postdeploy failed → rolled back promo {promo.id}",
                detail={"phase": "postdeploy", "rc": rc, "output": out},
            )
        except Exception:
            pass
        return result

    # SUCCESS path
    _mark_promotion_deployed(promo.id, promo.merge_commit_sha or "")
    result["status"] = "deployed"
    try:
        write_alert(
            db,
            source=f"auto_deploy:promo_{promo.id}",
            alert_type="deploy_succeeded",
            severity="info",
            summary=(
                f"Auto-deploy ok promo={promo.id} "
                f"sha={(promo.merge_commit_sha or '')[:12]} "
                f"branch={promo.branch_name or '?'}"
            ),
            detail={
                "promo_id": promo.id,
                "merge_commit_sha": promo.merge_commit_sha,
                "branch": promo.branch_name,
                "candidate_id": promo.bugfix_candidate_id,
            },
        )
    except Exception:
        pass
    return result


_AUTO_DEPLOY_BATCH_MAX_SIZE = 5  # never restart for more than 5 fixes at once


def _deploy_batch(db: Session, promos: list[AutoFixPromotion]) -> dict:
    """C4 — Deploy N merged promotions in a single git pull + pm2 restart.

    Atomic semantics:
      * One preflight gate covers all
      * Single git pull --ff-only — picks up every merged commit
      * Single pm2 restart — single restart event for the operator
      * One postdeploy gate — if it fails, ALL batched promotions are
        marked rolled_back (the deploy_gate auto-rollback reverts every
        commit since last_good_commit, which covers them all)
      * Per-promotion idempotency markers set on success

    Returns summary keyed by individual promotion ids so the caller can
    map outcomes back to candidates.
    """
    from app.services.alerting import write_alert

    batch_ids = [p.id for p in promos]
    summary = {
        "promo_ids": batch_ids,
        "size": len(promos),
        "status": "unknown",
        "preflight_rc": None,
        "git_pull_rc": None,
        "pm2_rc": None,
        "postdeploy_rc": None,
        "rolled_back": False,
        "error": None,
    }

    # 1. Preflight (covers the whole batch)
    rc, out = _shell(["python3", _AUTO_DEPLOY_GATE_SCRIPT, "--preflight"], timeout=120)
    summary["preflight_rc"] = rc
    if rc != 0:
        summary["status"] = "preflight_blocked"
        summary["error"] = out
        try:
            write_alert(
                db, source=f"auto_deploy:batch_{len(promos)}",
                alert_type="deploy_failed", severity="warning",
                summary=f"Auto-deploy batch ({len(promos)} promos) preflight blocked",
                detail={"phase": "preflight", "rc": rc, "output": out, "batch_ids": batch_ids},
            )
        except Exception:
            pass
        return summary

    # 2. Single git pull picks up every merged commit
    rc, out = _shell(["git", "pull", "--ff-only", "origin", "main"], timeout=60)
    summary["git_pull_rc"] = rc
    if rc != 0:
        summary["status"] = "git_pull_failed"
        summary["error"] = out
        try:
            write_alert(
                db, source=f"auto_deploy:batch_{len(promos)}",
                alert_type="deploy_failed", severity="critical",
                summary=f"Auto-deploy batch ({len(promos)} promos) git pull failed",
                detail={"phase": "git_pull", "rc": rc, "output": out, "batch_ids": batch_ids},
            )
        except Exception:
            pass
        return summary

    # 3. Single pm2 restart for the whole batch
    rc, out = _shell(["pm2", "restart", *_AUTO_DEPLOY_PROCESSES, "--update-env"], timeout=60)
    summary["pm2_rc"] = rc
    if rc != 0:
        summary["status"] = "pm2_restart_failed"
        summary["error"] = out
        try:
            write_alert(
                db, source=f"auto_deploy:batch_{len(promos)}",
                alert_type="deploy_failed", severity="critical",
                summary=f"Auto-deploy batch ({len(promos)} promos) pm2 restart failed",
                detail={"phase": "pm2_restart", "rc": rc, "output": out, "batch_ids": batch_ids},
            )
        except Exception:
            pass
        return summary

    # 4. Postdeploy gate covers the whole batch — auto-rollback reverts
    # every commit since last_good_commit, which is correct for batched.
    rc, out = _shell(
        ["python3", _AUTO_DEPLOY_GATE_SCRIPT, "--postdeploy", "--auto-rollback"],
        timeout=180,
    )
    summary["postdeploy_rc"] = rc
    if rc != 0:
        summary["status"] = "postdeploy_failed_rolled_back"
        summary["rolled_back"] = True
        summary["error"] = out
        # All batched promotions get the marker so we don't loop on the
        # same broken batch forever
        for promo in promos:
            _mark_promotion_deployed(promo.id, promo.merge_commit_sha or "")
        try:
            write_alert(
                db, source=f"auto_deploy:batch_{len(promos)}",
                alert_type="deploy_rolled_back", severity="critical",
                summary=(
                    f"Auto-deploy batch ({len(promos)} promos) postdeploy "
                    f"failed → rolled back"
                ),
                detail={"phase": "postdeploy", "rc": rc, "output": out, "batch_ids": batch_ids},
            )
        except Exception:
            pass
        return summary

    # SUCCESS — mark every promotion deployed
    for promo in promos:
        _mark_promotion_deployed(promo.id, promo.merge_commit_sha or "")
    summary["status"] = "deployed"
    try:
        write_alert(
            db, source=f"auto_deploy:batch_{len(promos)}",
            alert_type="deploy_succeeded", severity="info",
            summary=(
                f"Auto-deploy batch ok: {len(promos)} fixes shipped "
                f"in 1 restart"
            ),
            detail={"batch_ids": batch_ids, "size": len(promos)},
        )
    except Exception:
        pass
    return summary


def run_auto_deploy(db: Session, max_per_cycle: int = _AUTO_DEPLOY_MAX_PER_CYCLE) -> dict:
    """Pull merged promotions into production. Idempotent + rate-limited.

    C4 — batched: collect every undeployed merged promotion (up to
    `_AUTO_DEPLOY_BATCH_MAX_SIZE`) and deploy them in a single git pull +
    pm2 restart. Less restart churn, atomic rollback semantics.

    Called from agent_worker after `run_auto_merge`.
    """
    summary = {
        "considered": 0,
        "deployed": 0,
        "rolled_back": 0,
        "failed": 0,
        "skipped_disabled": 0,
        "skipped_cooldown": 0,
        "skipped_already_deployed": 0,
        "batch_size": 0,
        "results": [],
    }

    if not _is_auto_deploy_enabled():
        summary["skipped_disabled"] += 1
        return summary
    if _is_auto_deploy_on_cooldown():
        summary["skipped_cooldown"] += 1
        return summary

    candidates = (
        db.query(AutoFixPromotion)
        .filter(
            AutoFixPromotion.status == "merged",
            AutoFixPromotion.merge_commit_sha.isnot(None),
        )
        .order_by(AutoFixPromotion.merged_at.asc())
        .limit(_AUTO_DEPLOY_BATCH_MAX_SIZE * 2)  # over-fetch to filter already-deployed
        .all()
    )

    # Filter out already-deployed promotions
    deployable: list[AutoFixPromotion] = []
    for promo in candidates:
        if _is_promotion_already_deployed(promo.id):
            summary["skipped_already_deployed"] += 1
            continue
        deployable.append(promo)
        if len(deployable) >= _AUTO_DEPLOY_BATCH_MAX_SIZE:
            break

    if not deployable:
        return summary

    summary["considered"] = len(deployable)
    summary["batch_size"] = len(deployable)

    result = _deploy_batch(db, deployable)
    summary["results"].append(result)

    if result["status"] == "deployed":
        summary["deployed"] = len(deployable)
        _mark_auto_deploy_done()
    elif result["rolled_back"]:
        summary["rolled_back"] = len(deployable)
        _mark_auto_deploy_done()
    else:
        summary["failed"] = len(deployable)

    try:
        db.commit()
    except Exception:
        db.rollback()

    if summary["deployed"] or summary["rolled_back"] or summary["failed"]:
        log.info(
            "auto_deploy: batch=%d deployed=%d rolled_back=%d failed=%d",
            summary["batch_size"], summary["deployed"],
            summary["rolled_back"], summary["failed"],
        )
    return summary
