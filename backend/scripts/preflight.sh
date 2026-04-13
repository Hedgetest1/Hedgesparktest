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
