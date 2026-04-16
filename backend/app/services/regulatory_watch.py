"""
regulatory_watch.py — Self-updating worldwide compliance pipeline.

This service maintains a structured registry of privacy/security
regulations and continuously audits HedgeSpark's live behavior against
each rule. When a gap is detected, it emits ops_alerts that enter the
same triage → bugfix → deploy pipeline as any other system issue.

How it enters the self-debugging pipeline:
──────────────────────────────────────────
1. Each regulation has a list of RULES — deterministic checks that run
   against the live codebase, Redis state, and DB state.
2. Every agent_worker cycle, `run_regulatory_audit(db)` walks every
   enabled regulation and every rule within it.
3. A failing rule emits a `compliance_gap` ops_alert with:
   - The regulation reference (e.g. "GDPR Art. 17")
   - The gap description
   - A suggested fix category (code_change / config_change / manual)
4. `compliance_gap` alerts with `suggested_action=code_change` are
   picked up by the bugfix pipeline's triage phase (same as any
   `alert_type` in the scan window) → the pipeline proposes a patch
   → the security preflight guard validates → auto-apply or TIER_1.
5. The compliance score now includes `audit_log_integrity`,
   `breach_response_latency`, `llm_pii_guard_health`, and
   `telegram_webhook_security` — any regression from regulatory
   watch findings is reflected in the score and can auto-pause
   the pipeline.

How it self-updates:
────────────────────
The registry is a Python data structure (not a DB table) so the
monthly_evolution_audit or a manual code change can add/update rules.
Each rule has a `version` field — bumping the version re-runs the
check even if it previously passed.

The bugfix_pipeline already knows how to:
- Scan ops_alerts for triage-worthy events
- Generate LLM-proposed patches
- Validate them through the security preflight guard
- Auto-apply (TIER_0) or escalate (TIER_1/TIER_2)

By emitting standardized ops_alerts, regulatory_watch plugs into
all of this without any coupling to the bugfix code.

No LLM calls. No external API calls. Pure deterministic checks.
"""
from __future__ import annotations

import importlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

log = logging.getLogger("regulatory_watch")

# Derive backend root dynamically so regulatory checks work in CI (checked-out
# repo) and on production (/opt/wishspark/backend/).
_BACKEND_DIR = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent.parent.parent)) / "backend"

_AUDIT_COOLDOWN_KEY = "hs:regulatory_watch:last_run"
_AUDIT_COOLDOWN_S = 6 * 3600  # run at most every 6 hours


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("regulatory_watch: _redis failed: %s", exc)
        return None


# -----------------------------------------------------------------------
# Rule definition
# -----------------------------------------------------------------------

class RegRule:
    """A single compliance check within a regulation."""

    __slots__ = (
        "rule_id", "regulation", "article", "description",
        "check_fn", "suggested_action", "version", "enabled",
    )

    def __init__(
        self,
        *,
        rule_id: str,
        regulation: str,
        article: str,
        description: str,
        check_fn: Callable[[Session], bool],
        suggested_action: str = "code_change",
        version: int = 1,
        enabled: bool = True,
    ):
        self.rule_id = rule_id
        self.regulation = regulation
        self.article = article
        self.description = description
        self.check_fn = check_fn
        self.suggested_action = suggested_action
        self.version = version
        self.enabled = enabled


# -----------------------------------------------------------------------
# Check functions — each returns True if compliant, False if gap
# -----------------------------------------------------------------------

def _check_consent_gate_exists(db: Session) -> bool:
    """GDPR Art. 6/7 + ePrivacy: /track endpoint must check consent."""
    try:
        import ast
        with open(_BACKEND_DIR / "app" / "api" / "track.py", "r") as f:
            src = f.read()
        return "_consent_allows_ingestion" in src
    except Exception as exc:
        log.warning("regulatory_watch: _check_consent_gate_exists failed: %s", exc)
        return False


def _check_data_retention_active(db: Session) -> bool:
    """GDPR Art. 5(1)(e): retention sweep module must exist and be
    functional. The runtime 48h freshness check is handled by the
    compliance_score `retention_sweep` component — this rule only
    verifies the capability exists."""
    try:
        from app.services.data_retention import run_retention_sweep  # noqa: F401
        return True
    except ImportError:
        return False


def _check_erasure_endpoint_exists(db: Session) -> bool:
    """GDPR Art. 17: GDPR processor endpoint must exist."""
    try:
        from app.services.gdpr_processor import process_gdpr_request  # noqa: F401
        return True
    except ImportError:
        return False


