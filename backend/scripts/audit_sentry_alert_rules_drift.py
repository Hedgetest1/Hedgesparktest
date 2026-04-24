#!/usr/bin/env python
"""audit_sentry_alert_rules_drift.py — block commits that edit YAML
without re-running the sync.

Compares SHA-256 of `backend/config/sentry_alert_rules.yaml` to the
hash recorded in `backend/config/sentry_alert_rules.applied.lock`.
Mismatch = either YAML was edited and never applied, or someone
deleted/forgot the lock file.

Exit codes:
  * 0 — hashes match (or lock file legitimately absent — bootstrap mode)
  * 1 — drift detected (preflight blocks the commit)

Bootstrap: when lock file doesn't exist AND env is unconfigured,
audit passes with a "lock not yet initialized" notice. First successful
`scripts/sentry_sync_alert_rules.py --apply` writes the lock; from
that point onward drift is enforced.

Tier: TIER_0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sentry_alert_rules import (
    _RULES_LOCK,
    _RULES_YAML,
    compute_yaml_hash,
    load_local_rules,
    read_applied_hash,
)


@telemetered("audit_sentry_alert_rules_drift")
def main(argv: list[str] | None = None) -> int:
    if not _RULES_YAML.is_file():
        print(f"❌ Sentry alert rules YAML missing: {_RULES_YAML}")
        return 1

    # Schema validation — also catches duplicate names + missing keys.
    try:
        load_local_rules()
    except Exception as exc:
        print(f"❌ Sentry alert rules YAML invalid: {exc}")
        return 1

    yaml_hash = compute_yaml_hash()
    applied = read_applied_hash()

    if applied is None:
        # Bootstrap state: YAML exists but no successful apply yet. Pass
        # only when env is unconfigured (= founder hasn't set up Sentry
        # API auth, expected today). Once SENTRY_AUTH_TOKEN is set,
        # require an explicit first apply.
        if not os.getenv("SENTRY_AUTH_TOKEN", "").strip():
            print(
                "✅ Sentry alert rules YAML present, lock not yet initialized "
                "(SENTRY_AUTH_TOKEN unset — bootstrap mode)."
            )
            return 0
        print(
            "❌ Sentry alert rules: SENTRY_AUTH_TOKEN configured but lock file "
            f"missing. Run: ./venv/bin/python scripts/sentry_sync_alert_rules.py --apply"
        )
        return 1

    if applied != yaml_hash:
        print(
            "❌ Sentry alert rules drift detected.\n"
            f"   YAML hash    : {yaml_hash[:16]}…\n"
            f"   Applied hash : {applied[:16]}…\n"
            f"   Action       : run `./venv/bin/python scripts/sentry_sync_alert_rules.py --apply`\n"
            f"                  to push your YAML edits to Sentry, then commit the updated\n"
            f"                  {_RULES_LOCK.relative_to(_RULES_LOCK.parents[2])} alongside the YAML."
        )
        return 1

    rules = load_local_rules()
    print(
        f"✅ Sentry alert rules in sync — {len(rules)} rule(s), hash {yaml_hash[:12]}…"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
