#!/usr/bin/env python3
"""audit_alert_heal_coverage.py — Verify every alert_type written by
the autonomous pipeline has a documented heal path.

Born 2026-05-06 from external CTO audit FINDING 1: the prior
heal-detection fix added `invariant_audit_timeout` to the helper but
left 6+ other alert_types written by `bugfix_pipeline.py` without
heal coverage. Most damning: `fix_regressed_24h` (shipped same
session) accumulates forever after one write because the function
deletes the Redis key — no heal trigger possible without explicit
contract.

This audit is a CONTRACT enforcer: every `write_alert(alert_type=X)`
call site in `app/services/` and `app/workers/` must register one
of the following heal paths:
  1. Auto-resolve via `auto_resolve_alerts(alert_type=X)` somewhere
     in app/.
  2. Auto-resolve via `_auto_resolve_prior_invariant` (which heals
     a hard-coded set of types).
  3. Explicit `# heal-detection: <reason>` comment annotation
     within 5 lines of the write_alert call (e.g.,
     "self-clearing via TTL", "one-shot terminal alert by design").
  4. Listed in `_KNOWN_HEAL_BACKLOG` with rationale (intentional
     debt tracked for next sprint).

Exit codes
  0 — every alert_type has a heal contract (or is in backlog).
  1 — at least one alert_type is uncovered (preflight blocks).

# invariant-eligible: true
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

# Baseline-frozen: 2026-05-06 snapshot of pre-existing alert types
# without explicit heal contract. Audit blocks any NEW alert_type
# added without heal coverage — the existing 56 remain tracked debt.
# Founder direttiva 2026-05-06: zero new uncovered, retroactive
# close is next-sprint scope (per-site domain knowledge required).
# Each entry rationale = "pre-existing 2026-05-06 baseline; explicit
# heal pending sprint review" unless otherwise specified.
_BASELINE_PREEXISTING_2026_05_06 = "pre-existing 2026-05-06 baseline; explicit heal pending sprint review"

_KNOWN_HEAL_BACKLOG: dict[str, str] = {
    # bugfix-pipeline class — rationale specified inline below
    "fix_incomplete": (
        "Self-clearing: written from _post_apply_retro_check once per "
        "apply; new triage cycle replaces with a fresh candidate."
    ),
    "fix_regressed_24h": (
        "Terminal one-shot per candidate. check_preventer_regressions "
        "deletes the Redis key after writing so re-fire impossible "
        "for same candidate. Heal-detection comment annotated at the "
        "write_alert call site (bugfix_pipeline.py)."
    ),
    "chronic_thrashing": (
        "One-shot triage signal; self-clears via 7d acute_dedup window."
    ),
    "bugfix_apply_failed": (
        "Terminal per failed apply; heals on subsequent successful "
        "apply that writes bugfix_applied for the same candidate."
    ),
    "bugfix_rolled_back": (
        "Same heal pattern as bugfix_apply_failed."
    ),
    "governed_tier1_applied": (
        "Info-level (NOT critical), self-archives via 7d acute_dedup."
    ),
    "adversarial_critical_finding": (
        "Heals via human review of the bugfix candidate."
    ),
    "adversarial_partial_coverage": (
        "Self-clears when subsequent reviews complete with full coverage."
    ),
    # Pre-existing baseline (50 types). Each one needs per-domain heal
    # logic — too risky to hand-write in one commit. Tracked here so
    # any NEW alert_type added without heal fails preflight.
    "aggregation_cycle_slow": _BASELINE_PREEXISTING_2026_05_06,
    "approval_expired_unhandled": _BASELINE_PREEXISTING_2026_05_06,
    "audit_log_tampering": _BASELINE_PREEXISTING_2026_05_06,
    "benchmark_compute_failed": _BASELINE_PREEXISTING_2026_05_06,
    "billing_api_failure": _BASELINE_PREEXISTING_2026_05_06,
    "bulk_classify_errors": _BASELINE_PREEXISTING_2026_05_06,
    "chatbot_llm_hallucination": _BASELINE_PREEXISTING_2026_05_06,
    # circuit_breaker_tripped removed from backlog 2026-05-07: heal
    # coverage shipped via `_heal_circuit_breaker_alerts()` invoked
    # from both early-return branches of `agent_worker._check_circuit_
    # breaker` (dormant short-circuit + healthy reset). Closes #129083
    # noise class. Audit detects the auto_resolve_alerts call site
    # automatically.
    "deploy_failed": _BASELINE_PREEXISTING_2026_05_06,
    "deploy_rolled_back": _BASELINE_PREEXISTING_2026_05_06,
    "deploy_succeeded": _BASELINE_PREEXISTING_2026_05_06,
    "drift_chronic_escalation": _BASELINE_PREEXISTING_2026_05_06,
    # email_send_failed removed from backlog 2026-05-07: heal coverage
    # shipped via auto_resolve_alerts on the SENT branch of
    # email_orchestrator.orchestrate_send (line 663).
    "event_bus_emit_chronic_failure": _BASELINE_PREEXISTING_2026_05_06,
    "extractor_failures": _BASELINE_PREEXISTING_2026_05_06,
    "flag_rollback": _BASELINE_PREEXISTING_2026_05_06,
    "frontend_error": _BASELINE_PREEXISTING_2026_05_06,
    "frontend_error_spike": _BASELINE_PREEXISTING_2026_05_06,
    "goal_at_risk": _BASELINE_PREEXISTING_2026_05_06,
    "intelligence_degradation": _BASELINE_PREEXISTING_2026_05_06,
    "klaviyo_circuit_tripped": _BASELINE_PREEXISTING_2026_05_06,
    "llm_benchmark_regression": _BASELINE_PREEXISTING_2026_05_06,
    "llm_benchmark_run_failed": _BASELINE_PREEXISTING_2026_05_06,
    "llm_realmodel_drift": _BASELINE_PREEXISTING_2026_05_06,
    "low_conversion_rate": _BASELINE_PREEXISTING_2026_05_06,
    "manual_intervention_required": _BASELINE_PREEXISTING_2026_05_06,
    "merchant_bug_escalation": _BASELINE_PREEXISTING_2026_05_06,
    "merchant_reported_bug": _BASELINE_PREEXISTING_2026_05_06,
    "merchant_silent": _BASELINE_PREEXISTING_2026_05_06,
    # onboarding_drift, onboarding_stuck, slow_activation removed from
    # backlog 2026-05-07: heal coverage shipped via heal_per_shop_alerts
    # in onboarding_health.write_onboarding_alerts (lines 550, 596, 615).
    # Audit scanner extended same commit to recognize positional
    # alert_type in heal_per_shop_alerts calls (4-entry close from a
    # 10-line audit patch).
    "p95_slow_trend": _BASELINE_PREEXISTING_2026_05_06,
    "perf_network_layer_drift": _BASELINE_PREEXISTING_2026_05_06,
    # pixel_abandonment removed from backlog 2026-05-07: heal coverage
    # shipped via heal_per_shop_alerts in
    # onboarding_health.write_onboarding_alerts (line 573-576) — population
    # scan over long_abandon (>72h, top-3 alerted) auto-resolves any prior
    # alert whose shop_domain is no longer in the active set.
    "rars_compute_failed": _BASELINE_PREEXISTING_2026_05_06,
    "rars_volatility_projected": _BASELINE_PREEXISTING_2026_05_06,
    "refund_loss_compute_failed": _BASELINE_PREEXISTING_2026_05_06,
    "rollback_failed": _BASELINE_PREEXISTING_2026_05_06,
    "rum_regression": _BASELINE_PREEXISTING_2026_05_06,
    "semantic_drift": _BASELINE_PREEXISTING_2026_05_06,
    "sentry_fingerprint_storm": _BASELINE_PREEXISTING_2026_05_06,
    "sentry_incident_rate_spike": _BASELINE_PREEXISTING_2026_05_06,
    "sentry_parse_failure": _BASELINE_PREEXISTING_2026_05_06,
    "sentry_regression": _BASELINE_PREEXISTING_2026_05_06,
    # sentry_triage_stuck removed from backlog 2026-05-13: heal coverage
    # shipped via auto_resolve_alerts in observability_spikes.detect_sentry_
    # triage_stuck "drained" branch (line ~1116). Re-scope from `ready`
    # (terminal observability post-Brain-Vero) to `pending` (live producer)
    # also shipped same commit.
    "sentry_webhook_dark": _BASELINE_PREEXISTING_2026_05_06,
    "session_anomaly": _BASELINE_PREEXISTING_2026_05_06,
    "suspicious_traffic_pattern": _BASELINE_PREEXISTING_2026_05_06,
    "tracker_runtime_error_spike": _BASELINE_PREEXISTING_2026_05_06,
    "trust_action_failed": _BASELINE_PREEXISTING_2026_05_06,
    "trust_contract_auto_paused": _BASELINE_PREEXISTING_2026_05_06,
    "ux_frustration_spike": _BASELINE_PREEXISTING_2026_05_06,
    # webhook_delivery_failed removed from backlog 2026-05-07: heal
    # coverage shipped via auto_resolve_alerts in
    # signal_webhooks.emit_signal "delivered" branch (line 561).
    "webhook_repair_failed": _BASELINE_PREEXISTING_2026_05_06,
    "worker_auto_restarted": _BASELINE_PREEXISTING_2026_05_06,
    "worker_repeated_failure": _BASELINE_PREEXISTING_2026_05_06,
}

_HEAL_HELPER_NAMES = (
    "auto_resolve_alerts",
    "_auto_resolve_prior_invariant",
    "auto_heal",
    # Population-scanner heal helper. Signature
    # `heal_per_shop_alerts(db, source, alert_type, currently_affected)`.
    # The alert_type is the 3rd positional argument; the scanner below
    # special-cases this name to inspect args[2] when no kwarg matches.
    # Added 2026-05-07 from heal-detection-wirer stress-test #1
    # friction-finding #2 (closes onboarding_stuck / onboarding_drift /
    # slow_activation / pixel_abandonment in one extension).
    "heal_per_shop_alerts",
)
# Helpers whose alert_type is positional (not kwarg). Scanner inspects
# args[<N>] for these. Maps fname → positional index.
_HEAL_HELPER_POSITIONAL_AT = {
    "heal_per_shop_alerts": 2,
}
_HEAL_COMMENT_MARKER = "heal-detection:"


def _scan_write_alert_types(target: Path) -> dict[str, list[tuple[Path, int]]]:
    """Return {alert_type: [(file, lineno), ...]} for every
    write_alert call with an alert_type=<string-literal> kwarg.
    """
    out: dict[str, list[tuple[Path, int]]] = {}
    for pyfile in target.rglob("*.py"):
        source = safe_read_text(pyfile)
        if source is None:
            continue
        if "write_alert" not in source:
            continue
        try:
            tree = ast.parse(source, str(pyfile))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target_name = node.func
            name: str | None = None
            if isinstance(target_name, ast.Name):
                name = target_name.id
            elif isinstance(target_name, ast.Attribute):
                name = target_name.attr
            if name != "write_alert":
                continue
            for kw in node.keywords:
                if kw.arg != "alert_type":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    at = kw.value.value
                    out.setdefault(at, []).append(
                        (pyfile.relative_to(ROOT), node.lineno)
                    )
                break
    return out


def _scan_heal_calls(target: Path) -> set[str]:
    """Return alert_types referenced as `auto_resolve_alerts(..., alert_type=X)`
    or hard-listed inside _auto_resolve_prior_invariant body."""
    healed: set[str] = set()
    for pyfile in target.rglob("*.py"):
        source = safe_read_text(pyfile)
        if source is None:
            continue
        if not any(h in source for h in _HEAL_HELPER_NAMES):
            continue
        try:
            tree = ast.parse(source, str(pyfile))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                t = node.func
                fname = (
                    t.id if isinstance(t, ast.Name)
                    else t.attr if isinstance(t, ast.Attribute)
                    else None
                )
                if fname not in _HEAL_HELPER_NAMES:
                    continue
                # Positional alert_type lookup for helpers that take it
                # as a non-kwarg argument (e.g. heal_per_shop_alerts).
                pos_idx = _HEAL_HELPER_POSITIONAL_AT.get(fname)
                if pos_idx is not None and len(node.args) > pos_idx:
                    pos_arg = node.args[pos_idx]
                    if isinstance(pos_arg, ast.Constant) and isinstance(pos_arg.value, str):
                        healed.add(pos_arg.value)
                for kw in node.keywords:
                    if kw.arg != "alert_type":
                        continue
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        healed.add(kw.value.value)
        # Also scan tuples / lists of literal alert_types iterated in helpers
        # (covers _auto_resolve_prior_invariant's `for at in (...)` form).
        # Heuristic: any string literal next to "alert_type" in a Tuple/List.
        for m in re.finditer(
            r"alert_type=[\'\"]([^\'\"]+)[\'\"]", source,
        ):
            healed.add(m.group(1))
        for m in re.finditer(
            r"\(\s*[\'\"]invariant_regression[\'\"]\s*,\s*[\'\"]([^\'\"]+)[\'\"]", source,
        ):
            healed.add("invariant_regression")
            healed.add(m.group(1))
    return healed


def _scan_heal_comments(target: Path) -> dict[str, list[Path]]:
    """Return {alert_type: [files...]} where a `# heal-detection: ...`
    comment annotates the call site within ±5 lines of write_alert."""
    out: dict[str, list[Path]] = {}
    for pyfile in target.rglob("*.py"):
        source = safe_read_text(pyfile)
        if source is None:
            continue
        if "write_alert" not in source or _HEAL_COMMENT_MARKER not in source:
            continue
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if _HEAL_COMMENT_MARKER not in line:
                continue
            window = "\n".join(lines[max(0, i - 5):min(len(lines), i + 5)])
            for m in re.finditer(
                r'alert_type=[\'\"]([^\'\"]+)[\'\"]', window,
            ):
                out.setdefault(m.group(1), []).append(pyfile.relative_to(ROOT))
    return out


def main() -> int:
    written = _scan_write_alert_types(APP)
    healed = _scan_heal_calls(APP)
    commented = _scan_heal_comments(APP)

    uncovered: list[tuple[str, list[tuple[Path, int]]]] = []
    for alert_type, sites in written.items():
        if alert_type in healed:
            continue
        if alert_type in commented:
            continue
        if alert_type in _KNOWN_HEAL_BACKLOG:
            continue
        if alert_type.startswith("test_") or alert_type.startswith("_test"):
            continue  # test fixture artifacts
        uncovered.append((alert_type, sites))

    print(
        f"audit_alert_heal_coverage: {len(written)} alert_type(s) written, "
        f"{len(healed)} heal-helper-referenced, "
        f"{len(commented)} comment-annotated, "
        f"{len(_KNOWN_HEAL_BACKLOG)} backlog-tracked, "
        f"{len(uncovered)} UNCOVERED."
    )
    if uncovered:
        print("\nUNCOVERED alert_types (need heal contract):")
        for at, sites in uncovered:
            site_str = ", ".join(f"{f}:{ln}" for f, ln in sites[:3])
            print(f"  ✗ {at}  @ {site_str}")
        print(
            "\nFix one of:\n"
            "  1. Add an `auto_resolve_alerts(..., alert_type=X)` call "
            "in the recovery branch.\n"
            "  2. Add a `# heal-detection: <reason>` comment within "
            "5 lines of the write_alert call.\n"
            "  3. Register the type in `_KNOWN_HEAL_BACKLOG` in this "
            "audit script with explicit rationale."
        )
        return 1
    if _KNOWN_HEAL_BACKLOG:
        print(f"\nBACKLOG-tracked ({len(_KNOWN_HEAL_BACKLOG)} type(s)):")
        for at, reason in _KNOWN_HEAL_BACKLOG.items():
            print(f"  · {at}: {reason[:90]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
