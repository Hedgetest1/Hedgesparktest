#!/usr/bin/env python
"""sentry_sync_alert_rules.py — declarative sync of Sentry issue alert rules.

Reads `backend/config/sentry_alert_rules.yaml` (the source of truth)
and reconciles it with what's currently configured in the Sentry
project via the REST API.

Default mode: dry-run (no API writes). Pass `--apply` to write.
Default behavior: rules in Sentry but NOT in YAML are LEFT ALONE.
Pass `--prune` to also delete unmanaged rules (CAREFUL — this
removes anything someone configured in the UI for one-off purposes).

Auth: SENTRY_AUTH_TOKEN (project:write scope) + SENTRY_ORG +
SENTRY_PROJECT in env. Without them the script prints "skipped" and
exits 0 — safe to run from CI/preflight even when unconfigured.

After a successful --apply, the script writes the YAML's SHA-256 hash
into `backend/config/sentry_alert_rules.applied.lock` so the drift
audit (`audit_sentry_alert_rules_drift.py`) can detect "YAML edited
but never synced" on every commit.

Usage:
  ./venv/bin/python scripts/sentry_sync_alert_rules.py            # dry-run
  ./venv/bin/python scripts/sentry_sync_alert_rules.py --apply    # write
  ./venv/bin/python scripts/sentry_sync_alert_rules.py --apply --prune

Tier: TIER_0.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as script from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load backend/.env before reading SENTRY_AUTH_TOKEN / ORG / PROJECT.
from app.core.env_bootstrap import load_env
load_env()

from app.services.sentry_alert_rules import (
    apply_diff,
    compute_diff,
    compute_yaml_hash,
    fetch_remote_rules,
    is_configured,
    load_local_rules,
    rules_by_project,
    write_applied_hash,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="actually write to Sentry (default: dry-run)")
    ap.add_argument("--prune", action="store_true", help="also delete unmanaged remote rules (CAREFUL)")
    args = ap.parse_args(argv)

    local = load_local_rules()
    buckets = rules_by_project(local)
    print(f"Local rules in YAML: {len(local)} across {len(buckets)} project(s)")
    for proj, rs in buckets.items():
        print(f"  [{proj}] {len(rs)} rule(s):")
        for r in rs:
            print(f"    - {r['name']}")

    if not is_configured():
        print(
            "\nSENTRY_AUTH_TOKEN / SENTRY_ORG / SENTRY_PROJECT unset — skipping API calls."
        )
        return 0

    totals = {"created": 0, "updated": 0, "deleted": 0, "skipped_deletes": 0}
    errors: list[str] = []
    for proj, rs in buckets.items():
        print(f"\n=== Project: {proj} ===")
        remote = fetch_remote_rules(project_override=proj)
        print(f"Remote rules: {len(remote)}")
        diff = compute_diff(rs, remote)
        print(
            f"Diff: create={len(diff['to_create'])} update={len(diff['to_update'])} "
            f"delete={len(diff['to_delete'])}"
        )
        summary = apply_diff(
            diff, dry_run=not args.apply, delete_unmanaged=args.prune,
            project_override=proj,
        )
        for k in ("created", "updated", "deleted", "skipped_deletes"):
            totals[k] += summary[k]
        errors.extend(summary["errors"])

    print("\nTotals:")
    print(f"  created          : {totals['created']}")
    print(f"  updated          : {totals['updated']}")
    print(f"  deleted          : {totals['deleted']}")
    print(f"  skipped_deletes  : {totals['skipped_deletes']}")
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        return 1

    if args.apply:
        h = compute_yaml_hash()
        write_applied_hash(h)
        print(f"\n✅ Applied across {len(buckets)} project(s). Lock hash {h[:12]}…")
    else:
        print("\nDry-run only. Pass --apply to actually write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
