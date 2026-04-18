#!/usr/bin/env bash
# deploy.sh — atomic build + restart + verify for the Next.js dashboard.
#
# Why this exists
# ---------------
# On 2026-04-18 the landing rendered as unstyled white because a rebuild
# was performed mid-lifetime of the `wishspark-dashboard` PM2 process.
# Next.js/Turbopack holds chunk manifests in RAM; `npx next build`
# replaced the on-disk chunks but the running process kept serving HTML
# pointing at the OLD hashes, some of which were deleted during rebuild.
# Result: landing HTML referenced a CSS chunk that returned 500.
#
# CLAUDE.md §15 documented the two-step `next build` + `pm2 restart`
# ritual, but "looks fine, dashboard returned 200" made the restart
# skippable. This script makes it non-skippable: one command, builds,
# restarts, probes every chunk the served HTML references, and exits
# non-zero on any drift.
#
# Usage
# -----
#   ./dashboard/scripts/deploy.sh            # full build+restart+verify
#   ./dashboard/scripts/deploy.sh --no-build # restart+verify only (after external CI build)
#
# Pairs with `backend/scripts/audit_dashboard_live.py` which enforces
# the same invariant at preflight time (blocks commits when a build has
# drifted from the running process).

set -euo pipefail

DASHBOARD_DIR="/opt/wishspark/dashboard"
BACKEND_DIR="/opt/wishspark/backend"
HOST="http://127.0.0.1:3000"

GREEN='\033[0;32m'; RED='\033[0;31m'; YEL='\033[0;33m'; NC='\033[0m'
step()  { printf "\n%b==> %s%b\n" "$YEL" "$1" "$NC"; }
okay()  { printf "%b✓ %s%b\n"    "$GREEN" "$1" "$NC"; }
fail()  { printf "%b✗ %s%b\n"    "$RED"   "$1" "$NC"; }

DO_BUILD=1
for arg in "$@"; do
    case "$arg" in
        --no-build) DO_BUILD=0 ;;
        -h|--help)
            grep -E "^#( |$)" "$0" | sed 's/^# \?//'
            exit 0
            ;;
    esac
done

cd "$DASHBOARD_DIR"

if [ "$DO_BUILD" -eq 1 ]; then
    step "Building dashboard (npx next build)"
    # Capture output so the log is searchable post-failure but the tail
    # still streams for visibility.
    npx next build | tee /tmp/deploy_dashboard_build.log
    okay "build done — BUILD_ID=$(cat .next/BUILD_ID 2>/dev/null || echo '?')"
else
    step "Skipping build (--no-build)"
fi

step "Restarting PM2 process (wishspark-dashboard)"
pm2 restart wishspark-dashboard --update-env | tail -1
okay "restart submitted"

step "Waiting for dashboard to accept connections"
deadline=$(( $(date +%s) + 30 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -sf -o /dev/null --max-time 2 "$HOST/"; then
        okay "dashboard responding at $HOST"
        break
    fi
    sleep 1
done

step "Verifying served HTML references built chunks"
# Run the preflight audit in --strict mode — same invariant, same code.
if [ -x "$BACKEND_DIR/venv/bin/python" ]; then
    if "$BACKEND_DIR/venv/bin/python" "$BACKEND_DIR/scripts/audit_dashboard_live.py" --strict; then
        okay "all chunks resolve 200 — deploy healthy"
    else
        fail "asset audit failed post-deploy — chunks drifted"
        echo
        echo "This means the fresh build's HTML is still pointing at"
        echo "nonexistent chunks. Investigate: did the build succeed?"
        echo "Is there a stale worker serving old responses? Is the"
        echo "CDN (Traefik) caching aggressively?"
        exit 1
    fi
else
    fail "backend venv not found at $BACKEND_DIR/venv — cannot run asset audit"
    echo "Install the venv or run the audit manually with a system python."
    exit 1
fi

printf "\n%b✅ deploy.sh: build+restart+verify complete%b\n\n" "$GREEN" "$NC"
