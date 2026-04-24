"""Sentry issue-alert rules sync engine (D10 closure — IaC for alerts).

Pre-D10 the Sentry alert configuration lived only in the Sentry web UI:
manual clicks, no diff, no rollback, no code-review trail. If a rule
got deleted by accident or modified by a teammate, nothing in the
repo would catch it.

This module turns alert rules into infrastructure-as-code:

  1. `backend/config/sentry_alert_rules.yaml` is the declarative source
     of truth (6 rules shipped at module birth).
  2. `load_local_rules()` parses the YAML.
  3. `fetch_remote_rules()` GETs the live rules from Sentry's API.
  4. `compute_diff()` returns {to_create, to_update, to_delete}.
  5. `apply_diff()` POSTs / PUTs / DELETEs to make remote = local.
     Idempotent: running twice with no YAML change = zero API calls.
  6. `compute_yaml_hash()` is used by the drift audit to verify the
     YAML matches the lock file (i.e. someone edited YAML but never
     synced).

CLI wrapper: `scripts/sentry_sync_alert_rules.py` (dry-run by
default). Drift preflight: `scripts/audit_sentry_alert_rules_drift.py`
blocks commits where `sentry_alert_rules.yaml` content hash differs
from the recorded lock.

Auth: SENTRY_AUTH_TOKEN (scope `project:write`) + SENTRY_ORG +
SENTRY_PROJECT. Graceful no-op when unset (CLI prints "skipped").

Tier: TIER_0.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

log = logging.getLogger("sentry_alert_rules")

_API_BASE = "https://sentry.io/api/0"
_REQUEST_TIMEOUT_S = 15.0

_THIS_DIR = Path(__file__).resolve().parents[1]  # backend/app
_BACKEND_ROOT = _THIS_DIR.parent  # backend/
_RULES_YAML = _BACKEND_ROOT / "config" / "sentry_alert_rules.yaml"
_RULES_LOCK = _BACKEND_ROOT / "config" / "sentry_alert_rules.applied.lock"


def _credentials(project_override: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Return (token, org, project). `project_override` lets callers
    target a non-default project (used for multi-project support in
    YAML rules with a `project:` field)."""
    project = project_override or os.getenv("SENTRY_PROJECT", "").strip() or None
    return (
        os.getenv("SENTRY_AUTH_TOKEN", "").strip() or None,
        os.getenv("SENTRY_ORG", "").strip() or None,
        project,
    )


def load_local_rules(path: Path | None = None) -> list[dict[str, Any]]:
    """Parse the YAML rules into a list of dicts. Validates that each
    rule has the minimum required keys (name, action_match, conditions,
    actions). Raises ValueError on schema violation — the test suite
    catches missing required fields before deploy."""
    target = path or _RULES_YAML
    raw = target.read_text()
    parsed = yaml.safe_load(raw) or {}
    rules = parsed.get("rules", []) or []
    if not isinstance(rules, list):
        raise ValueError(f"{target}: top-level 'rules' must be a list")
    seen_names: set[str] = set()
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise ValueError(f"{target}: rule[{i}] must be a mapping")
        for k in ("name", "actionMatch", "conditions", "actions"):
            if k not in r:
                raise ValueError(f"{target}: rule[{i}] missing required key '{k}'")
        if r["name"] in seen_names:
            raise ValueError(f"{target}: duplicate rule name '{r['name']}'")
        seen_names.add(r["name"])
    return rules


def compute_yaml_hash(path: Path | None = None) -> str:
    """SHA-256 of the YAML file contents (raw bytes). Used by the drift
    audit to detect "YAML edited, sync not run"."""
    target = path or _RULES_YAML
    return hashlib.sha256(target.read_bytes()).hexdigest()


def read_applied_hash(path: Path | None = None) -> str | None:
    """Read the lock file written by the sync script's last successful
    apply. Returns None if the lock doesn't exist (never synced)."""
    target = path or _RULES_LOCK
    if not target.is_file():
        return None
    raw = target.read_text().strip()
    # Accept either bare hash or `hash <sha>` shape for flexibility.
    if raw.startswith("hash"):
        parts = raw.split()
        if len(parts) >= 2:
            return parts[1]
    return raw or None


def write_applied_hash(hash_hex: str, path: Path | None = None) -> None:
    """Stamp the lock file after a successful apply."""
    target = path or _RULES_LOCK
    target.write_text(hash_hex + "\n")


def fetch_remote_rules(project_override: str | None = None) -> list[dict[str, Any]]:
    """GET all rules currently configured in the given Sentry project
    (defaults to SENTRY_PROJECT env var). Empty list if API unconfigured
    or call fails. Never raises."""
    token, org, project = _credentials(project_override)
    if not (token and org and project):
        return []
    url = f"{_API_BASE}/projects/{org}/{project}/rules/"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            log.warning(
                "sentry_alert_rules: fetch_remote[%s] HTTP %d body=%s",
                project, resp.status_code, resp.text[:200],
            )
            return []
        return resp.json() or []
    except Exception as exc:
        log.warning("sentry_alert_rules: fetch_remote[%s] failed: %s", project, exc)
        return []


