#!/usr/bin/env python3
# invariant-eligible: true
# Promoted to invariant-eligible 2026-05-06 (G6 close) after the
# heal-detection migration sweep reached 100% coverage (59 writers,
# 7 heal-wired, 52 truthful opt-out). The audit now blocks new
# writer files that lack either a heal call or a # heal-detection:
# <reason> opt-out comment. Companion: feedback_brain_autonomous_
# alert_close_2026_05_05.md.
"""audit_alert_writer_heal_detection.py — Pin every write_alert site to a
heal-detection contract.

Born 2026-05-05 after the founder direttiva: the autonomous brain must
chiudere ogni situazione al 100% e impedirne il propagarsi. Concretely:
every alert writer that fires a *condition-based* alert (alert is open
because a bad state is currently observed) must ALSO close the alert
when the state recovers — otherwise alerts pile up indefinitely until
the severity-tiered TTL (6/24/72h) sweeps them, polluting probes and
masking signal.

The audit walks every `write_alert(...)` call site in `app/`, derives
the (source, alert_type) tuple it can, and verifies the same file has
either:
  - an `auto_resolve_alerts(...)` / `heal_per_shop_alerts(...)` /
    `_auto_resolve_prior_invariant(...)` / `resolve_alert(...)` call
    nearby, OR
  - an `# heal-detection: <reason>` opt-out comment justifying why the
    writer is intrinsically self-healing (e.g. it writes the alert
    already-resolved as an event log) or out-of-scope (audit-only
    visibility surface).

Empty list of opt-outs is acceptable; we expect the proportion to
shrink as writers are migrated. The list is documented in
docs/processors.md / feedback memory in due time.

Exit codes:
  0 — clean OR every offending writer is opt-outed
  1 — writers without heal-detection or opt-out comment

CLI:
  audit_alert_writer_heal_detection.py [--strict] [--report-only]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

ROOT = Path(__file__).resolve().parents[1] / "app"
WRITE_ALERT_RE = re.compile(r"\bwrite_alert\s*\(")
# Any of these in the same file counts as heal-detection coverage
HEAL_RE = re.compile(
    r"\b(?:auto_resolve_alerts|heal_per_shop_alerts|"
    r"_auto_resolve_prior_invariant|resolve_alert)\s*\("
)
OPT_OUT_RE = re.compile(r"#\s*heal-detection:\s*", re.IGNORECASE)

# Files explicitly exempted — typically test helpers or alert writers
# that are themselves a heal helper for another writer (no recursion).
EXEMPT_FILES = {
    "app/services/alerting.py",  # the heal helpers live here
    "app/services/invariant_monitor.py",  # uses _auto_resolve_prior_invariant directly
}


def scan_file(path: Path) -> tuple[bool, bool, bool]:
    """Return (writes_alerts, has_heal, has_opt_out)."""
    text = safe_read_text(path)
    if text is None:
        return (False, False, False)
    writes = bool(WRITE_ALERT_RE.search(text))
    if not writes:
        return (False, False, False)
    heal = bool(HEAL_RE.search(text))
    opt_out = bool(OPT_OUT_RE.search(text))
    return (writes, heal, opt_out)


def main(argv: list[str]) -> int:
    # 2026-05-06 G6 close: strict-by-default after coverage reached
    # 100%. The --strict flag is now a no-op shim retained for
    # invariant_monitor / preflight compatibility. --info-only is the
    # explicit operator opt-out for the rare case where a sprint
    # legitimately introduces a new writer family before the
    # opt-out / heal wiring lands (must close in the same PR).
    info_only = "--info-only" in argv
    findings: list[tuple[str, str]] = []
    coverage = {"writers": 0, "with_heal": 0, "opt_out": 0}

    for py in sorted(ROOT.rglob("*.py")):
        rel = str(py.relative_to(ROOT.parent))
        if rel in EXEMPT_FILES:
            continue
        writes, heal, opt = scan_file(py)
        if not writes:
            continue
        coverage["writers"] += 1
        if heal:
            coverage["with_heal"] += 1
            continue
        if opt:
            coverage["opt_out"] += 1
            continue
        findings.append((rel, "no heal-detection and no opt-out comment"))

    if findings:
        label = "ℹ" if info_only else "❌"
        print(f"{label} heal-detection gap — {len(findings)} writer file(s):")
        for rel, reason in findings:
            print(f"   {rel}: {reason}")
        print()
        print(
            "Fix: add heal-detection (auto_resolve_alerts / heal_per_shop_alerts) "
            "where the underlying condition can clear, OR an explicit\n"
            "    # heal-detection: <reason>\n"
            "comment if the alert is intrinsically self-healing."
        )
        print(
            f"Coverage: {coverage['with_heal']}/{coverage['writers']} writers "
            f"have heal-detection, {coverage['opt_out']} explicitly opt-out."
        )
        # Strict-by-default after G6 close (2026-05-06). --info-only
        # operator opt-out for the rare case where a sprint introduces
        # a new writer family before the heal wiring lands; that PR
        # MUST close the gap in the same commit.
        return 0 if info_only else 1

    print(
        f"✅ audit_alert_writer_heal_detection: {coverage['writers']} "
        f"writer file(s) scanned, {coverage['with_heal']} heal-wired, "
        f"{coverage['opt_out']} opt-out — clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
