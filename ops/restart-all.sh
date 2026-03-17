#!/usr/bin/env bash
set -euo pipefail
bash /opt/wishspark/ops/start-backend.sh
bash /opt/wishspark/ops/start-dashboard.sh
echo "WishSpark riavviato"
