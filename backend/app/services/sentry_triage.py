"""
sentry_triage.py — Incident intake, dedup, triage packet generation.

End-to-end pipeline:
    ingest_email(db, message_id, subject, body, from, to)
      → parse → dedup → store → group → generate triage packet

Public interface:
    ingest_email(db, ...) -> dict          — full intake pipeline
    generate_triage_packet(db, incident_id) -> dict  — AI-ready debugging input
    get_incident_families(db, ...) -> list  — grouped incident view
    get_triage_queue(db) -> list            — pending AI-triage items

Called by:
    - POST /webhooks/resend/inbound (real-time email intake)
    - agent_worker (periodic triage packet generation)
    - GET /ops/incidents/* (operator inspection)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.sentry_incident import SentryIncident
from app.services.sentry_parser import parse_sentry_email, parse_sentry_webhook

log = logging.getLogger("sentry_triage")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Intake pipeline
# ---------------------------------------------------------------------------

def ingest_email(
    db: Session,
    *,
    message_id: str | None,
    subject: str | None,
    body: str | None,
    from_addr: str | None = None,
    to_addr: str | None = None,
) -> dict:
    """
    Full intake pipeline for a Sentry alert email.

    Steps:
        1. Exact dedup on message_id
        2. Store raw email (DB-first, before parsing)
        3. Parse into structured fields
        4. Compute fingerprint
        5. Group into incident family
        6. Update recurrence counts
        7. Mark for triage packet generation

    Returns:
        {"status": "new" | "duplicate" | "parse_error",
         "incident_id": int | None,
         "fingerprint": str | None,
         "family_head_id": int | None,
         "recurrence_count": int}
    """
    # --- Step 1: Exact dedup ---
    if message_id:
        existing = (
            db.query(SentryIncident.id)
            .filter(SentryIncident.source_message_id == message_id)
            .first()
        )
        if existing:
            log.info("sentry_triage: duplicate message_id=%s → incident=%d", message_id, existing.id)
            return {
                "status": "duplicate",
                "incident_id": existing.id,
                "fingerprint": None,
                "family_head_id": None,
                "recurrence_count": 0,
            }

    # --- Step 1b: Noise denylist (founder direttiva 2026-05-05 0-error
    # mandate). Drop known-false-positive titles at intake — they are
    # expected dev-misconfiguration responses (e.g. /ops/* endpoints
    # raising 500 when OPS_API_KEY isn't set), not real bugs. Filtering
    # at intake prevents sentry_incidents accumulation, sentry_regression
    # alerts, and bugfix-triage waste. Generalized 2026-05-06 via
    # app.core.sentry_noise_filter to cover ALL secret-class env vars,
    # not just OPS_API_KEY (G7 close).
    from app.core.sentry_noise_filter import is_noise as _is_sentry_noise
    composite_text = f"{subject or ''}\n{body or ''}"
    if _is_sentry_noise(composite_text):
        log.info(
            "sentry_triage: noise-denylist drop message_id=%s subject=%s",
            message_id, (subject or "")[:80],
        )
        return {
            "status": "noise_dropped",
            "incident_id": None,
            "fingerprint": None,
            "family_head_id": None,
            "recurrence_count": 0,
        }

    # --- Step 2: Store raw (DB-first) ---
    incident = SentryIncident(
        source_message_id=message_id,
        source_type="email",
        raw_subject=subject[:512] if subject else None,
        raw_body=body[:50000] if body else None,  # cap at 50KB
        raw_from=from_addr[:256] if from_addr else None,
        raw_to=to_addr[:256] if to_addr else None,
        status="received",
    )
    db.add(incident)
    db.flush()
    log.info("sentry_triage: stored raw incident=%d message_id=%s", incident.id, message_id)

    # --- Step 3: Parse ---
    try:
        parsed = parse_sentry_email(subject, body, from_addr)
    except Exception as exc:
        incident.status = "parse_error"
        incident.parse_error = f"parse_crash: {exc}"[:512]
        _alert_parse_failure(db, incident)
        db.flush()
        return {
            "status": "parse_error",
            "incident_id": incident.id,
            "fingerprint": None,
            "family_head_id": None,
            "recurrence_count": 0,
        }

    # --- Step 4: Apply parsed fields ---
    _apply_parsed_fields(incident, parsed)

    if parsed.get("parse_error"):
        incident.parse_error = parsed["parse_error"][:512]
        incident.status = "parse_error"
        # Fire ops_alert for parse failures so they are never silent
        _alert_parse_failure(db, incident)
    else:
        incident.status = "parsed"

    # --- Step 5: Group into family ---
    family_head_id = None
    recurrence_count = 1

    if incident.fingerprint:
        # Find existing family head (first incident with same fingerprint)
        family_head = (
            db.query(SentryIncident)
            .filter(
                SentryIncident.fingerprint == incident.fingerprint,
                SentryIncident.id != incident.id,
                SentryIncident.family_head_id.is_(None),  # is itself a head
            )
            .order_by(SentryIncident.created_at.asc())
            .first()
        )

        if family_head:
            # Join existing family
            incident.family_head_id = family_head.id
            family_head_id = family_head.id

            # Count total family members
            recurrence_count = (
                db.query(func.count(SentryIncident.id))
                .filter(
                    SentryIncident.fingerprint == incident.fingerprint,
                )
                .scalar() or 1
            )

            # Update head's recurrence count
            family_head.recurrence_count = recurrence_count
            log.info(
                "sentry_triage: incident=%d joined family=%d (recurrence #%d) fp=%s",
                incident.id, family_head.id, recurrence_count,
                incident.fingerprint_input,
            )
        else:
            # This is the first — becomes the family head
            family_head_id = incident.id
            log.info(
                "sentry_triage: incident=%d is new family head fp=%s",
                incident.id, incident.fingerprint_input,
            )

    # --- Step 6: Mark for triage ---
    if incident.status == "parsed":
        incident.ai_triage_status = "pending"

    incident.recurrence_count = recurrence_count
    db.flush()

    return {
        "status": "new" if incident.status == "parsed" else incident.status,
        "incident_id": incident.id,
        "fingerprint": incident.fingerprint,
        "family_head_id": family_head_id,
        "recurrence_count": recurrence_count,
    }


def _apply_parsed_fields(incident: SentryIncident, parsed: dict) -> None:
    """Apply parsed fields to an incident record (shared by email + webhook intake)."""
    incident.error_type = parsed.get("error_type")
    incident.error_title = (parsed.get("error_title") or "")[:512] or None
    incident.project = parsed.get("project")
    incident.environment = parsed.get("environment")
    incident.severity = parsed.get("severity")
    incident.culprit = parsed.get("culprit")
    incident.stack_trace = parsed.get("stack_trace")
    incident.sentry_issue_url = parsed.get("sentry_issue_url")
    incident.fingerprint = parsed.get("fingerprint")
    incident.fingerprint_input = parsed.get("fingerprint_input")
    incident.subsystem_class = parsed.get("subsystem_class")
    incident.merchant_impact = parsed.get("merchant_impact")
    incident.affected_shop = (parsed.get("affected_shop") or "")[:256] or None
    incident.release = (parsed.get("release") or "")[:128] or None


def _alert_parse_failure(db: Session, incident: SentryIncident) -> None:
    """Fire an ops_alert when email/webhook parsing fails. Never silent."""
    try:
        from app.services.alerting import write_alert
        # heal-detection: triage event — per-incident log entry
        write_alert(
            db,
            severity="warning",
            source="sentry_triage",
            alert_type="sentry_parse_failure",
            summary=(
                f"Sentry incident #{incident.id} failed parsing: "
                f"{(incident.parse_error or 'unknown')[:120]}"
            ),
            detail={
                "incident_id": incident.id,
                "source_type": incident.source_type,
                "raw_subject": incident.raw_subject,
                "parse_error": incident.parse_error,
            },
        )
    except Exception as exc:
        log.warning("sentry_triage: failed to alert on parse error: %s", exc)


# ---------------------------------------------------------------------------
# Webhook intake pipeline (native Sentry JSON — no email parsing)
# ---------------------------------------------------------------------------

def ingest_webhook(
    db: Session,
    *,
    payload: dict,
    sentry_event_id: str | None = None,
) -> dict:
    """
    Intake pipeline for native Sentry webhook payloads.

    Same dedup/grouping/triage logic as ingest_email, but parses
    structured JSON instead of email HTML. Produces identical
    SentryIncident records for downstream pipeline compatibility.

    Returns same dict shape as ingest_email for API consistency.
    """
    # --- Step 1: Derive a stable dedup key ---
    # Use Sentry event ID or issue ID as the message_id equivalent
    data = payload.get("data", {})
    issue = data.get("issue", {})
    event = data.get("event", {})

    if not sentry_event_id:
        sentry_event_id = (
            event.get("event_id")
            or issue.get("id")
            or None
        )
    dedup_key = f"sentry_wh:{sentry_event_id}" if sentry_event_id else None

    # --- Step 2: Exact dedup ---
    if dedup_key:
        existing = (
            db.query(SentryIncident.id)
            .filter(SentryIncident.source_message_id == dedup_key)
            .first()
        )
        if existing:
            log.info("sentry_triage: duplicate webhook event=%s → incident=%d", dedup_key, existing.id)
            return {
                "status": "duplicate",
                "incident_id": existing.id,
                "fingerprint": None,
                "family_head_id": None,
                "recurrence_count": 0,
            }

    # --- Step 2b: Noise denylist (parity with ingest_email) ---
    # Drop expected operational noise BEFORE storing. Covers:
    #   - Secret-class env var missing 500s (`is_noise` regex)
    #   - Worker graceful-shutdown signal exceptions (KeyboardInterrupt
    #     / SystemExit / asyncio.CancelledError) raised at top of
    #     worker main loops on PM2 reload. Born 2026-05-13 after 11
    #     such incidents pushed the capillary scope probe to RED
    #     during a 35-commit deploy storm.
    #
    # Sentry webhook payload schema: `issue.title` is the human-
    # formatted label ("KeyboardInterrupt" bare OR "KeyboardInterrupt:
    # <msg>" colon-suffix). The canonical bare exception class lives
    # at `issue.metadata.type`. Both checked — Agent-review finding
    # 2026-05-13: prior version checking only `title` with exact-match
    # would silently let colon-formatted payloads through.
    from app.core.sentry_noise_filter import (
        is_noise as _is_sentry_noise,
        is_shutdown_signal_type as _is_shutdown_signal,
    )
    issue_title = issue.get("title") or ""
    issue_culprit = issue.get("culprit") or ""
    issue_meta_type = (issue.get("metadata") or {}).get("type") or ""
    noise_candidate = f"{issue_title}\n{issue_culprit}"
    if (
        _is_sentry_noise(noise_candidate)
        or _is_shutdown_signal(issue_title)
        or _is_shutdown_signal(issue_meta_type)
    ):
        log.info(
            "sentry_triage: noise-denylist drop webhook event=%s title=%s type=%s",
            dedup_key, issue_title[:80], issue_meta_type[:40],
        )
        return {
            "status": "noise_dropped",
            "incident_id": None,
            "fingerprint": None,
            "family_head_id": None,
            "recurrence_count": 0,
        }

    # --- Step 3: Store raw (DB-first) ---
    import json as _json
    raw_body = _json.dumps(payload, default=str)[:50000]

    incident = SentryIncident(
        source_message_id=dedup_key,
        source_type="sentry_webhook",
        raw_subject=(issue.get("title") or "")[:512] or None,
        raw_body=raw_body,
        raw_from="sentry-webhook",
        status="received",
    )
    db.add(incident)
    db.flush()
    log.info("sentry_triage: stored webhook incident=%d event=%s", incident.id, dedup_key)

    # --- Step 4: Parse (structured — no regex) ---
    try:
        parsed = parse_sentry_webhook(payload)
    except Exception as exc:
        incident.status = "parse_error"
        incident.parse_error = f"webhook_parse_crash: {exc}"[:512]
        _alert_parse_failure(db, incident)
        db.flush()
        return {
            "status": "parse_error",
            "incident_id": incident.id,
            "fingerprint": None,
            "family_head_id": None,
            "recurrence_count": 0,
        }

    # --- Step 5: Apply parsed fields ---
    _apply_parsed_fields(incident, parsed)

    if parsed.get("parse_error"):
        incident.parse_error = parsed["parse_error"][:512]
        incident.status = "parse_error"
        _alert_parse_failure(db, incident)
    else:
        incident.status = "parsed"

    # --- Step 6: Group into family (same logic as email intake) ---
    family_head_id = None
    recurrence_count = 1

    if incident.fingerprint:
        family_head = (
            db.query(SentryIncident)
            .filter(
                SentryIncident.fingerprint == incident.fingerprint,
                SentryIncident.id != incident.id,
                SentryIncident.family_head_id.is_(None),
            )
            .order_by(SentryIncident.created_at.asc())
            .first()
        )

        if family_head:
            incident.family_head_id = family_head.id
            family_head_id = family_head.id

            recurrence_count = (
                db.query(func.count(SentryIncident.id))
                .filter(SentryIncident.fingerprint == incident.fingerprint)
                .scalar() or 1
            )
            family_head.recurrence_count = recurrence_count
            log.info(
                "sentry_triage: webhook incident=%d joined family=%d (recurrence #%d)",
                incident.id, family_head.id, recurrence_count,
            )
        else:
            family_head_id = incident.id
            log.info(
                "sentry_triage: webhook incident=%d is new family head fp=%s",
                incident.id, incident.fingerprint_input,
            )

    # --- Step 7: Mark for triage ---
    if incident.status == "parsed":
        incident.ai_triage_status = "pending"

    incident.recurrence_count = recurrence_count
    db.flush()

    return {
        "status": "new" if incident.status == "parsed" else incident.status,
        "incident_id": incident.id,
        "fingerprint": incident.fingerprint,
        "family_head_id": family_head_id,
        "recurrence_count": recurrence_count,
    }


# ---------------------------------------------------------------------------
# Triage packet generation
# ---------------------------------------------------------------------------

def generate_triage_packet(db: Session, incident_id: int) -> dict | None:
    """
    Generate a structured AI-ready debugging packet for an incident.

    The packet contains everything a debugging agent needs:
        - normalized error summary
        - severity and affected subsystem
        - stack trace and culprit
        - recurrence history
        - existing related lessons
        - existing related bugfix candidates

    Returns the packet dict, or None if incident not found.
    Updates the incident's triage_packet and ai_triage_status.
    """
    incident = db.get(SentryIncident, incident_id)
    if not incident:
        return None

    # subsystem/criticality were inferred via project_brain.classify_file
    # (deleted with old brain Stage 2-E supersession). Fall back to neutral
    # defaults — Sentry triage doesn't gate on these fields downstream.
    subsystem = "unknown"
    criticality = "medium"

    # --- Get recurrence history ---
    family_incidents = []
    if incident.fingerprint:
        family = (
            db.query(
                SentryIncident.id,
                SentryIncident.created_at,
                SentryIncident.status,
                SentryIncident.error_title,
            )
            .filter(SentryIncident.fingerprint == incident.fingerprint)
            .order_by(SentryIncident.created_at.desc())
            .limit(10)
            .all()
        )
        family_incidents = [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                "status": r.status,
            }
            for r in family
        ]

    # --- Release correlation ---
    release_context = _build_release_context(db, incident)

    # --- Cross-family related families ---
    related_families = _find_related_families(db, incident)

    # --- Build packet ---
    packet = {
        "incident_id": incident.id,
        "generated_at": _now().isoformat() + "Z",

        # Error identity
        "error_type": incident.error_type,
        "error_title": incident.error_title,
        "severity": incident.severity,
        "project": incident.project,
        "environment": incident.environment,

        # Location
        "culprit": incident.culprit,
        "subsystem": subsystem,
        "criticality": criticality,
        "subsystem_class": incident.subsystem_class or "unknown",
        "merchant_impact": incident.merchant_impact or "low",
        "affected_shop": incident.affected_shop,

        # Evidence
        "stack_trace": incident.stack_trace,
        "sentry_issue_url": incident.sentry_issue_url,

        # Grouping
        "fingerprint": incident.fingerprint,
        "fingerprint_input": incident.fingerprint_input,
        "recurrence_count": incident.recurrence_count,
        "family_history": family_incidents,

        # First/last seen
        "first_seen": family_incidents[-1]["created_at"] if family_incidents else (
            incident.created_at.isoformat() + "Z" if incident.created_at else None
        ),
        "last_seen": family_incidents[0]["created_at"] if family_incidents else (
            incident.created_at.isoformat() + "Z" if incident.created_at else None
        ),

        # Release context
        "release": incident.release,
        "release_context": release_context,
        "is_regression_candidate": incident.is_regression_candidate,

        # Cross-family root cause detection
        "related_families": related_families,
        "co_occurring_families": [],
        "temporal_patterns": {"leading_indicators": [], "trailing_effects": []},

        # Root-cause hints from parsing
        "probable_root_cause_hints": _generate_hints(incident),
    }

    # --- Store and update status ---
    incident.triage_packet = json.dumps(packet, default=str)
    incident.ai_triage_status = "ready"
    if incident.status == "parsed":
        incident.status = "triaged"
    db.flush()

    log.info(
        "sentry_triage: packet generated for incident=%d fp=%s subsystem=%s",
        incident.id, incident.fingerprint_input, subsystem,
    )
    return packet


def _build_release_context(db: Session, incident: SentryIncident) -> dict:
    """
    Build release correlation context for an incident.

    Checks whether this error pattern appeared only after the current release,
    which makes it a regression candidate. Also reports per-release incident counts
    for this fingerprint to spot release-correlated spikes.
    """
    result: dict = {
        "release": incident.release,
        "is_regression_candidate": False,
        "incident_count_this_release": 0,
        "incident_count_previous_releases": 0,
    }

    if not incident.fingerprint:
        return result

    try:
        # Count incidents by release for this fingerprint
        release_counts = (
            db.query(SentryIncident.release, func.count(SentryIncident.id))
            .filter(SentryIncident.fingerprint == incident.fingerprint)
            .group_by(SentryIncident.release)
            .all()
        )

        counts_by_release = {r: c for r, c in release_counts}
        current_release = incident.release

        this_release_count = counts_by_release.get(current_release, 0)
        # Sum up all counts for releases other than current
        other_count = sum(c for r, c in counts_by_release.items() if r != current_release)

        result["incident_count_this_release"] = this_release_count
        result["incident_count_previous_releases"] = other_count

        # Regression heuristic: error appears ONLY in the current release
        # (or overwhelmingly in it), and current release is known
        if current_release and other_count == 0 and this_release_count >= 1:
            result["is_regression_candidate"] = True
            incident.is_regression_candidate = "yes"
        else:
            incident.is_regression_candidate = "no"

    except Exception as exc:
        log.warning("sentry_triage: release correlation failed: %s", exc)

    return result


def _find_related_families(db: Session, incident: SentryIncident) -> list[dict]:
    """
    Find other incident families that may share the same root cause.

    Uses deterministic similarity: same error_type AND overlapping culprit module.
    No LLM calls — fast, cheap, predictable.

    Returns a list of related family head summaries (max 5).
    """
    if not incident.error_type:
        return []

    try:
        # Find other family heads with the same error_type but different fingerprint
        related_heads = (
            db.query(
                SentryIncident.id,
                SentryIncident.fingerprint,
                SentryIncident.fingerprint_input,
                SentryIncident.error_type,
                SentryIncident.error_title,
                SentryIncident.culprit,
                SentryIncident.recurrence_count,
                SentryIncident.severity,
            )
            .filter(
                SentryIncident.error_type == incident.error_type,
                SentryIncident.family_head_id.is_(None),  # only heads
                SentryIncident.fingerprint != incident.fingerprint,
                SentryIncident.status.notin_(["ignored", "resolved"]),
            )
            .order_by(SentryIncident.recurrence_count.desc())
            .limit(20)
            .all()
        )

        if not related_heads:
            return []

        # Score similarity based on culprit module overlap
        incident_module = _extract_module(incident.culprit)
        results = []

        for head in related_heads:
            head_module = _extract_module(head.culprit)
            # Same module = strong signal; same parent = moderate
            if incident_module and head_module:
                if incident_module == head_module:
                    similarity = "same_module"
                elif incident_module.rsplit("/", 1)[0] == head_module.rsplit("/", 1)[0]:
                    similarity = "same_package"
                else:
                    similarity = "same_error_type_only"
            else:
                similarity = "same_error_type_only"

            # Only include if at least same_package
            if similarity in ("same_module", "same_package"):
                results.append({
                    "family_head_id": head.id,
                    "fingerprint": head.fingerprint,
                    "error_title": head.error_title,
                    "culprit": head.culprit,
                    "recurrence_count": head.recurrence_count,
                    "similarity": similarity,
                })

        return results[:5]

    except Exception as exc:
        log.warning("sentry_triage: related family search failed: %s", exc)
        return []




def _extract_module(culprit: str | None) -> str | None:
    """Extract the module/file path from a culprit string, normalized."""
    if not culprit:
        return None
    import re
    # Strip line numbers and extensions
    norm = re.sub(r":\d+$", "", culprit.strip())
    norm = re.sub(r"\.(py|js|ts|tsx)$", "", norm)
    norm = re.sub(r"^.*/(?:backend|app)/", "app/", norm)
    return norm


def _generate_hints(incident: SentryIncident) -> list[str]:
    """Generate probable root-cause hints from parsed data."""
    hints: list[str] = []

    if incident.error_type:
        if incident.error_type == "NameError":
            hints.append("Missing import or undefined variable — check recent code changes")
        elif incident.error_type == "AttributeError":
            hints.append("Object missing expected attribute — check for None values or API contract changes")
        elif incident.error_type == "TypeError":
            hints.append("Type mismatch — check function signatures and argument passing")
        elif incident.error_type == "KeyError":
            hints.append("Missing dict key — check API response schema or data model changes")
        elif incident.error_type == "IntegrityError":
            hints.append("Database constraint violation — check unique/foreign key constraints")
        elif incident.error_type == "OperationalError":
            hints.append("Database connection or query issue — check connection pool and query syntax")

    if incident.culprit:
        if "webhook" in incident.culprit.lower():
            hints.append("Webhook handler — check Shopify API changes or payload format")
        elif "worker" in incident.culprit.lower():
            hints.append("Background worker — check for stale connections or race conditions")
        elif "oauth" in incident.culprit.lower():
            hints.append("OAuth flow — check session/cookie handling and redirect logic")

    if incident.stack_trace and "connection" in incident.stack_trace.lower():
        hints.append("Possible database connection issue — check connection pool exhaustion")

    return hints


# ---------------------------------------------------------------------------
# Query helpers (for ops endpoints)
# ---------------------------------------------------------------------------

def get_incident_families(
    db: Session,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Get incident families (grouped by fingerprint).

    Returns family heads with recurrence counts, latest occurrence, and status.
    """
    q = db.query(SentryIncident).filter(
        SentryIncident.family_head_id.is_(None),  # only heads
    )
    if status:
        q = q.filter(SentryIncident.status == status)

    heads = q.order_by(SentryIncident.created_at.desc()).limit(limit).all()

    return [
        {
            "id": h.id,
            "created_at": h.created_at.isoformat() + "Z" if h.created_at else None,
            "error_type": h.error_type,
            "error_title": h.error_title,
            "culprit": h.culprit,
            "severity": h.severity,
            "fingerprint": h.fingerprint,
            "fingerprint_input": h.fingerprint_input,
            "recurrence_count": h.recurrence_count,
            "status": h.status,
            "ai_triage_status": h.ai_triage_status,
            "subsystem_class": h.subsystem_class,
            "merchant_impact": h.merchant_impact,
        }
        for h in heads
    ]


