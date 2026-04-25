"""
sentry_parser.py — Parse Sentry alert emails into structured incident data.

Handles both HTML and plain-text Sentry notification emails.
Defensive: never crashes on malformed input, always returns a result dict.

Public interface:
    parse_sentry_email(subject, body, from_addr) -> dict
    compute_fingerprint(error_type, culprit, stack_trace) -> (fingerprint, fingerprint_input)
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from typing import Any

log = logging.getLogger("sentry_parser")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    # Remove style/script blocks
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_between(text: str, start: str, end: str) -> str | None:
    """Extract text between two markers (case-insensitive)."""
    pattern = re.escape(start) + r"(.*?)" + re.escape(end)
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _first_match(text: str, *patterns: str) -> str | None:
    """Return the first regex match group(1) from multiple patterns."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Subject parsing
# ---------------------------------------------------------------------------

def _parse_subject(subject: str) -> dict:
    """
    Parse Sentry email subject line.

    Examples:
        "[Sentry] HedgeSpark-Backend: NameError: name 'set_session_cookie' is not defined"
        "[Sentry] HEDGESPARK | production | NameError"
        "[Sentry] New alert: High event rate"
    """
    result: dict[str, Any] = {}

    if not subject:
        return result

    # Strip [Sentry] prefix
    clean = re.sub(r"^\[Sentry\]\s*", "", subject).strip()

    # Try "Project: ErrorType: message" format
    m = re.match(r"^([^:]+?):\s*(\w+Error|\w+Exception|\w+Warning):\s*(.+)", clean)
    if m:
        result["project"] = m.group(1).strip()
        result["error_type"] = m.group(2).strip()
        result["error_title"] = f"{m.group(2).strip()}: {m.group(3).strip()}"
        return result

    # Try "Project | environment | ErrorType" format
    m = re.match(r"^([^|]+)\|\s*([^|]+)\|\s*(.+)", clean)
    if m:
        result["project"] = m.group(1).strip()
        result["environment"] = m.group(2).strip()
        result["error_title"] = m.group(3).strip()
        # Extract error type from title
        err_m = re.match(r"(\w+Error|\w+Exception|\w+Warning)", m.group(3).strip())
        if err_m:
            result["error_type"] = err_m.group(1)
        return result

    # Try "Project - ErrorType: message" format
    m = re.match(r"^([^-]+?)\s*-\s*(\w+Error|\w+Exception|\w+Warning):\s*(.+)", clean)
    if m:
        result["project"] = m.group(1).strip()
        result["error_type"] = m.group(2).strip()
        result["error_title"] = f"{m.group(2).strip()}: {m.group(3).strip()}"
        return result

    # Fallback: try to extract error type from anywhere in subject
    err_m = re.search(r"(\w+Error|\w+Exception|\w+Warning)", clean)
    if err_m:
        result["error_type"] = err_m.group(1)

    result["error_title"] = clean
    return result


# ---------------------------------------------------------------------------
# Body parsing
# ---------------------------------------------------------------------------

