"""
bugfix_prompt_grounding.py — LLM prompt grounding for the bugfix pipeline.

The LLM that generates patches in `bugfix_pipeline.propose_patch` cannot
ground its proposals to the real repo unless we tell it what exists. The
2026-04-11 audit found that the dominant failure modes were:

  * Hallucinated paths (`services/foo.py` instead of `app/services/foo.py`)
  * Invented function signatures (LLM assumed `_find_best_trigger` returns
    a tuple when the real function returns a dict)
  * Unrooted candidates (the candidate's `target_file` already does not
    exist on disk, so any LLM call is wasted budget)

This module is the prompt-engineering moat. Every helper here is
deterministic, AST-based, and zero-LLM. The output is plain text blocks
that the proposer concatenates into the LLM user message.

Public API
----------
build_file_manifest(domain, *, extra_files=None) -> str
extract_signatures(file_path) -> str
preflight_ground_candidate(candidate) -> tuple[bool, str]

All entry points are safe to call in any order — failures degrade to
empty strings or "ok" so they never break the existing pipeline.
"""
from __future__ import annotations

import ast
import logging
import os
from typing import Iterable

log = logging.getLogger("bugfix_prompt_grounding")

_BACKEND_DIR = "/opt/wishspark/backend"
_MAX_MANIFEST_ENTRIES = 30
_MAX_SIGNATURES_PER_FILE = 40

# B3 — hard quarantine. A (domain, failure_family) tuple is quarantined
# when it accumulates ≥ _QUARANTINE_THRESHOLD failures in 30 days.
# Subsequent candidates that match the family are rejected at preflight,
# saving LLM budget AND preventing recurring damage. The quarantine
# auto-expires after _QUARANTINE_TTL_DAYS so a family that genuinely
# heals can be retried.
_QUARANTINE_THRESHOLD = 5
_QUARANTINE_LOOKBACK_DAYS = 30
_QUARANTINE_TTL_DAYS = 60


def _abs(path: str) -> str:
    return os.path.join(_BACKEND_DIR, path)


# ---------------------------------------------------------------------------
# 1. File manifest scoped to a domain
# ---------------------------------------------------------------------------