def get_triage_queue(db: Session, limit: int = 20) -> list[dict]:
    """
    Get incidents pending AI triage (packet ready but not yet consumed).
    """
    incidents = (
        db.query(SentryIncident)
        .filter(SentryIncident.ai_triage_status == "ready")
        .order_by(SentryIncident.created_at.desc())
        .limit(limit)
        .all()
    )

    results = []
    for inc in incidents:
        packet = None
        if inc.triage_packet:
            try:
                packet = json.loads(inc.triage_packet)
            except (json.JSONDecodeError, ValueError):
                pass

        results.append({
            "incident_id": inc.id,
            "error_type": inc.error_type,
            "error_title": inc.error_title,
            "severity": inc.severity,
            "culprit": inc.culprit,
            "recurrence_count": inc.recurrence_count,
            "created_at": inc.created_at.isoformat() + "Z" if inc.created_at else None,
            "triage_packet": packet,
        })

    return results


def run_triage_generation(db: Session, max_per_cycle: int = 5) -> dict:
    """
    Generate triage packets for pending incidents.

    Called by agent_worker every 15 minutes.
    Returns: {"generated": N, "skipped": N, "errors": N}
    """
    summary = {"generated": 0, "skipped": 0, "errors": 0}

    pending = (
        db.query(SentryIncident)
        .filter(
            SentryIncident.ai_triage_status == "pending",
            SentryIncident.status.in_(["parsed"]),
        )
        .order_by(SentryIncident.created_at.asc())
        .limit(max_per_cycle)
        .all()
    )

    for inc in pending:
        try:
            packet = generate_triage_packet(db, inc.id)
            if packet:
                summary["generated"] += 1
            else:
                summary["skipped"] += 1
        except Exception as exc:
            summary["errors"] += 1
            log.warning("sentry_triage: packet generation failed for incident=%d: %s", inc.id, exc)

    return summary


