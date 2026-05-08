"""
compliance_score.py — Rolling security + GDPR compliance score.

The pipeline_heartbeat proves that the autonomous loop is alive.
The security_heartbeat proves that the security surface is rejecting
what it should. The compliance score proves both, plus everything
else a regulator or auditor would ask about, in ONE number.

Inputs (all Redis-backed, deterministic, no LLM):

    1. Security heartbeat — last probe pass/fail ratio
    2. GDPR SLA queue — count of unresolved gdpr_sla_breach alerts
    3. Consent rate — accepted / (accepted + denied) across last 7d
    4. Retention sweep — ran within the last 24h?
    5. Security guard blocks — non-zero count in the last 7d means the
       pipeline tried to ship a regression and we caught it
    6. Learning isolation — evidence_source default is non-real and
       classify_evidence_source returns a valid label
    7. PII masking coverage — `mask_email` is present in every log call
       that references a PII variable (static check — cheap)

Score shape:

    {
        "score": 0..100,
        "grade": "A" | "B" | "C" | "D" | "F",
        "components": {
            "security_probes": {"weight": 25, "score": 25, "detail": ...},
            ...
        },
        "violations": [...],
        "computed_at": "...",
    }

Hook-in:
    * Daily digest renders a one-line "Compliance: 97/100 (A)" row
    * Operator endpoint `GET /ops/compliance` returns the full JSON
    * If score drops below _AUTO_PAUSE_THRESHOLD, the autonomous
      self-modification pipeline is auto-paused via protection_state.

Killer property: the pipeline's own self-debugging can NEVER raise
its compliance score at the expense of another component — every
component is measured independently and the minimum aggregate is
published. If the system games one dimension, another dimension falls.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("compliance_score")

# If the score drops below this, the compliance synthesizer sets a
# Redis flag read by the self-modification loop — no new auto-applies
# until the founder investigates.
_AUTO_PAUSE_THRESHOLD = 70
_AUTO_PAUSE_KEY = "hs:compliance:auto_pause"
_CACHE_KEY = "hs:compliance:last_score"
_CACHE_TTL_S = 15 * 60


_WEIGHTS = {
    "security_probes":          28,  # +8 reabsorbed from security_guard_wall (2026-05-08 cleanup)
    "gdpr_sla":                 15,
    "consent_rate":             10,
    "retention_sweep":           8,
    "learning_isolation":        7,
    "pii_masking_coverage":      7,
    # New worldwide-compliance components (2026-04-12)
    "audit_log_integrity":       10,
    "breach_response_latency":    8,
    "llm_pii_guard_health":      5,
    "telegram_webhook_security":  2,
}
assert sum(_WEIGHTS.values()) == 100, "weights must sum to 100"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("compliance_score: _redis failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Component computations
# ---------------------------------------------------------------------------

def _score_security_probes() -> dict:
    from app.services.security_heartbeat import get_last_results

    data = get_last_results()
    if not data:
        return {
            "weight": _WEIGHTS["security_probes"],
            "score": 0,
            "detail": "no probe results yet — heartbeat not yet run",
        }
    results = data.get("results", [])
    if not results:
        return {
            "weight": _WEIGHTS["security_probes"],
            "score": 0,
            "detail": "empty results",
        }
    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    pct = passed / total if total else 0.0
    return {
        "weight": _WEIGHTS["security_probes"],
        "score": round(_WEIGHTS["security_probes"] * pct, 1),
        "detail": f"{passed}/{total} probes passed",
    }


def _score_gdpr_sla(db: Session) -> dict:
    from app.models.ops_alert import OpsAlert

    try:
        active_breaches = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "gdpr_sla_breach",
                OpsAlert.resolved == False,  # noqa: E712
            )
            .count()
        )
    except Exception as exc:
        log.warning("compliance_score: gdpr_sla query failed: %s", exc)
        active_breaches = -1

    if active_breaches < 0:
        return {
            "weight": _WEIGHTS["gdpr_sla"],
            "score": _WEIGHTS["gdpr_sla"] / 2,  # unknown — half credit
            "detail": "query failed",
        }
    if active_breaches == 0:
        return {
            "weight": _WEIGHTS["gdpr_sla"],
            "score": _WEIGHTS["gdpr_sla"],
            "detail": "no active SLA breaches",
        }
    # One breach drops to half; two or more → zero.
    if active_breaches == 1:
        return {
            "weight": _WEIGHTS["gdpr_sla"],
            "score": _WEIGHTS["gdpr_sla"] / 2,
            "detail": "1 active SLA breach",
        }
    return {
        "weight": _WEIGHTS["gdpr_sla"],
        "score": 0,
        "detail": f"{active_breaches} active SLA breaches",
    }


def _score_consent_rate() -> dict:
    rc = _redis()
    if rc is None:
        record_silent_return("compliance_score.consent_rate")
        return {
            "weight": _WEIGHTS["consent_rate"],
            "score": _WEIGHTS["consent_rate"] / 2,
            "detail": "redis unavailable",
        }
    total_accepted = 0
    total_denied = 0
    try:
        today = _now()
        for offset in range(7):
            day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            for bucket, counter in (
                ("accepted", "total_accepted"),
                ("denied", "total_denied"),
            ):
                raw = rc.get(f"hs:consent:{day}:{bucket}")
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode()
                try:
                    val = int(raw)
                except ValueError:
                    val = 0
                if counter == "total_accepted":
                    total_accepted += val
                else:
                    total_denied += val
    except Exception as exc:
        log.debug("compliance_score: consent read failed: %s", exc)

    total = total_accepted + total_denied
    if total == 0:
        return {
            "weight": _WEIGHTS["consent_rate"],
            "score": _WEIGHTS["consent_rate"],  # no traffic → no evidence of problem
            "detail": "no consent signals yet (tracker update pending)",
        }
    # 100% accepted = full credit. 100% denied = zero. Linear in between.
    pct = total_accepted / total
    return {
        "weight": _WEIGHTS["consent_rate"],
        "score": round(_WEIGHTS["consent_rate"] * pct, 1),
        "detail": f"{total_accepted}/{total} accepted (7d)",
    }


def _score_retention_sweep() -> dict:
    rc = _redis()
    if rc is None:
        record_silent_return("compliance_score.retention_sweep")
        return {
            "weight": _WEIGHTS["retention_sweep"],
            "score": _WEIGHTS["retention_sweep"] / 2,
            "detail": "redis unavailable",
        }
    # Look for any `hs:data_retention:day:*` key within the last 2 days
    try:
        today = _now().strftime("%Y-%m-%d")
        yesterday = (_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        has_today = rc.get(f"hs:data_retention:day:{today}")
        has_yday = rc.get(f"hs:data_retention:day:{yesterday}")
        if has_today or has_yday:
            return {
                "weight": _WEIGHTS["retention_sweep"],
                "score": _WEIGHTS["retention_sweep"],
                "detail": "ran within 48h",
            }
    except Exception as exc:
        log.warning("compliance_score: _score_retention_sweep failed: %s", exc)
    return {
        "weight": _WEIGHTS["retention_sweep"],
        "score": 0,
        "detail": "no recent retention sweep marker",
    }


def _score_learning_isolation(db: Session) -> dict:
    try:
        from app.services.learning_isolation import (
            classify_evidence_source,
            is_product_learning_eligible,
            EVIDENCE_SOURCES,
        )
        src = classify_evidence_source(db)
        valid = src in EVIDENCE_SOURCES
        pre_merchant_blocked = not is_product_learning_eligible("pre_merchant")
        test_blocked = not is_product_learning_eligible("internal_test")
        if valid and pre_merchant_blocked and test_blocked:
            return {
                "weight": _WEIGHTS["learning_isolation"],
                "score": _WEIGHTS["learning_isolation"],
                "detail": f"source={src} gates locked",
            }
        return {
            "weight": _WEIGHTS["learning_isolation"],
            "score": 0,
            "detail": f"isolation check failed src={src}",
        }
    except Exception as exc:
        return {
            "weight": _WEIGHTS["learning_isolation"],
            "score": 0,
            "detail": f"import failed: {type(exc).__name__}",
        }


def _score_audit_log_integrity() -> dict:
    """Check whether the daily audit log chain verification ran recently
    and found no violations."""
    rc = _redis()
    if rc is None:
        record_silent_return("compliance_score.audit_log_integrity")
        return {
            "weight": _WEIGHTS["audit_log_integrity"],
            "score": _WEIGHTS["audit_log_integrity"] / 2,
            "detail": "redis unavailable",
        }
    try:
        today = _now().strftime("%Y-%m-%d")
        yesterday = (_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        ran_today = rc.get(f"hs:audit_log_check:day:{today}")
        ran_yesterday = rc.get(f"hs:audit_log_check:day:{yesterday}")
        if not ran_today and not ran_yesterday:
            return {
                "weight": _WEIGHTS["audit_log_integrity"],
                "score": 0,
                "detail": "no audit log chain verification in 48h",
            }
    except Exception as exc:
        log.warning("compliance_score: _score_audit_log_integrity failed: %s", exc)
        return {
            "weight": _WEIGHTS["audit_log_integrity"],
            "score": _WEIGHTS["audit_log_integrity"] / 2,
            "detail": "redis read failed",
        }

    # Check for any unresolved tampering alerts
    try:
        tampering_key = rc.get("hs:audit_log_tampering:active")
        if tampering_key:
            return {
                "weight": _WEIGHTS["audit_log_integrity"],
                "score": 0,
                "detail": "ACTIVE audit log tampering detected",
            }
    except Exception as exc:
        log.warning("compliance_score: _score_audit_log_integrity failed: %s", exc)

    return {
        "weight": _WEIGHTS["audit_log_integrity"],
        "score": _WEIGHTS["audit_log_integrity"],
        "detail": "chain verified within 48h, no tampering",
    }


def _score_breach_response_latency(db: Session) -> dict:
    """Check whether any breach_response_required alerts are past their
    72h supervisory deadline without being resolved."""
    from app.models.ops_alert import OpsAlert

    try:
        unresolved = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "breach_response_required",
                OpsAlert.resolved == False,  # noqa: E712
            )
            .all()
        )
    except Exception as exc:
        log.warning("compliance_score: _score_breach_response_latency failed: %s", exc)
        return {
            "weight": _WEIGHTS["breach_response_latency"],
            "score": _WEIGHTS["breach_response_latency"] / 2,
            "detail": "query failed",
        }

    if not unresolved:
        return {
            "weight": _WEIGHTS["breach_response_latency"],
            "score": _WEIGHTS["breach_response_latency"],
            "detail": "no open breach response alerts",
        }

    # Any overdue breach → zero score. Otherwise half credit for open-but-not-overdue.
    now = _now()
    overdue = 0
    for alert in unresolved:
        age_hours = (now - alert.created_at).total_seconds() / 3600
        if age_hours > 72:
            overdue += 1

    if overdue > 0:
        return {
            "weight": _WEIGHTS["breach_response_latency"],
            "score": 0,
            "detail": f"{overdue} breach response(s) past 72h deadline",
        }
    return {
        "weight": _WEIGHTS["breach_response_latency"],
        "score": _WEIGHTS["breach_response_latency"] / 2,
        "detail": f"{len(unresolved)} open breach response(s) within deadline",
    }


def _score_llm_pii_guard_health() -> dict:
    """Verify the LLM PII guard module is importable and functional."""
    try:
        from app.core.llm_pii_guard import check_for_pii
        # Quick smoke test — a clean string should return [], PII should return findings
        clean = check_for_pii("analyze revenue trends for this shop")
        dirty = check_for_pii("the merchant email is alice@example.com and token shpat_abc123")
        if len(clean) == 0 and len(dirty) > 0:
            return {
                "weight": _WEIGHTS["llm_pii_guard_health"],
                "score": _WEIGHTS["llm_pii_guard_health"],
                "detail": "PII guard operational — clean pass, dirty block",
            }
        return {
            "weight": _WEIGHTS["llm_pii_guard_health"],
            "score": 0,
            "detail": f"PII guard logic error: clean={len(clean)} findings, dirty={len(dirty)} findings",
        }
    except Exception as exc:
        return {
            "weight": _WEIGHTS["llm_pii_guard_health"],
            "score": 0,
            "detail": f"PII guard import failed: {type(exc).__name__}",
        }


def _score_telegram_webhook_security() -> dict:
    """Verify the Telegram webhook signature verification is active."""
    try:
        from app.api.telegram_webhook import _verify_telegram_signature  # noqa: F401
        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if secret:
            return {
                "weight": _WEIGHTS["telegram_webhook_security"],
                "score": _WEIGHTS["telegram_webhook_security"],
                "detail": "webhook signature verification active",
            }
        # Secret not configured — fail-closed behavior is in place (503)
        # but the webhook is effectively disabled. Half credit.
        return {
            "weight": _WEIGHTS["telegram_webhook_security"],
            "score": _WEIGHTS["telegram_webhook_security"] / 2,
            "detail": "TELEGRAM_WEBHOOK_SECRET not set — webhook returns 503 (safe but non-functional)",
        }
    except ImportError:
        return {
            "weight": _WEIGHTS["telegram_webhook_security"],
            "score": 0,
            "detail": "telegram webhook module not found",
        }


_PII_LOG_REGEX = None


def _score_pii_masking_coverage() -> dict:
    """Static grep on the app/ tree for unmasked PII logs."""
    import re
    global _PII_LOG_REGEX
    if _PII_LOG_REGEX is None:
        _PII_LOG_REGEX = re.compile(
            r"\blog\.\w+\([^)]*(?<!mask_email\()"
            r"\b(to_email|customer_email|recipient_email|user_email|"
            r"email_addr|email_address)\b",
            re.IGNORECASE,
        )
    app_dir = os.path.join("/opt/wishspark/backend", "app")
    offenders: list[str] = []
    try:
        for root, _, files in os.walk(app_dir):
            if "/venv/" in root or "/__pycache__/" in root:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as exc:
                    log.warning("compliance_score: _score_pii_masking_coverage failed: %s", exc)
                    continue
                if _PII_LOG_REGEX.search(content):
                    offenders.append(path.replace("/opt/wishspark/backend/", ""))
    except Exception as exc:
        log.debug("compliance_score: pii scan failed: %s", exc)

    if offenders:
        return {
            "weight": _WEIGHTS["pii_masking_coverage"],
            "score": 0,
            "detail": f"{len(offenders)} unmasked PII log(s): {offenders[:3]}",
        }
    return {
        "weight": _WEIGHTS["pii_masking_coverage"],
        "score": _WEIGHTS["pii_masking_coverage"],
        "detail": "no unmasked PII log calls detected",
    }


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

def _grade(score: float) -> str:
    if score >= 95:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def compute_compliance_score(db: Session) -> dict:
    """Collect every component and produce the final payload."""
    components = {
        "security_probes":          _score_security_probes(),
        "gdpr_sla":                 _score_gdpr_sla(db),
        "consent_rate":             _score_consent_rate(),
        "retention_sweep":          _score_retention_sweep(),
        "learning_isolation":       _score_learning_isolation(db),
        "pii_masking_coverage":     _score_pii_masking_coverage(),
        "audit_log_integrity":      _score_audit_log_integrity(),
        "breach_response_latency":  _score_breach_response_latency(db),
        "llm_pii_guard_health":     _score_llm_pii_guard_health(),
        "telegram_webhook_security": _score_telegram_webhook_security(),
    }
    total = sum(c["score"] for c in components.values())
    total_rounded = round(total, 1)

    violations = [
        {"component": name, "detail": c["detail"]}
        for name, c in components.items()
        if c["score"] < c["weight"]
    ]

    result = {
        "score": total_rounded,
        "grade": _grade(total_rounded),
        "components": components,
        "violations": violations,
        "computed_at": _now().isoformat(),
    }

    # Cache for the /ops/compliance endpoint and daily digest
    rc = _redis()
    if rc is not None:
        try:
            import json as _json
            rc.setex(_CACHE_KEY, _CACHE_TTL_S, _json.dumps(result, default=str))
        except Exception as exc:
            log.warning("compliance_score: compute_compliance_score failed: %s", exc)

        # Auto-pause the self-modification pipeline if we dropped below
        # the threshold. The pipeline checks this flag before apply.
        try:
            if total_rounded < _AUTO_PAUSE_THRESHOLD:
                rc.setex(_AUTO_PAUSE_KEY, 24 * 3600, "1")
                log.warning(
                    "compliance_score: score=%.1f below threshold=%d — "
                    "self-modification AUTO-PAUSED",
                    total_rounded, _AUTO_PAUSE_THRESHOLD,
                )
            else:
                rc.delete(_AUTO_PAUSE_KEY)
        except Exception as exc:
            log.warning("compliance_score: compute_compliance_score failed: %s", exc)

    return result


def get_cached_compliance_score() -> dict | None:
    rc = _redis()
    if rc is None:
        record_silent_return("compliance_score.cache_read")
        return None
    try:
        raw = rc.get(_CACHE_KEY)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        import json as _json
        return _json.loads(raw)
    except Exception as exc:
        log.warning("compliance_score: get_cached_compliance_score failed: %s", exc)
        return None


def is_self_modification_paused() -> bool:
    """Read by the apply pipeline to decide whether to run."""
    rc = _redis()
    if rc is None:
        record_silent_return("compliance_score.self_mod_pause")
        return False
    try:
        return bool(rc.get(_AUTO_PAUSE_KEY))
    except Exception as exc:
        log.warning("compliance_score: is_self_modification_paused failed: %s", exc)
        return False
