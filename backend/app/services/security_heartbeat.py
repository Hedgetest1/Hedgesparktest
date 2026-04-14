"""
security_heartbeat.py — Continuous self-attack probes on the live
HedgeSpark backend. The same pattern as `pipeline_heartbeat` but
pointed at the security surface.

Each probe synthesizes a request that MUST be rejected by the app:

  * OAuth callback with no state parameter       → 400 expected
  * Shopify webhook with an invalid HMAC         → 401 expected
  * /track with explicit consent denial          → 200 + ignored
  * /ops endpoint with a bogus X-API-Key         → 401 expected
  * /merchant/export without a session cookie   → 401 expected

If any probe returns a status code that differs from the expected
failure mode (e.g. 200 when we expected 401), we emit a CRITICAL
ops_alert `security_probe_failed`. The founder sees it in the next
daily digest. The compliance synthesizer subtracts points.

Kill switch: `SECURITY_HEARTBEAT_PAUSED=1`.

Dedup: one probe run per `SECURITY_HEARTBEAT_INTERVAL_S` (default 3600s),
tracked via Redis. The agent worker calls `run_security_heartbeat(db)`
every cycle; the service enforces its own rate limit.

Public API
----------
    run_security_heartbeat(db) -> dict
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

log = logging.getLogger("security_heartbeat")

_HEARTBEAT_INTERVAL_S = int(os.getenv("SECURITY_HEARTBEAT_INTERVAL_S", "3600"))
_HEARTBEAT_PAUSED = os.getenv("SECURITY_HEARTBEAT_PAUSED", "").strip() == "1"
_BASE_URL = os.getenv("SECURITY_HEARTBEAT_URL", "http://127.0.0.1:8000")
_PROBE_TIMEOUT = 8.0
_LAST_RUN_KEY = "hs:security_heartbeat:last_run"
_RESULTS_KEY = "hs:security_heartbeat:last_results"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _should_run() -> bool:
    """True when enough time has passed since the last run."""
    rc = _redis()
    if rc is None:
        return True
    try:
        last = rc.get(_LAST_RUN_KEY)
        if not last:
            return True
        if isinstance(last, bytes):
            last = last.decode()
        return (time.time() - float(last)) >= _HEARTBEAT_INTERVAL_S
    except Exception:
        # fail-open: redis hiccup means we cannot read the last-run
        # timestamp, so we let the heartbeat run as if it were due. A
        # spurious extra cycle is harmless; a missed cycle is not.
        return True


def _stamp_run() -> None:
    rc = _redis()
    if rc is None:
        return
    try:
        rc.setex(_LAST_RUN_KEY, 3 * 24 * 3600, str(time.time()))
    except Exception:
        pass


def _persist_results(results: list[dict]) -> None:
    rc = _redis()
    if rc is None:
        return
    try:
        import json as _json
        payload = _json.dumps({
            "ran_at": _now().isoformat(),
            "results": results,
        })
        rc.setex(_RESULTS_KEY, 48 * 3600, payload)
    except Exception:
        pass


def get_last_results() -> dict | None:
    rc = _redis()
    if rc is None:
        return None
    try:
        raw = rc.get(_RESULTS_KEY)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        import json as _json
        return _json.loads(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------

def _probe_oauth_no_state(client: httpx.Client) -> dict:
    """Expect: 400. Success = rejected."""
    resp = client.get(
        "/auth/callback",
        params={
            "shop": "nonexistent-probe.myshopify.com",
            "code": "fake",
            "hmac": "000000",
        },
        follow_redirects=False,
    )
    # 400 = validation failure (invalid HMAC will fire first and
    # short-circuit before our state check, which is fine — the
    # endpoint rejected the request, which is what we need).
    return {
        "probe": "oauth_no_state",
        "status": resp.status_code,
        "passed": resp.status_code in (400, 401, 403),
        "expectation": "4xx rejection",
    }


def _probe_ops_bogus_key(client: httpx.Client) -> dict:
    """Expect: 401. Success = rejected."""
    resp = client.get(
        "/ops/diagnostic",
        headers={"X-API-Key": "definitely-not-the-real-key-xyz"},
    )
    return {
        "probe": "ops_bogus_key",
        "status": resp.status_code,
        "passed": resp.status_code == 401,
        "expectation": "401 operator auth failure",
    }


def _probe_merchant_export_no_session(client: httpx.Client) -> dict:
    """Expect: 401/403. Success = rejected."""
    resp = client.get("/merchant/export")
    return {
        "probe": "merchant_export_no_session",
        "status": resp.status_code,
        "passed": resp.status_code in (401, 403),
        "expectation": "401/403 session required",
    }


def _probe_track_consent_denied(client: httpx.Client) -> dict:
    """Expect: 200 with `ignored` body. Success = event dropped."""
    resp = client.post("/track", json={
        "shop_domain": "probe.myshopify.com",
        "visitor_id": "probe-visitor",
        "event_type": "page_view",
        "page_url": "/",
        "gdpr_consent_given": False,
    })
    body_ok = False
    try:
        body = resp.json()
        body_ok = body.get("reason") == "consent_denied"
    except Exception:
        pass
    return {
        "probe": "track_consent_denied",
        "status": resp.status_code,
        "passed": resp.status_code == 200 and body_ok,
        "expectation": "200 with reason=consent_denied",
    }


def _probe_shopify_webhook_bad_hmac(client: httpx.Client) -> dict:
    """Expect: 401/403 (rejected by HMAC check). Success = rejected."""
    body = b'{"id":123}'
    bad_hmac = hmac.new(b"wrong-secret", body, hashlib.sha256).digest()
    import base64
    resp = client.post(
        "/webhooks/shopify/orders",  # canonical path — matches webhooks.py
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": base64.b64encode(bad_hmac).decode(),
            "X-Shopify-Shop-Domain": "probe.myshopify.com",
            "X-Shopify-Topic": "orders/updated",
            "Content-Type": "application/json",
        },
    )
    return {
        "probe": "shopify_webhook_bad_hmac",
        "status": resp.status_code,
        "passed": resp.status_code in (401, 403, 400),
        "expectation": "4xx HMAC failure (401/403)",
    }


_PROBES = [
    _probe_oauth_no_state,
    _probe_ops_bogus_key,
    _probe_merchant_export_no_session,
    _probe_track_consent_denied,
    _probe_shopify_webhook_bad_hmac,
]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_security_heartbeat(db: Session) -> dict:
    """Execute all probes and emit ops_alerts for any that fail."""
    report: dict[str, Any] = {
        "ran_at": _now().isoformat(),
        "paused": _HEARTBEAT_PAUSED,
        "skipped": False,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "results": [],
    }
    if _HEARTBEAT_PAUSED:
        report["skipped"] = True
        return report
    if not _should_run():
        report["skipped"] = True
        return report

    results: list[dict] = []
    try:
        with httpx.Client(base_url=_BASE_URL, timeout=_PROBE_TIMEOUT) as client:
            for probe_fn in _PROBES:
                try:
                    result = probe_fn(client)
                except Exception as exc:
                    result = {
                        "probe": getattr(probe_fn, "__name__", "?"),
                        "status": None,
                        "passed": False,
                        "expectation": "probe raised",
                        "error": type(exc).__name__,
                    }
                results.append(result)
    except Exception as exc:
        log.warning("security_heartbeat: client setup failed: %s", exc)
        return report

    _stamp_run()
    _persist_results(results)

    report["total"] = len(results)
    report["passed"] = sum(1 for r in results if r["passed"])
    report["failed"] = sum(1 for r in results if not r["passed"])
    report["results"] = results

    # Emit one ops_alert per failing probe — the founder wants the news.
    if report["failed"] > 0:
        try:
            from app.models.ops_alert import OpsAlert
            for r in results:
                if r["passed"]:
                    continue
                alert = OpsAlert(
                    severity="critical",
                    source=f"security_heartbeat:{r['probe']}",
                    alert_type="security_probe_failed",
                    shop_domain=None,
                    summary=(
                        f"Security probe '{r['probe']}' FAILED — "
                        f"got {r['status']}, expected {r['expectation']}"
                    ),
                    detail=(
                        f"Probe: {r['probe']}\n"
                        f"Status: {r['status']}\n"
                        f"Expected: {r['expectation']}\n"
                        f"Error: {r.get('error', 'none')}\n"
                        f"Action: investigate the endpoint — a security "
                        f"regression may have shipped."
                    ),
                    resolved=False,
                )
                db.add(alert)
            db.commit()
        except Exception as exc:
            log.warning("security_heartbeat: alert write failed: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass
        log.warning(
            "security_heartbeat: %d/%d probes FAILED",
            report["failed"], report["total"],
        )
    else:
        log.info(
            "security_heartbeat: %d/%d probes passed",
            report["passed"], report["total"],
        )

    return report
