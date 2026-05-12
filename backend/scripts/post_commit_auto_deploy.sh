#!/usr/bin/env bash
# post_commit_auto_deploy.sh — TIER_0 auto-deploy (Phase 1.9.5).
#
# Installed as .git/hooks/post-commit. Runs after a commit lands.
# The commit ALREADY passed 31 preflight audits (pre-commit hook), so
# code hygiene is verified. This hook classifies the commit's tier per
# CLAUDE.md §10 and auto-invokes `./dashboard/scripts/deploy.sh` ONLY
# for TIER_0 commits.
#
# TIER_1 and TIER_2 commits stay manual-deploy for now. TIER_1 unlocks
# when Phase 2.0 Elite Auto-Deploy Stack ships (staging env + auto-
# rollback + holdout measurement + B1 activation, all at ≥9/10).
# Roadmap: project_elite_auto_deploy_phase_2_0.md.
#
# Safety:
# - Preflight already passed (31 audits blocked bad code)
# - audit_dashboard_live runs post-deploy inside deploy.sh; exits
#   non-zero if chunks drift → operator sees the failure
# - Logs everything to /tmp/auto_deploy.log for postmortem
#
# Escape: set HS_NO_AUTO_DEPLOY=1 in env to skip (useful during
# hotfix sessions where you want to inspect before deploying).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"
DASHBOARD="$REPO_ROOT/dashboard"
PY="$BACKEND/venv/bin/python"
LOG=/tmp/auto_deploy.log

# Colors (TTY-aware)
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YEL='\033[0;33m'; NC='\033[0m'
else
    GREEN=''; RED=''; YEL=''; NC=''
fi

log()  { printf "%b[auto-deploy] %s%b\n" "$YEL" "$1" "$NC"; }
ok()   { printf "%b[auto-deploy] ✓ %s%b\n" "$GREEN" "$1" "$NC"; }
fail() { printf "%b[auto-deploy] ✗ %s%b\n" "$RED" "$1" "$NC" >&2; }

# Health probe with exponential backoff. Born 2026-04-22 after the
# fixed `sleep 2 + curl once` pattern produced a false-FAIL on commit
# a423c44 when the backend took > 2s to bind to :8000 post-reload.
# Uvicorn's reload path varies by app size + import graph; a single
# 2s checkpoint is too optimistic. Retries on 1s → 2s → 4s → 8s
# delays (total budget ~15s), returns 0 on first success, 1 if the
# whole budget is exhausted without a healthy response.
_health_probe_with_backoff() {
    local url="$1"
    local delay=1
    local total=0
    local budget=15
    while [ "$total" -lt "$budget" ]; do
        if curl -sf -o /dev/null --max-time 3 "$url"; then
            return 0
        fi
        sleep "$delay"
        total=$((total + delay))
        delay=$((delay * 2))
        [ "$delay" -gt 8 ] && delay=8
    done
    return 1
}

# Opt-out via env var
if [ "${HS_NO_AUTO_DEPLOY:-0}" = "1" ]; then
    log "HS_NO_AUTO_DEPLOY=1 set — skipping auto-deploy for this commit"
    exit 0
fi

# Skip during rebases / merges / amend / cherry-pick etc.
# Git sets these env vars or creates these files during multi-commit ops.
if [ -n "${GIT_REBASE_IN_PROGRESS:-}" ] || \
   [ -d "$REPO_ROOT/.git/rebase-merge" ] || \
   [ -d "$REPO_ROOT/.git/rebase-apply" ] || \
   [ -f "$REPO_ROOT/.git/MERGE_HEAD" ] || \
   [ -f "$REPO_ROOT/.git/CHERRY_PICK_HEAD" ]; then
    log "multi-commit operation in progress — skip"
    exit 0
fi

# Classify commit
# Capture stdout (TIER line) and stderr (reasons) separately so the
# tier classification is unambiguous. Defense-in-depth: the classifier
# also flushes stdout first, but explicit stream separation here means
# even if a future buffering quirk reorders, we still parse stdout-only
# for the TIER. Born 2026-05-11 Senior+++ close.
TIER=$("$PY" "$BACKEND/scripts/classify_commit_tier.py" HEAD 2>/dev/null || true)
TIER_DETAIL=$("$PY" "$BACKEND/scripts/classify_commit_tier.py" HEAD 2>&1 1>/dev/null || true)
TIER_OUTPUT="${TIER}${TIER_DETAIL:+
$TIER_DETAIL}"

case "$TIER" in
    TIER_0)
        ok "commit classified TIER_0 — proceeding with auto-deploy"
        ;;
    TIER_1)
        log "commit is TIER_1 — manual deploy required (Phase 2.0 unlocks this)"
        echo "$TIER_OUTPUT" | tail -n +2 >&2
        echo ""
        log "Run manually when ready: cd $DASHBOARD && ./scripts/deploy.sh"
        exit 0
        ;;
    TIER_2)
        fail "commit touches TIER_2 governance files — manual deploy ONLY"
        echo "$TIER_OUTPUT" | tail -n +2 >&2
        echo ""
        log "Review diff carefully, then: cd $DASHBOARD && ./scripts/deploy.sh"
        exit 0
        ;;
    *)
        fail "classifier returned unexpected output: $TIER_OUTPUT"
        log "Falling back to manual deploy"
        exit 0
        ;;
esac

