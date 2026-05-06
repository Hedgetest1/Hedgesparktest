#!/usr/bin/env python3
"""audit_service_test_coverage.py — Pin service/worker test coverage
to a frozen baseline + block NEW uncovered modules.

Born 2026-05-06 after the founder reported the evolution pipeline
blocking on `lite_morning_digest.py` having no dedicated test file,
plus the brutal-audit revelation that 79 services + 16 workers +
99 API modules ship with zero test coverage.

Strategy: progressive allowlist hardening. The current uncovered
set is captured in `_KNOWN_UNCOVERED_BACKLOG` below. The audit:
  - PASSES if uncovered ⊆ allowlist (no new uncovered files).
  - FAILS if a NEW file appears without a `tests/test_<name>.py` AND
    no opt-out marker.

Each addition to `_KNOWN_UNCOVERED_BACKLOG` is a debt that must
shrink over time. The test-coverage hardening sprint is tracked in
project_open_decisions_backlog.md "C-* hardening" section.

Opt-out: in a service file, add a top-level comment:

    # test-coverage: <reason>

(e.g., "thin wrapper around third-party SDK; tested via integration
suite", "experimental flag-gated only, not in active path"). The
audit accepts this in lieu of a dedicated test file.

# invariant-eligible: false
# Reason: file-system scan, not a runtime invariant. Wired in
# preflight only — runtime re-fire would not detect a missing
# test (the static state is the truth).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TESTS = ROOT / "tests"

_OPT_OUT_RE = re.compile(r"^\s*#\s*test-coverage:\s*", re.MULTILINE)


def _existing_test_files() -> set[str]:
    """Return basenames (no extension) of every test file under tests/."""
    if not TESTS.is_dir():
        return set()
    return {p.stem for p in TESTS.rglob("test_*.py")}


def _modules_with_test_imports() -> set[str]:
    """Return the set of module basenames imported by ANY test file.

    A test that does `from app.services.foo import bar` counts as
    coverage for `foo.py` even if `tests/test_foo.py` doesn't exist —
    the test exercises the module via integration."""
    imported: set[str] = set()
    if not TESTS.is_dir():
        return imported
    pat = re.compile(
        r"from\s+app\.(?:services|workers|workers\.tasks|api|core)\.(\w+)\s+import|"
        r"import\s+app\.(?:services|workers|workers\.tasks|api|core)\.(\w+)"
    )
    for tf in TESTS.rglob("*.py"):
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in pat.finditer(text):
            mod = m.group(1) or m.group(2)
            if mod:
                imported.add(mod)
    return imported


def _has_opt_out(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return bool(_OPT_OUT_RE.search(text))


# Baseline as of 2026-05-06 — the founder's brutal-audit findings.
# Every file here is a known-uncovered module. Each one represents
# a debt to address; removing entries from this list is a forward
# step. ADDING entries means a new module shipped without coverage —
# investigate why before merging.
_KNOWN_UNCOVERED_BACKLOG: frozenset[str] = frozenset({
    # services/ (78 — `lite_morning_digest` was added in the same
    # commit + has its own test, so it's NOT in this set)
    "action_candidates_engine", "action_executor", "action_proof",
    "activation", "adaptive_governance", "agency", "analytics_assistant",
    "audience_segments", "autonomous_loop", "behavioral_cohorts",
    "brand_voice", "brief_engine", "chat_voice", "chatbot_llm_fallback",
    "cig_engine", "cohort_engine", "community_marketplace",
    "conversion_metrics", "conversion_service", "customer_churn_scorer",
    "email_templates", "empirical_calibration", "event_emitter",
    "external_lookup_service", "followup_worker", "inbound_email_processor",
    "instant_onboarding", "intelligence_report", "inventory_snapshot_fetcher",
    "inventory_snapshot_runner", "klaviyo_connection",
    "market_lookup_engine", "measurement_health", "merchant_churn_predictor",
    "merchant_digest", "multi_currency_rollup", "night_shift_calibration",
    "nudge_composer", "nudge_dna", "nudge_gating", "nudge_measurement",
    "nudge_optimizer", "nudge_rank", "order_ingestion", "p95_snapshot",
    "price_intelligence_engine", "price_radar_service", "price_sensitivity",
    "proactive_chat", "product_intelligence_engine", "proof_engine",
    "report_special_metrics", "revenue_autopsy", "revenue_forecast",
    "revenue_loss", "revenue_metrics", "rule_engine", "scoring_calibration",
    "sentry_triage", "setup_audit", "share_engine", "shopify_admin",
    "shopify_auth", "shopify_cogs_sync", "signal_text", "sip_engine",
    "slack_dispatcher", "soc2_controls", "store_context",
    "store_insight_engine", "storefront_preview", "system_diagnostic",
    "trust_outcome_measurement", "unique_product_engine", "utm_attribution",
    "webhook_monitor", "weekly_digest", "worker_watchdog",
    # workers/ (16)
    "gdpr_worker", "intelligence_worker", "nudge_optimization_worker",
    "segment_monitor_worker", "cleanup_task", "dashboard_asset_probe_task",
    "data_integrity_task", "email_dns_status_task", "night_shift_task",
    "nudge_compose_task", "product_metrics_task", "rollout_promotion_task",
    "store_metrics_task", "watchdog_task", "webhook_health_task",
})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="No-op shim — strict-by-default for new modules.")
    args = parser.parse_args()

    test_files = _existing_test_files()
    test_imports = _modules_with_test_imports()

    targets: list[Path] = []
    for sub in ("services", "workers"):
        d = APP / sub
        if not d.is_dir():
            continue
        targets.extend(d.rglob("*.py"))

    uncovered_now: list[Path] = []
    for p in targets:
        if p.name == "__init__.py":
            continue
        stem = p.stem
        if f"test_{stem}" in test_files or stem in test_imports:
            continue
        if _has_opt_out(p):
            continue
        uncovered_now.append(p)

    uncovered_basenames = {p.stem for p in uncovered_now}

    # New = currently uncovered but NOT in the frozen baseline.
    new_uncovered = uncovered_basenames - _KNOWN_UNCOVERED_BACKLOG

    # Stale = in baseline but actually now covered (someone added a
    # test). Forward debt-reduction signal.
    stale_baseline = _KNOWN_UNCOVERED_BACKLOG - uncovered_basenames

    if new_uncovered:
        print(
            f"audit_service_test_coverage: FAIL — {len(new_uncovered)} "
            f"new uncovered module(s) (no `tests/test_X.py`, no test "
            f"import, no `# test-coverage:` opt-out):"
        )
        for stem in sorted(new_uncovered):
            print(f"  {stem}")
        print(
            "\nFix one of:\n"
            "  1. Create `tests/test_<name>.py` with at least one "
            "test that imports the module.\n"
            "  2. Add a top-level comment in the module:\n"
            "       # test-coverage: <reason>\n"
            "     (e.g., 'thin wrapper around SDK', 'flag-gated experimental').\n"
            "  3. If this is intentional debt to track, add the basename "
            "to `_KNOWN_UNCOVERED_BACKLOG` in this audit script along "
            "with a justification in the same commit.\n"
        )
        return 1

    if stale_baseline:
        print(
            f"audit_service_test_coverage: OK — {len(uncovered_basenames)} "
            f"uncovered modules ({len(stale_baseline)} now-covered entries "
            f"can be removed from baseline):"
        )
        for stem in sorted(stale_baseline)[:5]:
            print(f"  remove from _KNOWN_UNCOVERED_BACKLOG: {stem}")
        return 0

    print(
        f"audit_service_test_coverage: OK — {len(uncovered_basenames)} "
        f"uncovered modules tracked in baseline (matches frozen set, "
        f"no new debt)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
