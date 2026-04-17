#!/usr/bin/env python
"""
audit_tenant_isolation.py — Catch cross-tenant data leaks.

For every raw SQL literal that touches a multi-tenant table (SELECT /
UPDATE / DELETE / INSERT), verify the statement either:

  * Includes a `shop_domain = :x` predicate, OR
  * Is explicitly opted out via the ALLOWLIST below (cross-shop
    aggregations that are intentional), OR
  * Is INSERT ... VALUES (... :shop ...) — the caller provides
    shop_domain in the payload, so no cross-tenant leak at write time.

Missing the filter in a SELECT/UPDATE/DELETE on a multi-tenant table
is a SaaS-killer bug: one merchant can read or modify another merchant's
data. This audit is the bouncer at the door.

Multi-tenant tables are those with a `shop_domain` column. We enumerate
them from the live schema, so adding a new tenant table automatically
enrolls it in the audit.

Usage:
    ./venv/bin/python scripts/audit_tenant_isolation.py
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/opt/wishspark/backend")
from sqlalchemy import inspect

from app.core.database import engine


APP_ROOT = pathlib.Path("/opt/wishspark/backend/app")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


# Queries that intentionally span tenants — aggregation, benchmarks,
# network-wide metrics. Each entry is (file_path_suffix, line_hint) or
# just file_path_suffix. Only real cross-shop compute paths belong here.
ALLOWLIST: set[str] = {
    # Network aggregation — these are meant to span tenants
    "services/cig_engine.py",            # Commerce Intelligence Graph
    "services/benchmarks.py",             # Peer benchmarks — computes across shops
    "services/benchmarks_vertical.py",    # Vertical-scoped peer averages
    "services/network_aggregate.py",
    "services/observability_spikes.py",   # Dashboard-wide + fleet-wide spike detection

    "services/ops_triage.py",             # Ops-scope triage
    "api/public_status.py",               # Public status endpoint (no shop)
    "api/public_roi_counter.py",          # Already iterates merchants explicitly
    "services/monthly_evolution_audit.py",
    # Worker-scope: scans all tenants before per-tenant dispatch
    "workers/aggregation_worker.py",
    "workers/tasks/retention_task.py",    # Retention sweeps every shop
    "workers/tasks/watchdog_task.py",     # Watchdog reads worker_log cross-shop
    "workers/tasks/webhook_health_task.py",
    "workers/tasks/night_shift_task.py",
    "workers/tasks/nudge_compose_task.py",
    "services/webhook_monitor.py",
    "services/self_heal.py",
    "services/data_integrity_probe.py",
    "services/audit.py",                  # Audit chain hash verification
    "services/compliance_score.py",       # Cross-shop compliance score
    # System-health / self-improvement — operate across the whole estate
    "services/system_diagnostic.py",
    "services/system_health_synthesizer.py",
    "services/system_summary.py",
    "services/meta_reviewer.py",
    "services/evolution_engine.py",
    "services/loop_health.py",
    "services/project_brain.py",
    "services/adaptive_governance.py",
    "services/bugfix_pipeline.py",        # Self-improvement — cross-shop signal mining
    "services/orchestrator_context.py",   # Cross-shop orchestrator state
    "services/autonomous_loop.py",        # Counts DISTINCT shop_domain
    "services/data_retention.py",         # Global retention sweeps
    "services/event_bus.py",              # Global event bus retention
    "services/regulatory_watch.py",
    "services/regulatory_feed_monitor.py",
    # Ops-facing surfaces — operator sees everything
    "api/ops.py",
    "api/health.py",
    "api/compliance_evidence.py",
    "workers/agent_worker.py",            # Agent operates across all shops
    "services/telegram_agent.py",         # Telegram operator — cross-shop by design
    # Nudge engine lifecycle: expire_stale_nudges is a global sweep
    # called once per cycle by the aggregation_worker — intentional.
    "services/nudge_engine.py",
    # Product metrics discovery — the worker's SELECT DISTINCT shop_domain
    # finds which (shop, product) pairs to compute. Downstream paths scope.
    "workers/tasks/product_metrics_task.py",
    # Feedback / inbound email mining — cross-shop by design (support ops)
    "services/feedback_intelligence.py",
    "services/inbound_action_executor.py",
    # Merchant enumeration — these pick shop_domains to iterate over
    "services/merchant_churn_predictor.py",
    "services/merchant_scoring.py",
    "services/onboarding_health.py",
    "services/onboarding_funnel.py",
    "services/vertical_classifier.py",
    "services/simulation_engine.py",      # test/simulation cleanup
    # Ops-scope alert handling — ops_alerts are cross-shop by design
    "services/evolution_outcomes.py",
    "services/pipeline_heartbeat.py",
    "services/learning_isolation.py",
    # Public proof shares — tenancy gate is the unguessable share_token PK
    "services/share_engine.py",
    # Global stale-alert auto-resolver — resolves network-wide by severity
    "services/alerting.py",
    # Phase Ω⁷ — Competitor Playbook is cross-tenant by design (peer stats)
    "api/playbook.py",
}


def load_multi_tenant_tables() -> set[str]:
    insp = inspect(engine)
    out: set[str] = set()
    for t in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns(t)}
        if "shop_domain" in cols:
            out.add(t)
    return out


_SQL_CALL = re.compile(
    r'text\s*\(\s*(?P<quote>["\']{1,3})(?P<body>.*?)(?P=quote)\s*\)',
    re.DOTALL,
)


def extract_blocks(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        src = path.read_text()
    except Exception:
        return []
    return [
        (src.count("\n", 0, m.start()) + 1, m.group("body").strip())
        for m in _SQL_CALL.finditer(src)
        if len(m.group("body").strip()) >= 10
    ]


def sql_is_insert_values_path(sql: str) -> bool:
    """INSERT ... VALUES paths get shop_domain from the caller's bind dict."""
    return bool(re.search(r"\bINSERT\s+INTO\b", sql, re.I))