def _check_export_endpoint_exists(db: Session) -> bool:
    """GDPR Art. 15/20: merchant self-serve export must exist."""
    try:
        from app.api.merchant_export import router  # noqa: F401
        return True
    except ImportError:
        return False


def _check_rectify_endpoint_exists(db: Session) -> bool:
    """GDPR Art. 16: rectification endpoint must exist."""
    try:
        from app.api.merchant_privacy import router  # noqa: F401
        return True
    except ImportError:
        return False


def _check_object_endpoint_exists(db: Session) -> bool:
    """GDPR Art. 21 / CCPA §1798.120: opt-out endpoint must exist."""
    try:
        from app.services.merchant_privacy import is_merchant_opted_out  # noqa: F401
        return True
    except ImportError:
        return False


def _check_breach_classifier_active(db: Session) -> bool:
    """GDPR Art. 33/34: breach classifier must be importable."""
    try:
        from app.services.breach_notification import process_breach_candidates  # noqa: F401
        return True
    except ImportError:
        return False


def _check_audit_log_hash_chain(db: Session) -> bool:
    """Audit log must use hash chain integrity."""
    try:
        from app.services.audit import verify_audit_log_chain  # noqa: F401
        return True
    except ImportError:
        return False


def _check_pii_guard_active(db: Session) -> bool:
    """No raw PII to LLMs — runtime guard must be importable."""
    try:
        from app.core.llm_pii_guard import check_for_pii  # noqa: F401
        return True
    except ImportError:
        return False


def _check_gpc_honored(db: Session) -> bool:
    """CCPA/CPRA: Global Privacy Control must be checked in /track."""
    try:
        with open(_BACKEND_DIR / "app" / "api" / "track.py", "r") as f:
            src = f.read()
        return "sec-gpc" in src.lower() or "globalPrivacyControl" in src.lower()
    except Exception as exc:
        log.warning("regulatory_watch: _check_gpc_honored failed: %s", exc)
        return False


def _check_security_headers(db: Session) -> bool:
    """OWASP: CSP + HSTS + X-Frame-Options must be in main.py."""
    try:
        with open(_BACKEND_DIR / "app" / "main.py", "r") as f:
            src = f.read()
        return all(h in src for h in [
            "Content-Security-Policy",
            "Strict-Transport-Security",
            "X-Frame-Options",
        ])
    except Exception as exc:
        log.warning("regulatory_watch: _check_security_headers failed: %s", exc)
        return False


def _check_token_encryption(db: Session) -> bool:
    """Merchant tokens must be encrypted at rest."""
    try:
        from app.core.token_crypto import encrypt_token, decrypt_token  # noqa: F401
        return True
    except ImportError:
        return False


def _check_webhook_hmac(db: Session) -> bool:
    """Shopify webhooks must verify HMAC signature."""
    try:
        with open(_BACKEND_DIR / "app" / "api" / "webhooks.py", "r") as f:
            src = f.read()
        return "hmac" in src.lower() and "verify" in src.lower()
    except Exception as exc:
        log.warning("regulatory_watch: _check_webhook_hmac failed: %s", exc)
        return False


def _check_uninstall_erasure_watchdog(db: Session) -> bool:
    """GDPR Art. 17: uninstall erasure watchdog must exist."""
    try:
        from app.services.uninstall_erasure import run_uninstall_erasure_watchdog  # noqa: F401
        return True
    except ImportError:
        return False


def _check_privacy_policy_endpoint(db: Session) -> bool:
    """Every jurisdiction requires a privacy policy."""
    try:
        from app.api.legal_pages import router  # noqa: F401
        return True
    except ImportError:
        return False


def _check_cookie_policy_endpoint(db: Session) -> bool:
    """ePrivacy Directive: cookie policy must be accessible."""
    try:
        from app.api.legal_pages import cookie_policy_json  # noqa: F401
        return True
    except ImportError:
        return False


def _check_consent_banner_available(db: Session) -> bool:
    """Merchants need a consent banner integration."""
    try:
        from app.api.consent_banner import router  # noqa: F401
        return True
    except ImportError:
        return False


def _check_telegram_webhook_signing(db: Session) -> bool:
    """Telegram webhook must verify signatures."""
    try:
        from app.api.telegram_webhook import _verify_telegram_signature  # noqa: F401
        return True
    except ImportError:
        return False