def _parse_body(body: str) -> dict:
    """
    Parse Sentry email body (HTML or plain text).

    Extracts:
        - stack_trace
        - culprit (file/module)
        - environment
        - project
        - error_type
        - error_title
        - sentry_issue_url
        - severity
    """
    result: dict[str, Any] = {}

    if not body:
        return result

    # Convert HTML to plain text for uniform parsing
    plain = _strip_html(body) if "<" in body and ">" in body else body

    # --- Stack trace extraction ---
    # Sentry emails typically have a traceback section
    trace = _first_match(
        plain,
        r"(Traceback \(most recent call last\):.*?)(?:\n\n|\Z)",
        r"(File \"[^\"]+\", line \d+.*?)(?:\n\n|\Z)",
        r"(Exception(?:al)?\s+Info.*?)(?:\n\n|\Z)",
    )
    if trace:
        # Limit to reasonable size
        result["stack_trace"] = trace[:4000]

    # --- Culprit extraction ---
    culprit = _first_match(
        plain,
        r"(?:culprit|Culprit)[:\s]+([^\n]+)",
        r"(?:in|File)\s+\"?([^\"\n]+\.py[:\d]*)",
    )
    if culprit:
        result["culprit"] = culprit[:512]
    elif trace:
        # Extract last File reference from stack trace as culprit
        files = re.findall(r"File \"([^\"]+)\", line (\d+)", trace)
        if files:
            last_file, last_line = files[-1]
            # Shorten to relative path
            short = re.sub(r".*/(?:backend|app)/", "app/", last_file)
            result["culprit"] = f"{short}:{last_line}"

    # --- Environment ---
    env = _first_match(
        plain,
        r"(?:Environment|environment)[:\s]+(\w+)",
        r"(?:env|ENV)[:\s]+(\w+)",
    )
    if env:
        result["environment"] = env

    # --- Project ---
    proj = _first_match(
        plain,
        r"(?:Project|project)[:\s]+([^\n]+)",
    )
    if proj:
        result["project"] = proj.strip()

    # --- Error type from body ---
    if "error_type" not in result:
        err = _first_match(
            plain,
            r"(\w+Error):\s",
            r"(\w+Exception):\s",
        )
        if err:
            result["error_type"] = err

    # --- Sentry issue URL ---
    url = _first_match(
        body,  # search original (may have href)
        r"(https?://[^\s\"<>]*sentry[^\s\"<>]*issues?/[^\s\"<>]*)",
        r"href=\"(https?://[^\s\"]*sentry[^\s\"]*issues?/[^\s\"]*)\"",
    )
    if url:
        result["sentry_issue_url"] = url[:512]

    # --- Severity heuristic ---
    lower = plain.lower()
    if any(kw in lower for kw in ["critical", "fatal", "unhandled"]):
        result["severity"] = "critical"
    elif any(kw in lower for kw in ["warning", "deprecat"]):
        result["severity"] = "warning"
    else:
        result["severity"] = "error"

    return result


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------

def compute_fingerprint(
    error_type: str | None,
    culprit: str | None,
    stack_trace: str | None,
) -> tuple[str, str]:
    """
    Compute a normalized fingerprint for incident dedup/grouping.

    Strategy:
        1. Normalize error_type (strip generic suffixes)
        2. Normalize culprit (strip line numbers, keep file + function)
        3. Extract top stack frame (last File reference)
        4. SHA-256 hash of normalized components

    Returns (fingerprint_hash, fingerprint_input_string).
    """
    parts: list[str] = []

    # Error type — normalize
    if error_type:
        parts.append(error_type.strip())
    else:
        parts.append("UnknownError")

    # Culprit — strip line numbers and extension for fuzzy match
    if culprit:
        # "app/api/shopify_oauth.py:525" → "app/api/shopify_oauth"
        norm_culprit = re.sub(r":\d+$", "", culprit.strip())
        # Strip absolute path prefix
        norm_culprit = re.sub(r"^.*/(?:backend|app)/", "app/", norm_culprit)
        # Strip file extension (.py, .js, .ts, .tsx) so email & webhook converge
        norm_culprit = re.sub(r"\.(py|js|ts|tsx)$", "", norm_culprit)
        parts.append(norm_culprit)
    elif stack_trace:
        # Extract last file from trace
        files = re.findall(r"File \"([^\"]+)\"", stack_trace)
        if files:
            norm = re.sub(r"^.*/(?:backend|app)/", "app/", files[-1])
            parts.append(norm)

    # Top frame — last function call in stack
    if stack_trace:
        funcs = re.findall(r"in (\w+)", stack_trace)
        if funcs:
            parts.append(funcs[-1])

    fingerprint_input = ":".join(parts)
    fingerprint_hash = hashlib.sha256(fingerprint_input.encode()).hexdigest()

    return fingerprint_hash, fingerprint_input


# ---------------------------------------------------------------------------
# Subsystem classification
# ---------------------------------------------------------------------------

