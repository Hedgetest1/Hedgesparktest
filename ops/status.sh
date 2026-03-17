#!/usr/bin/env bash
set -euo pipefail
echo "=== PORTA 8000 ==="
ss -ltnp | grep :8000 || true
echo
echo "=== PORTA 3000 ==="
ss -ltnp | grep :3000 || true
echo
echo "=== HEALTH BACKEND ==="
curl -s http://127.0.0.1:8000/health || true
echo
echo
echo "=== DASHBOARD OVERVIEW ==="
curl -s http://127.0.0.1:8000/dashboard/overview | head -c 800 || true
echo