def _check_oauth_state_enforced(db: Session) -> bool:
    """OAuth callback must enforce state/nonce parameter."""
    try:
        with open(_BACKEND_DIR / "app" / "api" / "shopify_oauth.py", "r") as f:
            src = f.read()
        # The fix was: "if not state or not _consume_nonce"
        return "if not state or" in src or "if not state " in src
    except Exception as exc:
        log.warning("regulatory_watch: _check_oauth_state_enforced failed: %s", exc)
        return False


def _check_timing_safe_api_key(db: Session) -> bool:
    """X-API-Key comparison must be timing-safe."""
    try:
        with open(_BACKEND_DIR / "app" / "core" / "deps.py", "r") as f:
            src = f.read()
        return "hmac.compare_digest" in src
    except Exception as exc:
        log.warning("regulatory_watch: _check_timing_safe_api_key failed: %s", exc)
        return False


def _check_gdpr_sla_enforcement(db: Session) -> bool:
    """GDPR SLA deadlines must be enforced."""
    try:
        from app.services.gdpr_sla import enforce_sla  # noqa: F401
        return True
    except ImportError:
        return False


def _check_security_heartbeat_active(db: Session) -> bool:
    """Security self-attack probes must be active."""
    try:
        from app.services.security_heartbeat import run_security_heartbeat  # noqa: F401
        return True
    except ImportError:
        return False


def _check_compliance_score_active(db: Session) -> bool:
    """Compliance score must be computable."""
    try:
        from app.services.compliance_score import compute_compliance_score  # noqa: F401
        return True
    except ImportError:
        return False


def _check_security_preflight_guard(db: Session) -> bool:
    """Self-debugging pipeline must have security preflight guard."""
    try:
        from app.services.security_preflight_guard import guard_candidate  # noqa: F401
        return True
    except ImportError:
        return False


# -----------------------------------------------------------------------
# The Registry
# -----------------------------------------------------------------------

