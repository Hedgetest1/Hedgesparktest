#!/usr/bin/env python3
"""audit_email_registry.py — preflight guard for email governance coherence.

Born 2026-04-22 after the drift class that silently hard-blocked 8
templates in prod (welcome, beta_welcome, setup_incomplete, 3×followup,
first_insight, connection_issue) plus a handful of orphan email_types
that were never wired through governance. The runtime governance layer
was correct; what failed was commit-time coherence between the sources
of truth that drifted apart: TEMPLATE_REGISTRY, IDENTITY_RULES,
producer call sites, and baseline hashes.

This audit enforces the 4 invariants that would have caught every
instance of that class at commit time:

    INV-1  Every email_type string passed to EmailIntent / submit_intent
           / send_immediate is present in TEMPLATE_REGISTRY.
    INV-2  Every sender referenced by TEMPLATE_REGISTRY[*]["sender"] is
           a key in IDENTITY_RULES, and the type appears in that
           identity's allowed_types set.
    INV-3  Every email_type in IDENTITY_RULES[*]["allowed_types"] is
           registered in TEMPLATE_REGISTRY (no ghosts).
    INV-4  Every template in _TEMPLATE_BASELINES matches the current
           render hash (i.e. no un-refreshed drift at commit time).
    INV-5  Every renderer key in email_templates._RENDERERS has a
           matching entry in _TEMPLATE_BASELINES. Catches the case
           where a dev ships a new _render_* without a baseline hash
           — INV-4 silently passes such a template because its drift
           check only iterates baselines. Added 2026-04-22 to close
           that gap (noted as debt in the original sprint).

Exit codes
----------
    0  all invariants hold
    1  at least one violation (preflight blocks the commit)

Run manually:
    ./venv/bin/python scripts/audit_email_registry.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

# Allow `from app.services... import ...` when invoked from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = Path(__file__).resolve().parent.parent
APP = BACKEND / "app"

# Directories where producers live. Tests + ops preview routes are
# intentionally excluded (they reference email_types for diagnostic
# purposes and aren't supposed to send).
PRODUCER_DIRS = [APP / "services", APP / "workers", APP / "api"]

# Exclusions: files that reference email_type for diagnostic / passthrough
# reasons, never as a producer.
EXCLUDED_FILES = {
    APP / "services" / "email_orchestrator.py",   # defines EmailIntent itself
    APP / "services" / "email_governance.py",      # defines TEMPLATE_REGISTRY
    APP / "services" / "email_performance.py",     # records stats across all types
    APP / "services" / "email_journey.py",         # read-only journey state
    APP / "services" / "merchant_email_service.py",  # generic submit wrapper
    APP / "api" / "ops.py",                        # /ops/emails diagnostic
    APP / "api" / "ops_email_preview.py",          # /ops/email/preview diag
    APP / "api" / "resend_webhooks.py",            # inbound webhook ingest
}

# Matches `email_type="<literal>"` or `email_type='<literal>'`.
# 2026-04-23 retro DA: also resolves the common indirection pattern
# where producers assign a constant and reference it:
#     EMAIL_TYPE = "welcome"
#     ...
#     submit_intent(EmailIntent(email_type=EMAIL_TYPE, ...))
# We walk the AST so the constant definition and the `email_type=`
# keyword argument are both captured, then resolve references.
_LITERAL_TYPE_RE = re.compile(r'''email_type\s*=\s*["']([a-z][a-z0-9_]+)["']''')


def _scan_producer_literals() -> dict[str, list[str]]:
    """Return {email_type: [file:line, ...]} for every literal producer
    reference OR constant-resolved reference."""
    import ast as _ast
    hits: dict[str, list[str]] = {}
    for root in PRODUCER_DIRS:
        for py in root.rglob("*.py"):
            if py in EXCLUDED_FILES:
                continue
            src = safe_read_text(py)
            if src is None:
                continue
            lines = src.splitlines()
            # Pass 1: literal inline references (fast regex).
            for ln, line in enumerate(lines, 1):
                m = _LITERAL_TYPE_RE.search(line)
                if not m:
                    continue
                et = m.group(1)
                hits.setdefault(et, []).append(f"{py.relative_to(BACKEND)}:{ln}")
            # Pass 2: AST-based resolution of constant-assigned email types.
            # Build a map {NAME: "literal"} for module-level string constants,
            # then find any `email_type=<Name>` kwarg whose Name is in the map.
            try:
                tree = _ast.parse(src, filename=str(py))
            except SyntaxError:
                continue
            const_map: dict[str, str] = {}
            for node in tree.body:
                if isinstance(node, _ast.Assign) and len(node.targets) == 1:
                    tgt = node.targets[0]
                    if isinstance(tgt, _ast.Name) and isinstance(node.value, _ast.Constant):
                        if isinstance(node.value.value, str):
                            const_map[tgt.id] = node.value.value
            if not const_map:
                continue
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.keyword):
                    continue
                if node.arg != "email_type":
                    continue
                if isinstance(node.value, _ast.Name) and node.value.id in const_map:
                    et = const_map[node.value.id]
                    hits.setdefault(et, []).append(
                        f"{py.relative_to(BACKEND)}:{node.value.lineno} (via constant {node.value.id})"
                    )
    return hits


def _extract_email_local(from_address: str) -> str:
    """Pull the email from `Display <local@domain>` or raw `local@domain`."""
    m = re.search(r"<([^>]+)>", from_address)
    return m.group(1) if m else from_address.strip()


@telemetered("audit_email_registry")
def main() -> int:
    try:
        from app.services.email_governance import (
            TEMPLATE_REGISTRY,
            IDENTITY_RULES,
            _TEMPLATE_BASELINES,
            check_template_drift,
        )
    except Exception as exc:
        print(f"FAIL: cannot import email_governance: {exc}", file=sys.stderr)
        return 1

    violations: list[str] = []

    # ── INV-1: producer literals ⊆ TEMPLATE_REGISTRY ─────────────────────
    producer_hits = _scan_producer_literals()
    registered = set(TEMPLATE_REGISTRY.keys())
    for et, sites in sorted(producer_hits.items()):
        if et not in registered:
            violations.append(
                f"INV-1 producer uses email_type {et!r} not in TEMPLATE_REGISTRY "
                f"(sites: {', '.join(sites[:3])}{'...' if len(sites) > 3 else ''})"
            )

    # ── INV-2: every registry sender has matching IDENTITY_RULES entry ──
    for et, entry in TEMPLATE_REGISTRY.items():
        sender = entry.get("sender")
        if not sender:
            violations.append(f"INV-2 TEMPLATE_REGISTRY[{et!r}] has no sender")
            continue
        if sender not in IDENTITY_RULES:
            violations.append(
                f"INV-2 TEMPLATE_REGISTRY[{et!r}].sender={sender!r} "
                f"has no IDENTITY_RULES entry"
            )
            continue
        allowed = IDENTITY_RULES[sender].get("allowed_types", set())
        if et not in allowed:
            violations.append(
                f"INV-2 IDENTITY_RULES[{sender!r}].allowed_types missing "
                f"{et!r} (TEMPLATE_REGISTRY maps this type to that sender)"
            )

    # ── INV-3: every IDENTITY_RULES allowed_type is in TEMPLATE_REGISTRY ─
    for sender, rules in IDENTITY_RULES.items():
        for et in rules.get("allowed_types", set()):
            if et not in registered:
                violations.append(
                    f"INV-3 IDENTITY_RULES[{sender!r}].allowed_types contains "
                    f"{et!r} which is not in TEMPLATE_REGISTRY (ghost type)"
                )

    # ── INV-4: every baseline matches current render ──────────────────
    try:
        drift_state = check_template_drift()
    except Exception as exc:
        violations.append(f"INV-4 check_template_drift failed: {exc}")
        drift_state = {}
    for name, status in drift_state.items():
        if status == "drifted":
            violations.append(
                f"INV-4 template {name!r} baseline is stale — run "
                f"`regenerate_baselines()` from app.services.email_governance "
                f"and update _TEMPLATE_BASELINES in the same commit"
            )

    # ── INV-5: every _RENDERERS key has a baseline ────────────────────
    # Inline-HTML producers (lite_morning_digest, roi_report, gdpr_processor,
    # onboarding_health) do NOT go through _RENDERERS — they build HTML
    # directly — so they legitimately have no baseline. This check covers
    # only the template renderers wired into `render_email()`.
    try:
        from app.services.email_templates import _RENDERERS
    except Exception as exc:
        violations.append(f"INV-5 cannot import _RENDERERS: {exc}")
        _RENDERERS = {}
    baseline_keys = set(_TEMPLATE_BASELINES.keys())
    for renderer_key in _RENDERERS.keys():
        if renderer_key not in baseline_keys:
            violations.append(
                f"INV-5 _RENDERERS[{renderer_key!r}] has no entry in "
                f"_TEMPLATE_BASELINES — add one via `regenerate_baselines()` "
                f"and a matching _BASELINE_CONTEXTS entry in "
                f"email_governance.py so INV-4 can track drift on it"
            )

    if violations:
        print(f"FAIL: {len(violations)} email-registry invariant violation(s):")
        for v in violations:
            print(f"  - {v}")
        return 1

    # Stable one-line summary for preflight.
    print(
        f"OK: email registry coherent — "
        f"{len(registered)} types, {len(IDENTITY_RULES)} identities, "
        f"{len(_TEMPLATE_BASELINES)} baselines, "
        f"{len(_RENDERERS)} renderers "
        f"({len(producer_hits)} producer literals checked)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
