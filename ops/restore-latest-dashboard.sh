#!/usr/bin/env bash
set -euo pipefail
cd /opt/wishspark/dashboard/src/app

LATEST="$(ls -1t page.tsx.*backup* page.tsx.*-backup 2>/dev/null | head -n 1 || true)"
if [ -z "${LATEST}" ]; then
  echo "Nessun backup trovato."
  exit 1
fi

cp page.tsx "page.tsx.manual-backup-$(date +%Y%m%d-%H%M%S)"
cp "$LATEST" page.tsx
echo "Ripristinato da: $LATEST"
