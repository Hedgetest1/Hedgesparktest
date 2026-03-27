#!/usr/bin/env bash
#
# deploy.sh — Safe manual deploy sequence for WishSpark VPS.
#
# Usage:
#   ssh root@<vps-ip> 'cd /opt/wishspark && bash scripts/deploy.sh'
#
# This script is intentionally NOT triggered automatically by CI.
# Full CD automation requires a staging environment and rollback mechanism
# that do not exist yet.  Until then, deploy is human-gated.
#
# Sequence:
#   1. Pull latest code
#   2. Install backend dependencies (pinned)
#   3. Run database migrations (additive only)
#   4. Build dashboard
#   5. Restart all PM2 services
#   6. Health check
#   7. Report result
#
# If any step fails, the script exits immediately (set -e).
# PM2 auto-restart protects against crashes during the restart window.
#
set -euo pipefail

REPO_DIR="/opt/wishspark"
BACKEND_DIR="$REPO_DIR/backend"
DASHBOARD_DIR="$REPO_DIR/dashboard"
HEALTH_URL="http://127.0.0.1:8000/system/health"

echo "=== WishSpark Deploy ==="
echo "  Time: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# 1. Pull
echo "[1/6] Pulling latest code..."
cd "$REPO_DIR"
git pull --ff-only origin main
echo ""

# 2. Backend deps
echo "[2/6] Installing backend dependencies..."
cd "$BACKEND_DIR"
venv/bin/pip install -r requirements.txt --quiet
echo ""

# 3. Migrations
echo "[3/6] Running database migrations..."
cd "$BACKEND_DIR"
venv/bin/python -m alembic upgrade head
echo ""

# 4. Dashboard build
echo "[4/6] Building dashboard..."
cd "$DASHBOARD_DIR"
node_modules/.bin/next build
echo ""

# 5. Restart
echo "[5/6] Restarting PM2 services..."
pm2 restart ecosystem.config.js --update-env
echo ""

# 6. Health check (wait for backend to start, then check)
echo "[6/6] Health check..."
sleep 5
HTTP_CODE=$(curl -s -o /tmp/health_response.json -w "%{http_code}" "$HEALTH_URL" || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    echo "  Backend health: OK (HTTP 200)"
    cat /tmp/health_response.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"  DB:     {d.get('database',{}).get('status','?')}\")
print(f\"  Redis:  {d.get('redis',{}).get('status','?')}\")
print(f\"  Status: {d.get('status','?')}\")
" 2>/dev/null || true
else
    echo "  WARNING: Backend health check returned HTTP $HTTP_CODE"
    echo "  Check logs: pm2 logs wishspark-backend --lines 20"
    exit 1
fi

echo ""
echo "=== Deploy complete ==="