# Prefix → subsystem_class mapping. First match wins.
_SUBSYSTEM_RULES: list[tuple[str, str]] = [
    ("dashboard/", "frontend_dashboard"),
    ("components/", "frontend_dashboard"),
    ("next/", "frontend_dashboard"),
    ("src/app/", "frontend_dashboard"),
    ("app/workers/", "worker"),
    ("workers/", "worker"),
    ("worker.py", "worker"),
    ("app/api/", "backend_api"),
    ("app/services/", "backend_api"),
    ("app/core/", "backend_api"),
    ("app/models/", "backend_api"),
    ("tracker/", "backend_api"),  # storefront scripts served by backend
]


def classify_subsystem(
    culprit: str | None = None,
    project: str | None = None,
    error_title: str | None = None,
) -> str:
    """
    Classify an incident into a subsystem category.

    Returns one of: frontend_dashboard, backend_api, worker, unknown.
    """
    # Check culprit path first (most reliable signal)
    if culprit:
        culprit_lower = culprit.lower()
        for prefix, cls in _SUBSYSTEM_RULES:
            if prefix in culprit_lower:
                return cls

    # Check project name for hints
    if project:
        proj_lower = project.lower()
        if any(kw in proj_lower for kw in ("dashboard", "frontend", "next", "react")):
            return "frontend_dashboard"
        if any(kw in proj_lower for kw in ("worker", "celery", "cron")):
            return "worker"

    # Check error title for JS-specific patterns
    if error_title:
        title_lower = error_title.lower()
        if any(kw in title_lower for kw in (
            "referenceerror", "syntaxerror", "dom ", "window.",
            "document.", "fetch", "cors", "chunk", "hydration",
            "react", "next", "uncaught",
        )):
            return "frontend_dashboard"

    return "unknown"


# Areas where errors directly impact merchants
_MERCHANT_FACING_PATHS = {
    "app/api/shopify_oauth", "app/api/billing", "app/api/webhooks",
    "app/api/track", "app/api/onboarding", "app/api/setup",
    "app/api/chat_support", "app/api/merchant", "app/api/nudge",
    "app/api/attribution", "app/api/orders", "app/api/segments",
    "app/api/dashboard", "app/api/brief", "app/api/session_replay",
    "app/services/order_ingestion", "app/services/onboarding",
    "app/services/merchant_chatbot", "app/services/webhook_health",
    "tracker/", "app/api/tracker",
}

# Areas that are internal-only — low merchant impact
_INTERNAL_PATHS = {
    "app/api/ops", "app/services/bugfix_pipeline",
    "app/services/promotion_pipeline", "app/services/evolution",
    "app/services/meta_reviewer", "app/services/scaling",
    "app/services/alerting", "app/services/system_summary",
    "app/services/project_brain", "app/services/reviewer_layer",
    "app/services/loop_health", "tests/",
}


