"""Bidirectional Sentry API client (SENTRY-2 closure).

Pre-2026-04-24 the sentry_triage flow was one-way: Sentry pushed errors
to us via email/webhook, we wrote SentryIncident rows + generated
triage packets, but Sentry never learned what we did. Operators
checking Sentry's UI saw "open" issues that our pipeline had already
classified or escalated to bugfix_candidates — confusing.

This module closes the loop: when our pipeline acts on an incident
(status transitions to `linked` or `resolved`), we POST back to
Sentry's API to either comment on the issue with the verdict + a link
to our internal record, OR set the issue status (resolve / ignore).

API surface
-----------
    add_issue_comment(issue_id, comment) -> bool
    set_issue_status(issue_id, status)   -> bool   # "resolved" | "ignored" | "unresolved"
    extract_issue_id(sentry_url)         -> str | None
    notify_triage_outcome(incident)      -> dict   # higher-level wrapper

Auth
----
Requires SENTRY_AUTH_TOKEN (personal auth token with `event:write`
scope) AND SENTRY_ORG. Graceful no-op when either is unset — returns
False / empty dict instead of raising. Triage path keeps working at
zero observable cost when API not configured.

Region note: HedgeSpark's project lives on the EU region
(de.sentry.io). The API host is `https://sentry.io` (Sentry serves
the API from the global host even for EU-region projects), but the
DSN ingest is region-routed. Verified empirically.

Tier: TIER_0 (observability output, no auth/data path).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger("sentry_api")

_API_BASE = "https://sentry.io/api/0"
_REQUEST_TIMEOUT_S = 10.0

# Match the issue ID segment of a Sentry issue URL. Examples:
#   https://sentry.io/organizations/hedgespark/issues/12345/
#   https://hedgespark.sentry.io/issues/12345/
#   https://sentry.io/organizations/hedgespark/issues/12345/events/abc/
_ISSUE_URL_PATTERN = re.compile(r"/issues/(\d+)(?:/|\Z)")


def _credentials() -> tuple[str | None, str | None]:
    return (
        os.getenv("SENTRY_AUTH_TOKEN", "").strip() or None,
        os.getenv("SENTRY_ORG", "").strip() or None,
    )


def extract_issue_id(sentry_url: str | None) -> str | None:
    """Pull the numeric issue ID out of a Sentry issue URL. Returns None
    when the URL doesn't match the expected pattern."""
    if not sentry_url:
        return None
    m = _ISSUE_URL_PATTERN.search(sentry_url)
    return m.group(1) if m else None


def add_issue_comment(issue_id: str, comment: str) -> bool:
    """POST a comment to a Sentry issue. Returns True on 201, False on
    auth missing / API failure / non-201 status. Never raises."""
    token, _org = _credentials()
    if not token:
        log.debug("sentry_api: SENTRY_AUTH_TOKEN unset — skip add_issue_comment(%s)", issue_id)
        return False
    if not issue_id:
        return False
    if not comment or not comment.strip():
        return False

    url = f"{_API_BASE}/issues/{issue_id}/comments/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {"text": comment[:4000]}  # Sentry comment cap

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = client.post(url, json=body, headers=headers)
        if resp.status_code in (200, 201):
            return True
        log.warning(
            "sentry_api: add_issue_comment issue=%s HTTP %d body=%s",
            issue_id, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:
        log.warning("sentry_api: add_issue_comment issue=%s failed: %s", issue_id, exc)
        return False


def set_issue_status(issue_id: str, status: str) -> bool:
    """PUT issue status update. Allowed values: resolved | ignored |
    unresolved. Returns True on 200/204, False otherwise. Never raises."""
    if status not in {"resolved", "ignored", "unresolved"}:
        log.warning("sentry_api: set_issue_status invalid status=%s", status)
        return False
    token, _org = _credentials()
    if not token:
        log.debug("sentry_api: SENTRY_AUTH_TOKEN unset — skip set_issue_status(%s)", issue_id)
        return False
    if not issue_id:
        return False

    url = f"{_API_BASE}/issues/{issue_id}/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {"status": status}

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = client.put(url, json=body, headers=headers)
        if resp.status_code in (200, 204):
            return True
        log.warning(
            "sentry_api: set_issue_status issue=%s status=%s HTTP %d",
            issue_id, status, resp.status_code,
        )
        return False
    except Exception as exc:
        log.warning("sentry_api: set_issue_status issue=%s failed: %s", issue_id, exc)
        return False


def notify_triage_outcome(
    sentry_issue_url: str | None,
    incident_status: str,
    verdict_summary: str | None = None,
    bugfix_candidate_id: int | None = None,
) -> dict[str, Any]:
    """Higher-level wrapper called from sentry_triage when an incident
    transitions to a terminal state.

    Behavior matrix:
      incident_status="linked" + bugfix_candidate_id:
          → comment on Sentry issue with verdict + candidate link
      incident_status="resolved":
          → comment "auto-resolved by HedgeSpark" + set_issue_status("resolved")
      incident_status="ignored":
          → set_issue_status("ignored")
      anything else:
          → no-op (intentional — only terminal transitions notify back)

    Returns {"posted": bool, "status_set": bool, "issue_id": str|None,
    "skipped_reason": str|None}.
    """
    out: dict[str, Any] = {
        "posted": False,
        "status_set": False,
        "issue_id": None,
        "skipped_reason": None,
    }

    issue_id = extract_issue_id(sentry_issue_url)
    if not issue_id:
        out["skipped_reason"] = "no issue_id in URL"
        return out
    out["issue_id"] = issue_id

    if incident_status == "linked":
        comment = f"HedgeSpark triage: linked to internal bugfix candidate"
        if bugfix_candidate_id is not None:
            comment += f" #{bugfix_candidate_id}"
        if verdict_summary:
            comment += f"\n\nVerdict: {verdict_summary[:1000]}"
        out["posted"] = add_issue_comment(issue_id, comment)
    elif incident_status == "resolved":
        comment = "HedgeSpark: auto-resolved by self-healing pipeline."
        if verdict_summary:
            comment += f"\n\n{verdict_summary[:1000]}"
        out["posted"] = add_issue_comment(issue_id, comment)
        out["status_set"] = set_issue_status(issue_id, "resolved")
    elif incident_status == "ignored":
        out["status_set"] = set_issue_status(issue_id, "ignored")
    else:
        out["skipped_reason"] = f"non-terminal incident status: {incident_status}"

    return out


def is_configured() -> bool:
    """Quick check used by tests + ops dashboards."""
    token, org = _credentials()
    return bool(token and org)
