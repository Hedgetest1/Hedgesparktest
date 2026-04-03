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
    incident = db.query(SentryIncident).get(incident_id)
    if not incident:
        return None

    # --- Determine affected subsystem via project_brain ---
    subsystem = "unknown"
    criticality = "medium"
    try:
        from app.services.project_brain import classify_file
        if incident.culprit:
            classification = classify_file(incident.culprit)
            subsystem = classification.get("domain", "unknown")
            criticality = classification.get("criticality", "medium")
    except Exception:
        pass

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

    # --- Find related lessons ---
    related_lessons = []
    try:
        from app.models.system_lesson import SystemLesson
        if subsystem != "unknown":
            lessons = (
                db.query(SystemLesson.id, SystemLesson.summary, SystemLesson.lesson_type, SystemLesson.confidence)
                .filter(
                    SystemLesson.domain == subsystem,
                    SystemLesson.status == "active",
                )
                .order_by(SystemLesson.confidence.desc())
                .limit(5)
                .all()
            )
            related_lessons = [
                {"id": l.id, "summary": l.summary, "type": l.lesson_type, "confidence": l.confidence}
                for l in lessons
            ]
    except Exception:
        pass

    # --- Find related bugfix candidates ---
    related_candidates = []
    try:
        from app.models.bugfix_candidate import BugFixCandidate
        if incident.error_title:
            # Search by similar title (prefix match)
            title_prefix = (incident.error_type or incident.error_title or "")[:50]
            if title_prefix:
                candidates = (
                    db.query(
                        BugFixCandidate.id,
                        BugFixCandidate.title,
                        BugFixCandidate.status,
                        BugFixCandidate.outcome_status,
                    )
                    .filter(BugFixCandidate.title.ilike(f"%{title_prefix}%"))
                    .order_by(BugFixCandidate.created_at.desc())
                    .limit(5)
                    .all()
                )
                related_candidates = [
                    {"id": c.id, "title": c.title, "status": c.status, "outcome": c.outcome_status}
                    for c in candidates
                ]
    except Exception:
        pass

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
        "co_occurring_families": _get_co_occurring(db, incident),
        "temporal_patterns": _get_temporal_patterns(db, incident),

        # Related system knowledge
        "related_lessons": related_lessons,
        "related_bugfix_candidates": related_candidates,

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
        log.debug("sentry_triage: release correlation failed: %s", exc)

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
        log.debug("sentry_triage: related family search failed: %s", exc)
        return []


def _get_co_occurring(db: Session, incident: SentryIncident) -> list[dict]:
    """Get co-occurring families from scoring_calibration (if available)."""
    if not incident.fingerprint:
        return []
    try:
        from app.services.scoring_calibration import find_co_occurring_families
        return find_co_occurring_families(db, incident.fingerprint)
    except Exception:
        return []


def _get_temporal_patterns(db: Session, incident: SentryIncident) -> dict:
    """Get leading/trailing temporal patterns for this incident family."""
    if not incident.fingerprint:
        return {"leading_indicators": [], "trailing_effects": []}
    try:
        from app.services.scoring_calibration import find_temporal_patterns
        return find_temporal_patterns(db, incident.fingerprint)
    except Exception:
        return {"leading_indicators": [], "trailing_effects": []}


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
            "linked_bugfix_candidate_id": h.linked_bugfix_candidate_id,
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

# Severity levels that always bypass recurrence (even at 1 occurrence)
_IMMEDIATE_SEVERITIES = {"critical"}


def _get_recurrence_threshold(incident: SentryIncident) -> int:
    """
    Compute the recurrence threshold for a specific incident based on
    severity, merchant impact, and subsystem class.
    """
    severity = incident.severity or "error"
    merchant_impact = incident.merchant_impact or "low"
    subsystem_class = incident.subsystem_class or "unknown"

    # Look up base threshold
    base = _RECURRENCE_POLICY.get((severity, merchant_impact))
    if base is None:
        # Unknown severity: use conservative default
        base = 3

    # Frontend noise filter: require more recurrence
    if subsystem_class == "frontend_dashboard":
        base = max(base, base * _FRONTEND_RECURRENCE_MULTIPLIER)

    return base