# ---------------------------------------------------------------------------
# Triage consumer — convert ready incidents into bugfix candidates
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Candidate creation policy — severity × subsystem × merchant-impact aware
# ---------------------------------------------------------------------------
#
# The goal: critical merchant-facing errors become candidates immediately.
# Warning-level frontend noise requires high recurrence to prove it matters.
#
# Policy table (recurrence threshold required before candidate creation):
#
#   severity    | merchant_impact=high | medium | low/none | frontend
#   ------------|----------------------|--------|----------|----------
#   critical    | 1 (immediate)        | 1      | 1        | 2
#   error       | 2                    | 2      | 3        | 5
#   warning     | 3                    | 5      | 5        | 8
#
# Frontend dashboard errors always require higher recurrence to filter noise.

_RECURRENCE_POLICY: dict[tuple[str, str], int] = {
    # (severity, merchant_impact) → minimum recurrence
    ("critical", "high"): 1,
    ("critical", "medium"): 1,
    ("critical", "low"): 1,
    ("critical", "none"): 1,
    ("error", "high"): 2,
    ("error", "medium"): 2,
    ("error", "low"): 3,
    ("error", "none"): 3,
    ("warning", "high"): 3,
    ("warning", "medium"): 5,
    ("warning", "low"): 5,
    ("warning", "none"): 5,
}

# Frontend dashboard incidents get an additional recurrence multiplier
_FRONTEND_RECURRENCE_MULTIPLIER = 2
