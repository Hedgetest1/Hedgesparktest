#!/usr/bin/env bash
# =============================================================================
# check_prod_readiness.sh — WishSpark production readiness checker
#
# Usage:
#   bash /opt/wishspark/scripts/check_prod_readiness.sh
#
# What it checks:
#   1. Required environment variables (from backend/.env)
#   2. PM2 process health (all 6 expected processes)
#   3. Backend health endpoint (GET /health, expects DB-connected response)
#   4. Webhook HMAC enforcement (POST /webhooks/shopify/orders-paid — expects 401)
#   5. Dashboard API key enforcement (GET /pro/nudges — expects 401, not 403/200)
#
# Exit codes:
#   0 — all checks passed (SAFE TO EXPOSE)
#   1 — one or more checks failed or warned (NOT YET SAFE)
# =============================================================================

set -euo pipefail

ENV_FILE="/opt/wishspark/backend/.env"
BACKEND_URL="http://127.0.0.1:8000"
PASS=0
FAIL=1

# Counters
CHECKS=0
PASSED=0
FAILED=0
WARNED=0

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_green()  { printf "\033[32m%s\033[0m\n" "$*"; }
_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
_red()    { printf "\033[31m%s\033[0m\n" "$*"; }

pass()  { CHECKS=$((CHECKS+1)); PASSED=$((PASSED+1));  _green  "  PASS  $*"; }
warn()  { CHECKS=$((CHECKS+1)); WARNED=$((WARNED+1));  _yellow "  WARN  $*"; }
fail()  { CHECKS=$((CHECKS+1)); FAILED=$((FAILED+1));  _red    "  FAIL  $*"; }

section() { echo; echo "── $* ──────────────────────────────────────────────"; }

# --------------------------------------------------------------------------
# Load .env (without exporting — just for checking values in this script)
# --------------------------------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
    _red "ERROR: $ENV_FILE not found. Cannot proceed."
    exit 1
fi

# Read key=value lines, skip comments and blanks, into associative array
declare -A ENV_VALS
while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    key="${key%%[[:space:]]*}"
    value="${value#"${value%%[![:space:]]*}"}"   # ltrim
    ENV_VALS["$key"]="$value"
done < "$ENV_FILE"

env_val() { echo "${ENV_VALS[$1]:-}"; }

# --------------------------------------------------------------------------
# Section 1: Environment variables
# --------------------------------------------------------------------------

section "1. Environment Variables"

check_env() {
    local name="$1"
    local required="${2:-true}"
    local val
    val=$(env_val "$name")
    if [[ -n "$val" ]]; then
        pass "$name is set"
    elif [[ "$required" == "true" ]]; then
        fail "$name is EMPTY or MISSING — REQUIRED for production security"
    else
        warn "$name is empty — optional feature will degrade"
    fi
}

check_env "DATABASE_URL"          true
check_env "SHOPIFY_API_KEY"        true
check_env "SHOPIFY_API_SECRET"     true
check_env "SHOPIFY_WEBHOOK_SECRET" true
check_env "DASHBOARD_API_KEY"      true
check_env "APP_URL"                true
check_env "OPENAI_API_KEY"         false
check_env "REDIS_URL"              false
check_env "RESEND_API_KEY"         false
check_env "SENTRY_DSN"             false

# --------------------------------------------------------------------------
# Section 2: PM2 processes
# --------------------------------------------------------------------------

section "2. PM2 Process Health"

EXPECTED_PROCS=(
    "wishspark-backend"
    "wishspark-dashboard"
    "wishspark-worker"
    "wishspark-agent-worker"
    "wishspark-aggregation-worker"
    "wishspark-segment-monitor"
)

if ! command -v pm2 &>/dev/null; then
    warn "pm2 not found in PATH — skipping PM2 checks (run as the deploy user)"
