#!/usr/bin/env bash
# ===========================================================================
# HedgeSpark Deploy Script — safe deploy with smoke verification
#
# Usage:
#   ./deploy.sh              Full deploy: test → build → restart → smoke
#   ./deploy.sh --smoke-only Run smoke checks without deploying
#   ./deploy.sh --rollback   Rollback to the previous git commit
#
# Exit codes:
#   0  Deploy succeeded, all smoke checks passed
#   1  Pre-deploy checks failed (tests, build)
#   2  Post-deploy smoke checks failed
#   3  Rollback executed
#
# This script is designed for:
#   - Human operators
#   - Autonomous AI deploy agents
#   - CI/CD pipelines
# ===========================================================================

set -euo pipefail

REPO="/opt/wishspark"
BACKEND="$REPO/backend"
DASHBOARD="$REPO/dashboard"
VENV="$BACKEND/venv/bin/python"
API="http://127.0.0.1:8000"
APP="http://127.0.0.1:3000"
DASHBOARD_KEY=$(grep DASHBOARD_API_KEY "$BACKEND/.env" 2>/dev/null | cut -d= -f2 || echo "")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; FAILURES=$((FAILURES + 1)); }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
FAILURES=0

# ===========================================================================
# SMOKE CHECK FUNCTION — reusable for post-deploy and standalone
# ===========================================================================

run_smoke_checks() {
    echo ""
    echo "═══ SMOKE CHECKS ═══"
    FAILURES=0

    # 1. Backend process alive
    if pm2 pid wishspark-backend >/dev/null 2>&1; then
        pass "Backend process: online"
    else
        fail "Backend process: NOT running"
    fi

    # 2. Dashboard process alive
    if pm2 pid wishspark-dashboard >/dev/null 2>&1; then
        pass "Dashboard process: online"
    else
        fail "Dashboard process: NOT running"
    fi

    # 3. /system/health responds
    HEALTH=$(curl -s --max-time 10 "$API/system/health" 2>/dev/null)
    if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status') in ('ok','degraded') else 1)" 2>/dev/null; then
        STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
        pass "/system/health: $STATUS"
    else
        fail "/system/health: NOT responding or critical"
    fi

    # 4. Database subsystem
    if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('subsystems',{}).get('database',{}).get('status')=='ok' else 1)" 2>/dev/null; then
        pass "Database: ok"
    else
        fail "Database: NOT ok"
    fi

    # 5. Redis subsystem
    if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('subsystems',{}).get('redis',{}).get('status')=='ok' else 1)" 2>/dev/null; then
        pass "Redis: ok"
    else
        warn "Redis: not ok (degraded mode acceptable)"
    fi

    # 6. Tracker endpoint
    TRACKER_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$API/tracker.js?v=8" 2>/dev/null)
    if [ "$TRACKER_STATUS" = "200" ]; then
        pass "Tracker endpoint: 200"
    else
        fail "Tracker endpoint: HTTP $TRACKER_STATUS"
    fi

    # 7. Session bootstrap route
    SESSION_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$API/auth/session?shop=hedgespark-dev.myshopify.com" 2>/dev/null)
    if [ "$SESSION_STATUS" = "302" ]; then
        pass "Session bootstrap: 302 redirect"
    else
        fail "Session bootstrap: HTTP $SESSION_STATUS (expected 302)"
    fi

    # 8. Ops diagnostic
    if [ -n "$DASHBOARD_KEY" ]; then
        DIAG_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$API/ops/diagnostic" -H "X-API-Key: $DASHBOARD_KEY" 2>/dev/null)
        if [ "$DIAG_STATUS" = "200" ]; then
            pass "Ops diagnostic: 200"
        else
            fail "Ops diagnostic: HTTP $DIAG_STATUS"
        fi
    else
        warn "Ops diagnostic: skipped (no DASHBOARD_API_KEY)"
    fi

    # 9. Dashboard reachable
    DASH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$APP" 2>/dev/null)
    if [ "$DASH_STATUS" = "200" ]; then
        pass "Dashboard: 200"
    else
        fail "Dashboard: HTTP $DASH_STATUS"
    fi

    # 10. Security headers
    HEADERS=$(curl -s -I --max-time 5 "$API/system/health" 2>/dev/null)
    if echo "$HEADERS" | grep -qi "x-content-type-options: nosniff"; then
        pass "Security headers: present"
    else
        fail "Security headers: missing"
    fi

    echo ""
    if [ $FAILURES -eq 0 ]; then
        echo -e "${GREEN}═══ ALL SMOKE CHECKS PASSED ═══${NC}"
        return 0
    else
        echo -e "${RED}═══ $FAILURES SMOKE CHECK(S) FAILED ═══${NC}"
        return 1
    fi
}

