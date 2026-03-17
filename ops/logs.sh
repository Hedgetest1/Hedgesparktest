#!/usr/bin/env bash
set -euo pipefail
echo "=== BACKEND LOG ==="
tail -n 80 /opt/wishspark/logs/backend.log || true
echo
echo "=== DASHBOARD LOG ==="
tail -n 80 /opt/wishspark/logs/dashboard.log || true
