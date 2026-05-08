"""
soc2_controls.py — Phase Ω''' SOC 2 Type II controls catalog.

Maps the SOC 2 Trust Services Criteria (TSC) to actual artefacts in
the HedgeSpark codebase + database. Each control has:

  * id            — TSC reference (CC1.1, CC2.2, ...)
  * category      — Common Criteria area
  * description   — what the control attests
  * evidence      — function name(s) that produce live evidence
  * status        — implemented | partial | not_started
  * artefact_path — file(s) where the control logic lives

The output is consumed by /pro/compliance/soc2 and surfaces in the
compliance evidence bundle. Every field is auditable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SOC2Control:
    id: str
    category: str
    description: str
    status: str  # "implemented" | "partial" | "not_started"
    evidence: list[str] = field(default_factory=list)
    artefact_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Catalog — Trust Services Criteria mapped to live HedgeSpark artefacts
# ---------------------------------------------------------------------------

CATALOG: list[SOC2Control] = [
    # --- Common Criteria — Control Environment ---
    SOC2Control(
        id="CC1.1",
        category="Control Environment",
        description="Demonstrates commitment to integrity and ethical values",
        status="implemented",
        evidence=["audit_log_chain_integrity"],
        artefact_paths=["app/services/audit.py", "tests/test_audit_log_hash_chain.py"],
    ),
    SOC2Control(
        id="CC1.4",
        category="Control Environment",
        description="Engineering competence — pre-commit preflight gates + pytest suite + commit-msg hooks enforce review on every change",
        status="implemented",
        evidence=["pytest_suite_2400_tests", "preflight_gate_chain"],
        artefact_paths=[
            "backend/scripts/preflight.sh",
            "backend/scripts/install_hooks.sh",
            "backend/tests/",
        ],
    ),

    # --- Common Criteria — Communication & Information ---
    SOC2Control(
        id="CC2.1",
        category="Communication & Information",
        description="Internal communication of security policies via audit logging",
        status="implemented",
        evidence=["audit_log_summary"],
        artefact_paths=["app/services/audit.py", "app/models/audit_log.py"],
    ),
    SOC2Control(
        id="CC2.2",
        category="Communication & Information",
        description="External communication via privacy + cookie policies + status page",
        status="implemented",
        evidence=["public_status_endpoint", "legal_pages_endpoint"],
        artefact_paths=[
            "app/api/legal_pages.py",
            "app/api/public_status.py",
            "dashboard/src/app/privacy/page.tsx",
            "dashboard/src/app/status/page.tsx",
        ],
    ),

    # --- Risk Assessment ---
    SOC2Control(
        id="CC3.1",
        category="Risk Assessment",
        description="Risk identification — anomaly fusion + causal explainer",
        status="implemented",
        evidence=["anomaly_fusion_alerts", "causal_explainer_hypotheses"],
        artefact_paths=[
            "app/services/anomaly_fusion.py",
            "app/services/causal_explainer.py",
        ],
    ),
    SOC2Control(
        id="CC3.4",
        category="Risk Assessment",
        description="Identification of changes — every commit captured in audit_log with hash chain integrity verification",
        status="implemented",
        evidence=["audit_log_chain_integrity", "preflight_change_classifier"],
        artefact_paths=[
            "app/services/audit.py",
            "backend/scripts/classify_commit_tier.py",
        ],
    ),

    # --- Monitoring Activities ---
    SOC2Control(
        id="CC4.1",
        category="Monitoring",
        description="Synthetic security heartbeat — ongoing security probes",
        status="implemented",
        evidence=["security_probes"],
        artefact_paths=["app/services/security_heartbeat.py"],
    ),
    SOC2Control(
        id="CC4.2",
        category="Monitoring",
        description="Continuous monitoring — invariant audits + alerting fleet detect deviations; operator + Brain Vero remediate",
        status="implemented",
        evidence=["invariant_audit_pass_rate", "ops_alerts_24h_count"],
        artefact_paths=[
            "app/services/invariant_monitor.py",
            "app/services/alerting.py",
            "app/services/system_health_synthesizer.py",
            "app/services/merchant_brain.py",
        ],
    ),

    # --- Control Activities ---
    SOC2Control(
        id="CC5.1",
        category="Control Activities",
        description="Tier-based execution policy — CLAUDE.md §10 defines TIER_0/1/2; commit-msg hooks enforce TIER_1 disclosure + TIER_2 fresh approval",
        status="implemented",
        evidence=["tier_policy_documented", "tier1_disclosure_audit"],
        artefact_paths=[
            "CLAUDE.md",
            "backend/scripts/audit_tier1_change_surfaced.py",
            "backend/scripts/audit_lateral_change_evidence.py",
        ],
    ),

    # --- Logical Access ---
    SOC2Control(
        id="CC6.1",
        category="Logical Access",
        description="Authentication — Shopify OAuth + signed merchant session JWT",
        status="implemented",
        evidence=["session_jwt_signing", "oauth_handshake"],
        artefact_paths=["app/core/merchant_session.py", "app/api/shopify_oauth.py"],
    ),
    SOC2Control(
        id="CC6.2",
        category="Logical Access",
        description="Token encryption at rest — AES-GCM with rotated keys",
        status="implemented",
        evidence=["token_crypto_in_use"],
        artefact_paths=["app/core/token_crypto.py"],
    ),
    SOC2Control(
        id="CC6.6",
        category="Logical Access",
        description="HMAC verification on every Shopify webhook",
        status="implemented",
        evidence=["webhook_hmac_validation"],
        artefact_paths=["app/api/webhooks.py", "app/api/shopify_refunds.py"],
    ),
    SOC2Control(
        id="CC6.7",
        category="Logical Access",
        description="LLM PII runtime guard prevents PII leakage to third-party AI",
        status="implemented",
        evidence=["llm_pii_events"],
        artefact_paths=["app/core/llm_pii_guard.py"],
    ),
    SOC2Control(
        id="CC6.8",
        category="Logical Access",
        description="CORS strict allowlist + CSRF guard middleware",
        status="implemented",
        evidence=["cors_allowlist", "csrf_guard_middleware"],
        artefact_paths=["app/main.py"],
    ),

    # --- System Operations ---
    SOC2Control(
        id="CC7.1",
        category="System Operations",
        description="Vulnerability identification — Sentry triage pipeline + LLM PII guard + Cloudflare CDN front + 90+ static-analysis audits in preflight",
        status="implemented",
        evidence=["sentry_incident_throughput", "preflight_audit_pass_rate"],
        artefact_paths=[
            "app/services/sentry_triage.py",
            "app/core/llm_pii_guard.py",
            "backend/scripts/preflight.sh",
        ],
    ),
    SOC2Control(
        id="CC7.2",
        category="System Operations",
        description="Anomaly detection + alerting with deduplication and external delivery",
        status="implemented",
        evidence=["ops_alerts_24h_count"],
        artefact_paths=["app/services/alerting.py", "app/core/alert_delivery.py"],
    ),
    SOC2Control(
        id="CC7.3",
        category="System Operations",
        description="Incident response — breach classifier + GDPR Art. 33/34 deadline tracking",
        status="implemented",
        evidence=["breach_events_summary"],
        artefact_paths=["app/services/breach_notification.py"],
    ),
    SOC2Control(
        id="CC7.4",
        category="System Operations",
        description="Recovery — post-commit auto-deploy with health verification; git revert as rollback path; Brain Vero per-merchant decision audit trail",
        status="implemented",
        evidence=["post_commit_deploy_log", "brain_decision_audit"],
        artefact_paths=[
            "backend/scripts/post_commit_auto_deploy.sh",
            "app/services/merchant_brain.py",
            "app/services/audit.py",
        ],
    ),

    # --- Change Management ---
    SOC2Control(
        id="CC8.1",
        category="Change Management",
        description="Change tracking — every commit passes preflight (90+ static audits + full pytest reflex) and writes to audit_log via post-commit hook",
        status="implemented",
        evidence=["preflight_gate_pass", "audit_log_summary"],
        artefact_paths=[
            "backend/scripts/preflight.sh",
            "app/services/audit.py",
            "backend/scripts/post_commit_auto_deploy.sh",
        ],
    ),

    # --- Risk Mitigation ---
    SOC2Control(
        id="CC9.1",
        category="Risk Mitigation",
        description="Trust contract bounds — merchant pre-approves risk parameters before autonomous execution",
        status="implemented",
        evidence=["trust_autonomy"],
        artefact_paths=[
            "app/services/trust_contract.py",
            "app/models/trust_contract.py",
        ],
    ),
    SOC2Control(
        id="CC9.2",
        category="Risk Mitigation",
        description="Vendor management — Sentry + Resend + Klaviyo + Shopify dependency tracking",
        status="partial",
        evidence=[],
        artefact_paths=["SERVER_CONTEXT.md"],
    ),

    # --- Confidentiality ---
    SOC2Control(
        id="C1.1",
        category="Confidentiality",
        description="Data classification — PII fields tagged + encryption at rest for sensitive tables",
        status="implemented",
        evidence=["token_crypto_in_use"],
        artefact_paths=["app/core/token_crypto.py"],
    ),
    SOC2Control(
        id="C1.2",
        category="Confidentiality",
        description="Data destruction on customer/shop redact requests",
        status="implemented",
        evidence=["gdpr_activity"],
        artefact_paths=[
            "app/services/gdpr_processor.py",
            "app/services/uninstall_erasure.py",
        ],
    ),

    # --- Availability ---
    SOC2Control(
        id="A1.1",
        category="Availability",
        description="Capacity monitoring — worker watchdog + system health synthesizer",
        status="implemented",
        evidence=["public_status_components"],
        artefact_paths=[
            "app/services/worker_watchdog.py",
            "app/services/system_health_synthesizer.py",
            "app/api/public_status.py",
        ],
    ),
    SOC2Control(
        id="A1.2",
        category="Availability",
        description="Backup and recovery — Postgres point-in-time recovery via managed db provider",
        status="partial",
        evidence=[],
        artefact_paths=[],
    ),

    # --- Processing Integrity ---
    SOC2Control(
        id="PI1.1",
        category="Processing Integrity",
        description="Audit log hash chain — tamper-evident transaction history",
        status="implemented",
        evidence=["audit_log_chain_integrity"],
        artefact_paths=[
            "app/services/audit.py",
            "tests/test_audit_log_hash_chain.py",
        ],
    ),
    SOC2Control(
        id="PI1.4",
        category="Processing Integrity",
        description="Output validation — preflight validator on every code change before merge",
        status="implemented",
        evidence=["preflight_validator_runs"],
        artefact_paths=["backend/scripts/preflight.sh"],
    ),

    # --- Privacy ---
    SOC2Control(
        id="P1.1",
        category="Privacy",
        description="Notice — privacy policy + cookie banner published",
        status="implemented",
        evidence=[],
        artefact_paths=[
            "app/api/legal_pages.py",
            "dashboard/src/app/privacy/page.tsx",
        ],
    ),
    SOC2Control(
        id="P3.1",
        category="Privacy",
        description="Choice and consent — GPC/CCPA + Art. 16/21 GDPR endpoints",
        status="implemented",
        evidence=[],
        artefact_paths=[
            "app/api/merchant_privacy.py",
            "app/services/regulatory_watch.py",
        ],
    ),
    SOC2Control(
        id="P4.2",
        category="Privacy",
        description="Use, retention, disposal — data retention policy + GDPR worker",
        status="implemented",
        evidence=["gdpr_activity"],
        artefact_paths=[
            "app/services/data_retention.py",
            "app/workers/gdpr_worker.py",
        ],
    ),
]


def get_catalog() -> list[dict]:
    return [c.to_dict() for c in CATALOG]


def summarize_catalog() -> dict:
    """Aggregate counts by status and category."""
    by_status: dict[str, int] = {}
    by_category: dict[str, dict] = {}
    for c in CATALOG:
        by_status[c.status] = by_status.get(c.status, 0) + 1
        cat = by_category.setdefault(c.category, {"total": 0, "implemented": 0, "partial": 0, "not_started": 0})
        cat["total"] += 1
        cat[c.status] = cat.get(c.status, 0) + 1

    total = len(CATALOG)
    implemented = by_status.get("implemented", 0)
    partial = by_status.get("partial", 0)
    coverage_pct = round(((implemented + partial * 0.5) / total) * 100, 1) if total else 0.0

    return {
        "total_controls": total,
        "by_status": by_status,
        "by_category": by_category,
        "coverage_pct": coverage_pct,
        "ready_for_audit": coverage_pct >= 80.0,
    }
