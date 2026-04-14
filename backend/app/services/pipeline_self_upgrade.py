"""
pipeline_self_upgrade.py — D5: autonomous scan for security advisories
on Python dependencies and conversion of each finding into a governed
BugFixCandidate that flows through the normal approval pipeline.

Design stance
-------------
The D-tier north-star item describes "auto-merge + auto-deploy" of
security patches. Shipping that literally would bypass the TIER_2
approval gate for anything dep-related — an unacceptable expansion of
blast radius even with the existing safety stack. Instead we:

  1. Run `pip-audit` (JSON mode) once per scheduled window.
  2. For every advisory found, upsert a `BugFixCandidate` with
     `source_type='dep_upgrade'`, `patch_risk_tier=2`, a rich context
     payload describing the CVE, affected version, and fix version.
  3. Hand the candidate to the normal triage → propose → (governed)
     apply pipeline. Operators / governed TIER_1 / TIER_2 weekly batch
     decide whether to ship.
  4. Surface scan outcome in the daily digest.

pip-audit is an optional dependency. If the binary is missing we
degrade to a documented noop so the worker never crashes.

Public API
----------
    run_self_upgrade_scan(db) -> dict
    _discover_vulnerabilities() -> list[dict]            # test seam
    _upsert_candidate_for_vuln(db, vuln) -> int | None   # test seam
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger("pipeline_self_upgrade")

_BACKEND_DIR = "/opt/wishspark/backend"
_VENV_PIP_AUDIT = os.path.join(_BACKEND_DIR, "venv", "bin", "pip-audit")
_SCAN_TIMEOUT_S = 300

# Cooldown between identical (package, vuln_id) candidate emissions.
# Without this, every run would re-upsert and flood the triage queue.
_DEDUP_LOOKBACK_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pip_audit_binary() -> str | None:
    """Locate the pip-audit executable. Prefer the venv, fall back to PATH."""
    if os.path.isfile(_VENV_PIP_AUDIT) and os.access(_VENV_PIP_AUDIT, os.X_OK):
        return _VENV_PIP_AUDIT
    path_binary = shutil.which("pip-audit")
    return path_binary


def _discover_vulnerabilities() -> list[dict]:
    """Run pip-audit in JSON mode and parse the result into a normalized
    list of vuln dicts. Returns [] on binary missing / parse error /
    subprocess failure. Never raises."""
    binary = _pip_audit_binary()
    if not binary:
        log.info("pipeline_self_upgrade: pip-audit not installed — scan skipped")
        return []

    try:
        proc = subprocess.run(
            [binary, "--format", "json", "--progress-spinner=off"],
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.warning("pipeline_self_upgrade: pip-audit timed out")
        return []
    except Exception as exc:
        log.warning("pipeline_self_upgrade: pip-audit invocation failed: %s", exc)
        return []

    # pip-audit returns non-zero when vulnerabilities are found — that's
    # a success-with-findings, not a runtime error. Parse regardless.
    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("pipeline_self_upgrade: pip-audit JSON parse failed: %s", exc)
        return []

    return _normalize_pip_audit_output(parsed)


def _normalize_pip_audit_output(parsed: Any) -> list[dict]:
    """Flatten pip-audit's per-package output into a list of dict records,
    one per (package, vuln). Handles both the new `dependencies` schema
    and the legacy list-of-packages schema."""
    records: list[dict] = []

    if isinstance(parsed, dict) and isinstance(parsed.get("dependencies"), list):
        deps = parsed["dependencies"]
    elif isinstance(parsed, list):
        deps = parsed
    else:
        return []

    for dep in deps:
        if not isinstance(dep, dict):
            continue
        pkg_name = dep.get("name") or dep.get("package")
        pkg_version = dep.get("version")
        vulns = dep.get("vulns") or dep.get("vulnerabilities") or []
        if not pkg_name or not isinstance(vulns, list):
            continue
        for v in vulns:
            if not isinstance(v, dict):
                continue
            vuln_id = v.get("id") or v.get("vuln_id")
            if not vuln_id:
                continue
            fix_versions = v.get("fix_versions") or v.get("fixed_in") or []
            if not isinstance(fix_versions, list):
                fix_versions = [str(fix_versions)]
            records.append({
                "package": pkg_name,
                "current_version": pkg_version or "?",
                "vuln_id": vuln_id,
                "fix_versions": [str(x) for x in fix_versions if x],
                "description": (v.get("description") or "")[:600],
                "aliases": v.get("aliases") or [],
            })

    return records


def _already_pending(db: Session, package: str, vuln_id: str) -> bool:
    """Return True if a non-closed candidate for the same (package, vuln)
    already exists in the dedup window. Uses context_json substring
    match — cheap and sufficient for a weekly scan."""
    from app.models.bugfix_candidate import BugFixCandidate

    cutoff = _now() - timedelta(days=_DEDUP_LOOKBACK_DAYS)
    needle = f'"vuln_id": "{vuln_id}"'
    try:
        exists = (
            db.query(BugFixCandidate.id)
            .filter(
                BugFixCandidate.source_type == "dep_upgrade",
                BugFixCandidate.created_at >= cutoff,
                BugFixCandidate.status.notin_(
                    ["rejected", "applied", "rolled_back", "discarded"]
                ),
                BugFixCandidate.context_json.like(f"%{needle}%"),
            )
            .first()
        )
        return exists is not None
    except Exception as exc:
        log.warning("pipeline_self_upgrade: dedup query failed: %s", exc)
        return False


def _upsert_candidate_for_vuln(db: Session, vuln: dict) -> int | None:
    """Create a TIER_2 BugFixCandidate for this vulnerability if none
    already exists in the dedup window. Returns the new id, or None
    if skipped."""
    from app.models.bugfix_candidate import BugFixCandidate

    package = vuln["package"]
    vuln_id = vuln["vuln_id"]

    if _already_pending(db, package, vuln_id):
        return None

    fix_versions = vuln.get("fix_versions") or []
    fix_hint = f" (fix: {', '.join(fix_versions)})" if fix_versions else ""
    title = f"Security advisory {vuln_id} in {package} {vuln['current_version']}{fix_hint}"
    summary = (
        f"pip-audit flagged {package}=={vuln['current_version']} as vulnerable "
        f"({vuln_id}). {vuln.get('description', '')}"
    )[:2000]

    context_payload = {
        "package": package,
        "current_version": vuln["current_version"],
        "vuln_id": vuln_id,
        "fix_versions": fix_versions,
        "aliases": vuln.get("aliases", []),
        "discovered_at": _now().isoformat(),
        "scanner": "pip-audit",
    }

    try:
        candidate = BugFixCandidate(
            source_type="dep_upgrade",
            source_ref=f"{package}:{vuln_id}",
            title=title[:256],
            summary=summary,
            status="open",
            affected_domain="dependencies",
            context_json=json.dumps(context_payload, default=str),
            patch_risk_tier=2,
        )
        db.add(candidate)
        db.flush()
        return candidate.id
    except Exception as exc:
        log.warning(
            "pipeline_self_upgrade: candidate creation failed for %s/%s: %s",
            package, vuln_id, exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


def run_self_upgrade_scan(db: Session) -> dict:
    """Entry point for the worker. Scans pip deps for advisories and
    proposes one TIER_2 bugfix candidate per new finding. Returns a
    report dict suitable for logging + digest surfacing."""
    vulns = _discover_vulnerabilities()

    report: dict[str, Any] = {
        "scanned_at": _now().isoformat(),
        "vulnerabilities_found": len(vulns),
        "candidates_created": 0,
        "candidates_skipped_dedup": 0,
    }

    if not vulns:
        return report

    created_ids: list[int] = []
    for v in vulns:
        new_id = _upsert_candidate_for_vuln(db, v)
        if new_id is None:
            report["candidates_skipped_dedup"] += 1
        else:
            created_ids.append(new_id)

    report["candidates_created"] = len(created_ids)
    report["created_ids"] = created_ids

    if created_ids:
        log.info(
            "pipeline_self_upgrade: %d new TIER_2 candidates from %d advisories",
            len(created_ids), len(vulns),
        )
    return report
