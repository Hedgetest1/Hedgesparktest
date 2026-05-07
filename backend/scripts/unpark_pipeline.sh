#!/usr/bin/env bash
# unpark_pipeline.sh — atomic un-park ceremony.
#
# Born 2026-05-07 closing Gap 5 from the §22.4 Agent audit of e65c615.
# When the founder un-parks the pipeline (1st paying merchant lands),
# the 88+ stuck candidates accumulated during dormancy would trip the
# circuit breaker on cycle 1 IF the enrichers are flipped without
# pre-cleanup. This script does cleanup-then-flip atomically so the
# breaker can't see inherited stuck-state.
#
# Usage:
#   ./scripts/unpark_pipeline.sh [--dry-run]
#
# What it does:
#   1. Discard `bugfix_candidates` rows in {open, analyzed,
#      patch_proposed} status older than 7d (status='discarded',
#      decision_reason='parked_pre_unpark_cleanup').
#   2. Auto-resolve stale `circuit_breaker_tripped` ops_alerts.
#   3. Print the env vars the founder must add to .env to flip
#      enrichers ON (the actual env-write is deliberately manual —
#      this script doesn't touch .env).
#
# After running, the founder edits .env to set:
#   ADVERSARIAL_REVIEWER_ENABLED=1
#   SIBLING_HUNT_ENABLED=1
#   ITERATIVE_FIX_ENABLED=1
# Then `pm2 restart wishspark-agent-worker --update-env`.
#
# `pipeline_state.is_pipeline_dormant()` returns False once any
# enricher is on, and the breaker resumes its full health-check.

set -euo pipefail

cd /opt/wishspark/backend

DRY_RUN=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN="-- DRY-RUN; no rows will be modified"
fi

echo "unpark_pipeline.sh — atomic un-park ceremony$DRY_RUN"
echo ""

if [[ -n "$DRY_RUN" ]]; then
    ./venv/bin/python -c "
from app.core.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
n_cand = db.execute(text(\"\"\"
    SELECT COUNT(*) FROM bugfix_candidates
    WHERE status IN ('open', 'analyzed', 'patch_proposed')
      AND created_at < NOW() - INTERVAL '7 days'
\"\"\")).scalar() or 0
n_alerts = db.execute(text(\"\"\"
    SELECT COUNT(*) FROM ops_alerts
    WHERE alert_type = 'circuit_breaker_tripped'
      AND resolved = false
\"\"\")).scalar() or 0
print(f'  would discard: {n_cand} stuck candidates >= 7d old')
print(f'  would resolve: {n_alerts} unresolved circuit_breaker_tripped alert(s)')
db.close()
"
else
    ./venv/bin/python -c "
from app.core.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
n_cand = db.execute(text(\"\"\"
    UPDATE bugfix_candidates SET
        status = 'discarded',
        decision_reason = 'parked_pre_unpark_cleanup'
    WHERE status IN ('open', 'analyzed', 'patch_proposed')
      AND created_at < NOW() - INTERVAL '7 days'
\"\"\")).rowcount
n_alerts = db.execute(text(\"\"\"
    UPDATE ops_alerts SET resolved = true, resolved_at = NOW()
    WHERE alert_type = 'circuit_breaker_tripped' AND resolved = false
\"\"\")).rowcount
db.commit()
print(f'  discarded: {n_cand} stuck candidates >= 7d old')
print(f'  resolved:  {n_alerts} unresolved circuit_breaker_tripped alert(s)')
db.close()
"
fi

echo ""
echo "next steps (manual):"
echo "  1. edit /opt/wishspark/.env, add (or set):"
echo "     ADVERSARIAL_REVIEWER_ENABLED=1"
echo "     SIBLING_HUNT_ENABLED=1"
echo "     ITERATIVE_FIX_ENABLED=1"
echo "  2. pm2 restart wishspark-agent-worker --update-env"
echo "  3. verify: ./venv/bin/python -c 'from app.services.pipeline_state import is_pipeline_dormant; print(is_pipeline_dormant())'  # should be False"
echo "  4. tail logs to confirm the breaker doesn't trip on cycle 1"
echo ""
echo "done."
