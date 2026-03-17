#!/usr/bin/env bash
set -euo pipefail

mkdir -p /opt/wishspark/ops

cat <<'SH' > /opt/wishspark/ops/start-backend.sh
#!/usr/bin/env bash
set -euo pipefail
cd /opt/wishspark/backend
source venv/bin/activate
pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8000" || true
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 >/opt/wishspark/logs/backend.log 2>&1 &
echo "Backend avviato su :8000"
SH

cat <<'SH' > /opt/wishspark/ops/start-dashboard.sh
#!/usr/bin/env bash
set -euo pipefail
cd /opt/wishspark/dashboard
fuser -k 3000/tcp || true
nohup npm run dev -- --hostname 0.0.0.0 --port 3000 >/opt/wishspark/logs/dashboard.log 2>&1 &
echo "Dashboard avviata su :3000"
SH

cat <<'SH' > /opt/wishspark/ops/restart-all.sh
#!/usr/bin/env bash
set -euo pipefail
bash /opt/wishspark/ops/start-backend.sh
bash /opt/wishspark/ops/start-dashboard.sh
echo "WishSpark riavviato"
SH

cat <<'SH' > /opt/wishspark/ops/status.sh
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
SH

cat <<'SH' > /opt/wishspark/ops/restore-latest-dashboard.sh
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
SH

cat <<'SH' > /opt/wishspark/ops/logs.sh
#!/usr/bin/env bash
set -euo pipefail
echo "=== BACKEND LOG ==="
tail -n 80 /opt/wishspark/logs/backend.log || true
echo
echo "=== DASHBOARD LOG ==="
tail -n 80 /opt/wishspark/logs/dashboard.log || true
SH

chmod +x /opt/wishspark/ops/*.sh
echo "OPS pronto in /opt/wishspark/ops"
echo "Script disponibili:"
ls -1 /opt/wishspark/ops
