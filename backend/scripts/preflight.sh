#!/usr/bin/env bash
# preflight.sh — run before every commit to catch latent bugs static
# analysis can find that unit tests cannot.
#
# Runs in <10 seconds. Exits non-zero on any finding so git's pre-commit
# hook refuses the commit until the operator fixes it.
#
# Installed via backend/scripts/install_hooks.sh which symlinks this
# file (plus a small wrapper) into .git/hooks/pre-commit.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"
PY="$BACKEND/venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "preflight: $PY not executable — is the venv set up?" >&2
    exit 2
fi

# Colors (TTY-aware)
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YEL='\033[0;33m'; NC='\033[0m'
else
    GREEN=''; RED=''; YEL=''; NC=''
fi

fail=0
step() { printf "\n%bpreflight › %s%b\n" "$YEL" "$1" "$NC"; }
ok()   { printf "%b  ✓ %s%b\n"            "$GREEN" "$1" "$NC"; }
bad()  { printf "%b  ✗ %s%b\n"            "$RED"   "$1" "$NC"; fail=1; }

cd "$BACKEND"

# ---------------------------------------------------------------------------
# 1. SQL schema audit — catches ghost tables
# ---------------------------------------------------------------------------
step "SQL schema audit (audit_sql_schema.py)"
if "$PY" scripts/audit_sql_schema.py > /tmp/preflight_schema.log 2>&1; then
    ok "no ghost tables"
else
    bad "ghost tables detected — see /tmp/preflight_schema.log"
    tail -30 /tmp/preflight_schema.log
fi

# ---------------------------------------------------------------------------
# 2. SQL column audit — catches ghost columns
# ---------------------------------------------------------------------------
step "SQL column audit (audit_sql_columns.py)"
if "$PY" scripts/audit_sql_columns.py > /tmp/preflight_columns.log 2>&1; then
    ok "no ghost columns"
else
    bad "ghost columns detected — see /tmp/preflight_columns.log"
    tail -30 /tmp/preflight_columns.log
fi

# ---------------------------------------------------------------------------
# 2b. Tenant isolation audit — catches unfiltered multi-tenant queries
# ---------------------------------------------------------------------------
step "Tenant isolation audit (audit_tenant_isolation.py)"
if "$PY" scripts/audit_tenant_isolation.py > /tmp/preflight_tenant.log 2>&1; then
    ok "no cross-tenant leaks"
else
    bad "tenant isolation risk — see /tmp/preflight_tenant.log"
    tail -40 /tmp/preflight_tenant.log
fi

# ---------------------------------------------------------------------------
# 2c. Model drift audit — catches SQLAlchemy model ↔ DB schema drift
# ---------------------------------------------------------------------------
step "Model drift audit (audit_model_drift.py)"
if "$PY" scripts/audit_model_drift.py > /tmp/preflight_model_drift.log 2>&1; then
    ok "all models in sync with DB"
else
    bad "model drift detected — see /tmp/preflight_model_drift.log"
    tail -30 /tmp/preflight_model_drift.log
fi

# ---------------------------------------------------------------------------
# 2d. Alembic drift gate — the hard gate. Any drift between Base.metadata
# and the live DB schema blocks the commit. This is the top-1-world bar:
# the type system must be load-bearing, not decorative.
# ---------------------------------------------------------------------------
step "Alembic drift check (alembic check)"
if "$BACKEND/venv/bin/alembic" check > /tmp/preflight_alembic.log 2>&1; then
    ok "no model/DB drift"
else
    bad "alembic drift detected — see /tmp/preflight_alembic.log"
    grep -E "Detected (added|removed|type|NULL|changed|comment)" /tmp/preflight_alembic.log | head -30 || true
fi

# ---------------------------------------------------------------------------
# 2e. Silent-fallback observability gate (Tier 2.1). Every `if rc is None`
# fast-path return in app/ must call record_silent_return() so prod Redis
# outages surface in /ops/silent-fallback instead of silently degrading
# subsystems. Baseline 0 bare reached on 2026-04-14 — keep it at 0.
# ---------------------------------------------------------------------------
step "Silent-fallback coverage (audit_silent_returns.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_silent_returns.py" --strict > /tmp/preflight_silent.log 2>&1; then
    ok "all silent fallbacks observed"
else
    bad "bare silent fallbacks detected — see /tmp/preflight_silent.log"
    tail -15 /tmp/preflight_silent.log || true
fi

# ---------------------------------------------------------------------------
# 2f. Exception-debug audit (Tier 2.2). Every debug-only swallow handler
# whose try-block touches a DB session or external client must escalate
# to log.warning (or write_alert) so operators see failures in prod.
# Baseline 0 prod-relevant reached on 2026-04-14.
# ---------------------------------------------------------------------------
step "Exception-debug audit (audit_exception_debug.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_exception_debug.py" --strict > /tmp/preflight_exc_debug.log 2>&1; then
    ok "no prod-relevant exception swallows at debug level"
else
    bad "prod-relevant debug-only swallows detected — see /tmp/preflight_exc_debug.log"
    tail -30 /tmp/preflight_exc_debug.log || true
fi

# ---------------------------------------------------------------------------
# 2g. Input-bounds audit (Tier 2.3). Every Pydantic request model field
# of type str / list / dict must declare an upper bound (max_length,
# max_items, or pattern=). OWASP A03/A04 — no unbounded user input
# reaches the DB, the logs, or the LLM prompt.
# ---------------------------------------------------------------------------
step "Input-bounds audit (audit_input_bounds.py --strict)"
if "$BACKEND/venv/bin/python" "$BACKEND/scripts/audit_input_bounds.py" --strict > /tmp/preflight_input_bounds.log 2>&1; then
    ok "all request fields have upper bounds"
else
    bad "unbounded request fields detected — see /tmp/preflight_input_bounds.log"
    tail -30 /tmp/preflight_input_bounds.log || true
fi

# ---------------------------------------------------------------------------
# 3. Python AST parse check — any syntax error blocks commit
# ---------------------------------------------------------------------------
step "Python AST parse (staged .py files)"
cd "$REPO_ROOT"
STAGED_PY="$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.py$' || true)"
if [ -z "$STAGED_PY" ]; then
    ok "no Python files staged"
else
    if REPO_ROOT="$REPO_ROOT" STAGED_PY="$STAGED_PY" "$PY" -c "
import ast, os, sys
root = os.environ['REPO_ROOT']
files = os.environ['STAGED_PY'].strip().split()
for f in files:
    try:
        ast.parse(open(os.path.join(root, f)).read())
    except SyntaxError as e:
        print(f'SYNTAX ERROR: {f}:{e.lineno} {e.msg}')
        sys.exit(1)
print(f'parsed {len(files)} files')
"; then
        ok "all staged Python files parse"
    else
        bad "syntax error in staged Python files"
    fi
fi
cd "$BACKEND"

# ---------------------------------------------------------------------------
# 4. Result
# ---------------------------------------------------------------------------
if [ "$fail" -eq 0 ]; then
    printf "\n%bpreflight: OK — commit allowed%b\n\n" "$GREEN" "$NC"
    exit 0
else
    printf "\n%bpreflight: BLOCKED — commit refused%b\n" "$RED" "$NC"
    printf "%brun \`git commit --no-verify\` to force (not recommended)%b\n\n" "$YEL" "$NC"
    exit 1
fi