REGULATORY_RULES: list[RegRule] = [
    # GDPR — core data-subject rights
    RegRule(
        rule_id="GDPR-6-7-consent",
        regulation="GDPR",
        article="Art. 6/7",
        description="Consent gate on /track endpoint",
        check_fn=_check_consent_gate_exists,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-5-1e-retention",
        regulation="GDPR",
        article="Art. 5(1)(e)",
        description="Automated data retention sweep (395d events, 730d sessions)",
        check_fn=_check_data_retention_active,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-15-20-export",
        regulation="GDPR",
        article="Art. 15/20",
        description="Merchant self-serve data export endpoint",
        check_fn=_check_export_endpoint_exists,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-16-rectify",
        regulation="GDPR",
        article="Art. 16",
        description="Right to rectification endpoint",
        check_fn=_check_rectify_endpoint_exists,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-17-erasure",
        regulation="GDPR",
        article="Art. 17",
        description="GDPR erasure processor endpoint",
        check_fn=_check_erasure_endpoint_exists,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-17-uninstall-watchdog",
        regulation="GDPR",
        article="Art. 17",
        description="Uninstall erasure watchdog (belt-and-braces for lost webhooks)",
        check_fn=_check_uninstall_erasure_watchdog,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-21-object",
        regulation="GDPR",
        article="Art. 21",
        description="Right to object / opt-out endpoint",
        check_fn=_check_object_endpoint_exists,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-33-34-breach",
        regulation="GDPR",
        article="Art. 33/34",
        description="Automated breach classifier with 72h deadline",
        check_fn=_check_breach_classifier_active,
        version=1,
    ),
    RegRule(
        rule_id="GDPR-SLA-enforcement",
        regulation="GDPR",
        article="Art. 12",
        description="GDPR SLA deadline enforcement on all request types",
        check_fn=_check_gdpr_sla_enforcement,
        version=1,
    ),

    # CCPA / CPRA
    RegRule(
        rule_id="CCPA-GPC",
        regulation="CCPA/CPRA",
        article="§1798.135(e)",
        description="Global Privacy Control (GPC) signal honored in /track",
        check_fn=_check_gpc_honored,
        version=1,
    ),

    # Security — OWASP / ISO 27001
    RegRule(
        rule_id="SEC-headers",
        regulation="OWASP",
        article="Security Headers",
        description="CSP + HSTS + X-Frame-Options on all responses",
        check_fn=_check_security_headers,
        version=1,
    ),
    RegRule(
        rule_id="SEC-token-encryption",
        regulation="OWASP",
        article="Sensitive Data",
        description="Merchant Shopify tokens encrypted at rest",
        check_fn=_check_token_encryption,
        version=1,
    ),
    RegRule(
        rule_id="SEC-webhook-hmac",
        regulation="Shopify",
        article="Webhook Security",
        description="Shopify webhook HMAC verification",
        check_fn=_check_webhook_hmac,
        version=1,
    ),
    RegRule(
        rule_id="SEC-telegram-signing",
        regulation="Security",
        article="Webhook Auth",
        description="Telegram webhook signature verification",
        check_fn=_check_telegram_webhook_signing,
        version=1,
    ),
    RegRule(
        rule_id="SEC-oauth-state",
        regulation="OWASP",
        article="CSRF",
        description="OAuth callback enforces state/nonce parameter",
        check_fn=_check_oauth_state_enforced,
        version=1,
    ),
    RegRule(
        rule_id="SEC-timing-safe",
        regulation="OWASP",
        article="Side Channel",
        description="API key comparison is timing-safe (hmac.compare_digest)",
        check_fn=_check_timing_safe_api_key,
        version=1,
    ),

    # Self-healing / self-debugging pipeline integrity
    RegRule(
        rule_id="PIPELINE-pii-guard",
        regulation="GDPR",
        article="Art. 32",
        description="LLM PII runtime guard prevents data egress",
        check_fn=_check_pii_guard_active,
        version=1,
    ),
    RegRule(
        rule_id="PIPELINE-audit-chain",
        regulation="ISO 27001",
        article="A.12.4",
        description="Audit log hash-chain integrity verification",
        check_fn=_check_audit_log_hash_chain,
        version=1,
    ),
    RegRule(
        rule_id="PIPELINE-security-heartbeat",
        regulation="Security",
        article="Continuous Testing",
        description="Hourly self-attack probes on security surface",
        check_fn=_check_security_heartbeat_active,
        version=1,
    ),
    RegRule(
        rule_id="PIPELINE-compliance-score",
        regulation="Governance",
        article="Auto-pause",
        description="Compliance score auto-pauses self-modification below threshold",
        check_fn=_check_compliance_score_active,
        version=1,
    ),
    RegRule(
        rule_id="PIPELINE-preflight-guard",
        regulation="Governance",
        article="Security Guard",
        description="Security preflight guard blocks regressions in self-debugging",
        check_fn=_check_security_preflight_guard,
        version=1,
    ),

    # Legal pages
    RegRule(
        rule_id="LEGAL-privacy-policy",
        regulation="GDPR/CCPA/LGPD",
        article="Transparency",
        description="Privacy policy endpoint accessible",
        check_fn=_check_privacy_policy_endpoint,
        version=1,
    ),
    RegRule(
        rule_id="LEGAL-cookie-policy",
        regulation="ePrivacy",
        article="Cookie Directive",
        description="Cookie policy endpoint accessible",
        check_fn=_check_cookie_policy_endpoint,
        version=1,
    ),
    RegRule(
        rule_id="LEGAL-consent-banner",
        regulation="ePrivacy",
        article="Consent",
        description="Consent banner script available for merchants",
        check_fn=_check_consent_banner_available,
        version=1,
    ),
]

# Index for fast lookup
_RULES_BY_ID: dict[str, RegRule] = {r.rule_id: r for r in REGULATORY_RULES}


# -----------------------------------------------------------------------
# Audit runner
# -----------------------------------------------------------------------

