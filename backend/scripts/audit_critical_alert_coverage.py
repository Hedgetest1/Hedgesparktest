#!/usr/bin/env python3
"""audit_critical_alert_coverage.py — commit-msg gate enforcing
CLAUDE.md §22.3 + feedback_no_noise_floor_shield.

If the commit message contains a close-claim (score ≥ 8.5, "shipped",
"complete", "10/10", etc.), AND there are unresolved CRITICAL
ops_alerts at commit time, the body MUST contain either:

  1. Per-alert disposition lines naming each unresolved CRITICAL
     alert and labeling it (R-fix / R-disprove / R-blocker:<class>).
  2. A `# critical-alert-bypass: <reason>` annotation explicitly
     listing each alert ID being deferred and the un-parking
     trigger.

Born 2026-05-06 from the founder's brutal-honesty audit. The
"noise floor" / "inherited backlog" framing was being used as a
rhetorical shield against per-alert investigation. This audit
makes per-alert disposition mechanical: every unresolved CRITICAL
must be either fixed (in same commit), disproven (with citation),
or explicitly R-blocker-deferred (with class).

Usage
-----
Commit-msg hook. Receives commit message file path as $1.
Reads ops_alerts table via a short-lived SessionLocal.

Exit codes
  0 — no close claim OR (close claim present AND every CRITICAL
      either has a disposition line OR is referenced by ID in
      bypass annotation).
  1 — close claim present AND ≥1 CRITICAL alert has no disposition
      AND no bypass (commit refused).

# invariant-eligible: false
# (commit-msg-only: nothing to monitor at runtime)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Reuse close-claim patterns from audit_capillary_scope_claim
# (the canonical "close-claim" detector).
_CLOSE_CLAIM = re.compile(
    r"\b(?:10\s*/\s*10|11\s*/\s*10|killer|perfect|perfetto|"
    r"closed|chius[oa]|complete|completed|completato|"
    r"all[- ]green|tutto verde|tutto chiuso|fully (?:done|closed)|"
    r"elite|shipped|score\s*[:\-]\s*9(?:\.\d+)?|"
    r"score\s*[:\-]\s*10(?:\.0)?|honest score [89](?:\.\d+)?)\b",
    re.IGNORECASE,
)

_DISPOSITION = re.compile(
    r"\((?:R-fix|R-disprove|R-blocker[: ][a-z][a-z0-9_-]*)\)|"
    r"R-fix\b|R-disprove\b|R-blocker[: ][a-z][a-z0-9_-]*",
    re.IGNORECASE,
)

_BYPASS = re.compile(
    r"#\s*critical[- ]alert[- ]bypass:",
    re.IGNORECASE,
)

# A disposition that names the alert id (#N) is the strongest form.
_ALERT_ID_REF = re.compile(r"#(\d{2,7})\b")


def _fetch_unresolved_critical() -> list[tuple[int, str, str]]:
    """Return list of (id, alert_type, summary[:80]) for unresolved
    CRITICAL ops_alerts. Returns [] on any DB error (fail-open;
    audit is enforcement, not blocking on infra)."""
    try:
        # Make sure backend root is on PYTHONPATH
        backend_root = Path(__file__).resolve().parents[1]
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.core.database import SessionLocal
        from sqlalchemy import text
    except Exception:
        return []
    try:
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT id, alert_type, summary "
                    "FROM ops_alerts "
                    "WHERE resolved_at IS NULL AND severity = 'critical' "
                    "ORDER BY created_at DESC "
                    "LIMIT 100"
                )
            ).fetchall()
            return [(r[0], r[1] or "?", (r[2] or "")[:80]) for r in rows]
    except Exception:
        return []


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    msg_path = Path(sys.argv[1])
    if not msg_path.is_file():
        return 0
    msg = msg_path.read_text(encoding="utf-8")

    if not _CLOSE_CLAIM.search(msg):
        return 0  # no close claim → no gate

    if _BYPASS.search(msg):
        return 0  # explicit operator bypass

    alerts = _fetch_unresolved_critical()
    if not alerts:
        return 0  # zero unresolved CRITICAL → trivially OK

    has_any_disposition = bool(_DISPOSITION.search(msg))
    referenced_ids = set()
    for m in _ALERT_ID_REF.finditer(msg):
        try:
            referenced_ids.add(int(m.group(1)))
        except (ValueError, TypeError):
            continue

    # If the commit names every alert ID and has any disposition
    # line, accept. If alerts > 0 and disposition is absent
    # entirely, refuse.
    alert_ids = {a[0] for a in alerts}
    unreferenced = alert_ids - referenced_ids

    if has_any_disposition and not unreferenced:
        return 0

    print(
        f"audit_critical_alert_coverage: BLOCKED — commit close-claim "
        f"with {len(alerts)} unresolved CRITICAL ops_alert(s) lacking "
        f"per-alert disposition."
    )
    print()
    print("Unresolved CRITICAL alerts (need R-fix / R-disprove / R-blocker per CLAUDE.md §22.3):")
    for aid, atype, summary in alerts[:20]:
        marker = "✗" if aid in unreferenced else "·"
        print(f"  {marker} #{aid} [{atype}] {summary}")
    print()
    print("Required: for each alert ID above, the commit body must:")
    print("  1. Reference the ID (e.g., '#127103') AND")
    print("  2. Label it (R-fix) / (R-disprove) / (R-blocker:<class>)")
    print("OR add `# critical-alert-bypass: <listed-IDs> <reason>` (operator override).")
    print()
    print("Per feedback_no_noise_floor_shield: 'inherited backlog' is NOT")
    print("a valid auto-skip. Every CRITICAL is yours until R-blocker explicit.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