def build_file_manifest(
    affected_domain: str | None,
    *,
    extra_files: Iterable[str] | None = None,
) -> str:
    """Return a markdown block listing real, in-domain file paths.

    Helps the LLM ground its `files` list to existing paths instead of
    inventing them. Always includes `extra_files` (e.g. the candidate's
    declared target_file) even if their domain doesn't match.
    """
    try:
        from app.services.project_brain import build_codebase_index
        idx = build_codebase_index()
    except Exception as exc:
        log.debug("grounding: codebase index unavailable: %s", exc)
        return ""

    files = idx.get("files") or []
    if not isinstance(files, list):
        return ""

    domain_files = []
    if affected_domain:
        for f in files:
            if not isinstance(f, dict):
                continue
            if f.get("domain") == affected_domain and f.get("path"):
                domain_files.append(f)
        # Highest line count first — bigger files are more likely to be
        # the actual target than __init__ stubs.
        domain_files.sort(key=lambda f: int(f.get("lines") or 0), reverse=True)
        domain_files = domain_files[:_MAX_MANIFEST_ENTRIES]

    seen: set[str] = set()
    lines: list[str] = []
    for f in domain_files:
        path = f["path"]
        if path in seen:
            continue
        seen.add(path)
        lines.append(
            f"  {path}   ({int(f.get('lines') or 0)} lines, "
            f"domain={f.get('domain', '?')})"
        )

    if extra_files:
        for path in extra_files:
            if not path or path in seen:
                continue
            full = _abs(path)
            if not os.path.isfile(full):
                continue
            try:
                line_count = sum(1 for _ in open(full, "rb"))
            except Exception:
                line_count = 0
            seen.add(path)
            lines.append(f"  {path}   ({line_count} lines, candidate scope)")

    if not lines:
        return ""

    return (
        "## Available file paths (relative to backend root)\n"
        "These are REAL files. Your `files` list must use paths from this set.\n"
        "Inventing a path will cause your patch to be rejected.\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# 2. AST-extracted signatures (no function bodies)
# ---------------------------------------------------------------------------


def extract_signatures(file_path: str) -> str:
    """Return a markdown block of the file's top-level def/class signatures.

    AST-driven so we never hallucinate. We deliberately exclude function
    bodies — only the def line + return annotation + the first
    docstring line if present.
    """
    full = _abs(file_path)
    if not os.path.isfile(full):
        return ""

    try:
        with open(full, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=full)
    except Exception as exc:
        log.debug("grounding: ast parse failed for %s: %s", file_path, exc)
        return ""

    sigs: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sigs.append(_format_def(node, source))
        elif isinstance(node, ast.ClassDef):
            sigs.append(_format_class(node, source))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                sigs.append(ast.unparse(node))
            except Exception:
                pass
        if len(sigs) >= _MAX_SIGNATURES_PER_FILE:
            break

    if not sigs:
        return ""

    module_path = file_path.replace("/", ".").replace(".py", "")
    return (
        f"## Real API for `{file_path}`\n"
        f"Import from `{module_path}`. Use these EXACT signatures — do not invent variants.\n"
        "```python\n" + "\n".join(sigs) + "\n```"
    )


def _format_def(node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> str:
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    returns = ""
    if node.returns is not None:
        try:
            returns = f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass
    line = f"{prefix}{node.name}({args}){returns}: ..."
    doc = ast.get_docstring(node)
    if doc:
        first = doc.splitlines()[0].strip()
        if first:
            line += f"  # {first[:80]}"
    return line


def _format_class(node: ast.ClassDef, source: str) -> str:
    bases = ""
    if node.bases:
        try:
            bases = "(" + ", ".join(ast.unparse(b) for b in node.bases) + ")"
        except Exception:
            pass
    head = f"class {node.name}{bases}: ..."
    methods = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append("    " + _format_def(child, source))
    if not methods:
        return head
    return head + "\n" + "\n".join(methods[:8])


# ---------------------------------------------------------------------------
# 3. Pre-flight semantic validator
# ---------------------------------------------------------------------------


def preflight_ground_candidate(candidate, db=None) -> tuple[bool, str]:
    """Reject ungroundable candidates BEFORE the LLM call.

    Returns (ok, reason). On reject, the proposer should set
    `failure_reason='prompt_ungrounded_preflight: <reason>'` and bail
    without spending LLM budget.

    Checks:
      1. If `context_json.target_file` is set, it must exist on disk.
      2. If `patch_files` is already populated, every entry must exist
         OR clearly be a new test file.
      3. **B3 hard quarantine**: if the candidate's
         (affected_domain, source_type) family has accumulated
         _QUARANTINE_THRESHOLD failures in the last _QUARANTINE_LOOKBACK_DAYS
         days, reject without spending LLM budget. Pass `db` to enable.
    """
    import json as _json

    if getattr(candidate, "context_json", None):
        try:
            ctx = _json.loads(candidate.context_json)
        except Exception:
            ctx = {}
        target = ctx.get("target_file") if isinstance(ctx, dict) else None
        if target and not os.path.isfile(_abs(target)):
            return False, f"target_file_not_found: {target}"

    if getattr(candidate, "patch_files", None):
        try:
            files = _json.loads(candidate.patch_files)
        except Exception:
            files = []
        for f in files or []:
            if not isinstance(f, str) or not f:
                continue
            full = _abs(f)
            if os.path.isfile(full):
                continue
            if f.startswith("tests/") and f.endswith(".py"):
                continue
            return False, f"patch_file_not_found: {f}"

    # B3 hard quarantine — only enforced when a DB session is provided.
    if db is not None:
        is_quarantined, q_reason = check_family_quarantine(
            db,
            affected_domain=getattr(candidate, "affected_domain", None),
            source_type=getattr(candidate, "source_type", None),
        )
        if is_quarantined:
            return False, f"quarantined_family: {q_reason}"

    # Security-aware preflight guard (2026-04-11 audit). Any candidate
    # whose diff — when present — attempts to introduce a known security
    # or GDPR regression is hard-rejected here, before either the LLM is
    # called OR git apply touches the tree. Diffless candidates (pre-LLM
    # path) pass through; they'll be re-checked after the LLM responds.
    if getattr(candidate, "patch_diff", None):
        try:
            from app.services.security_preflight_guard import guard_candidate
            allowed, reason = guard_candidate(candidate)
            if not allowed:
                return False, reason
        except Exception as exc:
            log.debug(
                "security_preflight_guard: non-fatal failure: %s", exc,
            )

    return True, "ok"


def extract_recent_failures(
    db, *, affected_domain: str | None, source_type: str | None,
    limit: int = 3,
) -> str:
    """C1 — Pull the last N PatchFingerprint failures for the same
    (domain, source_type) family and format them as a DO-NOT-REPEAT
    section for the LLM user message.

    Different from `build_hard_lesson_constraints` (in bugfix_pipeline)
    which aggregates failure_reason families. This one returns the
    actual recent failure traces — the LLM sees the precise prior
    attempts it must avoid.
    """
    if not affected_domain or not source_type:
        return ""
    try:
        from app.models.patch_fingerprint import PatchFingerprint
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        rows = (
            db.query(PatchFingerprint)
            .filter(
                PatchFingerprint.affected_domain == affected_domain,
                PatchFingerprint.source_type == source_type,
                PatchFingerprint.outcome.in_(
                    ["apply_failed", "rolled_back", "tests_failed", "test_timeout"]
                ),
                PatchFingerprint.created_at >= cutoff,
                PatchFingerprint.failure_reason.isnot(None),
            )
            .order_by(PatchFingerprint.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        log.warning("extract_recent_failures: query failed: %s", exc)
        return ""

    if not rows:
        return ""

    lines = [
        f"## Prior failed attempts in this family (last 30d)",
        "These exact approaches FAILED. Do NOT repeat them — try a different angle.",
    ]
    for i, fp in enumerate(rows, start=1):
        date_str = fp.created_at.strftime("%Y-%m-%d") if fp.created_at else "?"
        outcome = fp.outcome or "?"
        reason = (fp.failure_reason or "")[:200]
        lines.append(f"  {i}. [{date_str}] outcome={outcome} — {reason}")
    return "\n".join(lines)


def check_family_quarantine(
    db, *, affected_domain: str | None, source_type: str | None,
) -> tuple[bool, str]:
    """Return (is_quarantined, reason) for a (domain, source_type) family.

    A family is quarantined when its PatchFingerprint failure count in
    the last _QUARANTINE_LOOKBACK_DAYS exceeds _QUARANTINE_THRESHOLD.
    Operator-cleared families are honored via Redis flag.

    The lookback window is shorter than the auto-expire (60d) so that
    a family which has been clean for a month can be retried even if
    historical failures remain in PatchFingerprint.
    """
    if not affected_domain or not source_type:
        return False, "no_family_classification"

    # Check operator override first — Redis flag clears quarantine even
    # if the failure count is still high
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            cleared = rc.get(f"hs:quarantine:cleared:{affected_domain}:{source_type}")
            if cleared:
                return False, "operator_cleared"
    except Exception:
        pass

    try:
        from app.models.patch_fingerprint import PatchFingerprint
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=_QUARANTINE_LOOKBACK_DAYS
        )
        failure_count = (
            db.query(func.count(PatchFingerprint.id))
            .filter(
                PatchFingerprint.affected_domain == affected_domain,
                PatchFingerprint.source_type == source_type,
                PatchFingerprint.outcome.in_(
                    ["apply_failed", "rolled_back", "tests_failed", "test_timeout"]
                ),
                PatchFingerprint.created_at >= cutoff,
            )
            .scalar()
        )
        n = int(failure_count or 0)
        if n >= _QUARANTINE_THRESHOLD:
            return True, (
                f"{affected_domain}/{source_type} has {n} failures in "
                f"last {_QUARANTINE_LOOKBACK_DAYS}d (threshold={_QUARANTINE_THRESHOLD})"
            )
        return False, f"failures_30d={n}/{_QUARANTINE_THRESHOLD}"
    except Exception as exc:
        log.warning("check_family_quarantine: query failed: %s", exc)
        return False, "query_failed"


def get_quarantined_families(db) -> list[dict]:
    """Operator dashboard view: every (domain, source_type) family
    currently above quarantine threshold."""
    try:
        from app.models.patch_fingerprint import PatchFingerprint
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=_QUARANTINE_LOOKBACK_DAYS
        )
        rows = (
            db.query(
                PatchFingerprint.affected_domain,
                PatchFingerprint.source_type,
                func.count(PatchFingerprint.id).label("failures"),
            )
            .filter(
                PatchFingerprint.affected_domain.isnot(None),
                PatchFingerprint.source_type.isnot(None),
                PatchFingerprint.outcome.in_(
                    ["apply_failed", "rolled_back", "tests_failed", "test_timeout"]
                ),
                PatchFingerprint.created_at >= cutoff,
            )
            .group_by(PatchFingerprint.affected_domain, PatchFingerprint.source_type)
            .having(func.count(PatchFingerprint.id) >= _QUARANTINE_THRESHOLD)
            .all()
        )
        return [
            {"domain": r[0], "source_type": r[1], "failure_count": int(r[2])}
            for r in rows
        ]
    except Exception:
        return []


def clear_quarantine(domain: str, source_type: str, ttl_days: int = 7) -> bool:
    """Operator override: clear a quarantine for `ttl_days`. After
    expiry the quarantine re-evaluates from PatchFingerprint counts."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("bugfix_grounding.clear_quarantine")
            return False
        rc.setex(
            f"hs:quarantine:cleared:{domain}:{source_type}",
            ttl_days * 24 * 3600,
            "1",
        )
        return True
    except Exception:
        return False