def consume_triage_queue(db: Session, max_per_cycle: int = 5) -> dict:
    """
    Consume triaged incidents and create bugfix candidates.

    Only promotes incidents that are:
      - family heads (not recurrences — the head represents the family)
      - recurring (recurrence_count >= 2) OR severity is critical
      - not already linked to a bugfix candidate
      - not thrashing (3+ failed fix attempts)

    For each promoted incident:
      1. Create a BugFixCandidate with full triage context
      2. Set risk tier based on subsystem sensitivity
      3. Link the incident to the candidate
      4. Mark ai_triage_status = "consumed"

    Returns: {"consumed": N, "skipped": N, "deduped": N, "suppressed": N, "errors": N}
    """
    summary = {"consumed": 0, "skipped": 0, "deduped": 0, "suppressed": 0, "errors": 0}

    # Find ready incidents that are family heads (not members)
    ready = (
        db.query(SentryIncident)
        .filter(
            SentryIncident.ai_triage_status == "ready",
            SentryIncident.family_head_id.is_(None),  # only family heads
            SentryIncident.linked_bugfix_candidate_id.is_(None),  # not already linked
        )
        .order_by(SentryIncident.created_at.asc())
        .limit(max_per_cycle)
        .all()
    )

    for inc in ready:
        try:
            result = _consume_one_incident(db, inc, summary)
            if result == "consumed":
                summary["consumed"] += 1
            elif result == "skipped":
                summary["skipped"] += 1
            # deduped/suppressed are incremented inside _consume_one_incident
        except Exception as exc:
            summary["errors"] += 1
            log.warning(
                "sentry_triage: consume failed for incident=%d: %s",
                inc.id, exc,
            )

    return summary


def _consume_one_incident(
    db: Session,
    incident: SentryIncident,
    summary: dict,
) -> str:
    """
    Process a single triaged incident. Returns "consumed", "skipped",
    "deduped", or "suppressed".
    """
    # --- Gate: recurrence threshold (severity × impact × subsystem aware) ---
    threshold = _get_recurrence_threshold(incident)
    is_above_threshold = incident.recurrence_count >= threshold

    if not is_above_threshold:
        # Not actionable yet — mark skipped but keep watching
        # (future recurrences will re-evaluate via family head update)
        incident.ai_triage_status = "skipped"
        db.flush()
        log.info(
            "sentry_triage: skipped incident=%d (recurrence=%d, threshold=%d, "
            "severity=%s, impact=%s, subsystem=%s) — below threshold",
            incident.id, incident.recurrence_count, threshold,
            incident.severity, incident.merchant_impact, incident.subsystem_class,
        )
        return "skipped"

    # --- Dedup: check if a candidate already exists for this fingerprint ---
    source_ref = f"sentry_fp:{incident.fingerprint}" if incident.fingerprint else f"sentry_id:{incident.id}"

    from app.models.bugfix_candidate import BugFixCandidate
    existing = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "sentry_incident",
            BugFixCandidate.source_ref == source_ref,
            BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed", "approved", "applying"]),
        )
        .first()
    )
    if existing:
        # Link incident to existing candidate
        incident.linked_bugfix_candidate_id = existing.id
        incident.ai_triage_status = "consumed"
        incident.status = "linked"
        db.flush()
        summary["deduped"] += 1
        log.info(
            "sentry_triage: incident=%d deduped → existing candidate=%d",
            incident.id, existing.id,
        )
        return "deduped"

    # --- Thrash check: skip if this error pattern keeps failing ---
    try:
        from app.services.loop_health import is_source_thrashing
        if is_source_thrashing(db, "sentry_incident", source_ref):
            incident.ai_triage_status = "skipped"
            db.flush()
            summary["suppressed"] += 1
            log.info(
                "sentry_triage: suppressed thrashing incident=%d ref=%s",
                incident.id, source_ref,
            )
            return "suppressed"
    except Exception:
        pass

    # --- Create bugfix candidate ---
    # Parse triage packet for context
    packet = {}
    if incident.triage_packet:
        try:
            packet = json.loads(incident.triage_packet)
        except (json.JSONDecodeError, ValueError):
            pass

    subsystem = packet.get("subsystem", "unknown")
    criticality = packet.get("criticality", "medium")

    # Risk tier: sensitive subsystems need human approval
    is_sensitive = False
    try:
        from app.services.project_brain import classify_file
        if incident.culprit:
            classification = classify_file(incident.culprit)
            is_sensitive = classification.get("is_sensitive", False)
    except Exception:
        pass

    # TIER_2 = never auto-apply, TIER_1 = human approve, TIER_0 = auto-apply safe
    if is_sensitive or criticality == "critical":
        risk_tier = 2
    elif criticality == "high":
        risk_tier = 1
    else:
        risk_tier = 1  # default to human-approve for Sentry incidents

    # Build structured summary for the candidate
    candidate_summary = _build_candidate_summary(incident, packet)

    # --- Priority scoring (with adaptive calibration) ---
    from app.services.candidate_scoring import compute_priority_score
    calibration = None
    impact_signal = None
    impact_detail = None
    try:
        from app.services.scoring_calibration import get_scoring_calibration, compute_impact_signal
        calibration = get_scoring_calibration(db)
        impact_signal, impact_detail = compute_impact_signal(db, incident.affected_shop, incident.created_at)
    except Exception:
        pass  # calibration is optional — base scoring still works

    priority_score, priority_detail = compute_priority_score(
        severity=incident.severity,
        merchant_impact=incident.merchant_impact,
        recurrence_count=incident.recurrence_count,
        subsystem_class=incident.subsystem_class,
        is_regression_candidate=incident.is_regression_candidate,
        calibration=calibration,
        impact_signal=impact_signal,
    )

    candidate = BugFixCandidate(
        source_type="sentry_incident",
        source_ref=source_ref,
        title=_build_candidate_title(incident),
        summary=candidate_summary,
        context_json=json.dumps(packet, default=str) if packet else None,
        status="open",
        affected_domain=subsystem,
        patch_risk_tier=risk_tier,
        priority_score=priority_score,
        priority_detail=json.dumps(priority_detail, default=str),
    )
    db.add(candidate)
    db.flush()

    # Link incident to candidate
    incident.linked_bugfix_candidate_id = candidate.id
    incident.ai_triage_status = "consumed"
    incident.status = "linked"

    # Also link all family members to this candidate
    if incident.fingerprint:
        family_members = (
            db.query(SentryIncident)
            .filter(
                SentryIncident.fingerprint == incident.fingerprint,
                SentryIncident.id != incident.id,
                SentryIncident.linked_bugfix_candidate_id.is_(None),
            )
            .all()
        )
        for member in family_members:
            member.linked_bugfix_candidate_id = candidate.id
            if member.status in ("parsed", "triaged"):
                member.status = "linked"

    db.flush()

    log.info(
        "sentry_triage: created candidate=%d from incident=%d "
        "title=%r subsystem=%s risk=%d recurrence=%d",
        candidate.id, incident.id, candidate.title,
        subsystem, risk_tier, incident.recurrence_count,
    )
    return "consumed"


