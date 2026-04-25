"""sentry_poller.py — Pull Sentry issues into the triage pipeline.

The Sentry alert-rules YAML is configured to email the founder on bursts,
regressions, billing/auth errors, etc. Those emails do NOT automatically
reach the internal pipeline (sentry_triage → SentryIncident →
consume_triage_queue → BugFixCandidate). Without this poller, the same
flood that reaches the founder's inbox stays invisible to bug_triage and
never gets root-caused via the self-healing loop.

This module periodically queries the Sentry REST API for active issues
and feeds new ones (or freshly-firing ones) through the existing
`sentry_triage.ingest_webhook` path by synthesizing a webhook-shaped
payload from the API response.

Design notes
------------
- **Idempotent**: dedup is handled by `ingest_webhook` via
  `source_message_id="sentry_poll:{issue_id}:{lastSeen_hour}"`. The same
  issue firing the next hour creates a NEW SentryIncident (so the
  recurrence count grows for the family head); intra-hour repeats are
  collapsed to one row.
- **Bounded blast radius**: only the top-N issues in the last 24h with
  count >= MIN_COUNT are pulled. Old, stale issues (lastSeen > 60min ago)
  are skipped to keep volume in check.
- **Test-safe**: returns early when APP_ENV=test or SENTRY_AUTH_TOKEN is
  unset.
- **Cooldown**: Redis key `hs:sentry_poller:cooldown` enforces ≥3 min
  between polls so concurrent worker cycles don't hammer the API.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

log = logging.getLogger("sentry_poller")

# Sentry REST base — matches sentry_quota.py (de.sentry.io for EU region).
_SENTRY_REGION_HOST = os.getenv("SENTRY_REGION_HOST", "de.sentry.io")
_API_BASE = f"https://{_SENTRY_REGION_HOST}/api/0"

_MIN_COUNT = 10                  # only ingest issues with >=10 events in 24h
_LOOKBACK_MINUTES = 60           # only ingest issues that fired in last 60min
_TOP_N = 30                      # max issues to fetch per poll
_HTTP_TIMEOUT_SEC = 8.0
_COOLDOWN_KEY = "hs:sentry_poller:cooldown"
_COOLDOWN_SEC = 180              # one poll per 3 min


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_test_env() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() == "test"


def _redis_cooldown_active() -> bool:
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        r = _client()
        if r is None:
            record_silent_return("sentry_poller_cooldown_check")
            return False
        return bool(r.exists(_COOLDOWN_KEY))
    except Exception as exc:
        log.warning("sentry_poller: cooldown check failed (proceeding): %s", exc)
        record_silent_return("sentry_poller_cooldown_check")
        return False


def _redis_cooldown_arm() -> None:
    from app.core.silent_fallback import record_silent_return
    try:
        from app.core.redis_client import _client
        r = _client()
        if r is None:
            record_silent_return("sentry_poller_cooldown_arm")
            return
        r.set(_COOLDOWN_KEY, "1", ex=_COOLDOWN_SEC)
    except Exception as exc:
        log.warning("sentry_poller: cooldown arm failed (proceeding): %s", exc)
        record_silent_return("sentry_poller_cooldown_arm")


def _fetch_active_issues(token: str, org: str, project: str) -> list[dict]:
    """GET top-N unresolved issues for the project, sorted by lastSeen."""
    url = f"{_API_BASE}/projects/{org}/{project}/issues/"
    params = {
        "statsPeriod": "24h",
        "sort": "freq",
        "limit": str(_TOP_N),
        "query": "is:unresolved",
    }
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=_HTTP_TIMEOUT_SEC) as client:
        resp = client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    return data


def _fetch_latest_event(token: str, issue_id: str) -> dict | None:
    """GET the latest event for an issue (used to enrich the triage payload)."""
    url = f"{_API_BASE}/issues/{issue_id}/events/latest/"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:  # SILENT-EXCEPT-OK: best-effort enrichment
        # We can ingest the issue without the latest event — title +
        # culprit + tags from the issue payload are enough for
        # SentryIncident to be created. Log at warning so an outage of
        # the events endpoint becomes visible without breaking the poll.
        log.warning("sentry_poller: latest_event fetch failed for %s: %s", issue_id, exc)
        return None


def _build_webhook_payload(issue: dict, event: dict | None) -> dict:
    """Synthesize a Sentry-webhook-shaped payload for `ingest_webhook`.

    The pipeline's parser (parse_sentry_webhook) reads:
        payload.data.issue.title / culprit / metadata / project / tags
        payload.data.event.exception / tags / contexts / release
    so we only need to populate those fields. Anything we can copy from
    the API response we copy verbatim; anything we can't, we leave None
    and the parser falls back gracefully.
    """
    data: dict[str, Any] = {"issue": dict(issue), "event": dict(event or {})}
    # Sentry's REST issue payload uses "platform" at the top of the issue;
    # the webhook parser already handles it. Same for tags (we just pass
    # through).
    return {
        "action": "triggered",
        "installation": {"uuid": "rest-api-poll"},
        "data": data,
    }


def poll_recent_issues(
    db: Session,
    *,
    min_count: int = _MIN_COUNT,
    lookback_minutes: int = _LOOKBACK_MINUTES,
) -> dict[str, Any]:
    """Poll Sentry once and feed eligible issues to sentry_triage.

    Returns a summary dict describing what happened — used by the agent
    worker to log a one-line cycle receipt and by tests to assert the
    expected branches were hit.
    """
    if _is_test_env():
        return {"status": "skipped", "reason": "test_env"}

    token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    org = os.getenv("SENTRY_ORG", "").strip()
    project = os.getenv("SENTRY_PROJECT", "").strip()
    if not token or not org or not project:
        return {"status": "skipped", "reason": "missing_credentials"}

    if _redis_cooldown_active():
        return {"status": "skipped", "reason": "cooldown"}

    try:
        issues = _fetch_active_issues(token, org, project)
    except httpx.HTTPError as exc:
        log.warning("sentry_poller: fetch failed: %s", exc)
        return {"status": "error", "reason": "fetch_failed", "detail": str(exc)[:200]}

    _redis_cooldown_arm()

    cutoff = _now_utc().timestamp() - lookback_minutes * 60
    polled = 0
    forwarded = 0
    skipped_stale = 0
    skipped_low_volume = 0
    parse_errors = 0
    forwarded_ids: list[int] = []

    from app.services.sentry_triage import ingest_webhook

    for issue in issues:
        polled += 1
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            continue
        try:
            count = int(issue.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count < min_count:
            skipped_low_volume += 1
            continue
        last_seen_str = issue.get("lastSeen") or ""
        try:
            last_seen_ts = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00")).timestamp()
        except ValueError:
            skipped_stale += 1
            continue
        if last_seen_ts < cutoff:
            skipped_stale += 1
            continue

        # Bucket by lastSeen-hour so the same issue firing repeatedly within
        # an hour collapses to one SentryIncident, but a fresh fire next
        # hour creates a new row (the family-head recurrence count grows).
        hour_bucket = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc).strftime("%Y%m%dT%H")
        sentry_event_id = f"poll:{issue_id}:{hour_bucket}"

        latest_event = _fetch_latest_event(token, issue_id)
        payload = _build_webhook_payload(issue, latest_event)

        try:
            result = ingest_webhook(db, payload=payload, sentry_event_id=sentry_event_id)
            db.flush()
            if result.get("status") in ("new", "stored"):
                forwarded += 1
                inc_id = result.get("incident_id")
                if isinstance(inc_id, int):
                    forwarded_ids.append(inc_id)
            elif result.get("status") == "parse_error":
                parse_errors += 1
        except Exception as exc:
            parse_errors += 1
            log.warning("sentry_poller: ingest crashed for issue=%s: %s", issue_id, exc)
            db.rollback()

    summary = {
        "status": "ok",
        "polled": polled,
        "forwarded": forwarded,
        "skipped_stale": skipped_stale,
        "skipped_low_volume": skipped_low_volume,
        "parse_errors": parse_errors,
        "forwarded_ids": forwarded_ids,
    }
    log.info("sentry_poller: %s", json.dumps(summary, default=str))
    return summary