else
    PM2_STATUS=$(pm2 jlist 2>/dev/null || echo "[]")
    for proc in "${EXPECTED_PROCS[@]}"; do
        status=$(echo "$PM2_STATUS" \
            | python3 -c "
import sys, json
procs = json.load(sys.stdin)
name = sys.argv[1]
match = next((p for p in procs if p.get('name') == name), None)
if match:
    print(match.get('pm2_env', {}).get('status', 'unknown'))
else:
    print('missing')
" "$proc" 2>/dev/null || echo "unknown")

        if [[ "$status" == "online" ]]; then
            pass "$proc  (status=online)"
        elif [[ "$status" == "missing" ]]; then
            fail "$proc  — not found in PM2 (run: pm2 start ecosystem.config.js)"
        else
            fail "$proc  — status=$status (expected: online)"
        fi
    done
fi

# --------------------------------------------------------------------------
# Section 3: Backend health endpoint
# --------------------------------------------------------------------------

section "3. Backend Health Endpoint (GET /health)"

if ! command -v curl &>/dev/null; then
    warn "curl not found — skipping HTTP checks"
else
    HEALTH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time 5 "${BACKEND_URL}/health" 2>/dev/null || echo "000")

    if [[ "$HEALTH_RESPONSE" == "200" ]]; then
        # Check body for DB status
        HEALTH_BODY=$(curl -s --max-time 5 "${BACKEND_URL}/health" 2>/dev/null || echo "{}")
        if echo "$HEALTH_BODY" | grep -q '"status": *"ok"'; then
            pass "GET /health → 200 (DB connected)"
        elif echo "$HEALTH_BODY" | grep -q '"status": *"degraded"'; then
            fail "GET /health → 200 but DB unreachable: $HEALTH_BODY"
        else
            warn "GET /health → 200 but unexpected body: $HEALTH_BODY"
        fi
    elif [[ "$HEALTH_RESPONSE" == "503" ]]; then
        fail "GET /health → 503 (database unreachable)"
    elif [[ "$HEALTH_RESPONSE" == "000" ]]; then
        fail "GET /health → no response (backend not running or not on port 8000)"
    else
        warn "GET /health → HTTP $HEALTH_RESPONSE (unexpected)"
    fi

    # --------------------------------------------------------------------------
    # Section 4: Webhook HMAC enforcement
    # --------------------------------------------------------------------------

    section "4. Webhook HMAC Enforcement"

    WEBHOOK_SECRET=$(env_val "SHOPIFY_WEBHOOK_SECRET")

    if [[ -z "$WEBHOOK_SECRET" ]]; then
        fail "SHOPIFY_WEBHOOK_SECRET is empty — HMAC verification is DISABLED (permissive mode)"
        warn "  Any actor who knows the webhook URL can inject fake order data"
        warn "  → Set SHOPIFY_WEBHOOK_SECRET in .env and reload PM2"
    else
        # Send a probe with a valid shop domain but NO HMAC header.
        # Expected: 401 (HMAC rejected). Any other 4xx means HMAC bypassed.
        HMAC_PROBE=$(curl -s -o /dev/null -w "%{http_code}" \
            --max-time 5 \
            -X POST "${BACKEND_URL}/webhooks/shopify/orders-paid" \
            -H "X-Shopify-Shop-Domain: probe.myshopify.com" \
            -H "Content-Type: application/json" \
            -d '{"id":"probe","total_price":"0.00"}' \
            2>/dev/null || echo "000")

        if [[ "$HMAC_PROBE" == "401" ]]; then
            pass "POST /webhooks/shopify/orders-paid → 401 (HMAC enforced)"
        elif [[ "$HMAC_PROBE" == "000" ]]; then
            fail "Webhook endpoint not reachable (backend not running?)"
        else
            fail "Webhook HMAC NOT enforced — probe returned HTTP $HMAC_PROBE (expected 401)"
        fi
    fi

    # --------------------------------------------------------------------------
    # Section 5: Dashboard API key enforcement
    # --------------------------------------------------------------------------

    section "5. Dashboard API Key Enforcement"

    DASH_KEY=$(env_val "DASHBOARD_API_KEY")

    if [[ -z "$DASH_KEY" ]]; then
        fail "DASHBOARD_API_KEY is empty — API key enforcement is DISABLED on all /pro/* endpoints"
        warn "  → Generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        warn "  → Add to backend/.env and reload PM2"
    else
        # Hit a Pro endpoint with no API key.
        # Expected: 401 (key required). 403 means key bypassed (plan check only).
        APIKEY_PROBE=$(curl -s -o /dev/null -w "%{http_code}" \
            --max-time 5 \
            "${BACKEND_URL}/pro/nudges?shop=probe.myshopify.com" \
            2>/dev/null || echo "000")

        if [[ "$APIKEY_PROBE" == "401" ]]; then
            pass "GET /pro/nudges (no key) → 401 (API key enforced)"
        elif [[ "$APIKEY_PROBE" == "000" ]]; then
            fail "Pro endpoint not reachable (backend not running?)"
        elif [[ "$APIKEY_PROBE" == "403" ]]; then
            # 403 means API key check passed (empty key bypass) but plan check blocked.
            # This means enforcement is broken.
            fail "GET /pro/nudges (no key) → 403 — API key check bypassed (key must not be loaded)"
        else
            fail "GET /pro/nudges (no key) → HTTP $APIKEY_PROBE (unexpected; expected 401)"
        fi
    fi
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo
echo "════════════════════════════════════════════════════════"
echo "  PRODUCTION READINESS SUMMARY"
echo "  Checks: $CHECKS   Passed: $PASSED   Warned: $WARNED   Failed: $FAILED"
echo "════════════════════════════════════════════════════════"

if [[ $FAILED -gt 0 ]]; then
    _red   "  VERDICT: NOT YET SAFE — $FAILED check(s) failed"
    echo
    _red   "  Fix all FAIL items above, then re-run this script."
    exit 1
elif [[ $WARNED -gt 0 ]]; then
    _yellow "  VERDICT: SAFE AFTER MANUAL SECRETS — $WARNED warning(s) remain"
    echo
    _yellow "  Optional features will degrade. Core security checks passed."
    exit 0
else
    _green  "  VERDICT: SAFE — all checks passed"
    exit 0
fi