# ===========================================================================
# ROLLBACK
# ===========================================================================

do_rollback() {
    echo "═══ ROLLBACK ═══"
    cd "$REPO"
    CURRENT=$(git rev-parse HEAD)
    PREVIOUS=$(git rev-parse HEAD~1)
    echo "Current:  $CURRENT"
    echo "Rolling back to: $PREVIOUS"

    git checkout "$PREVIOUS" -- .
    cd "$DASHBOARD" && npx next build
    pm2 restart ecosystem.config.js

    echo "Waiting 10s for processes to stabilize..."
    sleep 10

    if run_smoke_checks; then
        echo -e "${GREEN}Rollback successful.${NC}"
        echo "Run 'git checkout main -- .' to restore latest code when ready."
    else
        echo -e "${RED}Rollback smoke checks ALSO failed. Manual intervention needed.${NC}"
    fi
    exit 3
}

# ===========================================================================
# MAIN
# ===========================================================================

case "${1:-}" in
    --smoke-only)
        run_smoke_checks
        exit $?
        ;;
    --rollback)
        do_rollback
        ;;
esac

echo "═══ HEDGESPARK DEPLOY ═══"
echo "Commit: $(cd $REPO && git log --oneline -1)"
echo ""

# --- PRE-DEPLOY: Tests ---
echo "═══ PRE-DEPLOY: Backend Tests ═══"
cd "$BACKEND"
if $VENV -m pytest tests/ --ignore=tests/test_scaling_intelligence.py -q --tb=line 2>&1 | tail -3 | grep -q "passed"; then
    PASSED=$($VENV -m pytest tests/ --ignore=tests/test_scaling_intelligence.py -q --tb=no 2>&1 | tail -1 | grep -o '[0-9]* passed')
    pass "Tests: $PASSED"
else
    fail "Tests: FAILED"
    echo -e "${RED}DEPLOY ABORTED — tests failed.${NC}"
    exit 1
fi

# --- PRE-DEPLOY: Dashboard build ---
echo ""
echo "═══ PRE-DEPLOY: Dashboard Build ═══"
cd "$DASHBOARD"
if npx next build 2>&1 | tail -1 | grep -q "Static"; then
    pass "Dashboard build: OK"
else
    fail "Dashboard build: FAILED"
    echo -e "${RED}DEPLOY ABORTED — dashboard build failed.${NC}"
    exit 1
fi

# --- DEPLOY: Record pre-deploy state ---
cd "$REPO"
PRE_COMMIT=$(git rev-parse HEAD)
echo ""
echo "═══ DEPLOY: Restarting PM2 ═══"
echo "Pre-deploy commit: $PRE_COMMIT"

pm2 restart ecosystem.config.js 2>&1 | tail -1

echo "Waiting 12s for all processes to start..."
sleep 12

# --- POST-DEPLOY: Smoke checks ---
if run_smoke_checks; then
    echo ""
    echo -e "${GREEN}═══ DEPLOY SUCCESSFUL ═══${NC}"
    echo "Commit: $(git log --oneline -1)"
    echo ""
    echo "If issues appear later:"
    echo "  ./deploy.sh --rollback"
    exit 0
else
    echo ""
    echo -e "${RED}═══ POST-DEPLOY SMOKE FAILED ═══${NC}"
    echo ""
    echo "Options:"
    echo "  1. Fix the issue and re-deploy"
    echo "  2. Rollback: ./deploy.sh --rollback"
    echo "  3. Manual: git checkout HEAD~1 -- . && npx next build && pm2 restart ecosystem.config.js"
    exit 2
fi
