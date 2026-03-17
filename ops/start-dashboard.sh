#!/usr/bin/env bash
set -euo pipefail
cd /opt/wishspark/dashboard
fuser -k 3000/tcp || true
nohup npm run dev -- --hostname 0.0.0.0 --port 3000 >/opt/wishspark/logs/dashboard.log 2>&1 &
echo "Dashboard avviata su :3000"