def rules_by_project(local: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket local YAML rules by the `project` field. Rules without an
    explicit `project` default to SENTRY_PROJECT (typically the backend
    project). Returned dict keys are project slugs; values are lists
    with the `project` field stripped (Sentry API rejects it)."""
    default_project = os.getenv("SENTRY_PROJECT", "").strip() or "_default"
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in local:
        target = r.get("project") or default_project
        clean = {k: v for k, v in r.items() if k != "project"}
        buckets.setdefault(target, []).append(clean)
    return buckets


def _normalize_rule_for_diff(rule: dict[str, Any]) -> dict[str, Any]:
    """Strip Sentry-server-only fields (id, dateCreated, etc.) so we
    can compare local YAML vs remote API response apples-to-apples."""
    drop = {"id", "dateCreated", "createdBy", "owner", "projects", "snoozeUntil",
            "snooze", "lastTriggered", "status"}
    return {k: v for k, v in rule.items() if k not in drop}


def compute_diff(
    local: list[dict[str, Any]],
    remote: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return {to_create, to_update, to_delete}. Rule identity is `name`."""
    local_by_name = {r["name"]: r for r in local}
    remote_by_name = {r["name"]: r for r in remote}

    to_create: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []
    to_delete: list[dict[str, Any]] = []

    for name, lrule in local_by_name.items():
        if name not in remote_by_name:
            to_create.append(lrule)
            continue
        rrule = remote_by_name[name]
        # Compare normalized payloads — if any difference, queue update.
        if _normalize_rule_for_diff(lrule) != _normalize_rule_for_diff(rrule):
            # Carry the remote id through so apply_diff can address the
            # right rule on the API.
            payload = dict(lrule)
            payload["_remote_id"] = rrule.get("id")
            to_update.append(payload)

    for name, rrule in remote_by_name.items():
        if name not in local_by_name:
            to_delete.append(rrule)

    return {"to_create": to_create, "to_update": to_update, "to_delete": to_delete}


def apply_diff(
    diff: dict[str, list[dict[str, Any]]],
    *,
    dry_run: bool = True,
    delete_unmanaged: bool = False,
    project_override: str | None = None,
) -> dict[str, Any]:
    """POST/PUT/DELETE to make Sentry match the local YAML.

    Args:
      dry_run: when True, prints what would happen + makes ZERO API
        calls. CLI default. Pass --apply on the command line to flip.
      delete_unmanaged: when False (default), rules that exist in
        Sentry but NOT in the YAML are LEFT ALONE — safer behavior to
        avoid clobbering rules someone added in the UI for a one-off
        debugging session. Pass --prune to flip.

    Returns {"created": N, "updated": N, "deleted": N, "skipped_deletes": N,
    "errors": [...]} for caller observability.
    """
    token, org, project = _credentials(project_override)
    summary = {"created": 0, "updated": 0, "deleted": 0, "skipped_deletes": 0, "errors": []}
    if not (token and org and project):
        summary["errors"].append("SENTRY_AUTH_TOKEN/ORG/PROJECT unset")
        return summary

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base = f"{_API_BASE}/projects/{org}/{project}/rules/"

    # CREATE
    for r in diff["to_create"]:
        if dry_run:
            log.info("sentry_alert_rules: [dry-run] CREATE %s", r["name"])
            summary["created"] += 1
            continue
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = client.post(base, json=r, headers=headers)
            if resp.status_code in (200, 201):
                summary["created"] += 1
            else:
                summary["errors"].append(f"CREATE {r['name']}: HTTP {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            summary["errors"].append(f"CREATE {r['name']}: {exc}")

    # UPDATE
    for r in diff["to_update"]:
        rid = r.pop("_remote_id", None)
        if not rid:
            summary["errors"].append(f"UPDATE {r['name']}: missing remote id")
            continue
        if dry_run:
            log.info("sentry_alert_rules: [dry-run] UPDATE %s (id=%s)", r["name"], rid)
            summary["updated"] += 1
            continue
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = client.put(f"{base}{rid}/", json=r, headers=headers)
            if resp.status_code in (200, 204):
                summary["updated"] += 1
            else:
                summary["errors"].append(f"UPDATE {r['name']}: HTTP {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            summary["errors"].append(f"UPDATE {r['name']}: {exc}")

    # DELETE (gated)
    for r in diff["to_delete"]:
        if not delete_unmanaged:
            log.info(
                "sentry_alert_rules: SKIP delete (unmanaged) %s — pass --prune to remove",
                r.get("name"),
            )
            summary["skipped_deletes"] += 1
            continue
        rid = r.get("id")
        if not rid:
            continue
        if dry_run:
            log.info("sentry_alert_rules: [dry-run] DELETE %s (id=%s)", r.get("name"), rid)
            summary["deleted"] += 1
            continue
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = client.delete(f"{base}{rid}/", headers=headers)
            if resp.status_code in (200, 202, 204):
                summary["deleted"] += 1
            else:
                summary["errors"].append(f"DELETE {r.get('name')}: HTTP {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            summary["errors"].append(f"DELETE {r.get('name')}: {exc}")

    return summary


def is_configured() -> bool:
    token, org, project = _credentials()
    return bool(token and org and project)