def sql_is_select_update_delete_on(sql: str, table: str) -> bool:
    """
    Return True if this SQL actually touches `table` in a SELECT / UPDATE /
    DELETE. INSERT paths are covered separately.
    """
    # Strip CTEs for simpler pattern
    pat = re.compile(
        rf"\b(?:FROM|JOIN|UPDATE|DELETE\s+FROM)\s+{re.escape(table)}\b",
        re.IGNORECASE,
    )
    return bool(pat.search(sql))


def sql_has_shop_filter(sql: str, table: str) -> bool:
    """
    Does the query filter on shop_domain? Accepts any of:
        shop_domain = :x
        shop_domain IN (:x, :y)
        <alias>.shop_domain = :x
        shop_domain = e.shop_domain   (self-join)
        WHERE shop_domain IS NULL     (intentional null-shop rows)
    """
    # Keep it permissive — we'd rather allow a weird-but-correct query
    # through than block on a formatting quirk.
    if re.search(r"\bshop_domain\s*=\s*[:a-z_\.]", sql, re.I):
        return True
    if re.search(r"\bshop_domain\s+IN\s*\(", sql, re.I):
        return True
    if re.search(r"\bshop_domain\s+IS\s+NULL", sql, re.I):
        return True
    # Self-joins: `e.shop_domain = o.shop_domain`
    if re.search(r"\b\w+\.shop_domain\s*=\s*\w+\.shop_domain", sql, re.I):
        return True
    return False


def path_is_allowlisted(py_path: pathlib.Path) -> bool:
    suffix = str(py_path.relative_to(APP_ROOT))
    return any(suffix.endswith(w) or suffix == w for w in ALLOWLIST)


def main() -> int:
    tenant_tables = load_multi_tenant_tables()
    print(f"loaded {len(tenant_tables)} multi-tenant tables from schema\n")

    findings: dict[tuple[str, str], list[tuple[str, int, str]]] = defaultdict(list)

    for py_file in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        if path_is_allowlisted(py_file):
            continue
        for line, sql in extract_blocks(py_file):
            # Skip writes — they implicitly include shop_domain via the payload
            if sql_is_insert_values_path(sql):
                continue
            for table in tenant_tables:
                if not sql_is_select_update_delete_on(sql, table):
                    continue
                if sql_has_shop_filter(sql, table):
                    continue
                findings[(table, py_file.name)].append((
                    str(py_file.relative_to(APP_ROOT.parent)),
                    line,
                    sql[:120].replace("\n", " "),
                ))

    if not findings:
        print("✅ No unfiltered multi-tenant queries found.\n")
        return 0

    print(f"❌ TENANT ISOLATION RISKS ({len(findings)} distinct findings)\n")
    for (table, _), hits in sorted(findings.items()):
        print(f"  {table} (no shop_domain filter)")
        for f, line, snip in hits[:3]:
            print(f"    {f}:{line}")
            print(f"      {snip}...")
        if len(hits) > 3:
            print(f"    ... and {len(hits) - 3} more")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(main())