def assess_merchant_impact(
    culprit: str | None = None,
    error_type: str | None = None,
    severity: str | None = None,
    subsystem_class: str | None = None,
) -> str:
    """
    Assess whether an error impacts merchants directly.

    Returns: high | medium | low | none.
    """
    # Frontend dashboard errors: medium by default (merchant sees broken UI)
    if subsystem_class == "frontend_dashboard":
        return "medium"

    if culprit:
        culprit_lower = culprit.lower()

        # Check internal-only paths first
        for path in _INTERNAL_PATHS:
            if path in culprit_lower:
                return "none"

        # Check merchant-facing paths
        for path in _MERCHANT_FACING_PATHS:
            if path in culprit_lower:
                # Critical severity in merchant path = high impact
                if severity == "critical":
                    return "high"
                return "high" if any(kw in culprit_lower for kw in (
                    "oauth", "billing", "webhook", "track", "onboarding",
                    "order", "checkout",
                )) else "medium"

    # Workers: low by default (async, retryable)
    if subsystem_class == "worker":
        return "low" if severity != "critical" else "medium"

    # Unknown subsystem: assume medium if severity >= error
    if severity == "critical":
        return "high"
    if severity == "error":
        return "medium"

    return "low"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_sentry_webhook(payload: dict) -> dict:
    """
    Parse a native Sentry webhook payload into the same structured fields
    as parse_sentry_email. Uses structured JSON directly — no regex guessing.

    Sentry webhook payloads (issue alerts) have this structure:
        {
            "action": "triggered" | "resolved" | ...,
            "data": {
                "issue": {
                    "title": "NameError: ...",
                    "culprit": "app/api/shopify_oauth",
                    "type": "error",
                    "metadata": {"type": "NameError", "value": "..."},
                    "platform": "python",
                    "project": {"slug": "hedgespark-backend", "name": "..."},
                    "tags": [{"key": "...", "value": "..."}],
                    ...
                },
                "event": {
                    "exception": {"values": [{"type": "...", "value": "...", "stacktrace": {...}}]},
                    "tags": [["key", "value"], ...],
                    "contexts": {...},
                    "release": "...",
                    ...
                }
            }
        }

    Returns dict with same keys as parse_sentry_email for pipeline compatibility.
    """
    result: dict[str, Any] = {}

    try:
        data = payload.get("data", {})
        issue = data.get("issue", {})
        event = data.get("event", {})

        # --- Error identity ---
        metadata = issue.get("metadata", {})
        result["error_type"] = metadata.get("type") or None
        result["error_title"] = issue.get("title") or metadata.get("value") or None

        # --- Project ---
        project_data = issue.get("project", {})
        if isinstance(project_data, dict):
            result["project"] = project_data.get("name") or project_data.get("slug")
        elif isinstance(project_data, str):
            result["project"] = project_data

        # --- Culprit ---
        result["culprit"] = issue.get("culprit") or None

        # --- Tags (structured — no regex needed) ---
        tags_dict: dict[str, str] = {}
        # Issue tags: list of {"key": ..., "value": ...}
        for tag in issue.get("tags", []):
            if isinstance(tag, dict):
                tags_dict[tag.get("key", "")] = tag.get("value", "")
        # Event tags: list of [key, value] pairs
        for tag in event.get("tags", []):
            if isinstance(tag, (list, tuple)) and len(tag) >= 2:
                tags_dict[str(tag[0])] = str(tag[1])

        result["environment"] = tags_dict.get("environment") or issue.get("level") and None
        if not result.get("environment"):
            result.pop("environment", None)

        # --- Shop domain (from tags) ---
        shop_domain = tags_dict.get("shop_domain") or tags_dict.get("shop") or None
        if shop_domain:
            result["affected_shop"] = shop_domain

        # --- Severity ---
        level = issue.get("level") or tags_dict.get("level") or "error"
        if level in ("fatal",):
            result["severity"] = "critical"
        elif level in ("warning",):
            result["severity"] = "warning"
        else:
            result["severity"] = level  # error, info, etc.

        # --- Sentry issue URL ---
        issue_url = issue.get("shortId") or None
        permalink = issue.get("permalink") or None
        if permalink:
            result["sentry_issue_url"] = permalink
        elif issue.get("id"):
            # Construct URL from org/project if available
            org = payload.get("installation", {}).get("uuid", "")
            if project_data and isinstance(project_data, dict):
                slug = project_data.get("slug", "")
                result["sentry_issue_url"] = f"https://sentry.io/issues/{issue['id']}/"

        # --- Stack trace ---
        stack_text = _extract_stacktrace_from_event(event)
        if stack_text:
            result["stack_trace"] = stack_text[:4000]

        # --- Release ---
        # Webhook payloads carry release as a string. The REST `events/latest`
        # response (used by sentry_poller) carries it as a dict shaped like
        # {"id": ..., "version": "hedgespark@<sha>", ...}. Normalize so the
        # downstream `release[:128]` slice always operates on a string.
        release_raw = event.get("release") or tags_dict.get("sentry:release") or None
        if isinstance(release_raw, dict):
            release = release_raw.get("version") or release_raw.get("shortVersion") or None
        elif isinstance(release_raw, str):
            release = release_raw
        else:
            release = None
        if release:
            result["release"] = release

        # --- Request context (route info) ---
        request_ctx = event.get("request", {})
        if isinstance(request_ctx, dict):
            url = request_ctx.get("url") or ""
            if url:
                result["request_url"] = url[:512]

        # --- Fingerprint ---
        fp_hash, fp_input = compute_fingerprint(
            result.get("error_type"),
            result.get("culprit"),
            result.get("stack_trace"),
        )
        result["fingerprint"] = fp_hash
        result["fingerprint_input"] = fp_input

        # --- Default severity ---
        if "severity" not in result:
            result["severity"] = "error"

        # --- Subsystem classification ---
        result["subsystem_class"] = classify_subsystem(
            culprit=result.get("culprit"),
            project=result.get("project"),
            error_title=result.get("error_title"),
        )

        # --- Merchant impact ---
        result["merchant_impact"] = assess_merchant_impact(
            culprit=result.get("culprit"),
            error_type=result.get("error_type"),
            severity=result.get("severity"),
            subsystem_class=result["subsystem_class"],
        )

    except Exception as exc:
        result["parse_error"] = f"webhook_parse_exception: {type(exc).__name__}: {str(exc)[:200]}"
        log.warning("sentry_parser: webhook parse error: %s", exc)

    return result


