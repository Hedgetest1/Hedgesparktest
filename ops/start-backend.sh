#!/usr/bin/env bash
set -euo pipefail
cd /opt/wishspark/backend
source venv/bin/activate
pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8000" || true
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 >/opt/wishspark/logs/backend.log 2>&1 &
echo "Backend avviato su :8000"