# Detect which side of the repo was touched. If only backend, skip the
# dashboard rebuild; if only docs/scripts, skip deploy entirely.
CHANGED_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null || echo "")
DASH_TOUCHED=0
BACKEND_TOUCHED=0
OTHER_TOUCHED=0
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        dashboard/*) DASH_TOUCHED=1 ;;
        backend/*)   BACKEND_TOUCHED=1 ;;
        *)           OTHER_TOUCHED=1 ;;
    esac
done <<< "$CHANGED_FILES"

if [ "$DASH_TOUCHED" = "0" ] && [ "$BACKEND_TOUCHED" = "0" ]; then
    log "commit touches only docs/memory/scripts — no deploy needed"
    exit 0
fi

# Log the attempt
{
    echo "======== $(date -u +%Y-%m-%dT%H:%M:%SZ) ========"
    echo "commit: $(git rev-parse HEAD)"
    echo "tier: $TIER"
    echo "dashboard_touched: $DASH_TOUCHED"
    echo "backend_touched: $BACKEND_TOUCHED"
} >> "$LOG"

# Run dashboard deploy when frontend changed (script also restarts
# dashboard PM2 and verifies served chunks).
if [ "$DASH_TOUCHED" = "1" ]; then
    log "running dashboard deploy..."
    if (cd "$DASHBOARD" && ./scripts/deploy.sh) >> "$LOG" 2>&1; then
        ok "dashboard deploy green"
    else
        fail "dashboard deploy FAILED — see $LOG"
        tail -20 "$LOG" >&2
        # Deploy script already handles rollback on audit_dashboard_live
        # failure; we just surface the error.
        exit 1
    fi
fi

# PM2 worker singletons. Born 2026-05-12 after discovering 4 workers
# had been running stale code for 26 days (created 2026-04-16) while
# backend reloaded normally on every commit. Without this list, every
# commit that touched a module imported by workers silently failed to
# take effect — module cache in long-running workers kept the old code.
# Smoking-gun example: scoring_calibration.py was deleted on 2026-05-09
# but agent_worker kept querying its bugfix_candidates table at every
# 15-min cycle through 2026-05-12 because the worker was never reloaded.
WISHSPARK_WORKERS=(
    wishspark-worker                # intelligence_worker.py
    wishspark-agent-worker          # agent_worker.py
    wishspark-aggregation-worker    # aggregation_worker.py
    wishspark-segment-monitor       # segment_monitor_worker.py
    wishspark-nudge-optimizer       # nudge_optimization_worker.py
    wishspark-gdpr-worker           # gdpr_worker.py
)

# Reload all worker singletons. Workers are cron-loops, idempotent on
# restart, and import from app.core / app.services — so any backend
# code change can affect them. Failures here are logged but do NOT
# abort the deploy: a missing worker reload degrades to a stale-cache
# alert later, not a user-facing outage.
_reload_workers() {
    local total=${#WISHSPARK_WORKERS[@]}
    local fails=0
    for w in "${WISHSPARK_WORKERS[@]}"; do
        if pm2 reload "$w" --update-env >> "$LOG" 2>&1; then
            :
        else
            log "worker reload FAILED: $w (continuing)"
            fails=$((fails+1))
        fi
    done
    if [ "$fails" = "0" ]; then
        ok "worker reloads: $total/$total green"
    else
        fail "worker reloads: $((total - fails))/$total green ($fails failed — investigate)"
    fi
}

# When only backend changed, reload wishspark-backend PM2 so the new
# Python code is actually running (deploy.sh handles dashboard only).
if [ "$BACKEND_TOUCHED" = "1" ] && [ "$DASH_TOUCHED" = "0" ]; then
    log "running backend reload..."
    if pm2 reload wishspark-backend --update-env >> "$LOG" 2>&1; then
        if _health_probe_with_backoff "http://127.0.0.1:8000/system/health"; then
            ok "backend reload green"
            _reload_workers
        else
            fail "backend reload but health probe FAILED after 15s budget"
            exit 1
        fi
    else
        fail "backend reload FAILED — see $LOG"
        tail -20 "$LOG" >&2
        exit 1
    fi
fi

# Both backend + dashboard: deploy.sh already restarts dashboard; we
# still need the backend reload in that case.
if [ "$BACKEND_TOUCHED" = "1" ] && [ "$DASH_TOUCHED" = "1" ]; then
    log "running backend reload (dashboard already deployed above)..."
    if pm2 reload wishspark-backend --update-env >> "$LOG" 2>&1; then
        if _health_probe_with_backoff "http://127.0.0.1:8000/system/health"; then
            ok "backend reload green"
            _reload_workers
        else
            fail "backend reload but health probe FAILED after 15s budget"
            exit 1
        fi
    fi
fi

ok "TIER_0 auto-deploy complete for commit $(git rev-parse --short HEAD)"

# Independent post-commit review (Gap 1, Phase L of the elite-tier
# brutal-CTO sprint). Runs as a separate process from the agent that
# wrote the commit, deterministic claim verifier — flags structural
# inconsistencies between commit message claims and the diff.
# Non-blocking; writes ops_alert on findings so next triage cycle
# catches them.
log "running independent post-commit review..."
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/post_commit_independent_review.py" >> "$LOG" 2>&1; then
    ok "independent review complete"
else
    log "independent review exited non-zero (non-fatal)"
fi

# LLM adversarial review was removed 2026-05-08: it wrote findings to
# ops_alert(adversarial_review_finding) for run_bug_triage Rule (deleted
# in Stage 2-E supersession). Without a consumer, the script produced
# alerts no one processed and burned LLM budget for no signal.