def _build_candidate_title(incident: SentryIncident) -> str:
    """Build a concise bugfix candidate title from incident data."""
    parts: list[str] = []

    if incident.error_type:
        parts.append(incident.error_type)

    if incident.culprit:
        # Shorten to filename
        culprit = incident.culprit
        if "/" in culprit:
            culprit = culprit.split("/")[-1]
        parts.append(f"in {culprit}")

    if not parts:
        parts.append(incident.error_title or "Unknown error")

    title = " ".join(parts)

    if incident.recurrence_count > 1:
        title = f"[x{incident.recurrence_count}] {title}"

    return title[:256]


def _build_candidate_summary(incident: SentryIncident, packet: dict) -> str:
    """Build a structured summary for the bugfix candidate."""
    lines: list[str] = []

    if incident.error_title:
        lines.append(f"Error: {incident.error_title}")

    if incident.culprit:
        lines.append(f"Location: {incident.culprit}")

    if incident.recurrence_count > 1:
        lines.append(f"Recurrences: {incident.recurrence_count}")

    if packet.get("subsystem") and packet["subsystem"] != "unknown":
        lines.append(f"Subsystem: {packet['subsystem']} (criticality: {packet.get('criticality', '?')})")

    hints = packet.get("probable_root_cause_hints", [])
    if hints:
        lines.append(f"Hints: {'; '.join(hints)}")

    lessons = packet.get("related_lessons", [])
    if lessons:
        lesson_summaries = [l["summary"][:80] for l in lessons[:3]]
        lines.append(f"Related lessons: {'; '.join(lesson_summaries)}")

    if incident.stack_trace:
        # Include last 5 lines of stack trace
        trace_lines = incident.stack_trace.strip().split("\n")
        tail = trace_lines[-5:] if len(trace_lines) > 5 else trace_lines
        lines.append(f"Stack trace (tail):\n{''.join('  ' + l + chr(10) for l in tail)}")

    if incident.sentry_issue_url:
        lines.append(f"Sentry: {incident.sentry_issue_url}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Re-evaluate skipped incidents when recurrence count increases
# ---------------------------------------------------------------------------

def reevaluate_skipped_families(db: Session) -> dict:
    """
    Check previously-skipped family heads that have gained new recurrences.

    When a new incident joins an existing family, the family head's
    recurrence_count is updated but ai_triage_status remains "skipped".
    This function re-checks whether the threshold is now met.

    Returns: {"reevaluated": N, "promoted": N}
    """
    summary = {"reevaluated": 0, "promoted": 0}

    # Fetch skipped heads with at least 2 recurrences (basic pre-filter).
    # The actual threshold is computed per-incident below.
    skipped_heads = (
        db.query(SentryIncident)
        .filter(
            SentryIncident.ai_triage_status == "skipped",
            SentryIncident.family_head_id.is_(None),
            SentryIncident.linked_bugfix_candidate_id.is_(None),
            SentryIncident.recurrence_count >= 2,  # broad pre-filter
        )
        .limit(20)
        .all()
    )

    for inc in skipped_heads:
        summary["reevaluated"] += 1
        threshold = _get_recurrence_threshold(inc)
        if inc.recurrence_count < threshold:
            continue  # still below this incident's specific threshold

        # Re-generate triage packet if missing
        if not inc.triage_packet:
            generate_triage_packet(db, inc.id)

        # Mark as ready for consumption
        inc.ai_triage_status = "ready"
        summary["promoted"] += 1
        log.info(
            "sentry_triage: promoted skipped incident=%d (recurrence=%d threshold=%d)",
            inc.id, inc.recurrence_count, threshold,
        )

    if summary["promoted"] > 0:
        db.flush()

    return summary
