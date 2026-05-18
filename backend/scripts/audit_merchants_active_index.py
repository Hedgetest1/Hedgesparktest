#!/usr/bin/env python
# invariant-eligible: false
#   Static source scan — migration declaration + hot-path SQL
#   contract. Commit-stage-only (like audit_partition_drop_safety).
"""audit_merchants_active_index.py — preventer for the J4-batch TIER_2
migration `zzzi_merchants_active_partial_idx` (design + verified by
independent Agent a0b4b92, 2026-05-18).

The migration adds a PARTIAL COVERING index
`ix_merchants_active_shop_domain ON merchants (shop_domain)
WHERE install_status='active'` that kills the per-cycle Seq Scan the
aggregation cold-tier + brain tick run. Three regression classes
this audit blocks:

  (A) the partial `WHERE install_status = 'active'` is dropped — that
      silently reverts to a selectivity-FRAGILE full index the
      planner ignores if 'active' is the dominant value (the column
      default) ⟹ the Seq Scan returns, the fix is a no-op.
  (B) the `op.get_context().autocommit_block()` non-txn wrapper is
      removed — `CREATE INDEX CONCURRENTLY` inside a txn FAILS the
      migration outright.
  (C) the hot-path query the index serves drifts (predicate /
      projection changed at the call sites) ⟹ the index becomes a
      silently-orphaned dead structure; fail loudly so it is
      re-evaluated.
  (D) the index is migration-only (not declared in the Merchant
      model `__table_args__`) ⟹ post-apply `alembic check`
      autogenerate reports it as `remove_index` drift and bricks
      the preflight alembic-check HARD gate (every subsequent
      commit fails). Ship-gate Agent a84d4e89 PROVED this on a
      scratch DB (CHECK_EXIT 255). Model parity is mandatory —
      mirrors the zzz3 product_metrics in-repo precedent.
  Plus: the downgrade must `DROP INDEX CONCURRENTLY IF EXISTS`
  (also non-txn) so rollback / INVALID-index reaping is safe.

Non-vacuous: it FAILs if the partial WHERE, the CONCURRENTLY, the
autocommit_block, the hot-path predicate, or the MODEL parity
declaration is removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
MIG = (_ROOT / "migrations" / "versions"
       / "zzzi_merchants_active_partial_idx.py")
AGG = _ROOT / "app" / "workers" / "aggregation_worker.py"
BRAIN = _ROOT / "app" / "services" / "merchant_brain.py"
MODEL = _ROOT / "app" / "models" / "merchant.py"
_IDX = "ix_merchants_active_shop_domain"


def main() -> int:
    bad: list[str] = []

    if not MIG.exists():
        print(f"audit_merchants_active_index: FAIL — migration missing: {MIG}")
        return 1
    m = MIG.read_text()
    up = m.split("def downgrade", 1)[0]
    down = m.split("def downgrade", 1)[1] if "def downgrade" in m else ""

    # (A) partial predicate present in upgrade
    if f"CREATE INDEX CONCURRENTLY IF NOT EXISTS" not in up \
            or _IDX not in up:
        bad.append("upgrade() missing `CREATE INDEX CONCURRENTLY IF NOT "
                   f"EXISTS {_IDX}` — online/idempotent build lost.")
    if "WHERE install_status = 'active'" not in up:
        bad.append("upgrade() lost the partial `WHERE install_status = "
                   "'active'` — reverts to a selectivity-FRAGILE full "
                   "index the planner ignores if 'active' is dominant "
                   "(the Seq Scan §12 fix becomes a no-op).")
    # (B) non-txn wrapper present (CONCURRENTLY in a txn FAILS)
    if "autocommit_block()" not in up:
        bad.append("upgrade() lost `op.get_context().autocommit_block()` "
                   "— CREATE INDEX CONCURRENTLY inside a txn fails the "
                   "migration.")
    # downgrade safety
    if (f"DROP INDEX CONCURRENTLY IF EXISTS {_IDX}" not in down
            or "autocommit_block()" not in down):
        bad.append("downgrade() must `DROP INDEX CONCURRENTLY IF EXISTS "
                   f"{_IDX}` inside autocommit_block() (rollback / "
                   "INVALID-index reaping safety).")

    # (C) hot-path contract pin — the index only earns its place while
    # these queries exist with this predicate+projection.
    if AGG.exists():
        a = AGG.read_text()
        if "install_status = 'active'" not in a:
            bad.append("aggregation_worker no longer filters "
                       "`install_status = 'active'` — the index the "
                       "migration adds may be orphaned; re-evaluate.")
    if BRAIN.exists():
        b = BRAIN.read_text()
        if 'install_status == "active"' not in b:
            bad.append("merchant_brain no longer filters "
                       "`Merchant.install_status == \"active\"` — index "
                       "may be orphaned; re-evaluate.")

    # (D) MODEL PARITY — the index MUST be declared in the Merchant
    # model `__table_args__`, not migration-only. A migration-only
    # index = post-apply `alembic check` autogenerate `remove_index`
    # drift = preflight alembic-check HARD gate bricked for every
    # subsequent commit (ship-gate Agent a84d4e89 proved CHECK_EXIT
    # 255 on a scratch DB). Mirrors the zzz3 product_metrics
    # precedent (index declared in BOTH model + migration).
    if not MODEL.exists():
        bad.append(f"Merchant model missing: {MODEL}")
    else:
        mm = MODEL.read_text()
        if (_IDX not in mm
                or "postgresql_where=text(\"install_status = 'active'\")"
                not in mm):
            bad.append(
                f"Merchant model `__table_args__` does not declare the "
                f"partial Index `{_IDX}` with "
                f"`postgresql_where=text(\"install_status = 'active'\")` "
                f"— a migration-only index makes post-apply `alembic "
                f"check` report `remove_index` drift and bricks the "
                f"preflight alembic-check HARD gate for every commit. "
                f"Model parity is mandatory (zzz3 precedent).")

    if bad:
        print("audit_merchants_active_index: FAIL — the merchants "
              "active-partial-index contract regressed:")
        for x in bad:
            print(f"  - {x}")
        return 1
    print("audit_merchants_active_index: OK — migration declares the "
          "partial covering index (CONCURRENTLY + WHERE active + "
          "autocommit_block + safe downgrade); hot-path predicate "
          "contract intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