def run_regulatory_audit(db: Session) -> dict[str, Any]:
    """Walk every enabled rule in the registry. Emit ops_alerts for
    failures. Return a summary report for the daily digest.

    Dedup: a `compliance_gap` alert per rule_id is deduped by checking
    for an existing unresolved alert with matching source.

    Self-healing: when a previously-failing rule now passes, the
    existing alert is auto-resolved — no manual operator intervention.
    """
    rc = _redis()
    if rc is not None:
        try:
            last = rc.get(_AUDIT_COOLDOWN_KEY)
            if last:
                last_ts = float(last.decode() if isinstance(last, bytes) else last)
                if (_now().timestamp() - last_ts) < _AUDIT_COOLDOWN_S:
                    return {"skipped": True, "reason": "cooldown"}
        except Exception as exc:
            log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)

    from app.models.ops_alert import OpsAlert
    from app.services.audit import write_audit_log

    report: dict[str, Any] = {
        "ran_at": _now().isoformat(),
        "total_rules": 0,
        "passed": 0,
        "failed": 0,
        "new_alerts": 0,
        "auto_resolved": 0,
        "gaps": [],
    }

    for rule in REGULATORY_RULES:
        if not rule.enabled:
            continue
        report["total_rules"] += 1

        try:
            compliant = rule.check_fn(db)
        except Exception as exc:
            log.warning(
                "regulatory_watch: rule %s check raised: %s",
                rule.rule_id, exc,
            )
            compliant = False

        source_tag = f"regulatory:{rule.rule_id}:v{rule.version}"

        if compliant:
            report["passed"] += 1
            # Auto-resolve any existing gap alert for this rule
            try:
                existing = (
                    db.query(OpsAlert)
                    .filter(
                        OpsAlert.alert_type == "compliance_gap",
                        OpsAlert.source == source_tag,
                        OpsAlert.resolved == False,  # noqa: E712
                    )
                    .first()
                )
                if existing is not None:
                    existing.resolved = True
                    db.flush()
                    report["auto_resolved"] += 1
                    log.info(
                        "regulatory_watch: auto-resolved %s (now compliant)",
                        rule.rule_id,
                    )
            except Exception as exc:
                log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)
            continue

        # Failed
        report["failed"] += 1
        report["gaps"].append({
            "rule_id": rule.rule_id,
            "regulation": rule.regulation,
            "article": rule.article,
            "description": rule.description,
            "suggested_action": rule.suggested_action,
        })

        # Dedup check — don't double-alert
        try:
            existing = (
                db.query(OpsAlert)
                .filter(
                    OpsAlert.alert_type == "compliance_gap",
                    OpsAlert.source == source_tag,
                    OpsAlert.resolved == False,  # noqa: E712
                )
                .first()
            )
            if existing is not None:
                continue  # already alerted
        except Exception as exc:
            log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)
            continue

        # Emit new alert
        try:
            alert = OpsAlert(
                severity="warning" if rule.suggested_action == "manual" else "critical",
                source=source_tag,
                alert_type="compliance_gap",
                shop_domain=None,
                summary=(
                    f"[{rule.regulation} {rule.article}] {rule.description} — "
                    f"COMPLIANCE GAP (action: {rule.suggested_action})"
                ),
                detail=(
                    f"Regulatory rule {rule.rule_id} (v{rule.version}) failed.\n"
                    f"Regulation: {rule.regulation} {rule.article}\n"
                    f"Gap: {rule.description}\n"
                    f"Suggested action: {rule.suggested_action}\n\n"
                    f"This alert was auto-generated by the regulatory watch "
                    f"pipeline. If the gap is a code_change, the bugfix "
                    f"pipeline will attempt to propose a patch."
                ),
                resolved=False,
            )
            db.add(alert)
            db.flush()
            report["new_alerts"] += 1

            write_audit_log(
                db,
                actor_type="system",
                actor_name="regulatory_watch",
                action_type="compliance_gap_detected",
                target_type="regulation",
                target_id=rule.rule_id,
                status="completed",
                metadata={
                    "regulation": rule.regulation,
                    "article": rule.article,
                    "description": rule.description,
                    "suggested_action": rule.suggested_action,
                    "version": rule.version,
                },
            )
        except Exception as exc:
            log.warning(
                "regulatory_watch: alert write for %s failed: %s",
                rule.rule_id, exc,
            )
            try:
                db.rollback()
            except Exception as exc:
                log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)

    if report["new_alerts"] > 0 or report["auto_resolved"] > 0:
        try:
            db.commit()
        except Exception as exc:
            log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)
            try:
                db.rollback()
            except Exception as exc:
                log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)

    if report["failed"] > 0:
        log.warning(
            "regulatory_watch: %d/%d rules failed — %d new alerts, %d auto-resolved",
            report["failed"], report["total_rules"],
            report["new_alerts"], report["auto_resolved"],
        )

    # Update cooldown
    if rc is not None:
        try:
            rc.setex(_AUDIT_COOLDOWN_KEY, _AUDIT_COOLDOWN_S, str(_now().timestamp()))
        except Exception as exc:
            log.warning("regulatory_watch: run_regulatory_audit failed: %s", exc)

    return report


def get_regulatory_summary() -> dict[str, Any]:
    """Return a static summary of the regulatory registry for the
    daily digest — no DB or Redis needed."""
    by_regulation: dict[str, int] = {}
    for r in REGULATORY_RULES:
        if not r.enabled:
            continue
        by_regulation[r.regulation] = by_regulation.get(r.regulation, 0) + 1
    return {
        "total_rules": sum(1 for r in REGULATORY_RULES if r.enabled),
        "regulations_covered": list(by_regulation.keys()),
        "rules_per_regulation": by_regulation,
    }
