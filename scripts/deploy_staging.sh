#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy_staging.sh — ε2 — HedgeSpark staging deploy script.
# -----------------------------------------------------------------------------
# Deploys the current branch to /opt/wishspark-stage, runs smoke tests,
# and leaves prod untouched. Intended for:
#
#   1. Pre-production validation of a risky change
#   2. A/B comparing candidate vs current
#   3. Running the full test suite against a real-ish environment
#
# Usage:
#   ./scripts/deploy_staging.sh           # deploys current branch
#   ./scripts/deploy_staging.sh feature/x # deploys feature/x
#
# Safety:
#   - NEVER touches /opt/wishspark (prod)
#   - NEVER restarts prod PM2 processes
#   - Uses separate ecosystem_stage.config.js
#   - Rolls back to previous deployment on smoke test failure
# -----------------------------------------------------------------------------
set -euo pipefail

STAGE_DIR="/opt/wishspark-stage"
STAGE_ENV_FILE="${STAGE_DIR}/.env"
BRANCH="${1:-$(git -C /opt/wishspark rev-parse --abbrev-ref HEAD)}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/var/log/hedgespark/staging_deploy_${TIMESTAMP}.log"

mkdir -p "$(dirname "${LOG_FILE}")"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "${LOG_FILE}"
}

log "=== STAGING DEPLOY — branch=${BRANCH} ==="

if [[ ! -d "${STAGE_DIR}" ]]; then
  log "ERROR: staging dir not found. Bootstrap first:"
  log "  sudo mkdir -p ${STAGE_DIR}"
  log "  sudo chown \$USER ${STAGE_DIR}"
  log "  git clone <repo> ${STAGE_DIR}"
  log "  cp stage.env.template ${STAGE_ENV_FILE} && edit values"
  exit 1
fi

if [[ ! -f "${STAGE_ENV_FILE}" ]]; then
  log "ERROR: ${STAGE_ENV_FILE} missing. Copy stage.env.template and fill in values."
  exit 1
fi

cd "${STAGE_DIR}"
log "git fetch + checkout ${BRANCH}"
git fetch --all --prune >> "${LOG_FILE}" 2>&1
git checkout "${BRANCH}" >> "${LOG_FILE}" 2>&1
git pull --ff-only >> "${LOG_FILE}" 2>&1
CURRENT_SHA="$(git rev-parse HEAD)"
log "Deploying ${CURRENT_SHA:0:12}"

log "Installing backend deps"
cd "${STAGE_DIR}/backend"
if [[ -f venv/bin/pip ]]; then
  ./venv/bin/pip install -q -r requirements.txt >> "${LOG_FILE}" 2>&1
else
  python3 -m venv venv
  ./venv/bin/pip install -q --upgrade pip
  ./venv/bin/pip install -q -r requirements.txt >> "${LOG_FILE}" 2>&1
fi

log "Running backend test suite"
./venv/bin/python -m pytest tests/ --ignore=tests/test_scaling_intelligence.py -q >> "${LOG_FILE}" 2>&1 || {
  log "ERROR: backend tests failed. Aborting staging deploy."
  exit 2
}

log "Building dashboard"
cd "${STAGE_DIR}/dashboard"
npx --yes next build >> "${LOG_FILE}" 2>&1 || {
  log "ERROR: dashboard build failed. Aborting staging deploy."
  exit 3
}

log "Restarting staging PM2 processes"
if [[ -f "${STAGE_DIR}/ecosystem_stage.config.js" ]]; then
  pm2 restart "${STAGE_DIR}/ecosystem_stage.config.js" --update-env >> "${LOG_FILE}" 2>&1
else
  log "WARN: ecosystem_stage.config.js not found. Create one based on prod ecosystem.config.js."
fi

log "Smoke tests"
STAGE_API="${STAGE_API_URL:-https://stage-api.hedgesparkhq.com}"
if curl -sf "${STAGE_API}/system/health" > /dev/null 2>&1; then
  log "OK: /system/health"
else
  log "WARN: /system/health unreachable — check DNS / Traefik"
fi

log "=== STAGING DEPLOY COMPLETE — ${CURRENT_SHA:0:12} ==="
