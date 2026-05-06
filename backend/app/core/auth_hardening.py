"""
auth_hardening.py — Login-path anomaly detection + secret posture audit.

Two concerns bundled (both are lightweight and Redis-backed):

  1. Login velocity & anomaly detection. Tracks the IP + user agent
     fingerprint per shop session creation and flags:
       - Too many session creations per window (velocity)
       - Session creation from an IP never seen before AND with a
         fingerprint never seen before (novel device)
     Flags are written to the alerting pipeline; this module is
     advisory — it does NOT block logins (that would be a TIER_2
     auth-flow change requiring explicit human approval).

  2. Secret posture — observability surface for ops review. Hard
     enforcement (FATAL crash on missing critical secrets) lives in
     app/main.py::_startup_env_audit. This module exposes the broader
     posture (status + length per secret) via /ops/auth/posture so
     operators can verify configuration health on demand.

Public API
----------
    record_session_creation(shop, ip, user_agent) -> AuthEvent
    audit_secrets() -> list[dict]
    auth_posture() -> dict  # surface for ops dashboard
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger("auth_hardening")

_VELOCITY_WINDOW_SEC = 600  # 10 minutes
_VELOCITY_THRESHOLD = 5     # >5 session creations in 10min = anomaly
_FINGERPRINT_TTL_SEC = 90 * 24 * 3600  # 90 days of known devices


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("auth_hardening: redis client init failed: %s", exc)
        return None


@dataclass
class AuthEvent:
    shop_domain: str
    anomalous: bool
    reasons: list[str]
    velocity_count: int
    is_new_fingerprint: bool


def _fingerprint(ip: str | None, user_agent: str | None) -> str:
    raw = f"{ip or 'noip'}|{user_agent or 'noua'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def record_session_creation(
    shop_domain: str,
    ip: str | None,
    user_agent: str | None,
) -> AuthEvent:
    """
    Register one session creation and return whether it looks anomalous.

    This NEVER blocks. It records, classifies, and writes an alert when
    the event looks suspicious. The caller continues normally.
    """
    reasons: list[str] = []
    fp = _fingerprint(ip, user_agent)
    rc = _redis()

    velocity_count = 0
    is_new_fingerprint = True

    if rc is not None:
        try:
            now = int(time.time())
            vel_key = f"hs:auth:vel:{shop_domain}"
            # Push, trim to window, count
            rc.zadd(vel_key, {f"{now}:{fp}": now})
            rc.zremrangebyscore(vel_key, 0, now - _VELOCITY_WINDOW_SEC)
            rc.expire(vel_key, _VELOCITY_WINDOW_SEC * 2)
            velocity_count = int(rc.zcard(vel_key) or 0)

            fp_key = f"hs:auth:known_fp:{shop_domain}"
            # sismember returns 1 if already known
            is_known = bool(rc.sismember(fp_key, fp))
            is_new_fingerprint = not is_known
            rc.sadd(fp_key, fp)
            rc.expire(fp_key, _FINGERPRINT_TTL_SEC)
        except Exception as exc:
            log.debug("auth_hardening: redis failed: %s", exc)

    if velocity_count > _VELOCITY_THRESHOLD:
        reasons.append(f"velocity_{velocity_count}_in_{_VELOCITY_WINDOW_SEC//60}min")
    if is_new_fingerprint and velocity_count > 1:
        reasons.append("novel_device_plus_velocity")

    anomalous = bool(reasons)

    if anomalous:
        _write_alert(shop_domain, reasons, ip=ip, user_agent=user_agent, fingerprint=fp)

    return AuthEvent(
        shop_domain=shop_domain,
        anomalous=anomalous,
        reasons=reasons,
        velocity_count=velocity_count,
        is_new_fingerprint=is_new_fingerprint,
    )


def _write_alert(
    shop_domain: str,
    reasons: list[str],
    *,
    ip: str | None,
    user_agent: str | None,
    fingerprint: str,
) -> None:
    try:
        from app.services.alerting import write_alert
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            # heal-detection: auth hardening event — per-violation log
            write_alert(
                db,
                severity="warning",
                source="auth_hardening",
                alert_type="session_anomaly",
                summary=f"Anomalous session creation for {shop_domain}: {', '.join(reasons)}",
                shop_domain=shop_domain,
                detail={
                    "reasons": reasons,
                    "ip": ip,
                    "user_agent": (user_agent or "")[:200],
                    "fingerprint": fingerprint,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        log.warning("auth_hardening: alert write failed: %s", exc)


# ---------------------------------------------------------------------------
# Secret posture audit
# ---------------------------------------------------------------------------

# Critical secrets whose presence + minimum quality the ops endpoint
# /ops/auth/posture surfaces for human review. Each entry is
# (env_var, min_length, description). Hard-enforcement (FATAL on missing)
# for the truly load-bearing ones lives in app/main.py::_startup_env_audit;
# this list is the broader observability view for ops.
_CRITICAL_SECRETS: list[tuple[str, int, str]] = [
    ("MERCHANT_SESSION_SECRET", 32, "Session JWT signing key"),
    ("MERCHANT_TOKEN_ENCRYPTION_KEY", 32, "Merchant token encryption key"),
    ("SHOPIFY_API_SECRET", 16, "Shopify OAuth secret"),
    ("OPS_API_KEY", 16, "Ops endpoint admin key"),
    ("SHOPIFY_WEBHOOK_SECRET", 16, "Shopify webhook HMAC secret"),
    ("TELEGRAM_WEBHOOK_SECRET", 16, "Telegram webhook signature secret"),
    ("RESEND_WEBHOOK_SECRET", 16, "Resend webhook signature secret"),
]

_WEAK_VALUES = {"changeme", "secret", "password", "dev", "test", "admin"}


def audit_secrets() -> list[dict]:
    """Return a row per critical secret with its posture."""
    out = []
    for name, min_len, desc in _CRITICAL_SECRETS:
        value = os.environ.get(name, "")
        status = "ok"
        issue = None
        if not value:
            status = "missing"
            issue = "environment variable not set"
        elif len(value) < min_len:
            status = "weak"
            issue = f"shorter than {min_len} characters"
        elif value.lower() in _WEAK_VALUES:
            status = "weak"
            issue = "placeholder value"
        out.append({
            "name": name,
            "description": desc,
            "status": status,
            "issue": issue,
            "length": len(value) if value else 0,
        })
    return out


def auth_posture() -> dict:
    """High-level posture summary for ops dashboard."""
    secrets = audit_secrets()
    missing = [s for s in secrets if s["status"] == "missing"]
    weak = [s for s in secrets if s["status"] == "weak"]
    return {
        "secrets": {
            "total": len(secrets),
            "ok": sum(1 for s in secrets if s["status"] == "ok"),
            "missing": len(missing),
            "weak": len(weak),
            "detail": secrets,
        },
        "session_anomaly": {
            "velocity_window_seconds": _VELOCITY_WINDOW_SEC,
            "velocity_threshold": _VELOCITY_THRESHOLD,
            "fingerprint_ttl_seconds": _FINGERPRINT_TTL_SEC,
        },
    }