def _extract_stacktrace_from_event(event: dict) -> str | None:
    """Extract a text stacktrace from Sentry event exception data."""
    try:
        exc_data = event.get("exception", {})
        values = exc_data.get("values", [])
        if not values:
            return None

        lines: list[str] = []
        for exc_val in values:
            exc_type = exc_val.get("type", "Exception")
            exc_value = exc_val.get("value", "")

            st = exc_val.get("stacktrace", {})
            frames = st.get("frames", [])

            if frames:
                lines.append("Traceback (most recent call last):")
                for frame in frames:
                    fname = frame.get("filename", "?")
                    lineno = frame.get("lineno", "?")
                    func = frame.get("function", "?")
                    lines.append(f'  File "{fname}", line {lineno}, in {func}')
                    context_line = frame.get("context_line", "")
                    if context_line:
                        lines.append(f"    {context_line.strip()}")

            lines.append(f"{exc_type}: {exc_value}")

        return "\n".join(lines) if lines else None
    except Exception as exc:
        log.warning(
            "sentry_parser: stack trace extraction failed (%s): %s",
            type(exc).__name__, str(exc)[:200],
        )
        return None


def parse_sentry_email(
    subject: str | None,
    body: str | None,
    from_addr: str | None = None,
) -> dict:
    """
    Parse a Sentry alert email into structured fields.

    Returns a dict with all extracted fields (missing fields are absent).
    Never raises — parsing failures are captured in the 'parse_error' field.

    Fields returned:
        error_type, error_title, project, environment, severity,
        culprit, stack_trace, sentry_issue_url,
        fingerprint, fingerprint_input,
        parse_error (if parsing partially failed)
    """
    result: dict[str, Any] = {}

    try:
        # Parse subject
        if subject:
            subj_data = _parse_subject(subject)
            result.update(subj_data)

        # Parse body
        if body:
            body_data = _parse_body(body)
            # Body fields only fill gaps — subject takes precedence
            for k, v in body_data.items():
                if k not in result or not result[k]:
                    result[k] = v

        # Compute fingerprint
        fp_hash, fp_input = compute_fingerprint(
            result.get("error_type"),
            result.get("culprit"),
            result.get("stack_trace"),
        )
        result["fingerprint"] = fp_hash
        result["fingerprint_input"] = fp_input

        # Default severity
        if "severity" not in result:
            result["severity"] = "error"

    except Exception as exc:
        result["parse_error"] = f"parser_exception: {type(exc).__name__}: {str(exc)[:200]}"
        log.warning("sentry_parser: unexpected error: %s", exc)

    # --- Subsystem classification ---
    result["subsystem_class"] = classify_subsystem(
        culprit=result.get("culprit"),
        project=result.get("project"),
        error_title=result.get("error_title"),
    )

    # --- Merchant impact ---
    result["merchant_impact"] = assess_merchant_impact(
        culprit=result.get("culprit"),
        error_type=result.get("error_type"),
        severity=result.get("severity"),
        subsystem_class=result["subsystem_class"],
    )

    return result
