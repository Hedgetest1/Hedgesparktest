"""
bugfix_pipeline.py — Bug triage, patch proposal, and human-gated fix pipeline.

Triage: reads ops_alerts + outcome data → creates BugFixCandidate rows
Proposal: builds context → calls LLM → stores patch on candidate row
Apply: safe apply with test verification and rollback

Public interface:
    run_bug_triage(db) -> dict          — scan for new bugs, create candidates
    propose_patch(db, candidate_id) -> bool  — LLM proposes patch for a candidate
    run_auto_propose(db) -> dict        — auto-propose for open candidates
    run_auto_apply(db) -> dict          — auto-apply TIER_0 candidates
    apply_bugfix_candidate(db, id) -> ApplyResult

Unified pipeline integration:
    - Rule 4 in triage scans merchant_reported_bug alerts (from chatbot)
    - Back-links support incidents when candidates are created from chatbot alerts
    - Propagates resolution to linked support incidents after successful apply
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.models.bugfix_candidate import BugFixCandidate

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("bugfix_pipeline")

_TRIAGE_LOOKBACK_HOURS = 24
# Generic recurring-alert catch-all threshold (Rule 7). Anything that
# recurs at least this many times in the lookback window AND isn't
# handled by a specific rule becomes a candidate. Tuned to match
# `worker_repeated_failure` semantics: 3 strikes = signal, not noise.
_GENERIC_RECURRENCE_THRESHOLD = 3
# Cross-shop pattern compaction (B2). When the same alert template
# fires across N+ distinct shops, create a fleet-wide candidate.
_FLEET_WIDE_MIN_SHOPS = 3


# ---------------------------------------------------------------------------
# Patch fingerprinting — prevents retrying identical failed approaches
# ---------------------------------------------------------------------------

import hashlib


def _compute_patch_fingerprint(title: str, files_json: str | None, patch_diff: str | None = None) -> str:
    """
    Compute a SHA-256 fingerprint for a patch based on its identity.
    Normalized: sorted file list + lowercased title keywords.
    """
    parts = []

    # Normalize title into sorted keywords
    if title:
        words = sorted(set(title.lower().split()))
        parts.append(" ".join(words))

    # Normalize file list
    if files_json:
        try:
            files = json.loads(files_json)
            if isinstance(files, list):
                parts.append("|".join(sorted(files)))
        except (json.JSONDecodeError, ValueError):
            parts.append(files_json[:200])

    # Include first 500 chars of diff for diff-level dedup
    if patch_diff:
        parts.append(patch_diff[:500])

    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


import re

# Patterns stripped during diff normalization
_DIFF_STRIP_PATTERNS = [
    re.compile(r"^@@\s.*@@.*$", re.MULTILINE),     # hunk headers
    re.compile(r"^---\s.*$", re.MULTILINE),          # file headers
    re.compile(r"^\+\+\+\s.*$", re.MULTILINE),      # file headers
    re.compile(r"^diff\s--git.*$", re.MULTILINE),    # diff command line
    re.compile(r"^index\s[0-9a-f]+\.\.[0-9a-f]+.*$", re.MULTILINE),  # index line
]


def _compute_diff_fingerprint(patch_diff: str | None) -> str | None:
    """
    Compute a normalized diff fingerprint that catches semantically identical patches
    even when cosmetic details differ (whitespace, comments, context lines, hunk headers).

    Normalization rules:
    1. Strip all hunk headers (@@...@@), file headers (---/+++), diff/index lines
    2. Keep only +/- lines (actual changes), strip leading +/-
    3. Collapse all whitespace to single spaces
    4. Strip inline comments (# ... at end of line)
    5. Sort remaining lines (order-independent — same changes in different order = same hash)
    6. Lowercase everything
    """
    if not patch_diff or not patch_diff.strip():
        return None

    normalized = patch_diff

    # Step 1: Strip headers and metadata
    for pattern in _DIFF_STRIP_PATTERNS:
        normalized = pattern.sub("", normalized)

    # Step 2: Keep only change lines (+ or -), strip the prefix
    change_lines = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        if stripped.startswith("+") or stripped.startswith("-"):
            # Remove the +/- prefix
            content = stripped[1:].strip()
            if not content:
                continue  # skip empty change lines

            # Step 3: Collapse whitespace
            content = re.sub(r"\s+", " ", content)

            # Step 4: Strip trailing comments
            content = re.sub(r"\s*#\s*.*$", "", content)

            if content:
                change_lines.append(content.lower())

    if not change_lines:
        return None

    # Step 5: Sort for order-independence
    change_lines.sort()

    raw = "\n".join(change_lines)
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# C3 — AST skeleton fingerprint (structural duplicate detection)
# ---------------------------------------------------------------------------
#
# The existing fingerprint dimensions catch:
#   - identity (title + files + diff bytes)
#   - normalized diff (whitespace + comments + order independent)
#
# Both miss the case where the LLM proposes the SAME structural fix
# but with renamed variables ("retry_count" → "attempt_count"). The
# AST skeleton fingerprint normalizes every identifier to a placeholder
# (`name_0`, `name_1`, ...) and hashes the resulting structure. Two
# patches that share an AST skeleton implement the same strategy.
#
# Stored in Redis to avoid a migration:
#   `hs:patchfp:skeleton:{hash}` → JSON {"candidate_id":N, "outcome":S,
#                                        "failure_reason":S, "ts":iso}
# 60-day TTL — long enough to catch repeat strategies, short enough
# that genuinely different attempts after months of evolution can pass.

_SKELETON_REDIS_PREFIX = "hs:patchfp:skeleton"
_SKELETON_TTL_SECONDS = 60 * 24 * 3600

# D1 — Pipeline immune system. When a patch regresses for a specific
# (source_type, source_ref) scope, we remember its AST skeleton as an
# "antigen" bound to that scope. Future candidates sharing the same scope
# whose skeleton matches the antigen are hard-rejected even if the
# global skeleton cache has expired or another shop applied the same
# fix successfully. The scope hash is SHA-256(source_type|source_ref)
# so a fleet-wide source (`probe:cvr_drift:*`) and a per-shop source
# (`alert:foo.myshopify.com:xyz`) each get their own immune memory.
#
# Key format: hs:antigen:{scope_hash}:{skeleton_hash} → JSON metadata
# TTL: 180 days — long enough for biological immunity, short enough
# that a genuinely healed scope can eventually try the same strategy.
_ANTIGEN_REDIS_PREFIX = "hs:antigen"
_ANTIGEN_TTL_SECONDS = 180 * 24 * 3600


def _compute_ast_skeleton_fingerprint(patch_diff: str | None) -> str | None:
    """Return a stable hash of the structural skeleton of the diff's
    added Python lines. None if the diff is empty or unparseable.

    Strategy:
      1. Extract all `+` lines (excluding `+++` headers and empty lines)
      2. Join into pseudo-source
      3. Try to AST-parse it; if it fails, normalize textually instead
      4. For successful parse: walk the tree, replace every Name/arg/
         attribute identifier with a placeholder bound by appearance order,
         drop docstrings + comments
      5. Hash the resulting `ast.dump`
    """
    if not patch_diff:
        return None

    added: list[str] = []
    for line in patch_diff.split("\n"):
        if line.startswith("+++"):
            continue
        if not line.startswith("+"):
            continue
        body = line[1:].rstrip()
        if not body.strip():
            continue
        if body.lstrip().startswith("#"):
            continue
        added.append(body)
    if not added:
        return None

    pseudo_source = "\n".join(added)
    try:
        import ast as _ast
        tree = _ast.parse(pseudo_source)
    except SyntaxError:
        # Diffs often span partial blocks. Fall back to a textual normalization
        # that strips identifiers and string literals so renames still collapse.
        return _textual_skeleton_fingerprint(added)

    # Walk the AST and rename every identifier consistently
    name_map: dict[str, str] = {}

    def _placeholder(original: str) -> str:
        if original not in name_map:
            name_map[original] = f"id_{len(name_map)}"
        return name_map[original]

    import ast as _ast
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            node.id = _placeholder(node.id)
        elif isinstance(node, _ast.arg):
            node.arg = _placeholder(node.arg)
        elif isinstance(node, _ast.Attribute):
            node.attr = _placeholder(node.attr)
        elif isinstance(node, _ast.FunctionDef) or isinstance(node, _ast.AsyncFunctionDef):
            node.name = _placeholder(node.name)
        elif isinstance(node, _ast.ClassDef):
            node.name = _placeholder(node.name)
        elif isinstance(node, _ast.Constant):
            if isinstance(node.value, str):
                node.value = "STR"
            elif isinstance(node.value, (int, float)):
                node.value = 0

    try:
        skeleton = _ast.dump(tree, annotate_fields=False, include_attributes=False)
    except Exception:
        return _textual_skeleton_fingerprint(added)
    return hashlib.sha256(skeleton.encode()).hexdigest()


def _textual_skeleton_fingerprint(added_lines: list[str]) -> str | None:
    """Fallback skeleton when AST parse fails — strip identifiers and
    string literals via regex, sort lines, hash."""
    if not added_lines:
        return None
    norm: list[str] = []
    for line in added_lines:
        s = re.sub(r"#.*$", "", line)
        s = re.sub(r'"[^"]*"', '"STR"', s)
        s = re.sub(r"'[^']*'", "'STR'", s)
        s = re.sub(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", "ID", s)
        s = re.sub(r"\b\d+\b", "0", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            norm.append(s)
    if not norm:
        return None
    norm.sort()
    return hashlib.sha256("\n".join(norm).encode()).hexdigest()


def _redis_safe():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _record_skeleton_fingerprint(
    candidate_id: int, skeleton_hash: str, outcome: str,
    failure_reason: str | None,
) -> None:
    """Persist a skeleton hash to Redis for future structural dedup."""
    rc = _redis_safe()
    if rc is None or not skeleton_hash:
        return
    try:
        payload = json.dumps({
            "candidate_id": candidate_id,
            "outcome": outcome,
            "failure_reason": (failure_reason or "")[:300],
            "ts": _now().isoformat(),
        }, default=str)
        rc.setex(f"{_SKELETON_REDIS_PREFIX}:{skeleton_hash}", _SKELETON_TTL_SECONDS, payload)
    except Exception as exc:
        log.debug("skeleton_fp: record failed: %s", exc)


def _check_skeleton_fingerprint(skeleton_hash: str | None) -> dict | None:
    """Look up a skeleton hash in Redis. Returns the stored failure
    metadata or None."""
    if not skeleton_hash:
        return None
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.skeleton_check")
        return None
    try:
        raw = rc.get(f"{_SKELETON_REDIS_PREFIX}:{skeleton_hash}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        data["match_type"] = "ast_skeleton"
        return data
    except Exception:
        return None


# ---------------------------------------------------------------------------
# D3 — LLM cost amortization (template-cached fixes)
# ---------------------------------------------------------------------------
# When a family of bugs keeps recurring we pay the LLM tax for every
# occurrence even though the diff is structurally identical. D3 caches a
# successful fix as a reusable template keyed on
# (affected_domain, source_type, sorted target files). A subsequent
# candidate in the same family hits the cache, skips `_call_llm`, and
# flows through the existing validate/apply path. Linear cost in
# families, not in incidents.
#
# Anti-theater: only cache templates after a fix has actually APPLIED
# successfully (tests + health check + git commit). Never cache proposals
# that failed — and never cache templates that can't be re-validated
# against the current repo state on recall.

_FIX_TEMPLATE_REDIS_PREFIX = "hs:fix_template"
_FIX_TEMPLATE_TTL_SECONDS = 7 * 24 * 3600
_FIX_TEMPLATE_HIT_COUNTER = "hs:fix_template_hits"


def _compute_fix_template_key(candidate: BugFixCandidate) -> str | None:
    """Family+files key for the fix template cache. None if we can't
    form a stable key (no domain, no source_type, or no file anchors)."""
    domain = getattr(candidate, "affected_domain", None) or ""
    source_type = getattr(candidate, "source_type", None) or ""
    if not domain or not source_type:
        return None

    files: list[str] = []
    if candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            if isinstance(ctx, dict):
                tf = ctx.get("target_file")
                if isinstance(tf, str) and tf:
                    files.append(tf)
        except Exception as exc:
            log.warning("bugfix_pipeline: template key context_json parse failed: %s", exc)
    if candidate.patch_files:
        try:
            for f in json.loads(candidate.patch_files) or []:
                if isinstance(f, str) and f and f not in files:
                    files.append(f)
        except Exception as exc:
            log.warning("bugfix_pipeline: template key patch_files parse failed: %s", exc)
    if not files:
        return None

    raw = f"{domain}|{source_type}|{'|'.join(sorted(files))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _lookup_fix_template(template_key: str | None) -> dict | None:
    """Return the cached template payload (dict) or None."""
    if not template_key:
        return None
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.fix_template_lookup")
        return None
    try:
        raw = rc.get(f"{_FIX_TEMPLATE_REDIS_PREFIX}:{template_key}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.debug("fix_template: lookup failed: %s", exc)
    return None


def _store_fix_template(template_key: str | None, candidate: BugFixCandidate) -> None:
    """Persist a successful patch as a reusable template keyed on
    (domain, source_type, files). Called from the apply success path.

    Learning-isolation gate: only templates produced by `real_merchant`
    evidence may be stored. Pre-merchant / internal_test / sandbox
    patches never enter the cache, so pytest runs on the production
    host (via deploy.sh) can never poison the reuse path.
    """
    if not template_key or not candidate.patch_diff:
        return
    try:
        from app.services.learning_isolation import is_product_learning_eligible
        if not is_product_learning_eligible(
            getattr(candidate, "evidence_source", None)
        ):
            return
    except Exception:
        return
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.fix_template_store")
        return
    try:
        files_list: list[str] = []
        if candidate.patch_files:
            try:
                files_list = [
                    f for f in (json.loads(candidate.patch_files) or [])
                    if isinstance(f, str)
                ]
            except Exception:
                files_list = []
        payload = json.dumps({
            "patch_summary": candidate.patch_summary or "",
            "diff": candidate.patch_diff,
            "files": files_list,
            "test_command": candidate.test_command or "",
            "source_candidate_id": candidate.id,
            "stored_at": _now().isoformat(),
        }, default=str)
        rc.setex(
            f"{_FIX_TEMPLATE_REDIS_PREFIX}:{template_key}",
            _FIX_TEMPLATE_TTL_SECONDS,
            payload,
        )
        log.info(
            "fix_template: stored template key=%s from candidate #%d",
            template_key[:12], candidate.id,
        )
    except Exception as exc:
        log.debug("fix_template: store failed: %s", exc)


def _incr_fix_template_hit() -> None:
    """Bump the weekly cache-hit counter (for the daily digest)."""
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.fix_template_hit_incr")
        return
    try:
        week = _now().strftime("%G-W%V")
        key = f"{_FIX_TEMPLATE_HIT_COUNTER}:{week}"
        rc.incr(key)
        rc.expire(key, 30 * 24 * 3600)
    except Exception as exc:
        log.debug("fix_template: hit counter failed: %s", exc)


def _mark_template_reuse(context_json: str | None, *, source_candidate_id) -> str:
    """Annotate the candidate's context_json with template-reuse metadata
    so downstream (digest, audit, drift analysis) can tell apart LLM-born
    proposals from cache-born ones."""
    try:
        ctx = json.loads(context_json) if context_json else {}
        if not isinstance(ctx, dict):
            ctx = {"_raw": ctx}
    except Exception:
        ctx = {}
    ctx["fix_template_reuse"] = {
        "source_candidate_id": source_candidate_id,
        "reused_at": _now().isoformat(),
    }
    return json.dumps(ctx, default=str)


# D4 — adversarial report storage + weekly counters

_ADVERSARIAL_HIT_COUNTER = "hs:adversarial_probes"


def _record_adversarial_report(candidate: BugFixCandidate, report: dict) -> None:
    """Attach the adversarial report to the candidate's context_json and
    bump the weekly weak-probe counter."""
    try:
        ctx = json.loads(candidate.context_json) if candidate.context_json else {}
        if not isinstance(ctx, dict):
            ctx = {"_raw": ctx}
    except Exception:
        ctx = {}
    ctx["adversarial_report"] = {
        "fragility_score": int(report.get("fragility_score", 0)),
        "function_count": int(report.get("function_count", 0)),
        "parse_status": report.get("parse_status", "unknown"),
        "probes": report.get("probes", [])[:10],
        "analysed_at": _now().isoformat(),
    }
    candidate.context_json = json.dumps(ctx, default=str)

    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.adversarial_counter")
        return
    try:
        week = _now().strftime("%G-W%V")
        for suffix, value in (
            ("runs", 1),
            ("weak", int(report.get("fragility_score", 0))),
        ):
            key = f"{_ADVERSARIAL_HIT_COUNTER}:{week}:{suffix}"
            rc.incrby(key, value)
            rc.expire(key, 30 * 24 * 3600)
    except Exception as exc:
        log.debug("adversarial: counter bump failed: %s", exc)


def get_adversarial_report_this_week() -> dict:
    """Weekly counts for the daily digest: runs + weak patterns flagged."""
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.adversarial_read")
        return {"runs": 0, "weak": 0}
    try:
        week = _now().strftime("%G-W%V")
        runs_raw = rc.get(f"{_ADVERSARIAL_HIT_COUNTER}:{week}:runs")
        weak_raw = rc.get(f"{_ADVERSARIAL_HIT_COUNTER}:{week}:weak")
        def _as_int(v) -> int:
            if not v:
                return 0
            if isinstance(v, bytes):
                v = v.decode()
            try:
                return int(v)
            except ValueError:
                return 0
        return {"runs": _as_int(runs_raw), "weak": _as_int(weak_raw)}
    except Exception:
        return {"runs": 0, "weak": 0}


# Security guard block counter — feeds the compliance synthesizer
_SECURITY_GUARD_COUNTER = "hs:security_guard_blocks"


def _bump_security_guard_block_counter() -> None:
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.security_guard_bump")
        return
    try:
        day = _now().strftime("%Y-%m-%d")
        key = f"{_SECURITY_GUARD_COUNTER}:{day}"
        rc.incr(key)
        rc.expire(key, 90 * 24 * 3600)
    except Exception as exc:
        log.debug("security_guard: counter bump failed: %s", exc)


def get_security_guard_blocks_7d() -> int:
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.security_guard_read")
        return 0
    total = 0
    try:
        from datetime import timedelta as _td
        today = _now()
        for offset in range(7):
            day = (today - _td(days=offset)).strftime("%Y-%m-%d")
            raw = rc.get(f"{_SECURITY_GUARD_COUNTER}:{day}")
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                total += int(raw)
            except ValueError:
                pass
    except Exception:
        return 0
    return total


def get_fix_template_hits_this_week() -> int:
    """Return the number of template cache hits for the current ISO week."""
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.fix_template_hits_read")
        return 0
    try:
        week = _now().strftime("%G-W%V")
        raw = rc.get(f"{_FIX_TEMPLATE_HIT_COUNTER}:{week}")
        if not raw:
            return 0
        if isinstance(raw, bytes):
            raw = raw.decode()
        return int(raw)
    except Exception:
        return 0


def _compute_antigen_scope_key(
    source_type: str | None, source_ref: str | None,
) -> str | None:
    """Hash of (source_type, source_ref) used as the immune-system scope.
    Returns None if we can't form a meaningful scope (either field missing)."""
    if not source_type or not source_ref:
        return None
    raw = f"{source_type}|{source_ref}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _record_antigen(
    scope_key: str | None, skeleton_hash: str | None,
    candidate_id: int, outcome: str, failure_reason: str | None,
    evidence_source: str | None = None,
) -> None:
    """Persist an antigen: (scope, skeleton) bound to a regression outcome.

    Learning-isolation gate: only real-merchant regressions record
    antigens. Test regressions (pytest on prod host via deploy.sh)
    would otherwise poison the immune system with false positives.
    """
    if not scope_key or not skeleton_hash:
        return
    try:
        from app.services.learning_isolation import is_product_learning_eligible
        if not is_product_learning_eligible(evidence_source):
            return
    except Exception:
        return
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.antigen_record")
        return
    try:
        payload = json.dumps({
            "candidate_id": candidate_id,
            "outcome": outcome,
            "failure_reason": (failure_reason or "")[:300],
            "ts": _now().isoformat(),
        }, default=str)
        rc.setex(
            f"{_ANTIGEN_REDIS_PREFIX}:{scope_key}:{skeleton_hash}",
            _ANTIGEN_TTL_SECONDS,
            payload,
        )
    except Exception as exc:
        log.debug("antigen: record failed: %s", exc)


def _check_antigen(
    scope_key: str | None, skeleton_hash: str | None,
) -> dict | None:
    """Lookup a (scope, skeleton) antigen. Returns stored failure metadata."""
    if not scope_key or not skeleton_hash:
        return None
    rc = _redis_safe()
    if rc is None:
        record_silent_return("bugfix_pipeline.antigen_check")
        return None
    try:
        raw = rc.get(f"{_ANTIGEN_REDIS_PREFIX}:{scope_key}:{skeleton_hash}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        if isinstance(data, dict):
            data["match_type"] = "immune_antigen"
            return data
    except Exception as exc:
        log.debug("antigen: check failed: %s", exc)
    return None


def _check_patch_fingerprint(
    db: Session, fingerprint: str, diff_fp: str | None = None, lookback_days: int = 30,
    skeleton_fp: str | None = None,
    source_type: str | None = None, source_ref: str | None = None,
) -> dict | None:
    """
    Check if a patch with this fingerprint (or diff fingerprint or AST
    skeleton fingerprint) recently failed. Four-dimensional structural
    dedup: identity, normalized diff, AST skeleton, immune antigen.
    The antigen layer (D1) scopes skeleton memory to (source_type,
    source_ref), catching scope-specific regressions that the global
    skeleton layer misses (different scopes, same skeleton, one regressed).
    Returns dict with candidate_id and outcome if found, None otherwise.
    """
    # Immune antigen — scoped skeleton memory. Catches "this scope has
    # already rejected this strategy" even if the fleet-wide skeleton
    # cache expired or never saw it fail.
    antigen_scope = _compute_antigen_scope_key(source_type, source_ref)
    antigen_match = _check_antigen(antigen_scope, skeleton_fp)
    if antigen_match:
        return antigen_match

    # AST skeleton match — fastest, Redis-backed, catches renamed variants
    skel_match = _check_skeleton_fingerprint(skeleton_fp)
    if skel_match:
        return skel_match

    try:
        from app.models.patch_fingerprint import PatchFingerprint
        from sqlalchemy import or_

        cutoff = _now() - timedelta(days=lookback_days)

        # Build filter: match identity fingerprint OR diff fingerprint
        fp_conditions = [PatchFingerprint.fingerprint == fingerprint]
        if diff_fp:
            fp_conditions.append(PatchFingerprint.diff_fingerprint == diff_fp)

        fp = (
            db.query(PatchFingerprint)
            .filter(
                or_(*fp_conditions),
                PatchFingerprint.outcome.in_(["rolled_back", "apply_failed", "tests_failed", "test_timeout"]),
                PatchFingerprint.created_at >= cutoff,
            )
            .order_by(PatchFingerprint.created_at.desc())
            .first()
        )
        if fp:
            match_type = "diff" if (diff_fp and fp.diff_fingerprint == diff_fp) else "identity"
            return {
                "candidate_id": fp.bugfix_candidate_id,
                "outcome": fp.outcome,
                "failure_reason": fp.failure_reason,
                "created_at": fp.created_at,
                "match_type": match_type,
            }
    except Exception as exc:
        log.warning("patch_fingerprint: check failed (non-fatal): %s", exc)
    return None


def _record_patch_fingerprint(
    db: Session, candidate: BugFixCandidate, outcome: str,
    failure_reason: str | None = None,
) -> None:
    """Record a patch fingerprint (identity + normalized diff + AST
    skeleton) for future dedup."""
    try:
        from app.models.patch_fingerprint import PatchFingerprint
        fp_hash = _compute_patch_fingerprint(
            candidate.title, candidate.patch_files, candidate.patch_diff,
        )
        diff_fp = _compute_diff_fingerprint(candidate.patch_diff)
        domain = _classify_candidate_domain(candidate)
        from app.services.learning_isolation import label_fingerprint
        fp = PatchFingerprint(
            fingerprint=fp_hash,
            diff_fingerprint=diff_fp,
            bugfix_candidate_id=candidate.id,
            outcome=outcome,
            failure_reason=failure_reason[:500] if failure_reason else None,
            source_type=candidate.source_type,
            source_ref=candidate.source_ref,
            affected_domain=domain,
            patch_files=candidate.patch_files,
        )
        label_fingerprint(db, fp, candidate)
        db.add(fp)
        db.flush()
    except Exception as exc:
        log.warning("patch_fingerprint: record failed (non-fatal): %s", exc)

    # C3 — also record AST skeleton fingerprint (Redis-backed) for
    # structural duplicate detection. Failures only — successful
    # patches do not pollute the skeleton index.
    if outcome in ("apply_failed", "rolled_back", "tests_failed", "test_timeout"):
        try:
            skel = _compute_ast_skeleton_fingerprint(candidate.patch_diff)
            if skel:
                _record_skeleton_fingerprint(
                    candidate.id, skel, outcome, failure_reason,
                )
                # D1 — also bind the skeleton to the (source_type, source_ref)
                # antigen scope. The immune system remembers which strategies
                # have regressed per-scope, not just fleet-wide.
                scope_key = _compute_antigen_scope_key(
                    candidate.source_type, candidate.source_ref,
                )
                _record_antigen(
                    scope_key, skel, candidate.id, outcome, failure_reason,
                    evidence_source=getattr(candidate, "evidence_source", None),
                )
        except Exception as exc:
            log.debug("skeleton_fp: record skipped: %s", exc)


def _lookup_lessons_for_proposal(db: Session, domain: str) -> tuple[str | None, list[int]]:
    """
    Look up active lessons for a domain to inject into LLM context.
    Returns (formatted text block, list of lesson IDs used) or (None, []).

    ISOLATION: All evidence sources contribute to TECHNICAL context (patch
    formatting, failure patterns). But lessons are clearly labeled so the
    LLM understands the evidence weight. Only real_merchant lessons are
    marked as high-trust; pre-merchant lessons are labeled as low-trust
    technical reference.
    """
    try:
        from app.models.system_lesson import SystemLesson
        from app.services.learning_isolation import is_product_learning_eligible
        lessons = (
            db.query(SystemLesson)
            .filter(
                SystemLesson.status == "active",
                SystemLesson.confidence >= 0.3,
                SystemLesson.domain.in_([domain, "unknown"]),
            )
            .order_by(SystemLesson.confidence.desc())
            .limit(5)
            .all()
        )
        if not lessons:
            return None, []

        lesson_ids = [l.id for l in lessons]
        lines = [f"## Institutional Memory — Lessons for domain '{domain}'"]
        for l in lessons:
            marker = "✓" if l.lesson_type == "effective_pattern" else "✗"
            source = getattr(l, "evidence_source", None) or "pre_merchant"
            trust_tag = "HIGH-TRUST" if is_product_learning_eligible(source) else "TECHNICAL-ONLY"
            lines.append(
                f"- {marker} [{l.lesson_type}] [{trust_tag}] {l.summary} "
                f"(confidence: {l.confidence:.1f}, evidence: {l.evidence_count})"
            )
        return "\n".join(lines), lesson_ids
    except Exception:
        return None, []


# ---------------------------------------------------------------------------
# Hard lesson constraints — institutional memory as DO-NOT rules
# ---------------------------------------------------------------------------
#
# Every failed patch writes a PatchFingerprint row with a failure_reason.
# The sum of YOUR observed failures becomes a set of hard constraints
# injected into every future propose_patch for the same domain:
#
#   "## DO NOT
#    Over the last 90 days these approaches failed in this domain:
#      - hallucinated_import (4x): LLM added imports for non-existent
#        modules. Only import from files listed in the grounding manifest.
#      - corrupt_patch (3x): diffs with malformed hunks. Every `@@` must
#        match `^@@ -\d+,\d+ \+\d+,\d+ @@`.
#      - file_not_found (2x): LLM referenced paths that do not exist.
#        Only touch paths from the file list above."
#
# This is the competitive moat piece: a fresh deployment has zero rules
# and learns them the hard way. A production deployment with months of
# real failures has a rich specific rule set that a copycat cannot
# replicate without the same failure history.

#: Human-readable explanations for each failure-reason prefix we detect.
_FAILURE_REASON_TEMPLATES: dict[str, str] = {
    "llm_returned_empty_diff": (
        "Your diff was empty. Always return a non-empty unified diff "
        "or return the canonical 'unable to determine' response."
    ),
    "json_parse_error": (
        "Your response was not valid JSON. Return ONLY a JSON object, "
        "no prose, no markdown fences."
    ),
    "diff_validation_failed": (
        "Your diff had structural errors. Every hunk must have valid "
        "'@@ -start,count +start,count @@' headers and every changed "
        "line must start with +/-."
    ),
    "semantic_validation_failed": (
        "Your diff referenced files or symbols that do not exist. Only "
        "modify files listed in the grounding manifest; only use imports "
        "that resolve on disk."
    ),
    "apply_check_failed": (
        "git apply --check rejected your diff. The patch must apply cleanly "
        "against the current HEAD with no fuzz."
    ),
    "tests_failed": (
        "The test_command you proposed failed after apply. Make sure any "
        "imports/symbols you add are wired correctly and run without error."
    ),
    "fingerprint_dedup": (
        "Your approach is semantically identical to a previously failed "
        "attempt. Try a fundamentally different angle."
    ),
    "diff_fingerprint_dedup": (
        "Your diff is semantically identical to a previously failed diff. "
        "Do not repeat the same approach."
    ),
    "prompt_ungrounded_preflight": (
        "A prior candidate pointed at a file that does not exist on disk. "
        "Only propose patches against paths from the grounding manifest."
    ),
    "quarantined_family": (
        "This (domain, source_type) family is hard-quarantined after "
        "5+ recent failures. Wait for the quarantine to expire (60d) "
        "or operator-clear it explicitly via /ops/quarantine."
    ),
    "phantom_path": (
        "You wrote `+++ b/<path>` to a file that does not exist and did not "
        "introduce it via `--- /dev/null`. Use only paths from the manifest."
    ),
    "duplicate_symbol": (
        "You added a function whose name already exists in the same file. "
        "Read the file's signatures before adding new defs."
    ),
    "hallucinated_import": (
        "You imported a symbol that does not exist in the target module. "
        "Only import names from the AST signatures shown in the prompt."
    ),
    "untested_significant_change": (
        "Non-trivial app/ changes must ship with a co-committed test file. "
        "Add a tests/test_*.py edit alongside the production change."
    ),
}


def build_hard_lesson_constraints(
    db: Session, *, affected_domain: str | None, source_type: str | None,
    max_rules: int = 6, lookback_days: int = 90,
) -> str | None:
    """
    Build a "## DO NOT" prompt section from historical PatchFingerprint
    failures in the same (domain, source_type). Returns None if no
    relevant history exists.

    Called by propose_patch before invoking the LLM. Pre-LLM enforcement
    saves budget that would otherwise go to re-generating known-bad
    approaches.
    """
    if not affected_domain:
        return None

    try:
        from app.models.patch_fingerprint import PatchFingerprint
        cutoff = _now() - timedelta(days=lookback_days)
        rows = (
            db.query(
                PatchFingerprint.failure_reason,
                func.count(PatchFingerprint.id).label("n"),
            )
            .filter(
                PatchFingerprint.affected_domain == affected_domain,
                PatchFingerprint.outcome.in_(["apply_failed", "rolled_back", "ineffective"]),
                PatchFingerprint.created_at >= cutoff,
                PatchFingerprint.failure_reason.isnot(None),
            )
            .group_by(PatchFingerprint.failure_reason)
            .order_by(func.count(PatchFingerprint.id).desc())
            .limit(max_rules * 2)  # pull extra, group by family
            .all()
        )
    except Exception as exc:
        log.warning("hard_lessons: fingerprint query failed: %s", exc)
        return None

    if not rows:
        return None

    # Group by failure-reason prefix family (everything before the first `:`)
    families: dict[str, int] = {}
    for reason, n in rows:
        prefix = (reason or "").split(":", 1)[0].strip()
        if prefix:
            families[prefix] = families.get(prefix, 0) + int(n)

    if not families:
        return None

    # Sort by count desc, keep top N
    top = sorted(families.items(), key=lambda kv: kv[1], reverse=True)[:max_rules]

    lines = [
        f"## DO NOT — observed failure modes in domain '{affected_domain}' (last {lookback_days}d)",
        "These approaches failed repeatedly. Do NOT repeat them:",
    ]
    for prefix, count in top:
        explanation = _FAILURE_REASON_TEMPLATES.get(
            prefix,
            f"failure family '{prefix}' has occurred {count}x — avoid repetition of this pattern.",
        )
        lines.append(f"- [{count}x] {prefix}: {explanation}")

    return "\n".join(lines)


def _classify_candidate_domain(candidate: BugFixCandidate) -> str | None:
    """Classify a candidate into a domain using project_brain."""
    if candidate.affected_domain:
        return candidate.affected_domain
    if not candidate.patch_files:
        return None
    try:
        from app.services.project_brain import classify_file
        files = json.loads(candidate.patch_files)
        if files:
            result = classify_file(files[0])
            domain = result.get("domain")
            candidate.affected_domain = domain
            return domain
    except Exception as exc:
        log.warning("bugfix_pipeline: domain classification failed: %s", exc)
    return None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Triage: scan for actionable bugs → create candidates
# ---------------------------------------------------------------------------

def run_bug_triage(db: Session) -> dict:
    """
    Scan ops_alerts and action_outcomes for patterns that indicate bugs.
    Create BugFixCandidate rows for new findings. Dedup by source_type+source_ref.
    Suppresses sources that are thrashing (3+ failed attempts in 30 days).
    """
    summary = {"scanned": 0, "created": 0, "deduped": 0, "suppressed": 0}
    cutoff = _now() - timedelta(hours=_TRIAGE_LOOKBACK_HOURS)

    # Rule 1: GDPR failures → likely code bug
    gdpr_alerts = db.execute(text("""
        SELECT id, shop_domain, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'gdpr_failure' AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for alert in gdpr_alerts:
        summary["scanned"] += 1
        ref = f"alert_{alert[0]}"
        if _should_skip_source(db, "ops_alert", ref, summary):
            continue
        _create_candidate(
            db,
            source_type="ops_alert",
            source_ref=ref,
            title=f"GDPR processing failure (alert {alert[0]})",
            summary_text=alert[2],
            context={"alert_id": alert[0], "shop": alert[1], "detail": alert[3]},
        )
        summary["created"] += 1

    # Rule 2: Repeated worker failures → likely code or config bug
    worker_alerts = db.execute(text("""
        SELECT id, source, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'worker_repeated_failure' AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for alert in worker_alerts:
        summary["scanned"] += 1
        ref = f"worker_{alert[1]}"
        if _should_skip_source(db, "ops_alert", ref, summary):
            continue
        _create_candidate(
            db,
            source_type="ops_alert",
            source_ref=ref,
            title=f"Worker {alert[1]} repeated failures",
            summary_text=alert[2],
            context={"alert_id": alert[0], "worker": alert[1], "detail": alert[3]},
        )
        summary["created"] += 1

    # Rule 3: Repeated no_effect outcomes → action implementation may be broken
    no_effect = db.execute(text("""
        SELECT action_type, target_id, COUNT(*) AS cnt
        FROM action_outcomes
        WHERE outcome_status = 'no_effect' AND executed_at >= :cutoff
        GROUP BY action_type, target_id
        HAVING COUNT(*) >= 3
        LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for row in no_effect:
        summary["scanned"] += 1
        ref = f"outcome_{row[0]}_{row[1]}"
        if _should_skip_source(db, "outcome", ref, summary):
            continue
        _create_candidate(
            db,
            source_type="outcome",
            source_ref=ref,
            title=f"Action {row[0]} repeatedly ineffective on {row[1]}",
            summary_text=f"{row[2]} consecutive no_effect outcomes for {row[0]} targeting {row[1]}",
            context={"action_type": row[0], "target": row[1], "count": row[2]},
        )
        summary["created"] += 1

    # Rule 4: Merchant-reported bugs → chatbot-originated alerts
    # Consumes alerts created by merchant_chatbot._route_to_pipeline()
    merchant_bugs = db.execute(text("""
        SELECT id, shop_domain, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'merchant_reported_bug'
          AND resolved = false
          AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for alert in merchant_bugs:
        summary["scanned"] += 1
        ref = f"merchant_bug_alert_{alert[0]}"
        if _should_skip_source(db, "support_incident", ref, summary):
            continue
        candidate = _create_candidate(
            db,
            source_type="support_incident",
            source_ref=ref,
            title=f"Merchant reported: {(alert[2] or '')[:150]}",
            summary_text=alert[2],
            context={"alert_id": alert[0], "shop": alert[1], "detail": alert[3]},
        )
        summary["created"] += 1

        # Back-link: update any support incidents linked to this alert
        _backlink_incidents_to_candidate(db, alert_id=alert[0], candidate_id=candidate.id)

    # Rule 5: Frontend errors → React/JS crashes, failed fetches, render bugs.
    # The reporter in /ops/frontend-errors builds a stable `fe:{component}:{hash8}`
    # source so repeated reports of the same crash collapse into one candidate.
    # We group by source rather than by alert_id so the triage picks the most
    # recent fingerprint once — subsequent alerts in the same window are
    # harmless (dedup in _should_skip_source catches them).
    fe_alerts = db.execute(text("""
        SELECT id, source, shop_domain, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'frontend_error'
          AND resolved = false
          AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 10
    """), {"cutoff": cutoff}).fetchall()

    seen_fe_sources: set[str] = set()
    for alert in fe_alerts:
        source_key = alert[1] or f"alert_{alert[0]}"
        if source_key in seen_fe_sources:
            continue
        seen_fe_sources.add(source_key)
        summary["scanned"] += 1
        # Use the fingerprinted source directly as source_ref so thrash
        # detection and reopening key off the same identity across reports.
        ref = source_key
        if _should_skip_source(db, "frontend_error", ref, summary):
            continue
        _create_candidate(
            db,
            source_type="frontend_error",
            source_ref=ref,
            title=f"Frontend error: {(alert[3] or '')[:180]}",
            summary_text=alert[3],
            context={
                "alert_id": alert[0],
                "shop": alert[2],
                "detail": alert[4],
                "source": source_key,
            },
        )
        summary["created"] += 1

    # Rule 6: Semantic drift → silent data corruption caught by data_integrity_probe.
    # Each drift alert has a source of the form `probe:{check}:{shop}` which
    # becomes the stable source_ref. Dedup + thrash suppression follow the
    # same path as every other triage rule.
    drift_alerts = db.execute(text("""
        SELECT id, source, shop_domain, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'semantic_drift'
          AND resolved = false
          AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 10
    """), {"cutoff": cutoff}).fetchall()

    seen_drift_sources: set[str] = set()
    for alert in drift_alerts:
        source_key = alert[1] or f"alert_{alert[0]}"
        if source_key in seen_drift_sources:
            continue
        seen_drift_sources.add(source_key)
        summary["scanned"] += 1
        if _should_skip_source(db, "semantic_drift", source_key, summary):
            continue
        _create_candidate(
            db,
            source_type="semantic_drift",
            source_ref=source_key,
            title=f"Semantic drift: {(alert[3] or '')[:180]}",
            summary_text=alert[3],
            context={
                "alert_id": alert[0],
                "shop": alert[2],
                "detail": alert[4],
                "source": source_key,
            },
        )
        summary["created"] += 1

    # Rule 7 — generic recurring-alert catch-all.
    # Anything in ops_alerts that recurs >= _GENERIC_RECURRENCE_THRESHOLD
    # times in the lookback window AND isn't already handled by Rules 1-6
    # AND has severity in ('warning','critical') becomes a candidate.
    # This closes the gap where new subsystems write alerts but never get
    # triaged because they don't match a hand-coded rule.
    handled_alert_types = (
        "gdpr_failure",
        "worker_repeated_failure",
        "merchant_reported_bug",
        "frontend_error",
        "semantic_drift",
        # Self-healing meta — already managed by the pipeline itself
        "chronic_thrashing",
        "bugfix_apply_failed",
        "bugfix_rolled_back",
    )
    generic_alerts = db.execute(text("""
        SELECT alert_type, source, MAX(id) AS latest_id, MAX(shop_domain) AS shop,
               COUNT(*) AS occurrences,
               MAX(summary) AS latest_summary,
               MAX(severity) AS severity
        FROM ops_alerts
        WHERE created_at >= :cutoff
          AND resolved = false
          AND severity IN ('warning', 'critical')
          AND alert_type NOT IN :handled
        GROUP BY alert_type, source
        HAVING COUNT(*) >= :min_recurrence
        ORDER BY MAX(created_at) DESC
        LIMIT 10
    """), {
        "cutoff": cutoff,
        "handled": handled_alert_types,
        "min_recurrence": _GENERIC_RECURRENCE_THRESHOLD,
    }).fetchall()

    for row in generic_alerts:
        alert_type, source, latest_id, shop, occurrences, latest_summary, severity = row
        summary["scanned"] += 1
        ref = f"generic:{alert_type}:{source or 'unknown'}"
        if _should_skip_source(db, "ops_alert_generic", ref, summary):
            continue
        _create_candidate(
            db,
            source_type="ops_alert_generic",
            source_ref=ref,
            title=f"{alert_type} recurring ({occurrences}x): {(latest_summary or '')[:140]}",
            summary_text=latest_summary or f"{alert_type} from {source} recurred {occurrences} times",
            context={
                "alert_type": alert_type,
                "source": source,
                "latest_alert_id": latest_id,
                "shop": shop,
                "occurrences": occurrences,
                "severity": severity,
            },
        )
        summary["created"] += 1

    # Rule 8 — cross-shop pattern compaction (B2). When the SAME
    # `(alert_type, source_template)` appears in ≥ 3 distinct shops in
    # the lookback window, create ONE fleet-wide candidate that covers
    # all of them. This is the asymmetric advantage at scale: a fix
    # shipped once heals N shops simultaneously.
    #
    # Source template normalization: collapses shop-specific identifiers
    # so semantically identical signals across shops match. Example:
    #   probe:cvr_drift:shop_a → probe:cvr_drift:*
    #   webhook:resend_rejected:order_42 → webhook:resend_rejected:*
    handled_for_fleet = (
        "frontend_error",  # frontend errors are visibility-only, no auto-fix
        "chronic_thrashing",
        "bugfix_apply_failed",
        "bugfix_rolled_back",
        "heartbeat_synthetic_test",
        "heartbeat_ok",
        "heartbeat_failed",
        "deploy_succeeded",
        "deploy_failed",
        "deploy_rolled_back",
    )
    fleet_alerts = db.execute(text("""
        SELECT alert_type,
               regexp_replace(source, ':[^:*]+$', ':*') AS source_template,
               COUNT(DISTINCT shop_domain) AS shops_affected,
               COUNT(*) AS total_occurrences,
               MAX(severity) AS severity,
               MAX(summary) AS latest_summary,
               MAX(id) AS latest_id
        FROM ops_alerts
        WHERE created_at >= :cutoff
          AND resolved = false
          AND severity IN ('warning', 'critical')
          AND alert_type NOT IN :handled
          AND shop_domain IS NOT NULL
        GROUP BY alert_type, regexp_replace(source, ':[^:*]+$', ':*')
        HAVING COUNT(DISTINCT shop_domain) >= :min_shops
        ORDER BY COUNT(DISTINCT shop_domain) DESC, COUNT(*) DESC
        LIMIT 5
    """), {
        "cutoff": cutoff,
        "handled": handled_for_fleet,
        "min_shops": _FLEET_WIDE_MIN_SHOPS,
    }).fetchall()

    for row in fleet_alerts:
        alert_type, src_template, shops_affected, total_occurrences, severity, latest_summary, latest_id = row
        summary["scanned"] += 1
        ref = f"fleet:{alert_type}:{src_template}"
        if _should_skip_source(db, "fleet_wide", ref, summary):
            continue
        cand = _create_candidate(
            db,
            source_type="fleet_wide",
            source_ref=ref,
            title=(
                f"Fleet-wide: {alert_type} affecting {shops_affected} shops "
                f"({total_occurrences} occurrences)"
            ),
            summary_text=latest_summary or f"{alert_type} from {src_template}",
            context={
                "alert_type": alert_type,
                "source_template": src_template,
                "shops_affected": shops_affected,
                "total_occurrences": total_occurrences,
                "severity": severity,
                "latest_alert_id": latest_id,
                "scope": "fleet_wide",
            },
        )
        # Bump priority for fleet-wide candidates — they heal N shops at once
        try:
            cand.priority_score = min(100, (cand.priority_score or 0) + 20)
            db.flush()
        except Exception as exc:
            log.warning("bugfix_pipeline: fleet-wide priority boost failed: %s", exc)
        summary["created"] += 1

    # Recover stuck candidates (applying for >10 min)
    _recover_stuck_candidates(db)

    if summary["created"] > 0:
        db.flush()
        log.info("bugfix_triage: scanned=%d created=%d deduped=%d", summary["scanned"], summary["created"], summary["deduped"])

    return summary


#: Source types whose candidates must NOT be auto-proposed by the LLM.
#: These are triaged and shown in the operator dashboard, but the LLM
#: propose_patch pipeline does not attempt to generate a fix — either
#: because the LLM lacks training context for the target (e.g. React
#: frontend code) or because the fix inherently needs human judgment.
_VISIBILITY_ONLY_SOURCE_TYPES: frozenset[str] = frozenset({
    # Phase-1 frontend bridge: we capture the error + alert the operator
    # but the LLM is not yet trained on the specific dashboard codebase.
    # Auto-proposing .tsx patches would burn budget and stuck the candidate
    # in apply_failed loops. Re-enable when we ship frontend LLM enrichment.
    "frontend_error",
})


def is_visibility_only(source_type: str | None) -> bool:
    """Return True if candidates of this source_type must be handled by a
    human operator instead of the LLM auto-propose loop."""
    return (source_type or "") in _VISIBILITY_ONLY_SOURCE_TYPES


def run_auto_propose(db: Session, max_per_cycle: int = 2) -> dict:
    """
    Auto-propose patches for open/analyzed candidates that have not yet
    been attempted. Max 2 per cycle to control LLM cost.

    Visibility-only source types (see _VISIBILITY_ONLY_SOURCE_TYPES) are
    excluded from auto-propose. They still flow through triage, still
    produce ops_alert rows, still appear in the operator dashboard and
    in loop_health weakness scoring — they just don't get an LLM proposal.
    """
    summary = {"attempted": 0, "proposed": 0, "failed": 0, "skipped_visibility": 0}

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status.in_(["open", "analyzed"]),
            BugFixCandidate.proposal_attempted_at.is_(None),
            ~BugFixCandidate.source_type.in_(list(_VISIBILITY_ONLY_SOURCE_TYPES)),
        )
        .order_by(
            BugFixCandidate.priority_score.desc().nullslast(),
            BugFixCandidate.created_at,
        )
        .limit(max_per_cycle)
        .all()
    )

    # Observability: count how many candidates we skipped because they
    # were visibility-only, so ops dashboard can show the backlog of
    # "awaiting human triage".
    summary["skipped_visibility"] = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status.in_(["open", "analyzed"]),
            BugFixCandidate.proposal_attempted_at.is_(None),
            BugFixCandidate.source_type.in_(list(_VISIBILITY_ONLY_SOURCE_TYPES)),
        )
        .count()
    )

    for c in candidates:
        summary["attempted"] += 1
        c.proposal_attempted_at = _now()
        try:
            success = propose_patch(db, c.id)
            if success:
                summary["proposed"] += 1
                # Determine provider from env
                c.proposal_provider = "anthropic" if os.getenv("ANTHROPIC_API_KEY", "").strip() else (
                    "openai" if os.getenv("OPENAI_API_KEY", "").strip() else "none"
                )
            else:
                summary["failed"] += 1
                c.proposal_error = c.failure_reason or "proposal_returned_false"
        except Exception as exc:
            summary["failed"] += 1
            c.proposal_error = str(exc)[:500]
            log.warning("auto_propose: failed id=%d: %s", c.id, exc)
        db.flush()

    if summary["attempted"] > 0:
        log.info(
            "auto_propose: attempted=%d proposed=%d failed=%d",
            summary["attempted"], summary["proposed"], summary["failed"],
        )

    return summary


def _has_open_candidate(db: Session, source_type: str, source_ref: str) -> bool:
    """
    Check whether this source already has a candidate we should not duplicate.

    We consider three classes of "don't re-triage":

      1. **Active states** — any candidate in open/analyzed/patch_proposed/
         approved/applying is still being processed. Re-triaging would
         create a parallel duplicate that races the one already in flight.

      2. **Recent terminal states** — a candidate in apply_failed /
         rolled_back / discarded within the last 24h means we already
         tried (or explicitly chose not to act) very recently. Creating
         a new candidate within the dedup window would just loop the
         pipeline (observed pattern in prod: merchant_bug_alert_16985
         had 2 discarded candidates 15 minutes apart because the
         previous dedup only considered active states).

      3. **Recent successful applications** — an `applied` candidate
         within 24h, regardless of outcome_status. Outcome evaluation
         is async (48h delay), so we must not re-triage a just-applied
         fix while we're still measuring it.

    After 24h the window opens again: if a source keeps producing
    incidents, thrash_score() is the secondary gate — it either
    suppresses or annotates the retry with prior-failure context.
    """
    # Step 1 — any active candidate blocks duplicates unconditionally.
    active = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == source_type,
        BugFixCandidate.source_ref == source_ref,
        BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed", "approved", "applying"]),
    ).first()
    if active is not None:
        return True

    # Step 2 — any recent terminal attempt within 24h blocks duplicates.
    window_start = _now() - timedelta(hours=24)
    recent_terminal = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == source_type,
        BugFixCandidate.source_ref == source_ref,
        BugFixCandidate.status.in_(["apply_failed", "rolled_back", "discarded", "applied"]),
        BugFixCandidate.created_at >= window_start,
    ).first()
    return recent_terminal is not None


def _should_skip_source(db: Session, source_type: str, source_ref: str, summary: dict) -> bool:
    """
    Dedup + graduated thrash gate + escalation.

    Uses the new thrash_score() from loop_health which returns a continuous
    signal in [0, 1] instead of the old binary is_source_thrashing().

    * score == 0   → clean, do not skip.
    * 0 < score < 0.5 → mild history; do not skip, the triage pipeline can
                      still create candidates. The annotation in the
                      context_json lets propose_patch() enrich the prompt
                      with "prior attempts failed, try a different angle."
    * score >= 0.5 → heavy thrash; skip and escalate.
    """
    if _has_open_candidate(db, source_type, source_ref):
        summary["deduped"] += 1
        return True
    try:
        from app.services.loop_health import thrash_score
        score = thrash_score(db, source_type, source_ref)
        # Preserve a legacy signal for observability
        summary.setdefault("thrash_scores", {})[f"{source_type}:{source_ref}"] = round(score, 2)
        if score >= 0.5:
            summary["suppressed"] = summary.get("suppressed", 0) + 1
            log.info(
                "triage: suppressed high-thrash source=%s ref=%s score=%.2f",
                source_type, source_ref, score,
            )
            _escalate_thrashing(db, source_type, source_ref)
            return True
    except ImportError:
        pass
    return False


def _escalate_thrashing(db: Session, source_type: str, source_ref: str) -> None:
    """
    Create a dedup-safe operator alert for a chronically thrashing source.
    Only creates one unresolved alert per source — won't spam.
    """
    try:
        from app.models.ops_alert import OpsAlert
        existing = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "chronic_thrashing",
                OpsAlert.source == f"{source_type}:{source_ref}",
                OpsAlert.resolved == False,
            )
            .first()
        )
        if existing:
            return  # already escalated, not yet resolved

        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source=f"{source_type}:{source_ref}",
            alert_type="chronic_thrashing",
            summary=(
                f"Bug source '{source_ref}' ({source_type}) has failed 3+ times in 30 days. "
                f"Auto-fix attempts are now suppressed. Manual investigation required."
            ),
            detail={
                "source_type": source_type,
                "source_ref": source_ref,
                "action": "Manual investigation needed — auto-fix pipeline cannot resolve this.",
            },
        )
        log.warning("triage: ESCALATED thrashing source=%s ref=%s → ops_alert created", source_type, source_ref)
    except Exception as exc:
        log.warning("triage: thrash escalation failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Priority scoring — deterministic, cheap, explainable
# ---------------------------------------------------------------------------
#
# Before 2026-04-11 the bugfix_candidates.priority_score column existed but
# was NEVER POPULATED — audit showed 85/86 rows with NULL. run_auto_propose
# and run_auto_apply both use `ORDER BY priority_score DESC NULLS LAST`,
# which with all-NULL data degenerates to plain FIFO by created_at. A
# critical webhook bug was processed after a stale low-criticality
# evolution proposal simply because the latter was older.
#
# This function computes a 0–100 integer score at candidate creation time
# based on five weighted signals. The result is stored on the row and
# used by the batch selection ORDER BY. Pure function, no DB calls, no
# LLM, safe to call thousands of times per minute.
#
# Signal weights sum to 1.0. Weights can be tuned but the structure must
# stay linear so a human can always explain "why this came before that".

_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 1.00,
    "warning":  0.55,
    "info":     0.20,
}

# Domain criticality is authoritative from project_brain. These weights
# mirror _DOMAIN_CRITICALITY but are kept local so we don't have to import
# project_brain at priority time (it does a cooldown-aware filesystem scan).
_DOMAIN_CRITICALITY_WEIGHTS: dict[str, float] = {
    "critical": 1.00,
    "high":     0.75,
    "medium":   0.50,
    "low":      0.25,
}

# Source type weights — encode "how often has this signal type proven
# to be a real, fixable bug". Tuning knob for future, starting values
# reflect deterministic observations from the first prod cycle.
_SOURCE_TYPE_WEIGHTS: dict[str, float] = {
    "sentry_incident":   1.00,  # real runtime exception with stacktrace
    "ops_alert":         0.90,  # deterministic alert from write_alert
    "semantic_drift":    0.85,  # silent data corruption — high value
    "recurrence":        0.85,  # reopened from ineffective — must try harder
    "support_incident":  0.75,  # merchant-reported, high trust
    "outcome":           0.65,  # 3+ no_effect action outcomes
    "frontend_error":    0.55,  # visibility-only anyway
    "evolution":         0.40,  # proactive refactor, not a bug
    "auto_rollback":     0.95,  # rollback of a bad fix — urgent
    "manual":            0.60,
}


def compute_priority_score(
    *,
    severity: str | None,
    source_type: str | None,
    affected_domain_criticality: str | None = None,
    recurrence_count: int = 0,
    age_minutes: int = 0,
    domain_effectiveness_pct: float | None = None,
    domain_sample_size: int = 0,
) -> tuple[int, dict[str, float]]:
    """
    Compute a 0-100 priority score with explainable breakdown.

    Pure function. Same inputs → same score. Suitable for calling
    inside _create_candidate (hot path).

    2026-04-11 elite-sprint addition: `domain_effectiveness_pct` and
    `domain_sample_size` come from adaptive_governance.DomainProfile and
    modulate the score based on how successful past fixes in the same
    domain have been. This is the primary competitive moat — a copycat
    architecture cannot replicate months of your own merchant telemetry
    feeding back into scoring. Fresh deployments start neutral and
    converge over time to YOUR merchant base.

    Returns
    -------
    (score, breakdown)
        score: int in [0, 100], rounded, higher = fix first
        breakdown: dict of component contributions for observability
    """
    sev = (severity or "warning").lower()
    sev_w = _SEVERITY_WEIGHTS.get(sev, 0.5)

    crit = (affected_domain_criticality or "medium").lower()
    crit_w = _DOMAIN_CRITICALITY_WEIGHTS.get(crit, 0.5)

    src = (source_type or "manual").lower()
    src_w = _SOURCE_TYPE_WEIGHTS.get(src, 0.5)

    # Recency — fresh incidents win but we don't want oscillation, so
    # use a step function instead of a continuous decay.
    if age_minutes <= 60:
        recency_w = 1.00
    elif age_minutes <= 24 * 60:
        recency_w = 0.70
    elif age_minutes <= 7 * 24 * 60:
        recency_w = 0.40
    else:
        recency_w = 0.20

    # Recurrence — a source the system has seen multiple times deserves
    # priority because repeat failures signal a deeper root cause.
    if recurrence_count >= 5:
        recur_w = 1.00
    elif recurrence_count >= 3:
        recur_w = 0.75
    elif recurrence_count >= 2:
        recur_w = 0.50
    elif recurrence_count >= 1:
        recur_w = 0.30
    else:
        recur_w = 0.10

    # Track record — per-domain historical success rate (adaptive_governance
    # DomainProfile). Uses Wilson-style dampening so tiny samples stay near
    # neutral (0.5). At 10+ measured outcomes the real effectiveness takes
    # over. At 50+ samples the signal is fully trusted.
    if domain_effectiveness_pct is None or domain_sample_size == 0:
        track_record_w = 0.5  # neutral — no data
    else:
        eff = max(0.0, min(1.0, domain_effectiveness_pct / 100.0))
        # Dampening: sample_size / (sample_size + 10) is the confidence
        # in the measurement; the remainder stays neutral at 0.5.
        confidence = domain_sample_size / (domain_sample_size + 10)
        track_record_w = eff * confidence + 0.5 * (1 - confidence)

    # Weighted sum. Weights now sum to 1.0 over 6 dimensions:
    #   severity 0.30 + criticality 0.22 + recency 0.13 + recurrence 0.13
    #   + source_type 0.10 + track_record 0.12
    # track_record is intentionally small enough to not dominate (12%)
    # because it is lagging evidence, but big enough to reorder ties.
    contributions = {
        "severity":     sev_w        * 0.30,
        "criticality":  crit_w       * 0.22,
        "recency":      recency_w    * 0.13,
        "recurrence":   recur_w      * 0.13,
        "source_type":  src_w        * 0.10,
        "track_record": track_record_w * 0.12,
    }
    total = sum(contributions.values())
    score = int(round(total * 100))
    score = max(0, min(100, score))
    return score, contributions


def _infer_alert_severity_from_context(source_type: str, context: dict) -> str:
    """Best-effort severity extraction from context_json. Fail-soft."""
    if not isinstance(context, dict):
        return "warning"
    # ops_alert contexts carry the full alert row under "detail"
    detail = context.get("detail")
    if isinstance(detail, dict):
        sev = detail.get("severity")
        if isinstance(sev, str):
            return sev.lower()
    # Some rules pass alert_id and not the full row — caller is
    # responsible for enriching. Default to warning.
    return "warning"


def _infer_domain_criticality(affected_domain: str | None) -> str:
    """Map affected_domain to criticality using project_brain semantics
    without actually importing project_brain (cooldown-bound FS scan)."""
    if not affected_domain:
        return "medium"
    _LOCAL_CRITICALITY = {
        "billing": "critical",
        "shopify_auth": "critical",
        "auth": "critical",
        "webhooks": "critical",
        "frontend_billing": "critical",
        "frontend_onboarding": "critical",
        "frontend_auth": "critical",
        "shopify_integration": "high",
        "orchestrator": "high",
        "autofix": "high",
        "model_governance": "high",
        "llm_infra": "high",
        "infra": "high",
        "migrations": "high",
        "frontend": "high",
        "merchant_api": "medium",
        "nudges": "medium",
        "workers": "medium",
        "support": "medium",
        "reviewer": "medium",
        "intelligence": "low",
        "tracking": "low",
        "observability": "low",
        "tests": "low",
    }
    return _LOCAL_CRITICALITY.get(affected_domain, "medium")


def _create_candidate(
    db: Session, *, source_type: str, source_ref: str,
    title: str, summary_text: str, context: dict,
) -> BugFixCandidate:
    # Compute priority at creation time so run_auto_propose and
    # run_auto_apply can order meaningfully. The affected_domain is not
    # yet known here (it's resolved later by _classify_candidate_domain
    # once patch_files exist), so we score without it — the domain
    # contribution will be a recompute opportunity post-propose.
    inferred_severity = _infer_alert_severity_from_context(source_type, context)
    recurrence_count = int(context.get("recurrence_count") or 0) if isinstance(context, dict) else 0
    score, breakdown = compute_priority_score(
        severity=inferred_severity,
        source_type=source_type,
        affected_domain_criticality=None,  # unknown at creation
        recurrence_count=recurrence_count,
        age_minutes=0,
    )

    # Stash the breakdown alongside the original context so ops can
    # explain *why* this score — critical for operator trust.
    enriched = dict(context) if isinstance(context, dict) else {"raw": context}
    enriched["priority_breakdown"] = {k: round(v, 3) for k, v in breakdown.items()}
    enriched["priority_inferred_severity"] = inferred_severity

    c = BugFixCandidate(
        source_type=source_type,
        source_ref=source_ref,
        title=title,
        summary=summary_text,
        context_json=json.dumps(enriched, default=str),
        status="open",
        priority_score=score,
    )
    db.add(c)
    db.flush()
    return c


def backfill_priority_scores(db: Session, limit: int = 500) -> dict:
    """
    Populate priority_score on historical candidates that were created
    before deterministic scoring was added. One-shot cleanup utility
    callable from an ops cron or ad-hoc Python.

    Only touches rows where priority_score IS NULL or = 0. Never modifies
    scores that were already computed. Safe to run repeatedly.
    """
    summary = {"scanned": 0, "backfilled": 0, "errors": 0}
    rows = (
        db.query(BugFixCandidate)
        .filter(
            (BugFixCandidate.priority_score.is_(None))
            | (BugFixCandidate.priority_score == 0),
        )
        .order_by(BugFixCandidate.created_at.desc())
        .limit(limit)
        .all()
    )
    summary["scanned"] = len(rows)
    for c in rows:
        try:
            severity = "warning"
            recurrence_count = 0
            if c.context_json:
                try:
                    ctx = json.loads(c.context_json)
                    if isinstance(ctx, dict):
                        severity = (
                            ctx.get("priority_inferred_severity")
                            or _infer_alert_severity_from_context(c.source_type, ctx)
                        )
                        recurrence_count = int(ctx.get("recurrence_count") or 0)
                except (ValueError, TypeError):
                    pass

            age_minutes = 0
            if c.created_at:
                age_minutes = max(0, int((_now() - c.created_at).total_seconds() / 60))

            crit = _infer_domain_criticality(c.affected_domain)
            score, _ = compute_priority_score(
                severity=severity,
                source_type=c.source_type,
                affected_domain_criticality=crit,
                recurrence_count=recurrence_count,
                age_minutes=age_minutes,
            )
            c.priority_score = score
            summary["backfilled"] += 1
        except Exception as exc:
            summary["errors"] += 1
            log.debug("backfill_priority: id=%d error: %s", c.id, exc)

    if summary["backfilled"]:
        db.flush()
        log.info(
            "backfill_priority: scanned=%d backfilled=%d errors=%d",
            summary["scanned"], summary["backfilled"], summary["errors"],
        )
    return summary


def recompute_priority_after_classification(
    candidate: BugFixCandidate,
    db: Session | None = None,
) -> None:
    """
    Recompute a candidate's priority_score AFTER affected_domain is set
    (which happens in _classify_candidate_domain during propose_patch).
    Called from propose_patch to refine the FIFO-time estimate once we
    know the real domain criticality.

    If `db` is provided, also injects the per-domain track record from
    adaptive_governance.DomainProfile — this is the competitive moat:
    priority is modulated by historical merchant-specific success rates
    that a fresh deployment simply cannot have.

    In-place mutation — caller must flush.
    """
    try:
        ctx = json.loads(candidate.context_json or "{}")
    except (ValueError, TypeError):
        ctx = {}
    severity = ctx.get("priority_inferred_severity") or "warning"
    recurrence_count = int(ctx.get("recurrence_count") or 0)

    # Compute age from created_at
    now = _now()
    age_minutes = 0
    if candidate.created_at:
        age_minutes = max(0, int((now - candidate.created_at).total_seconds() / 60))

    crit = _infer_domain_criticality(candidate.affected_domain)

    # Pull domain track record (optional — if db not provided or lookup
    # fails, we fall back to neutral scoring, no regression).
    domain_eff: float | None = None
    domain_n = 0
    if db is not None and candidate.affected_domain:
        try:
            from app.services.adaptive_governance import get_domain_profiles
            profiles = get_domain_profiles(db)
            profile = profiles.get(candidate.affected_domain)
            if profile is not None:
                domain_eff = profile.effectiveness_pct
                domain_n = profile.total_measured or 0
        except Exception as exc:
            log.debug("priority: domain profile lookup failed (non-fatal): %s", exc)

    score, breakdown = compute_priority_score(
        severity=severity,
        source_type=candidate.source_type,
        affected_domain_criticality=crit,
        recurrence_count=recurrence_count,
        age_minutes=age_minutes,
        domain_effectiveness_pct=domain_eff,
        domain_sample_size=domain_n,
    )
    candidate.priority_score = score
    # Refresh breakdown in context for observability
    ctx["priority_breakdown"] = {k: round(v, 3) for k, v in breakdown.items()}
    ctx["priority_domain_criticality"] = crit
    if domain_eff is not None:
        ctx["priority_domain_effectiveness_pct"] = round(domain_eff, 2)
        ctx["priority_domain_sample_size"] = domain_n
    candidate.context_json = json.dumps(ctx, default=str)


def _backlink_incidents_to_candidate(db: Session, alert_id: int, candidate_id: int):
    """
    Find support incidents linked to this ops_alert and set their
    linked_bugfix_candidate_id + transition status to 'investigating'.
    """
    from app.models.support_incident import SupportIncident
    incidents = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.linked_ops_alert_id == alert_id,
            SupportIncident.status.in_(["open", "triaged"]),
        )
        .all()
    )
    for inc in incidents:
        inc.linked_bugfix_candidate_id = candidate_id
        inc.status = "investigating"
        log.info("bugfix_triage: linked incident=%d → candidate=%d, status→investigating",
                 inc.id, candidate_id)
    if incidents:
        db.flush()


def _recover_stuck_candidates(db: Session):
    """
    Detect candidates that have rotted in intermediate states and escalate
    them. Previously this only recovered 'applying' — the dangerous state.
    Phase-5 hardening extends the sweep to every stuck state so backlog
    accumulation cannot silently starve the pipeline:

      * applying     > 10 min  → reset to patch_proposed (old behavior)
      * open         > 72 h    → escalate via ops_alert
      * analyzed     > 48 h    → escalate via ops_alert
      * patch_proposed > 168 h → escalate via ops_alert

    Escalation means: raise a unique, dedup-safe ops_alert so the operator
    can investigate. We never silently advance a non-applying candidate —
    doing so would hide a root-cause problem (LLM budget exhaustion, reviewer
    deadlock, etc.). The alert IS the action.
    """
    now = _now()

    # 1. Recover 'applying' — destructive-state recovery (reset to prior state).
    applying_cutoff = now - timedelta(minutes=10)
    stuck_applying = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "applying",
            BugFixCandidate.decided_at.isnot(None),
            BugFixCandidate.decided_at <= applying_cutoff,
        )
        .all()
    )
    for c in stuck_applying:
        c.status = "patch_proposed"
        c.failure_reason = "stuck_in_applying_recovered"
        log.warning("bugfix_triage: recovered stuck applying candidate id=%d", c.id)
    if stuck_applying:
        db.flush()

    # 2. Stuck intermediate states — emit ops_alert (dedup-safe), don't mutate.
    _STUCK_THRESHOLDS: list[tuple[str, timedelta, str]] = [
        ("open",           timedelta(hours=72),  "pipeline_stall_open"),
        ("analyzed",       timedelta(hours=48),  "pipeline_stall_analyzed"),
        ("patch_proposed", timedelta(hours=168), "pipeline_stall_proposed"),
    ]

    try:
        from app.models.ops_alert import OpsAlert
        from app.services.alerting import write_alert
    except ImportError:
        return

    for state, max_age, alert_type in _STUCK_THRESHOLDS:
        cutoff = now - max_age
        stuck_rows = (
            db.query(BugFixCandidate)
            .filter(
                BugFixCandidate.status == state,
                BugFixCandidate.created_at <= cutoff,
            )
            .order_by(BugFixCandidate.created_at)
            .limit(10)
            .all()
        )
        if not stuck_rows:
            continue

        # Aggregate into a single alert per state — we escalate the backlog,
        # not each individual row. Individual IDs go in the detail for audit.
        ids = [int(c.id) for c in stuck_rows]
        source_key = f"bugfix_pipeline:stuck:{state}"
        existing = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == alert_type,
                OpsAlert.source == source_key,
                OpsAlert.resolved == False,
            )
            .first()
        )
        if existing is not None:
            continue  # dedup: already flagged, not yet acknowledged
        try:
            write_alert(
                db,
                severity="warning",
                source=source_key,
                alert_type=alert_type,
                summary=(
                    f"{len(stuck_rows)} bugfix candidate(s) have been in '{state}' "
                    f"for longer than {max_age}. Pipeline may be stalled."
                ),
                detail={
                    "state": state,
                    "threshold_hours": int(max_age.total_seconds() / 3600),
                    "stuck_count": len(stuck_rows),
                    "candidate_ids": ids,
                    "action": f"Investigate why candidates are not leaving '{state}' "
                              "(LLM budget? reviewer blocking? missing phases in agent_worker?).",
                },
            )
            log.warning(
                "bugfix_triage: ESCALATED stuck state=%s count=%d",
                state, len(stuck_rows),
            )
        except Exception as exc:
            log.debug("bugfix_triage: stuck escalation failed (non-fatal): %s", exc)


def _propagate_resolution(db: Session, candidate: BugFixCandidate):
    """
    When a bugfix candidate is applied, mark linked support incidents as
    fix_applied — but do NOT set resolution_summary yet.

    The merchant message is withheld until the fix is verified effective
    (48h outcome measurement) or an operator manually confirms.

    Status flow: investigating → fix_applied → resolved (after verification)
    """
    from app.models.support_incident import SupportIncident
    incidents = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.linked_bugfix_candidate_id == candidate.id,
            SupportIncident.status.in_(["open", "triaged", "investigating"]),
        )
        .all()
    )
    for inc in incidents:
        inc.status = "fix_applied"
        inc.resolved_by = "auto_bugfix"
        inc.resolved_at = _now()
        # resolution_summary is deliberately NOT set here.
        # It will be set when the outcome is measured as effective,
        # or when an operator manually verifies.
        inc.resolution_verified = False
        log.info("bugfix_pipeline: fix applied for incident=%d via candidate=%d (awaiting verification)", inc.id, candidate.id)
    if incidents:
        db.flush()


# ---------------------------------------------------------------------------
# Domain effectiveness context for LLM
# ---------------------------------------------------------------------------

def _get_domain_effectiveness_context(db: Session) -> str | None:
    """
    Build a short context block showing per-domain patch effectiveness
    over the last 90 days. Helps the LLM calibrate its approach.

    Groups by affected_domain (actual domain intelligence, not source_type).
    Falls back to source_type grouping if no domain data available.
    """
    # Try per-domain grouping first (actual domain intelligence)
    domain_rows = db.execute(text("""
        SELECT
            COALESCE(bc.affected_domain, 'unknown') AS domain,
            bc.outcome_status,
            COUNT(*) as cnt
        FROM bugfix_candidates bc
        WHERE bc.outcome_status IS NOT NULL
          AND bc.outcome_measured_at >= NOW() - INTERVAL '90 days'
        GROUP BY COALESCE(bc.affected_domain, 'unknown'), bc.outcome_status
        ORDER BY domain
    """)).fetchall()

    if not domain_rows:
        return None

    # Aggregate by domain
    stats: dict[str, dict[str, int]] = {}
    for domain, outcome, cnt in domain_rows:
        if domain not in stats:
            stats[domain] = {}
        stats[domain][outcome] = cnt

    lines = ["## System Learning Context (90-day effectiveness by domain)"]
    for domain, outcomes in sorted(stats.items()):
        total = sum(outcomes.values())
        effective = outcomes.get("effective", 0)
        pct = round(effective / total * 100) if total > 0 else 0
        lines.append(f"- {domain}: {effective}/{total} effective ({pct}%)")

    # Add overall
    all_effective = sum(o.get("effective", 0) for o in stats.values())
    all_total = sum(sum(o.values()) for o in stats.values())
    if all_total > 0:
        lines.append(f"- OVERALL: {all_effective}/{all_total} effective ({round(all_effective/all_total*100)}%)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff normalization + structural + semantic validation
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict | None:
    """
    Extract a JSON object from LLM output. Handles:
    - Markdown code fences wrapping the JSON
    - Trailing text after the JSON
    - Leading text before the JSON
    - Truncated output (partial JSON)

    Uses brace-matching to find the outermost { ... } object.
    Returns the parsed dict, or None if extraction fails.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Fast path: try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Brace-matching: find the outermost { ... } handling string escaping
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None

    # Unbalanced braces — truncated output
    return None


def _normalize_diff(raw_diff: str) -> str:
    """Normalize LLM-generated diff text into a format git apply will accept."""
    if not raw_diff:
        return raw_diff
    diff = raw_diff.replace("\r\n", "\n").replace("\r", "\n")
    lines = diff.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    diff = "\n".join(lines).strip()

    # Fix content lines in new-file patches
    if "--- /dev/null" in diff:
        fixed_lines = []
        in_hunk = False
        for line in diff.split("\n"):
            if line.startswith("@@"):
                in_hunk = True
                fixed_lines.append(line)
            elif not in_hunk:
                fixed_lines.append(line)
            elif line.startswith(("+", "-", "\\")):
                fixed_lines.append(line)
            elif line == "":
                fixed_lines.append("+")
            elif line.startswith(" "):
                fixed_lines.append("+" + line)
            else:
                fixed_lines.append("+" + line)
        diff = "\n".join(fixed_lines)

    # Fix hunk line counts
    _hunk_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')
    result_lines = diff.split("\n")
    for i, line in enumerate(result_lines):
        m = _hunk_re.match(line)
        if m:
            old_count = 0
            new_count = 0
            for j in range(i + 1, len(result_lines)):
                cl = result_lines[j]
                if cl.startswith("@@") or cl.startswith("--- ") or cl.startswith("+++ "):
                    break
                if cl.startswith("+"):
                    new_count += 1
                elif cl.startswith("-"):
                    old_count += 1
                elif cl.startswith(" "):
                    old_count += 1
                    new_count += 1
            result_lines[i] = f"@@ -{m.group(1)},{old_count} +{m.group(3)},{new_count} @@{m.group(5) or ''}"
    diff = "\n".join(result_lines)

    if diff and not diff.endswith("\n"):
        diff += "\n"
    return diff


def _validate_diff_structure(diff: str) -> tuple[bool, str]:
    """Structural validation of a unified diff."""
    if not diff or not diff.strip():
        return False, "empty_diff"
    lines = diff.strip().split("\n")
    if len(lines) < 4:
        return False, f"too_short: {len(lines)} lines"
    if not any(l.startswith("--- ") for l in lines):
        return False, "missing_minus_header"
    if not any(l.startswith("+++ ") for l in lines):
        return False, "missing_plus_header"
    if not any(l.startswith("@@ ") for l in lines):
        return False, "missing_hunk_marker"
    for i, line in enumerate(lines):
        if not line:
            continue
        if line[0] in ('+', '-', ' ', '@', '\\'):
            continue
        if line.startswith(("diff --git", "index ", "new file mode", "old mode", "new mode")):
            continue
        return False, f"text_contamination: line {i+1}: {line[:60]}"
    _hunk_pat = re.compile(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@')
    for line in lines:
        if line.startswith("@@ ") and not _hunk_pat.match(line):
            return False, f"malformed_hunk: {line[:60]}"
    return True, "valid"


def _validate_patch_semantics(patch_diff: str, patch_files_json: str | None) -> tuple[bool, str]:
    """Semantic validation: check imports resolve, files exist, symbols are real.

    Hardened with 4 hallucination rules from the 2026-04-11 LLM sprint:
      A. phantom_path     — `+++ b/<path>` to a file that doesn't exist
                            and isn't introduced via `--- /dev/null`
      B. duplicate_symbol — added function whose name already exists
                            in the same file (LLM regenerated existing fn)
      C. hallucinated_import — `+from app.foo import bar` where bar doesn't
                                exist in the imported module
      D. untested_significant_change — non-trivial .py change with no
                                       co-committed test file
    """
    if not patch_diff:
        return False, "empty_diff"
    try:
        files = json.loads(patch_files_json) if patch_files_json else []
    except (json.JSONDecodeError, ValueError):
        files = []
    for f in files:
        target_path = os.path.join(_BACKEND_DIR, f)
        is_new_file = "--- /dev/null" in patch_diff and f"+++ b/{f}" in patch_diff
        if not is_new_file and not os.path.isfile(target_path):
            return False, f"file_not_found: {f}"

    # Rule A — phantom_path: every `+++ b/<path>` must resolve OR be a new file
    plus_paths = re.findall(r'^\+\+\+ b/(\S+)', patch_diff, re.MULTILINE)
    for pp in plus_paths:
        if pp == "/dev/null":
            continue
        full = os.path.join(_BACKEND_DIR, pp)
        if os.path.isfile(full):
            continue
        # New file marker must be present for this exact path
        if f"--- /dev/null\n+++ b/{pp}" in patch_diff or f"--- /dev/null\r\n+++ b/{pp}" in patch_diff:
            continue
        return False, f"phantom_path: {pp}"

    added_lines = [l[1:] for l in patch_diff.split("\n") if l.startswith("+") and not l.startswith("+++")]
    joined_source = "\n".join(added_lines)

    # Rule B — duplicate_symbol: added `def name(` that already exists in target file
    added_def_pattern = re.compile(r'^(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', re.MULTILINE)
    for f in files:
        target_path = os.path.join(_BACKEND_DIR, f)
        if not os.path.isfile(target_path):
            continue
        try:
            with open(target_path, "r") as fh:
                existing_source = fh.read()
        except Exception:
            continue
        existing_defs = set(re.findall(r'^(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', existing_source, re.MULTILINE))
        for name in added_def_pattern.findall(joined_source):
            if name.startswith("test_"):
                continue
            if name in existing_defs:
                return False, f"duplicate_symbol: def {name} already exists in {f}"

    # Rule C — hallucinated_import (existing logic, kept) + extended to non-app modules
    import_pattern = re.compile(
        r'from (app\.\S+) import \(([^)]+)\)'
        r'|from (app\.\S+) import ([^\n(]+)',
        re.DOTALL,
    )
    for m in import_pattern.finditer(joined_source):
        module = m.group(1) or m.group(3)
        names_str = m.group(2) or m.group(4)
        if not module or not names_str:
            continue
        module_path = module.replace(".", "/") + ".py"
        full_path = os.path.join(_BACKEND_DIR, module_path)
        if not os.path.isfile(full_path):
            return False, f"hallucinated_import: module {module} does not exist"
        imported_names = [
            n.strip().split(" as ")[0].strip()
            for n in names_str.replace("\n", ",").split(",")
            if n.strip()
        ]
        try:
            with open(full_path, "r") as fh:
                source = fh.read()
            for name in imported_names:
                if not name:
                    continue
                if (f"def {name}" not in source
                        and f"class {name}" not in source
                        and f"{name} =" not in source
                        and f"{name}:" not in source):
                    return False, f"hallucinated_import: {name} not in {module}"
        except Exception as exc:
            log.warning("bugfix_pipeline: import validation failed: %s", exc)

    # Rule D — untested_significant_change: a non-trivial .py change to
    # app/ code without any test file in the same patch.
    nontrivial_app_files = [
        f for f in files
        if f.startswith("app/") and f.endswith(".py")
    ]
    has_test_file = any(f.startswith("tests/") and f.endswith(".py") for f in files)
    added_app_lines = sum(
        1 for l in patch_diff.split("\n")
        if l.startswith("+") and not l.startswith("+++")
        and not l.strip().startswith("#")
        and l.strip()
    )
    if nontrivial_app_files and not has_test_file and added_app_lines > 20:
        return False, (
            f"untested_significant_change: {added_app_lines} added lines across "
            f"{len(nontrivial_app_files)} app file(s) with no test update"
        )

    return True, "valid"


# ---------------------------------------------------------------------------
# Patch proposal: LLM generates a fix suggestion
# ---------------------------------------------------------------------------

_PATCH_SYSTEM_PROMPT = """You are a senior backend engineer fixing a bug in the HedgeSpark SaaS platform.

Given the bug context (alert details, error info, affected subsystem), propose a minimal, safe fix.

RULES:
- Output a JSON object with these fields:
  - patch_summary: one paragraph explaining the fix
  - files: list of file paths relative to the backend directory (e.g. "tests/test_foo.py")
  - diff: the proposed changes as a unified diff
  - test_command: pytest command to verify the fix (e.g. "python -m pytest tests/test_foo.py -v")
- Be conservative — propose the smallest change that fixes the root cause
- Never propose changes to encryption, auth, or billing logic
- If you cannot determine the fix, return {"patch_summary": "Unable to determine fix", "files": [], "diff": "", "test_command": ""}

DIFF FORMAT (critical — malformed diffs will be rejected):
- For new files use `--- /dev/null` and `+++ b/path/to/file.py`
- Every added line MUST start with `+`
- Include proper hunk headers: `@@ -start,count +start,count @@`
- Do NOT wrap the diff in markdown code fences
- The diff MUST end with a newline character

Respond with strict JSON only."""


def propose_patch(db: Session, candidate_id: int) -> bool:
    """
    Call LLM to propose a patch for a BugFixCandidate.
    Stores result on the candidate row. Does NOT apply anything.
    Returns True if proposal was generated.
    """
    candidate = db.get(BugFixCandidate, candidate_id)
    if not candidate or candidate.status not in ("open", "analyzed"):
        return False

    candidate.status = "analyzed"

    # Classify domain early (for fingerprinting and context)
    _classify_candidate_domain(candidate)

    # Pre-flight: reject ungroundable candidates BEFORE the LLM call.
    # The 2026-04-11 audit found that the dominant LLM-budget waste was
    # candidates pointing at non-existent files. Catching them here saves
    # the budget AND prevents PatchFingerprint pollution.
    try:
        from app.services.bugfix_prompt_grounding import preflight_ground_candidate
        ok, reason = preflight_ground_candidate(candidate, db=db)
        if not ok:
            log.info(
                "propose_patch: PREFLIGHT REJECT id=%d reason=%s",
                candidate.id, reason,
            )
            candidate.failure_reason = f"prompt_ungrounded_preflight: {reason}"
            db.flush()
            return False
    except Exception as exc:
        log.warning("propose_patch: preflight skipped (non-fatal): %s", exc)

    # Recompute priority now that we know the affected_domain criticality.
    # At _create_candidate time we only had severity + source_type + recurrence;
    # now we can add the domain dimension AND the adaptive track record
    # (per-domain historical success rate from DomainProfile). The latter
    # is the competitive moat: priorities improve as your merchant base
    # generates more outcome telemetry.
    try:
        recompute_priority_after_classification(candidate, db=db)
    except Exception as exc:
        log.debug("propose_patch: priority recompute failed (non-fatal): %s", exc)

    # Fingerprint pre-check: reject if identical patch recently failed
    pre_fp = _compute_patch_fingerprint(candidate.title, candidate.patch_files)
    failed_match = _check_patch_fingerprint(db, pre_fp)
    if failed_match:
        log.info(
            "propose_patch: FINGERPRINT REJECT id=%d — matches failed candidate #%d (%s)",
            candidate.id, failed_match["candidate_id"], failed_match["outcome"],
        )
        candidate.failure_reason = (
            f"fingerprint_dedup: identical approach failed in candidate #{failed_match['candidate_id']} "
            f"({failed_match['outcome']})"
        )
        db.flush()
        return False

    # Build context
    context_parts = [f"## Bug: {candidate.title}", f"Summary: {candidate.summary}"]

    # Inject source file API for evolution proposals
    if candidate.context_json:
        try:
            _ctx = json.loads(candidate.context_json)
            target = _ctx.get("target_file")
            if target:
                _target_path = os.path.join(_BACKEND_DIR, target)
                if os.path.isfile(_target_path):
                    with open(_target_path, "r") as _f:
                        _lines = _f.readlines()
                    _api_lines: list[str] = []
                    for i, line in enumerate(_lines):
                        stripped = line.rstrip()
                        if stripped.startswith(("import ", "from ")):
                            _api_lines.append(stripped)
                        elif stripped.startswith(("def ", "async def ", "class ")):
                            _api_lines.append(stripped)
                            if i + 1 < len(_lines) and _lines[i + 1].strip().startswith(('"""', "'''")):
                                _api_lines.append("    " + _lines[i + 1].strip())
                    _module_path = target.replace("/", ".").replace(".py", "")
                    context_parts.append(
                        f"## Source File API: {target}\n"
                        f"REAL function/class signatures. Import from `{_module_path}`. "
                        f"Do NOT invent classes or functions.\n"
                        f"```python\n" + "\n".join(_api_lines) + "\n```\n"
                        f"Write 3-5 SHORT test functions (not a class). "
                        f"Use unittest.mock.Mock() for db Session parameters."
                    )
        except Exception as exc:
            log.warning("bugfix_pipeline: test co-commit hint generation failed: %s", exc)

    # Recurrence-aware context: if this is a follow-up from an ineffective fix,
    # tell the LLM explicitly so it tries a different approach.
    if candidate.source_type == "recurrence" and candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            context_parts.append(
                "## IMPORTANT — Previous Fix Was Ineffective\n"
                "A prior attempt to fix this bug did NOT resolve it. "
                "You MUST try a fundamentally different approach.\n\n"
                f"Previous patch summary: {ctx.get('previous_patch_summary', 'unknown')}\n"
                f"Previous files changed: {ctx.get('previous_files', 'unknown')}\n"
                f"Alerts before previous fix: {ctx.get('alerts_before', '?')}\n"
                f"Alerts after previous fix: {ctx.get('alerts_after', '?')} (should have decreased)\n"
                f"Original bug: {ctx.get('original_title', 'unknown')}\n"
                f"Original source: {ctx.get('original_source_type', '?')}/{ctx.get('original_source_ref', '?')}\n\n"
                "Do NOT repeat the same fix. Investigate the root cause more deeply."
            )
            # Still include remaining context
            remaining = {k: v for k, v in ctx.items()
                         if k not in ("previous_patch_summary", "previous_files",
                                      "alerts_before", "alerts_after",
                                      "original_title", "original_source_type",
                                      "original_source_ref", "previous_candidate_id",
                                      "previous_outcome")}
            if remaining:
                context_parts.append(f"Additional context: {json.dumps(remaining, indent=2)}")
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")

    elif candidate.source_type == "sentry_incident" and candidate.context_json:
        # Sentry triage packet — structured production error with parsed evidence
        try:
            pkt = json.loads(candidate.context_json)
            sentry_parts: list[str] = ["## Sentry Production Error"]

            # Error identity
            if pkt.get("error_type"):
                sentry_parts.append(f"Error type: {pkt['error_type']}")
            if pkt.get("error_title"):
                sentry_parts.append(f"Error: {pkt['error_title']}")

            # Location
            if pkt.get("culprit"):
                sentry_parts.append(f"File: {pkt['culprit']}")
            if pkt.get("subsystem") and pkt["subsystem"] != "unknown":
                sentry_parts.append(f"Subsystem: {pkt['subsystem']} (criticality: {pkt.get('criticality', '?')})")

            # Environment
            if pkt.get("environment"):
                sentry_parts.append(f"Environment: {pkt['environment']}")

            # Recurrence
            recurrence = pkt.get("recurrence_count", 1)
            if recurrence > 1:
                sentry_parts.append(f"Recurrences: {recurrence} (this error keeps happening)")
                if pkt.get("first_seen"):
                    sentry_parts.append(f"First seen: {pkt['first_seen']}")
                if pkt.get("last_seen"):
                    sentry_parts.append(f"Last seen: {pkt['last_seen']}")

            # Stack trace — the most critical evidence
            if pkt.get("stack_trace"):
                trace = pkt["stack_trace"]
                # Cap at 2000 chars to leave room for other context
                if len(trace) > 2000:
                    trace = trace[-2000:]
                sentry_parts.append(f"\n## Stack Trace\n```\n{trace}\n```")

            # Root-cause hints from parser
            hints = pkt.get("probable_root_cause_hints", [])
            if hints:
                sentry_parts.append("\n## Root-Cause Hints")
                for h in hints:
                    sentry_parts.append(f"- {h}")

            # Related lessons from system memory
            lessons = pkt.get("related_lessons", [])
            if lessons:
                sentry_parts.append("\n## Related Lessons from System Memory")
                for lesson in lessons[:3]:
                    sentry_parts.append(
                        f"- [{lesson.get('type', '?')}] {lesson.get('summary', '?')} "
                        f"(confidence: {lesson.get('confidence', '?')})"
                    )

            # Related past fix attempts
            past_candidates = pkt.get("related_bugfix_candidates", [])
            if past_candidates:
                sentry_parts.append("\n## Previous Fix Attempts")
                for pc in past_candidates[:3]:
                    sentry_parts.append(
                        f"- #{pc.get('id', '?')}: {pc.get('title', '?')} "
                        f"(status: {pc.get('status', '?')}, outcome: {pc.get('outcome', 'unknown')})"
                    )

            # Sentry link for reference
            if pkt.get("sentry_issue_url"):
                sentry_parts.append(f"\nSentry issue: {pkt['sentry_issue_url']}")

            context_parts.append("\n".join(sentry_parts))
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")

    elif candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            context_parts.append(f"Context: {json.dumps(ctx, indent=2)}")
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")

    # Inject domain effectiveness context — helps LLM understand what works
    try:
        domain_context = _get_domain_effectiveness_context(db)
        if domain_context:
            context_parts.append(domain_context)
    except Exception as exc:
        log.warning("bugfix_pipeline: domain effectiveness context failed: %s", exc)

    # Inject relevant lessons from persistent memory + track which were used
    _lesson_ids_used = []
    try:
        domain = candidate.affected_domain or "unknown"
        lesson_context, _lesson_ids_used = _lookup_lessons_for_proposal(db, domain)
        if lesson_context:
            context_parts.append(lesson_context)
        if _lesson_ids_used:
            candidate.lesson_ids_used = json.dumps(_lesson_ids_used)
    except Exception as exc:
        log.warning("bugfix_pipeline: lesson lookup for proposal failed: %s", exc)

    # Inject grounded file manifest + signatures (L1/L5 from the LLM
    # prompt-engineering sprint). The LLM cannot ground its `files` list
    # to existing paths unless we tell it what exists. We scope the
    # manifest to the candidate's affected_domain and always include the
    # declared target_file so the LLM sees the exact ground truth.
    try:
        from app.services.bugfix_prompt_grounding import (
            build_file_manifest, extract_signatures,
        )
        extra: list[str] = []
        target_file_for_sigs: str | None = None
        if candidate.context_json:
            try:
                _gctx = json.loads(candidate.context_json)
                tf = _gctx.get("target_file")
                if tf:
                    extra.append(tf)
                    target_file_for_sigs = tf
            except Exception as exc:
                log.warning("bugfix_pipeline: grounding context_json parse failed: %s", exc)
        if candidate.patch_files:
            try:
                for pf in json.loads(candidate.patch_files) or []:
                    if pf and pf not in extra:
                        extra.append(pf)
            except Exception as exc:
                log.warning("bugfix_pipeline: grounding patch_files parse failed: %s", exc)

        manifest = build_file_manifest(candidate.affected_domain, extra_files=extra)
        if manifest:
            context_parts.append(manifest)

        sigs_files = [f for f in extra if f and f.endswith(".py")]
        if not sigs_files and target_file_for_sigs:
            sigs_files = [target_file_for_sigs]
        for sf in sigs_files[:3]:  # cap at 3 files of signatures to stay under prompt budget
            sig_block = extract_signatures(sf)
            if sig_block:
                context_parts.append(sig_block)

        # C1 — inject the last 3 failure traces from the same family
        # so the LLM sees institutional memory, not just the current
        # candidate. Turns the proposer from amnesiac to grounded.
        try:
            from app.services.bugfix_prompt_grounding import extract_recent_failures
            failures_block = extract_recent_failures(
                db,
                affected_domain=candidate.affected_domain,
                source_type=candidate.source_type,
            )
            if failures_block:
                context_parts.append(failures_block)
        except Exception as exc:
            log.debug("propose_patch: failure history injection failed (non-fatal): %s", exc)
    except Exception as exc:
        log.debug("propose_patch: grounding injection failed (non-fatal): %s", exc)

    # Inject HARD failure-mode constraints from patch_fingerprint history.
    # This is the competitive moat injection: the accumulated failure
    # catalog from YOUR merchant base becomes the LLM's DO-NOT list.
    # A fresh deployment has no constraints; a production deployment
    # teaches the LLM what has already been tried and failed.
    try:
        hard_constraints = build_hard_lesson_constraints(
            db,
            affected_domain=candidate.affected_domain,
            source_type=candidate.source_type,
        )
        if hard_constraints:
            context_parts.append(hard_constraints)
    except Exception as exc:
        log.debug("propose_patch: hard lessons injection failed (non-fatal): %s", exc)

    user_message = "\n\n".join(context_parts)

    # Call LLM with routing context
    file_count = 1
    if candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            file_count = len(ctx.get("files", [])) or 1
        except Exception as exc:
            log.warning("bugfix_pipeline: file_count extraction failed: %s", exc)
    # D3 — template cache short-circuit. If a fresh successful template
    # exists for this (domain, source_type, files) family, reuse its diff
    # without spending LLM budget. The cached payload still flows through
    # the existing parser + validator + fingerprint gates below, so any
    # repo drift is caught the same way as a live LLM response.
    template_key = _compute_fix_template_key(candidate)
    cached_template = _lookup_fix_template(template_key)
    raw = ""
    if cached_template:
        try:
            cached_files = cached_template.get("files") or []
            cached_diff = cached_template.get("diff") or ""
            files_ok = bool(cached_diff.strip()) and all(
                (
                    isinstance(f, str)
                    and (
                        os.path.isfile(os.path.join(_BACKEND_DIR, f))
                        or (f.startswith("tests/") and f.endswith(".py"))
                    )
                )
                for f in cached_files
                if f
            )
            if files_ok:
                raw = json.dumps({
                    "patch_summary": cached_template.get("patch_summary", ""),
                    "diff": cached_diff,
                    "files": cached_files,
                    "test_command": cached_template.get("test_command", ""),
                })
                _incr_fix_template_hit()
                candidate.context_json = _mark_template_reuse(
                    candidate.context_json,
                    source_candidate_id=cached_template.get("source_candidate_id"),
                )
                log.info(
                    "propose_patch: TEMPLATE CACHE HIT id=%d key=%s (from candidate #%s)",
                    candidate.id,
                    (template_key or "")[:12],
                    cached_template.get("source_candidate_id"),
                )
        except Exception as exc:
            log.debug("propose_patch: template reuse skipped: %s", exc)
            raw = ""

    if not raw:
        raw = _call_llm(user_message, patch_risk_tier=None, file_count=file_count)
    if not raw:
        candidate.failure_reason = "llm_call_failed"
        db.flush()
        return False

    # Parse response — robust JSON extraction
    data = _extract_json(raw)
    if data is None:
        candidate.failure_reason = f"json_parse_error: could not extract valid JSON ({len(raw)} chars)"
        db.flush()
        return False

    candidate.patch_summary = data.get("patch_summary", "")
    raw_diff = data.get("diff", "")
    candidate.patch_files = json.dumps(data.get("files", []))
    # Normalize test_command: always use venv python, never bare "python"
    _raw_test_cmd = data.get("test_command", "")
    _venv_py = f"{_BACKEND_DIR}/venv/bin/python"
    if _raw_test_cmd.startswith("python3 "):
        candidate.test_command = f"{_venv_py} {_raw_test_cmd[8:]}"
    elif _raw_test_cmd.startswith("python "):
        candidate.test_command = f"{_venv_py} {_raw_test_cmd[7:]}"
    elif _raw_test_cmd.startswith("pytest "):
        candidate.test_command = f"{_venv_py} -m pytest {_raw_test_cmd[7:]}"
    else:
        candidate.test_command = _raw_test_cmd

    # Reject empty/whitespace-only diffs
    if not raw_diff or not raw_diff.strip():
        candidate.failure_reason = "llm_returned_empty_diff"
        db.flush()
        return False

    # Normalize + validate diff
    candidate.patch_diff = _normalize_diff(raw_diff)
    valid, reason = _validate_diff_structure(candidate.patch_diff)
    if not valid:
        candidate.failure_reason = f"diff_validation_failed: {reason}"
        db.flush()
        return False
    sem_valid, sem_reason = _validate_patch_semantics(candidate.patch_diff, candidate.patch_files)
    if not sem_valid:
        candidate.failure_reason = f"semantic_validation_failed: {sem_reason}"
        db.flush()
        return False

    # Security-aware preflight guard (2026-04-11). Post-LLM re-scan — we
    # now have the final diff and can detect any regression the LLM
    # tried to introduce (PII logging, HMAC weakening, consent bypass,
    # SQL injection, secret hardcoding, rate-limit removal). Hard reject.
    try:
        from app.services.security_preflight_guard import guard_candidate
        allowed_sec, sec_reason = guard_candidate(candidate)
        if not allowed_sec:
            log.warning(
                "propose_patch: SECURITY REJECT id=%d — %s",
                candidate.id, sec_reason,
            )
            candidate.failure_reason = sec_reason
            _bump_security_guard_block_counter()
            db.flush()
            return False
    except Exception as exc:
        log.warning("propose_patch: security guard non-fatal: %s", exc)

    # POST-LLM diff fingerprint check: now that we have the actual diff,
    # check identity, normalized diff, AND AST skeleton fingerprint.
    # The skeleton catches "same strategy with renamed variables" which
    # the other two dimensions miss.
    post_diff_fp = _compute_diff_fingerprint(candidate.patch_diff)
    post_skeleton_fp = _compute_ast_skeleton_fingerprint(candidate.patch_diff)
    if post_diff_fp or post_skeleton_fp:
        post_fp_hash = _compute_patch_fingerprint(candidate.title, candidate.patch_files, candidate.patch_diff)
        diff_match = _check_patch_fingerprint(
            db, post_fp_hash,
            diff_fp=post_diff_fp,
            skeleton_fp=post_skeleton_fp,
            source_type=candidate.source_type,
            source_ref=candidate.source_ref,
        )
        if diff_match:
            log.info(
                "propose_patch: DIFF FINGERPRINT REJECT id=%d — semantically matches "
                "failed candidate #%d (%s, match_type=%s)",
                candidate.id, diff_match["candidate_id"], diff_match["outcome"],
                diff_match.get("match_type", "unknown"),
            )
            candidate.failure_reason = (
                f"diff_fingerprint_dedup: LLM proposed semantically identical patch to "
                f"failed candidate #{diff_match['candidate_id']} ({diff_match['outcome']})"
            )
            db.flush()
            return False

    candidate.status = "patch_proposed"

    # Classify risk tier
    tier, tier_reasons = classify_patch_risk(candidate.patch_files, candidate.patch_diff)
    candidate.patch_risk_tier = tier

    # Classify remediation type from patch metadata
    try:
        from app.services.scoring_calibration import classify_remediation
        candidate.remediation_class = classify_remediation(
            candidate.patch_files, candidate.patch_summary, candidate.patch_diff,
        )
    except Exception:
        candidate.remediation_class = "unknown"

    # Compute fix confidence — gates auto-apply at TIER_0 (with adaptive calibration)
    try:
        from app.services.candidate_scoring import compute_fix_confidence
        calibration = None
        try:
            from app.services.scoring_calibration import get_scoring_calibration
            calibration = get_scoring_calibration(db)
        except Exception as exc:
            log.warning("bugfix_pipeline: scoring calibration lookup failed: %s", exc)
        conf_score, conf_detail = compute_fix_confidence(
            db, candidate, calibration=calibration,
        )
        candidate.fix_confidence = conf_score
        candidate.confidence_detail = json.dumps(conf_detail, default=str)
        log.info(
            "bugfix_pipeline: confidence id=%d score=%d remediation=%s",
            candidate.id, conf_score, candidate.remediation_class,
        )
    except Exception as exc:
        log.warning("bugfix_pipeline: confidence scoring failed id=%d: %s", candidate.id, exc)

    db.flush()
    log.info("bugfix_pipeline: classified id=%d tier=%d reasons=%s", candidate.id, tier, tier_reasons)

    # Notify via Slack if configured
    try:
        from app.core.alert_delivery import _SLACK_URL
        if _SLACK_URL:
            import httpx
            httpx.post(_SLACK_URL, json={
                "text": (
                    f":wrench: *PATCH PROPOSED* — `{candidate.title}`\n"
                    f"*Files:* {', '.join(data.get('files', []))}\n"
                    f"*Summary:* {(candidate.patch_summary or '')[:200]}\n"
                    f"*ID:* {candidate.id}\n"
                    f"_Review via: GET /ops/bugfixes/{candidate.id}_"
                ),
            }, timeout=5.0)
            candidate.notified_at = _now()
    except Exception as exc:
        log.warning("bugfix_pipeline: slack proposal notification failed: %s", exc)

    # Telegram: send reviewer pre-assessment for non-TIER_0 patches
    if tier != PATCH_TIER_0:
        try:
            from app.services.reviewer_layer import review_entity
            from app.services.telegram_agent import send_reviewer_verdict, is_configured
            assessment = review_entity(db, "bugfix_candidate", candidate.id)
            if assessment:
                candidate.reviewer_assessment_id = assessment.id
                db.flush()
                if is_configured():
                    send_reviewer_verdict(assessment, entity_title=candidate.title)
        except Exception as exc:
            log.warning("bugfix_pipeline: reviewer assessment after propose failed: %s", exc)

    log.info("bugfix_pipeline: patch proposed id=%d title=%s", candidate.id, candidate.title)
    return True


def _call_llm(
    user_message: str,
    patch_risk_tier: int | None = None,
    file_count: int = 1,
    previous_failed: bool = False,
) -> str:
    """
    Call LLM for patch proposal. Budget-guarded + model-routed.
    Returns raw response text or empty string.
    If Sonnet fails, retries once with Opus (escalation).
    """
    from app.core.llm_budget import check_budget, record_usage, record_blocked

    # Runtime PII guard (2026-04-11 audit). The DPIA promises aggregated
    # metrics only to third-party LLM providers. Previously this relied
    # on code review. Now every outgoing prompt is scanned for email,
    # IBAN, phone, credit-card, JWT, Shopify token, and provider API
    # key patterns. Any match hard-blocks the call before the HTTP
    # request is made, increments the weekly violation counter, and
    # returns empty (same path as a budget exhaustion).
    try:
        from app.core.llm_pii_guard import assert_clean, LLMPayloadViolation
        assert_clean(user_message, context="bugfix_proposal")
    except LLMPayloadViolation as exc:
        log.error("bugfix_pipeline: %s", exc)
        return ""
    except Exception as exc:
        log.debug("bugfix_pipeline: llm_pii_guard non-fatal: %s", exc)

    allowed, reason = check_budget("bugfix_proposal")
    if not allowed:
        record_blocked("bugfix_proposal", reason)
        log.info("bugfix_pipeline: LLM call blocked by budget: %s", reason)
        return ""

    from app.core.llm_router import select_model
    import httpx

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    sel = select_model(
        module="bugfix_proposal",
        patch_risk_tier=patch_risk_tier,
        file_count=file_count,
        previous_failed=previous_failed,
        anthropic_available=bool(anthropic_key),
        openai_available=bool(openai_key),
    )

    text, actual_provider, actual_model = _call_provider(sel, user_message, anthropic_key, openai_key)

    if text:
        record_usage("bugfix_proposal", tokens_used=len(text) // 4, provider=actual_provider, model=actual_model)
        return text

    # Escalation: if Sonnet failed and not already escalated, try Opus once
    if not previous_failed and not sel.escalation:
        log.info("bugfix_pipeline: Sonnet failed, escalating to Opus")
        return _call_llm(user_message, patch_risk_tier=patch_risk_tier, file_count=file_count, previous_failed=True)

    return ""


def _call_provider(sel, user_message: str, anthropic_key: str, openai_key: str) -> tuple[str, str, str]:
    """
    Make the actual API call based on model selection. Handles 429 with backoff.

    Returns (raw_text, actual_provider, actual_model) tuple.
    Empty string on failure.
    Rejects truncated output (max_tokens reached) before returning —
    truncated JSON is unparseable and should not propagate.
    """
    import httpx
    from app.core.llm_budget import is_provider_backed_off, record_429

    anthropic_failed = False
    if sel.provider == "anthropic" and anthropic_key:
        if is_provider_backed_off("anthropic"):
            log.info("bugfix_pipeline: Anthropic backed off (429 cooldown)")
            anthropic_failed = True
        else:
            try:
                resp = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={
                        "model": sel.model,
                        "max_tokens": sel.max_tokens,
                        "temperature": 0.1,
                        "system": _PATCH_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_message}],
                    },
                    timeout=60.0,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    # Reject truncated output — truncated JSON is unparseable
                    stop = body.get("stop_reason", "")
                    if stop == "max_tokens":
                        log.warning("bugfix_pipeline: Anthropic output TRUNCATED (max_tokens=%d)", sel.max_tokens)
                        anthropic_failed = True
                    else:
                        return body.get("content", [{}])[0].get("text", ""), "anthropic", sel.model
                elif resp.status_code == 429:
                    record_429("anthropic")
                    anthropic_failed = True
                else:
                    log.warning("bugfix_pipeline: Anthropic %s returned %d", sel.model, resp.status_code)
                    anthropic_failed = True
            except Exception as exc:
                log.warning("bugfix_pipeline: Anthropic %s failed: %s", sel.model, type(exc).__name__)
                anthropic_failed = True

    if openai_key:
        if is_provider_backed_off("openai"):
            log.info("bugfix_pipeline: OpenAI backed off (429 cooldown)")
            return "", "openai", sel.model
        model = sel.model if sel.provider == "openai" else "gpt-4o-mini"
        if anthropic_failed:
            log.info("bugfix_pipeline: anthropic unavailable → fallback=openai model=%s", model)
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "max_tokens": sel.max_tokens,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _PATCH_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                },
                timeout=60.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                choice = body.get("choices", [{}])[0]
                # Reject truncated output
                finish = choice.get("finish_reason", "")
                if finish == "length":
                    log.warning("bugfix_pipeline: OpenAI output TRUNCATED (max_tokens=%d)", sel.max_tokens)
                    return "", "openai", model
                return choice.get("message", {}).get("content", ""), "openai", model
            if resp.status_code == 429:
                record_429("openai")
            else:
                log.warning("bugfix_pipeline: OpenAI %s returned %d", model, resp.status_code)
        except Exception as exc:
            log.warning("bugfix_pipeline: OpenAI %s failed: %s", model, type(exc).__name__)

    return "", sel.provider, sel.model


# ---------------------------------------------------------------------------
# Patch risk tiering — deterministic classifier
# ---------------------------------------------------------------------------

PATCH_TIER_0 = 0  # Ultra-safe: auto-apply
PATCH_TIER_1 = 1  # Human-approve required (default)
PATCH_TIER_2 = 2  # Never auto-apply (forbidden paths)

_MAX_SAFE_DIFF_LINES = 120

# Paths that are explicitly safe for TIER_0 auto-apply
_SAFE_PATH_PREFIXES = [
    "app/services/signal_text",
    "app/services/digest_formatter",
    "app/services/nudge_rank",
    "app/services/revenue_metrics",
    "app/services/utm_attribution",
    "app/services/conversion_metrics",
    "tests/",
]

# ----- Self-modification guard (elite sprint 2026-04-11) -----
#
# The self-healing pipeline must NEVER auto-patch its own guts. If the
# LLM proposes a change to a file that IS the self-healing pipeline
# itself, the candidate is force-downgraded to TIER_1 (proposal only,
# human review required). This prevents a catastrophic feedback loop
# where a buggy auto-fix breaks the very code that would detect and
# revert it.
#
# Examples of paths that flip to TIER_1:
#   app/services/bugfix_pipeline.py  (this file!)
#   app/services/loop_health.py
#   app/services/orchestrator.py
#   app/services/reviewer_layer.py
#   app/services/project_brain.py
#   app/services/evolution_outcomes.py
#   app/services/merge_intelligence.py
#   app/services/promotion_pipeline.py
#   app/services/adaptive_governance.py
#   app/services/data_integrity_probe.py
#   app/services/alerting.py
#   app/workers/agent_worker.py
#   app/workers/aggregation_worker.py
#   app/core/protection_state.py
#   app/core/version.py
#   scripts/deploy_gate.py
_SELF_MODIFICATION_PREFIXES: tuple[str, ...] = (
    "app/services/bugfix_pipeline",
    "app/services/loop_health",
    "app/services/orchestrator",
    "app/services/reviewer_layer",
    "app/services/project_brain",
    "app/services/evolution_outcomes",
    "app/services/evolution_converter",
    "app/services/evolution_bet_governance",
    "app/services/evolution_engine",
    "app/services/evolution_reinforcement",
    "app/services/merge_intelligence",
    "app/services/promotion_pipeline",
    "app/services/adaptive_governance",
    "app/services/data_integrity_probe",
    "app/services/alerting",
    "app/services/outcome_evaluator",
    "app/services/action_learning",
    "app/services/meta_reviewer",
    "app/services/monthly_evolution_audit",
    "app/services/bugfix_prompt_grounding",  # deferred LLM sprint target
    "app/workers/agent_worker",
    "app/workers/aggregation_worker",
    "app/core/protection_state",
    "app/core/version",
    "app/core/llm_budget",
    "app/core/llm_router",
    "app/core/tier_check",
    "scripts/deploy_gate",
)


# ---------------------------------------------------------------------------
# Predictive outcome gate — refuse to burn apply budget on low-odds fixes
# ---------------------------------------------------------------------------
#
# Before run_auto_apply commits an apply, we estimate the probability that
# this candidate would be measured 'effective' using historical outcomes
# for the SAME (affected_domain, source_type) pair in the last 90 days.
#
# Why this is a competitive moat:
#   A fresh deployment has zero historical data, so the gate starts
#   neutral and lets everything through (P = 0.5). As your merchant
#   base accrues telemetry over months, the gate becomes tighter
#   and tighter — specifically for YOUR failure modes. A copycat
#   cannot replicate this without spending equivalent time in
#   production with equivalent merchant volume. The architecture
#   is the template; the telemetry is the moat.
#
# Default threshold: 0.25 — if prior history shows <25% of similar
# candidates ended effective, downgrade to manual review. This is the
# floor; the actual threshold adapts from adaptive_governance at
# runtime.

_PREDICT_OUTCOME_MIN_SAMPLES = 5   # need at least 5 prior outcomes
_PREDICT_OUTCOME_DEFAULT_FLOOR = 0.25  # skip apply if predicted p < 25%

# C2 — Bayesian gate. Instead of a flat point estimate `good / total`,
# we compute the LOWER 5% bound of a Beta(good+1, fails+1) posterior
# (the conservative estimate). This is the right tool because:
#
#   * With n=5 wins, n=0 losses, naive ratio = 1.0 → wildly optimistic.
#     Beta(6,1) lower 5% bound ≈ 0.61 → realistic.
#   * With n=50 wins, n=2 losses, naive ratio ≈ 0.96. Beta(51,3) lower
#     5% bound ≈ 0.89 → still strict but tighter.
#   * As n grows, the posterior collapses around the true rate.
#     Auto-applies become more aggressive ONLY where the moat data
#     proves them safe. No competitor can replicate this without
#     accumulating the same telemetry corpus.
#
# We use a Wilson-like closed-form approximation (no scipy dependency,
# zero new packages, deterministic, ~10 lines of math). The bound is
# safe under all degenerate inputs.


def _beta_lower_bound_5pct(wins: int, losses: int) -> float:
    """Conservative lower bound on the Beta(wins+1, losses+1) posterior.

    Uses the Wilson score interval at z=1.645 (5% one-sided), which
    matches Beta(α,β) lower 5% within ~0.01 across all sample sizes
    we care about. Pure Python, no scipy, deterministic.

    Edge cases:
      * wins=0 losses=0 → returns 0.0 (no evidence, conservative)
      * wins=0 losses>0 → bound very low (we punish unmeasured failures)
      * wins>0 losses=0 → bound rises with sample size
    """
    n = wins + losses
    if n == 0:
        return 0.0
    z = 1.645  # one-sided 95% confidence
    p_hat = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = p_hat + z2 / (2 * n)
    margin = z * ((p_hat * (1 - p_hat) / n + z2 / (4 * n * n)) ** 0.5)
    lower = (center - margin) / denom
    return max(0.0, min(1.0, lower))


def predict_outcome_probability(
    db: Session,
    *,
    affected_domain: str | None,
    source_type: str | None,
    lookback_days: int = 90,
) -> tuple[float, int]:
    """
    Return (predicted_effective_probability, sample_size) for the given
    (domain, source_type).

    The probability is the **conservative lower bound** of a Beta posterior
    (5% one-sided) computed from historical outcomes. As sample size grows
    the bound tightens around the true rate; with no history it returns
    the neutral prior 0.5 so first-of-its-kind candidates are not blocked.

    This is read-only; no side effects.
    """
    if not affected_domain or not source_type:
        return 0.5, 0

    try:
        cutoff = _now() - timedelta(days=lookback_days)
        row = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE outcome_status = 'effective') AS good,
                COUNT(*) FILTER (WHERE outcome_status IN ('effective','ineffective')) AS measured,
                COUNT(*) FILTER (WHERE status IN ('apply_failed','rolled_back')) AS hard_fails,
                COUNT(*) AS total
            FROM bugfix_candidates
            WHERE affected_domain = :dom
              AND source_type = :src
              AND created_at >= :cutoff
              AND status IN ('applied','apply_failed','rolled_back')
        """), {
            "dom": affected_domain,
            "src": source_type,
            "cutoff": cutoff,
        }).fetchone()
    except Exception as exc:
        log.warning("predict_outcome: query failed: %s", exc)
        return 0.5, 0

    if not row:
        return 0.5, 0

    good = int(row[0] or 0)
    measured = int(row[1] or 0)
    hard_fails = int(row[2] or 0)
    total = int(row[3] or 0)

    if total < _PREDICT_OUTCOME_MIN_SAMPLES:
        # Insufficient history — let it through with neutral prior
        return 0.5, total

    # Wins: measured "effective" outcomes.
    # Losses: measured "ineffective" + hard fails (apply_failed, rolled_back).
    wins = good
    losses = max(0, measured - good) + hard_fails

    if wins + losses == 0:
        return 0.5, total

    bayesian_lower = _beta_lower_bound_5pct(wins, losses)
    return bayesian_lower, total


def should_skip_apply_by_prediction(
    db: Session, candidate: BugFixCandidate,
) -> tuple[bool, str]:
    """
    Return (skip, reason). If skip is True, run_auto_apply must NOT fire
    this candidate — downgrade to manual review instead.
    """
    p, n = predict_outcome_probability(
        db,
        affected_domain=candidate.affected_domain,
        source_type=candidate.source_type,
    )
    if n < _PREDICT_OUTCOME_MIN_SAMPLES:
        return False, f"insufficient_history_n={n}"
    if p < _PREDICT_OUTCOME_DEFAULT_FLOOR:
        return True, f"predicted_effective_pct={p:.0%}_from_{n}_samples"
    return False, f"predicted_effective_pct={p:.0%}_from_{n}_samples"


def touches_self_healing_pipeline(patch_files_json: str | None) -> tuple[bool, list[str]]:
    """
    Return (True, matches) if the patch touches any file in the self-healing
    pipeline itself. The pipeline must not auto-patch its own guts — that's
    a catastrophic feedback loop risk. Force such candidates to TIER_1 so
    a human reviews the change.

    Pure function, no DB. Used by classify_patch_risk and as an explicit
    guard in run_auto_apply.
    """
    if not patch_files_json:
        return False, []
    try:
        files = json.loads(patch_files_json)
    except (ValueError, TypeError):
        return False, []
    matches: list[str] = []
    for f in files:
        if not isinstance(f, str):
            continue
        for prefix in _SELF_MODIFICATION_PREFIXES:
            if f.startswith(prefix):
                matches.append(f)
                break
    return bool(matches), matches

# Diff patterns that indicate dangerous content (force TIER_2)
_DANGEROUS_DIFF_PATTERNS = [
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
    "__import__",
    "MERCHANT_TOKEN_ENCRYPTION_KEY",
    "SHOPIFY_API_SECRET",
    "DASHBOARD_API_KEY",
]


def classify_patch_risk(patch_files_json: str | None, patch_diff: str | None) -> tuple[int, list[str]]:
    """
    Classify patch risk tier. Returns (tier, reasons).

    TIER_0 only if ALL:
      - all files in safe path prefixes
      - no forbidden paths
      - diff <= 120 lines
      - no dangerous patterns in diff
    TIER_2 if any forbidden path or dangerous pattern found.
    Else TIER_1.
    """
    reasons: list[str] = []

    if not patch_files_json or not patch_diff:
        return PATCH_TIER_1, ["no_patch_data"]

    try:
        files = json.loads(patch_files_json)
    except (json.JSONDecodeError, ValueError):
        return PATCH_TIER_1, ["invalid_files_json"]

    if not files:
        return PATCH_TIER_1, ["empty_file_list"]

    # Check forbidden paths → TIER_2
    for f in files:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern in str(f):
                return PATCH_TIER_2, [f"forbidden: {f}"]

    # Check self-modification — if the patch touches the self-healing
    # pipeline itself, force TIER_1 (proposal, human review). This is
    # not TIER_2 (outright banned) because legitimate pipeline updates
    # should still flow through the normal review path — we just want
    # a human in the loop for any change to the guts of the system
    # that is making changes.
    self_mod, self_files = touches_self_healing_pipeline(patch_files_json)
    if self_mod:
        return PATCH_TIER_1, [f"self_modification: {','.join(self_files[:3])}"]

    # Check dangerous diff patterns → TIER_2
    diff_lower = (patch_diff or "").lower()
    for pattern in _DANGEROUS_DIFF_PATTERNS:
        if pattern.lower() in diff_lower:
            return PATCH_TIER_2, [f"dangerous_pattern: {pattern}"]

    # Check diff size
    diff_lines = len([l for l in (patch_diff or "").split("\n") if l.startswith("+") or l.startswith("-")])
    if diff_lines > _MAX_SAFE_DIFF_LINES:
        reasons.append(f"large_diff: {diff_lines} lines")
        return PATCH_TIER_1, reasons

    # Check all files are in safe prefixes → TIER_0
    all_safe = True
    for f in files:
        if not any(str(f).startswith(prefix) or prefix in str(f) for prefix in _SAFE_PATH_PREFIXES):
            all_safe = False
            reasons.append(f"non_safe_path: {f}")
            break

    if all_safe:
        return PATCH_TIER_0, ["all_files_safe", f"diff_lines={diff_lines}"]

    return PATCH_TIER_1, reasons or ["default_tier_1"]


def _notify_reviewer_block(candidate, assessment):
    """Send Telegram notification when reviewer blocks auto-apply."""
    try:
        from app.services.telegram_agent import send_reviewer_verdict, is_configured
        if is_configured():
            send_reviewer_verdict(assessment, entity_title=candidate.title)
    except Exception as exc:
        log.warning("bugfix_pipeline: telegram reviewer verdict failed: %s", exc)


# Maximum auto-applies per calendar day. Hard safety limit.
_MAX_AUTO_APPLIES_PER_DAY = 5


def _get_adaptive_daily_cap(db: Session) -> int:
    """Get the adaptive daily auto-apply cap (bounded, evidence-aware)."""
    try:
        from app.services.adaptive_governance import get_adaptive_thresholds
        return get_adaptive_thresholds(db).max_auto_applies_per_day
    except Exception:
        return _MAX_AUTO_APPLIES_PER_DAY  # fallback to static default


def _check_daily_apply_cap(db: Session) -> bool:
    """
    Check if daily auto-apply cap has been reached.
    Uses adaptive cap when evidence is available, falls back to static default.
    Returns True if cap reached (no more applies allowed today).
    """
    cap = _get_adaptive_daily_cap(db)
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE decided_by = 'auto_tier_0'
          AND applied_at >= :today
          AND status = 'applied'
    """), {"today": today_start}).fetchone()
    applied_today = count[0] if count else 0

    if applied_today >= cap:
        log.info("auto_apply: DAILY CAP reached (%d/%d adaptive) — skipping", applied_today, cap)
        return True
    return False


# ---------------------------------------------------------------------------
# Domain-level autonomy budgets
# ---------------------------------------------------------------------------

# Per-domain daily caps based on domain stability
_DOMAIN_BUDGET_DEFAULT = 2       # healthy domains: 2 auto-applies per day
_DOMAIN_BUDGET_UNSTABLE = 1      # unstable domains: 1 per day
_DOMAIN_BUDGET_QUARANTINE = 0    # quarantined domains: 0 (human-only)

# Weakness score thresholds (from loop_health.score_subsystem_weakness)
_WEAKNESS_UNSTABLE_THRESHOLD = 15
_WEAKNESS_QUARANTINE_THRESHOLD = 30


def _get_domain_budget(db: Session, domain: str) -> int:
    """
    Get the daily auto-apply budget for a domain.

    Uses per-domain adaptive profiles when available:
    - Per-domain effectiveness history
    - Per-domain operator feedback (approval/rejection rates)
    - Weakness score
    Falls back to global adaptive defaults, then to static defaults.

    Returns max allowed auto-applies per day for this domain.
    0 = quarantined (no auto-apply allowed).
    """
    if not domain or domain == "unknown":
        return _DOMAIN_BUDGET_DEFAULT

    try:
        # Try per-domain profile first (highest intelligence)
        try:
            from app.services.adaptive_governance import get_domain_profiles
            profiles = get_domain_profiles(db)
            if domain in profiles:
                return profiles[domain].budget
        except Exception as exc:
            log.warning("bugfix_pipeline: adaptive governance profile lookup failed: %s", exc)

        # Fallback: global adaptive thresholds + weakness score
        try:
            from app.services.adaptive_governance import get_adaptive_thresholds
            thresholds = get_adaptive_thresholds(db)
            budget_default = thresholds.domain_budget_default
            unstable_threshold = thresholds.weakness_unstable_threshold
            quarantine_threshold = thresholds.weakness_quarantine_threshold
        except Exception:
            budget_default = _DOMAIN_BUDGET_DEFAULT
            unstable_threshold = _WEAKNESS_UNSTABLE_THRESHOLD
            quarantine_threshold = _WEAKNESS_QUARANTINE_THRESHOLD

        from app.services.loop_health import score_subsystem_weakness
        weakness_ranking = score_subsystem_weakness(db, lookback_days=30)
        weakness_map = {w["domain"]: w["score"] for w in weakness_ranking}
        score = weakness_map.get(domain, 0)

        if score >= quarantine_threshold:
            return _DOMAIN_BUDGET_QUARANTINE
        if score >= unstable_threshold:
            return _DOMAIN_BUDGET_UNSTABLE
        return budget_default
    except Exception:
        return _DOMAIN_BUDGET_DEFAULT


def _check_domain_budget(db: Session, domain: str) -> bool:
    """
    Check if a domain's daily auto-apply budget is exhausted.
    Returns True if budget exhausted (no more applies allowed for this domain today).
    """
    if not domain or domain == "unknown":
        return False  # unknown domains fall under global cap only

    budget = _get_domain_budget(db, domain)

    if budget == 0:
        log.info("auto_apply: DOMAIN QUARANTINED domain=%s — no auto-apply allowed", domain)
        return True

    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE decided_by = 'auto_tier_0'
          AND applied_at >= :today
          AND status = 'applied'
          AND affected_domain = :domain
    """), {"today": today_start, "domain": domain}).fetchone()
    applied_today = count[0] if count else 0

    if applied_today >= budget:
        log.info(
            "auto_apply: DOMAIN BUDGET exhausted domain=%s (%d/%d) — skipping",
            domain, applied_today, budget,
        )
        return True
    return False


def reclassify_proposed_candidates(db: Session) -> dict:
    """Re-evaluate tier and confidence for patch_proposed candidates."""
    summary = {"reclassified": 0, "confidence_updated": 0}
    candidates = db.query(BugFixCandidate).filter(BugFixCandidate.status == "patch_proposed").all()
    for c in candidates:
        if not c.patch_files or not c.patch_diff:
            continue
        new_tier, reasons = classify_patch_risk(c.patch_files, c.patch_diff)
        if new_tier != c.patch_risk_tier:
            c.patch_risk_tier = new_tier
            summary["reclassified"] += 1
        try:
            from app.services.candidate_scoring import compute_fix_confidence
            new_conf, _ = compute_fix_confidence(db, c)
            if new_conf != c.fix_confidence:
                c.fix_confidence = new_conf
                summary["confidence_updated"] += 1
        except Exception as exc:
            log.warning("bugfix_pipeline: confidence recalibration failed for candidate=%d: %s", c.id, exc)
    if summary["reclassified"] > 0 or summary["confidence_updated"] > 0:
        db.flush()
    return summary


def run_auto_apply(db: Session, max_per_cycle: int = 1) -> dict:
    """
    Auto-approve + auto-apply PATCH_TIER_0 candidates.
    Max 1 per cycle, max 5 per day. Stops on any failure.
    """
    import time as _time

    summary = {"attempted": 0, "applied": 0, "failed": 0, "skipped": 0}

    # Compliance kill switch — when the compliance synthesizer has
    # dropped the rolling score below `_AUTO_PAUSE_THRESHOLD`, NO new
    # auto-applies are allowed until the score recovers. The founder
    # investigates, fixes, and the flag clears on the next tick.
    try:
        from app.services.compliance_score import is_self_modification_paused
        if is_self_modification_paused():
            log.warning("bugfix_pipeline: auto-apply paused by compliance score")
            summary["skipped"] = 1
            summary["reason"] = "compliance_auto_pause"
            return summary
    except Exception as exc:
        log.debug("compliance_score auto-pause check failed (non-fatal): %s", exc)

    # Daily aggregate safety cap
    if _check_daily_apply_cap(db):
        summary["skipped"] = 1
        return summary

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "patch_proposed",
            BugFixCandidate.patch_risk_tier == PATCH_TIER_0,
        )
        .order_by(
            BugFixCandidate.priority_score.desc().nullslast(),
            BugFixCandidate.created_at,
        )
        .limit(max_per_cycle)
        .all()
    )

    for c in candidates:
        # Re-verify tier (defensive)
        tier, reasons = classify_patch_risk(c.patch_files, c.patch_diff)
        if tier != PATCH_TIER_0:
            c.patch_risk_tier = tier
            summary["skipped"] += 1
            db.flush()
            continue

        # Confidence gate — TIER_0 auto-apply only if confidence >= 40
        _MIN_AUTO_APPLY_CONFIDENCE = 40
        if c.fix_confidence is not None and c.fix_confidence < _MIN_AUTO_APPLY_CONFIDENCE:
            log.info(
                "auto_apply: CONFIDENCE GATE blocked id=%d confidence=%d (min=%d)",
                c.id, c.fix_confidence, _MIN_AUTO_APPLY_CONFIDENCE,
            )
            c.patch_risk_tier = 1  # escalate to human-approve
            summary["skipped"] += 1
            db.flush()
            continue

        # Domain-level autonomy budget check
        _classify_candidate_domain(c)
        if c.affected_domain and _check_domain_budget(db, c.affected_domain):
            summary["skipped"] += 1
            continue

        # Predictive outcome gate — don't burn apply budget on candidates
        # whose historical (domain, source_type) lineage has a <25%
        # effective rate. Downgrade to manual review. This is the
        # competitive moat: the threshold becomes sharper over months
        # as your merchant base generates more outcome telemetry.
        try:
            skip, reason = should_skip_apply_by_prediction(db, c)
            if skip:
                log.info(
                    "auto_apply: PREDICTIVE GATE blocked id=%d %s",
                    c.id, reason,
                )
                c.patch_risk_tier = 1  # escalate to human-approve
                c.failure_reason = f"predicted_ineffective: {reason}"
                summary["skipped"] += 1
                db.flush()
                continue
        except Exception as exc:
            log.warning(
                "auto_apply: predictive gate errored — escalating id=%d to TIER_1: %s",
                c.id, exc,
            )
            c.patch_risk_tier = 1
            c.failure_reason = f"predictive_gate_error: {exc}"
            summary["skipped"] += 1
            db.flush()
            continue

        # Reviewer gate — deterministic assessment before auto-apply
        try:
            from app.services.reviewer_layer import review_entity
            assessment = review_entity(db, "bugfix_candidate", c.id)
            if assessment:
                c.reviewer_assessment_id = assessment.id
                db.flush()
                if assessment.verdict == "reject":
                    log.info("auto_apply: REVIEWER BLOCKED id=%d verdict=reject", c.id)
                    summary["skipped"] += 1
                    _notify_reviewer_block(c, assessment)
                    continue
                if assessment.verdict == "refine":
                    log.info("auto_apply: REVIEWER HELD id=%d verdict=refine", c.id)
                    summary["skipped"] += 1
                    _notify_reviewer_block(c, assessment)
                    continue
                if not assessment.auto_approvable:
                    log.info("auto_apply: REVIEWER NOT AUTO-APPROVABLE id=%d", c.id)
                    summary["skipped"] += 1
                    _notify_reviewer_block(c, assessment)
                    continue
        except Exception as exc:
            log.warning(
                "auto_apply: reviewer error — escalating id=%d to TIER_1: %s",
                c.id, exc,
            )
            c.patch_risk_tier = 1
            c.failure_reason = f"reviewer_gate_error: {exc}"
            summary["skipped"] += 1
            db.flush()
            continue

        summary["attempted"] += 1

        # Auto-approve
        c.status = "approved"
        c.decided_by = "auto_tier_0"
        c.decided_at = _now()
        db.flush()

        from app.services.audit import write_audit_log
        write_audit_log(
            db, actor_type="system", actor_name="auto_apply",
            action_type="bugfix_auto_approved", target_type="bugfix",
            target_id=str(c.id), status="completed", approval_mode="autonomous",
            metadata={"tier": 0, "reasons": reasons,
                      "reviewer_assessment_id": c.reviewer_assessment_id},
        )
        db.flush()

        # Apply
        result = apply_bugfix_candidate(db, c.id)
        db.flush()

        if result.status == "applied":
            summary["applied"] += 1
            # Slack notify success
            try:
                from app.core.alert_delivery import _SLACK_URL
                if _SLACK_URL:
                    import httpx
                    httpx.post(_SLACK_URL, json={
                        "text": (
                            f":white_check_mark: *AUTO-APPLIED* — `{c.title}`\n"
                            f"*SHA:* `{c.git_commit_sha or 'N/A'}`\n"
                            f"*Tests:* passed\n*ID:* {c.id}"
                        ),
                    }, timeout=5.0)
            except Exception as exc:
                log.warning("bugfix_pipeline: auto_apply slack notification failed: %s", exc)
            log.info("auto_apply: SUCCESS id=%d title=%s", c.id, c.title)
        else:
            summary["failed"] += 1
            log.warning("auto_apply: FAILED id=%d status=%s reason=%s", c.id, result.status, result.failure_reason)
            break  # Stop further auto-applies this cycle

    if summary["attempted"] > 0:
        log.info("auto_apply: attempted=%d applied=%d failed=%d", summary["attempted"], summary["applied"], summary["failed"])

    return summary


# ---------------------------------------------------------------------------
# Governed TIER_1 auto-apply (M4) — strict-gated autonomy for the
# files where the LLM is allowed to touch but a single mistake costs.
# ---------------------------------------------------------------------------
#
# TIER_1 historically required a human approver. After the L-sprint
# tightened the LLM grounding (preflight + manifest + signatures + diff
# rules) the floor is high enough to attempt small TIER_1 fixes
# automatically — but ONLY when ALL of these are simultaneously true:
#
#   G1. Confidence score >= _GOV_TIER1_MIN_CONFIDENCE (default 80, vs 40 for T0)
#   G2. Predictive effective rate >= _GOV_TIER1_MIN_PROBABILITY (default 0.60)
#   G3. Predictive sample size >= _GOV_TIER1_MIN_SAMPLES (default 5)
#   G4. Reviewer.verdict == 'approve' AND auto_approvable == True
#   G5. Patch adds < _GOV_TIER1_MAX_ADDED_LINES (default 50)
#   G6. Patch touches exactly 1 production file (+ optionally a single test)
#   G7. Domain track record: at least _GOV_TIER1_MIN_DOMAIN_WINS recent successes
#       in the same affected_domain (default 3)
#   G8. Zero PatchFingerprint failure matches (no semantic dup)
#   G9. Patch does NOT touch self-healing pipeline (defense-in-depth)
#   G10. AUTO_APPLY_TIER1=1 env kill switch (default ON, set to "0" to halt)
#
# Any failed gate => skip cleanly (no escalation, no failure_reason
# update). The candidate stays in patch_proposed and the operator can
# review at leisure.

_GOV_TIER1_MIN_CONFIDENCE = 80
_GOV_TIER1_MIN_PROBABILITY = 0.60
_GOV_TIER1_MIN_SAMPLES = 5
_GOV_TIER1_MAX_ADDED_LINES = 50
_GOV_TIER1_MIN_DOMAIN_WINS = 3
_GOV_TIER1_MAX_PER_CYCLE = 1
_GOV_TIER1_MAX_PER_DAY = 3


def _is_governed_tier1_enabled() -> bool:
    """Default ON. Set AUTO_APPLY_TIER1=0 to halt every future TIER_1
    auto-apply in 5 seconds. The gate stack inside `run_governed_tier1_auto_apply`
    is the actual safety net; the env var is the operator emergency stop."""
    return os.getenv("AUTO_APPLY_TIER1", "1").strip() not in ("0", "false", "False", "")


def _gov_tier1_count_added_lines(patch_diff: str | None) -> int:
    if not patch_diff:
        return 0
    return sum(
        1 for l in patch_diff.split("\n")
        if l.startswith("+") and not l.startswith("+++") and l.strip() and not l.strip().startswith("#")
    )


def _gov_tier1_count_production_files(patch_files_json: str | None) -> tuple[int, int]:
    """Return (production_file_count, test_file_count)."""
    if not patch_files_json:
        return 0, 0
    try:
        files = json.loads(patch_files_json) or []
    except Exception:
        return 0, 0
    prod = sum(1 for f in files if isinstance(f, str) and not f.startswith("tests/"))
    test = sum(1 for f in files if isinstance(f, str) and f.startswith("tests/"))
    return prod, test


def _gov_tier1_domain_wins(db: Session, domain: str | None) -> int:
    """Recent successful effective applies in this domain over the last 90d."""
    if not domain:
        return 0
    try:
        cutoff = _now() - timedelta(days=90)
        row = db.execute(text("""
            SELECT COUNT(*) FROM bugfix_candidates
            WHERE affected_domain = :dom
              AND status = 'applied'
              AND outcome_status = 'effective'
              AND applied_at >= :cutoff
        """), {"dom": domain, "cutoff": cutoff}).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _gov_tier1_check_daily_cap(db: Session) -> bool:
    """Return True if today's TIER_1 governed apply cap is reached."""
    try:
        cutoff = _now() - timedelta(hours=24)
        row = db.execute(text("""
            SELECT COUNT(*) FROM bugfix_candidates
            WHERE patch_risk_tier = 1
              AND status = 'applied'
              AND decided_by = 'auto_tier_1_governed'
              AND applied_at >= :cutoff
        """), {"cutoff": cutoff}).fetchone()
        return int(row[0] or 0) >= _GOV_TIER1_MAX_PER_DAY if row else False
    except Exception:
        return True  # Fail-closed on query error


def run_governed_tier1_auto_apply(
    db: Session, max_per_cycle: int = _GOV_TIER1_MAX_PER_CYCLE,
) -> dict:
    """
    Strict-gated TIER_1 auto-apply. Same shape as run_auto_apply but with
    a much higher bar. Runs after the TIER_0 path each cycle.
    """
    summary = {
        "attempted": 0,
        "applied": 0,
        "failed": 0,
        "skipped": 0,
        "skipped_disabled": 0,
        "skipped_daily_cap": 0,
        "gate_failures": {},
    }

    if not _is_governed_tier1_enabled():
        summary["skipped_disabled"] = 1
        return summary

    if _gov_tier1_check_daily_cap(db):
        summary["skipped_daily_cap"] = 1
        return summary

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "patch_proposed",
            BugFixCandidate.patch_risk_tier == PATCH_TIER_1,
        )
        .order_by(
            BugFixCandidate.priority_score.desc().nullslast(),
            BugFixCandidate.created_at,
        )
        .limit(max_per_cycle * 5)  # over-fetch so we can skip ungated ones
        .all()
    )

    def _fail_gate(name: str, c: BugFixCandidate, reason: str = "") -> None:
        summary["skipped"] += 1
        summary["gate_failures"][name] = summary["gate_failures"].get(name, 0) + 1
        log.info(
            "governed_tier1: GATE %s blocked id=%d %s",
            name, c.id, reason,
        )

    for c in candidates:
        if summary["applied"] >= max_per_cycle:
            break

        # G1. Confidence
        if c.fix_confidence is None or c.fix_confidence < _GOV_TIER1_MIN_CONFIDENCE:
            _fail_gate("confidence", c, f"score={c.fix_confidence}")
            continue

        # G2 + G3. Predictive history
        try:
            p, n = predict_outcome_probability(
                db,
                affected_domain=c.affected_domain,
                source_type=c.source_type,
            )
            if n < _GOV_TIER1_MIN_SAMPLES:
                _fail_gate("predictive_samples", c, f"n={n}")
                continue
            if p < _GOV_TIER1_MIN_PROBABILITY:
                _fail_gate("predictive_probability", c, f"p={p:.2f}")
                continue
        except Exception:
            _fail_gate("predictive_error", c)
            continue

        # G5. Patch size
        added_lines = _gov_tier1_count_added_lines(c.patch_diff)
        if added_lines >= _GOV_TIER1_MAX_ADDED_LINES:
            _fail_gate("patch_too_large", c, f"added={added_lines}")
            continue

        # G6. Single production file (test file optional)
        prod, _test = _gov_tier1_count_production_files(c.patch_files)
        if prod != 1:
            _fail_gate("not_single_file", c, f"prod_files={prod}")
            continue

        # G7. Domain track record
        wins = _gov_tier1_domain_wins(db, c.affected_domain)
        if wins < _GOV_TIER1_MIN_DOMAIN_WINS:
            _fail_gate("domain_track_record", c, f"wins={wins}")
            continue

        # G8. Zero PatchFingerprint failure matches
        try:
            pre_fp = _compute_patch_fingerprint(c.title, c.patch_files, c.patch_diff)
            failed_match = _check_patch_fingerprint(db, pre_fp)
            if failed_match:
                _fail_gate("fingerprint_match", c, f"matches_{failed_match['candidate_id']}")
                continue
        except Exception as exc:
            log.warning("bugfix_pipeline: fingerprint pre-check failed: %s", exc)

        # G9. Defense-in-depth: never auto-touch the self-healing pipeline
        try:
            touches, matches = touches_self_healing_pipeline(c.patch_files)
            if touches:
                _fail_gate("self_healing_touch", c, f"matches={matches[:2]}")
                continue
        except Exception as exc:
            log.warning("bugfix_pipeline: self-healing touch check failed: %s", exc)

        # G4. Reviewer verdict — strictest check, runs last because it
        # writes a row in reviewer_assessments
        try:
            from app.services.reviewer_layer import review_entity
            assessment = review_entity(db, "bugfix_candidate", c.id)
            if assessment is None:
                _fail_gate("reviewer_unavailable", c)
                continue
            c.reviewer_assessment_id = assessment.id
            db.flush()
            if assessment.verdict != "approve":
                _fail_gate("reviewer_verdict", c, f"verdict={assessment.verdict}")
                continue
            if not assessment.auto_approvable:
                _fail_gate("reviewer_not_auto_approvable", c)
                continue
            risk = (assessment.risk_level or "").lower()
            if risk not in ("", "low", "none"):
                _fail_gate("reviewer_risk", c, f"risk={risk}")
                continue
        except Exception as exc:
            _fail_gate("reviewer_error", c, str(exc)[:80])
            continue

        # All gates passed — auto-approve + apply
        summary["attempted"] += 1
        c.status = "approved"
        c.decided_by = "auto_tier_1_governed"
        c.decided_at = _now()
        db.flush()

        from app.services.audit import write_audit_log
        write_audit_log(
            db, actor_type="system", actor_name="governed_tier1",
            action_type="bugfix_auto_approved", target_type="bugfix",
            target_id=str(c.id), status="completed", approval_mode="autonomous_governed",
            metadata={
                "tier": 1,
                "confidence": c.fix_confidence,
                "predictive_p": p,
                "predictive_n": n,
                "added_lines": added_lines,
                "domain": c.affected_domain,
                "domain_wins": wins,
                "reviewer_assessment_id": c.reviewer_assessment_id,
            },
        )
        db.flush()

        result = apply_bugfix_candidate(db, c.id)
        db.flush()

        if result.status == "applied":
            summary["applied"] += 1
            log.info(
                "governed_tier1: APPLIED id=%d title=%s",
                c.id, (c.title or "")[:80],
            )
            try:
                from app.services.alerting import write_alert
                write_alert(
                    db,
                    source=f"governed_tier1:{c.affected_domain or 'unknown'}",
                    alert_type="governed_tier1_applied",
                    severity="info",
                    summary=f"TIER_1 governed apply ok id={c.id} {(c.title or '')[:80]}",
                    detail={
                        "candidate_id": c.id,
                        "domain": c.affected_domain,
                        "confidence": c.fix_confidence,
                        "added_lines": added_lines,
                    },
                )
            except Exception as exc:
                log.warning("bugfix_pipeline: untested_change alert write failed: %s", exc)
        else:
            summary["failed"] += 1
            log.warning(
                "governed_tier1: FAILED id=%d status=%s reason=%s",
                c.id, result.status, result.failure_reason,
            )
            break  # Halt on any failure — preserves the cooldown intent

    return summary


# ---------------------------------------------------------------------------
# Safety blocklist — file paths that must NEVER be auto-patched
# ---------------------------------------------------------------------------

# Imported from tier_check — single source of truth for protected paths.
# Used by legacy _check_forbidden_paths (defense-in-depth behind pre_apply_guard).
try:
    from app.core.tier_check import _TIER_2_PATTERNS as _FORBIDDEN_PATH_PATTERNS
except ImportError:
    _FORBIDDEN_PATH_PATTERNS = [
        "app/core/token_crypto",
        "app/core/merchant_session",
        "app/core/deps.py",
        "app/api/billing",
        "app/api/shopify_oauth",
        "app/services/orchestrator.py",
        "app/models/action_approval",
        "app/services/email_templates.py",
        "app/services/email_orchestrator.py",
        "app/services/email_governance.py",
        "app/services/brand_voice.py",
        "app/core/email.py",
        "migrations/",
    ]

_REPO_DIR = "/opt/wishspark"
_BACKEND_DIR = "/opt/wishspark/backend"


def _check_forbidden_paths(patch_files_json: str | None) -> str | None:
    """Return rejection reason if any file is in the forbidden list."""
    if not patch_files_json:
        return None
    try:
        files = json.loads(patch_files_json)
    except (json.JSONDecodeError, ValueError):
        return None
    for f in files:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern in str(f):
                return f"forbidden_path: {f} matches {pattern}"
    return None


def _tracker_version_bumped(patch_diff: str | None) -> bool:
    """Check if a patch that touches tracker JS also bumps TRACKER_VERSION."""
    if not patch_diff:
        return False
    # Look for a change to tracker_version.py in the diff
    return "tracker_version" in patch_diff.lower() and (
        "+TRACKER_VERSION" in patch_diff or "+tracker_version" in patch_diff.lower()
    )


# ---------------------------------------------------------------------------
# Safe apply pipeline — human-gated, test-verified, reversible
# ---------------------------------------------------------------------------

@dataclass
class ApplyResult:
    status: str = "pending"
    test_passed: bool = False
    test_output: str = ""
    health_ok: bool = False
    failure_reason: str | None = None


def _release_apply_lock(candidate_id: int) -> None:
    """Release the Redis execution lock for this candidate. Fail-safe."""
    try:
        from app.core.telegram_safety import release_execution_lock
        release_execution_lock("bugfix", str(candidate_id))
    except Exception as exc:
        log.debug("auto_apply: lock release failed (non-fatal): %s", exc)


def apply_bugfix_candidate(db: Session, candidate_id: int) -> ApplyResult:
    """
    Apply an approved patch with verification and rollback.

    Wraps _apply_bugfix_candidate_impl with Redis-backed restart-safety
    gates (idempotency + execution lock). Added 2026-04-11 elite sprint.
    If agent_worker crashes mid-apply and restarts, the idempotency key
    blocks re-entry for 5 minutes, preventing double-apply. The lock
    is ALWAYS released in the finally block so a crash does not
    deadlock the candidate forever (lock TTL is the ultimate backstop).

    Sequence: idempotency → lock → preconditions → clean check → apply
    --check → apply → tests → restart → health → success or rollback.

    On success: propagates resolution to linked support incidents.
    """
    result = ApplyResult()

    # --- Restart-safe idempotency gate ---
    try:
        from app.core.telegram_safety import (
            check_idempotency,
            acquire_execution_lock,
        )
        if not check_idempotency("auto_apply", str(candidate_id), critical=True):
            log.warning(
                "auto_apply: IDEMPOTENCY — candidate_id=%d already applied "
                "within window, refusing re-entry",
                candidate_id,
            )
            result.status = "apply_failed"
            result.failure_reason = "idempotency_block: duplicate apply within 5min"
            return result
        if not acquire_execution_lock("bugfix", str(candidate_id), critical=True):
            log.warning(
                "auto_apply: LOCK HELD — candidate_id=%d apply already in flight",
                candidate_id,
            )
            result.status = "apply_failed"
            result.failure_reason = "execution_lock: concurrent apply in progress"
            return result
    except Exception as exc:
        log.warning("auto_apply: idempotency infra error: %s — FAIL CLOSED", exc)
        result.status = "apply_failed"
        result.failure_reason = f"idempotency_infra_error: {type(exc).__name__}"
        return result

    try:
        return _apply_bugfix_candidate_impl(db, candidate_id)
    finally:
        _release_apply_lock(candidate_id)


def _apply_bugfix_candidate_impl(db: Session, candidate_id: int) -> ApplyResult:
    """
    Inner implementation of the apply flow. Called ONLY from
    apply_bugfix_candidate which holds the idempotency gate + execution
    lock. Do not call directly — you will bypass restart safety.
    """
    import subprocess
    import tempfile

    result = ApplyResult()
    candidate = db.get(BugFixCandidate, candidate_id)

    if not candidate:
        result.status = "apply_failed"
        result.failure_reason = "candidate_not_found"
        return result

    if candidate.status != "approved":
        result.status = "apply_failed"
        result.failure_reason = f"wrong_status: {candidate.status}"
        return result

    if not candidate.patch_diff or not candidate.patch_diff.strip():
        result.status = "apply_failed"
        result.failure_reason = "empty_patch_diff"
        candidate.status = "apply_failed"
        candidate.failure_reason = result.failure_reason
        db.flush()
        return result

    # === EXECUTION POLICY ENFORCEMENT (tier_check + file_lock) ===
    _MAX_PATCH_FILES = 8  # hard cap — patches touching >8 files are too risky for auto-apply
    _apply_files = []
    if candidate.patch_files:
        try:
            _apply_files = json.loads(candidate.patch_files)
        except (json.JSONDecodeError, ValueError):
            pass

    if len(_apply_files) > _MAX_PATCH_FILES:
        result.status = "apply_failed"
        result.failure_reason = f"hard_file_cap: patch touches {len(_apply_files)} files (max {_MAX_PATCH_FILES})"
        candidate.status = "apply_failed"
        candidate.failure_reason = result.failure_reason
        db.flush()
        log.warning("apply: BLOCKED — %d files exceeds hard cap of %d", len(_apply_files), _MAX_PATCH_FILES)
        return result

    try:
        from app.core.pre_apply_guard import guard_pre_apply, release_guard
        guard = guard_pre_apply(
            files=_apply_files,
            patch_diff=candidate.patch_diff,
            owner="bugfix_pipeline",
        )
        if guard.blocked:
            result.status = "apply_failed"
            result.failure_reason = f"guard_blocked: {guard.block_reason}"
            candidate.status = "apply_failed"
            candidate.failure_reason = result.failure_reason
            db.flush()
            _write_apply_alert(db, candidate, result)
            return result
        if not guard.allowed and candidate.decided_by == "auto_tier_0":
            # Auto-apply attempted on a patch that the guard escalated beyond TIER_0
            release_guard(_apply_files, "bugfix_pipeline")
            result.status = "apply_failed"
            result.failure_reason = f"tier_escalated: guard returned {guard.label} — auto-apply requires TIER_0"
            candidate.status = "apply_failed"
            candidate.failure_reason = result.failure_reason
            db.flush()
            _write_apply_alert(db, candidate, result)
            return result
    except ImportError:
        pass  # Fallback to legacy forbidden path check below

    # Legacy forbidden path check (retained as defense-in-depth)
    forbidden = _check_forbidden_paths(candidate.patch_files)
    if forbidden:
        result.status = "apply_failed"
        result.failure_reason = forbidden
        candidate.status = "apply_failed"
        candidate.failure_reason = forbidden
        db.flush()
        _write_apply_alert(db, candidate, result)
        return result

    patch_path = None
    try:
        normalized_diff = _normalize_diff(candidate.patch_diff)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, dir="/tmp") as f:
            f.write(normalized_diff)
            patch_path = f.name

        candidate.status = "applying"
        db.flush()

        # Git tree clean check — skip for new-file patches.
        # New files (--- /dev/null) never conflict with existing dirty files,
        # Dirty-tree check — ALWAYS required, no exceptions.
        #
        # Before 2026-04-11 this check was SKIPPED for new-file patches
        # (--- /dev/null) on the theory that "git apply works fine on a
        # dirty tree". That was half the story. After apply, the commit
        # routine ran `git add -A` which committed ALL working-tree
        # changes, not just the patch. On 2026-04-11 we observed the
        # pipeline commit an operator's in-flight session (ClientErrorBoundary,
        # ErrorReporterInstaller, new tests, etc.) under the message
        # "apply bugfix candidate #45693". That is both misleading AND
        # dangerous — any developer working in this repo could have
        # their uncommitted changes captured and merged by the first
        # autonomous apply.
        #
        # New invariant: working tree must be clean, period. If a
        # developer needs to work alongside the pipeline, they must
        # use a branch or set an env gate.
        git_status = subprocess.run(
            ["git", "diff", "--quiet"], cwd=_BACKEND_DIR,
            capture_output=True, timeout=10,
        )
        if git_status.returncode != 0:
            return _fail_apply(db, candidate, result, "git_tree_dirty")
        # Also check staged changes (git add but not committed)
        git_staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=_BACKEND_DIR,
            capture_output=True, timeout=10,
        )
        if git_staged.returncode != 0:
            return _fail_apply(db, candidate, result, "git_index_dirty")
        # And untracked files that match backend paths (these would get
        # swept up by git add -A in the commit routine if we used that path)
        git_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=10,
        )
        if git_untracked.returncode == 0 and git_untracked.stdout.strip():
            # Even untracked files are a risk — the operator may have
            # in-flight work. Fail closed.
            return _fail_apply(
                db, candidate, result,
                f"git_untracked_present: {git_untracked.stdout.strip().splitlines()[:3]}",
            )

        # git apply --check
        check = subprocess.run(
            ["git", "apply", "--check", patch_path], cwd=_BACKEND_DIR,
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode != 0:
            return _fail_apply(db, candidate, result, f"apply_check_failed: {check.stderr[:300]}")

        # Apply
        apply_cmd = subprocess.run(
            ["git", "apply", patch_path], cwd=_BACKEND_DIR,
            capture_output=True, text=True, timeout=10,
        )
        if apply_cmd.returncode != 0:
            return _fail_apply(db, candidate, result, f"apply_failed: {apply_cmd.stderr[:300]}")

        # Run tests — for new test-only files, just verify the new file can be imported
        # (doesn't break any existing code). Full regression suite is too heavyweight
        # for adding a new file that can't affect production.
        _venv_python = f"{_BACKEND_DIR}/venv/bin/python"
        _new_test_files: list[str] = []
        _is_new_file_patch = "--- /dev/null" in (candidate.patch_diff or "")
        if _is_new_file_patch:
            try:
                _flist = json.loads(candidate.patch_files) if candidate.patch_files else []
                _new_test_files = [f for f in _flist if f.startswith("tests/")]
            except (json.JSONDecodeError, ValueError):
                pass

        if _new_test_files:
            # For new test files: just verify they can be collected by pytest (syntax check)
            test_cmd = f"{_venv_python} -m pytest {' '.join(_new_test_files)} --collect-only -q"
        else:
            test_cmd = f"{_venv_python} -m pytest tests/ --ignore=tests/test_scaling_intelligence.py --ignore=tests/test_merge_intelligence.py -q"
        log.info("bugfix_apply: running tests: %s", test_cmd)
        try:
            test_run = subprocess.run(
                test_cmd.split(), cwd=_BACKEND_DIR,
                capture_output=True, text=True, timeout=300,
                env={**os.environ, "PYTHONPATH": _BACKEND_DIR},
            )
        except subprocess.TimeoutExpired:
            _rollback_patch(patch_path)
            return _fail_apply(db, candidate, result, "test_timeout: tests exceeded 300s", rolled_back=True)
        result.test_output = (test_run.stdout[-500:] + "\n" + test_run.stderr[-500:]).strip()
        result.test_passed = test_run.returncode == 0
        candidate.test_result = result.test_output[:2000]
        log.info("bugfix_apply: tests completed rc=%d passed=%s", test_run.returncode, result.test_passed)

        if not result.test_passed:
            _rollback_patch(patch_path)
            return _fail_apply(db, candidate, result, "tests_failed", rolled_back=True)

        # Frontend build verification (if guard flagged it)
        _needs_frontend = False
        _needs_tracker_bump = False
        try:
            _needs_frontend = guard.requires_frontend_build
            _needs_tracker_bump = guard.requires_tracker_bump
        except (NameError, AttributeError):
            # guard may not exist if ImportError path was taken above
            from app.core.tier_check import require_frontend_build, require_tracker_bump
            _needs_frontend = require_frontend_build(_apply_files)
            _needs_tracker_bump = require_tracker_bump(_apply_files)

        if _needs_frontend:
            log.info("bugfix_apply: running frontend build verification (dashboard files touched)")
            try:
                from app.core.pre_apply_guard import verify_frontend_build
                build_ok, build_output = verify_frontend_build()
                if not build_ok:
                    _rollback_patch(patch_path)
                    return _fail_apply(
                        db, candidate, result,
                        f"frontend_build_failed: {build_output[:300]}",
                        rolled_back=True,
                    )
            except Exception as exc:
                _rollback_patch(patch_path)
                return _fail_apply(
                    db, candidate, result,
                    f"frontend_build_error: {str(exc)[:200]}",
                    rolled_back=True,
                )

        if _needs_tracker_bump:
            # Verify TRACKER_VERSION was bumped in the patch
            if not _tracker_version_bumped(candidate.patch_diff):
                _rollback_patch(patch_path)
                return _fail_apply(
                    db, candidate, result,
                    "tracker_version_not_bumped: patch modifies tracker JS but does not bump TRACKER_VERSION",
                    rolled_back=True,
                )

        # Restart + health
        subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
        import time
        time.sleep(4)

        try:
            import httpx
            health = httpx.get("http://127.0.0.1:8000/system/health", timeout=8.0)
            result.health_ok = health.status_code == 200
        except Exception:
            result.health_ok = False

        if not result.health_ok:
            _rollback_patch(patch_path)
            subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
            return _fail_apply(db, candidate, result, "health_check_failed", rolled_back=True)

        # Git commit (local only, no push)
        commit_sha = _git_commit_patch(candidate)
        if commit_sha is None:
            # Commit failed — rollback
            _rollback_patch(patch_path)
            subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
            return _fail_apply(db, candidate, result, "git_commit_failed", rolled_back=True)

        # Success
        result.status = "applied"
        candidate.status = "applied"
        candidate.applied_at = _now()
        candidate.git_commit_sha = commit_sha
        candidate.failure_reason = None
        candidate.outcome_status = None  # will be measured 48h later by evolution_outcomes
        _classify_candidate_domain(candidate)
        db.flush()

        # Record successful patch fingerprint (outcome will be updated after 48h measurement)
        _record_patch_fingerprint(db, candidate, outcome="applied")

        # D3 — cache the template for this family so subsequent matching
        # candidates can reuse the diff without LLM spend. Only stored on
        # real apply success (tests + health + git commit all green).
        try:
            _store_fix_template(_compute_fix_template_key(candidate), candidate)
        except Exception as exc:
            log.debug("bugfix_apply: fix_template store failed: %s", exc)

        # D4 — adversarial fragility analysis. Static AST analysis of the
        # added lines surfaces unguarded subscripts, division-by-param,
        # iteration without None checks, etc. Advisory only: we record
        # the report on the candidate and bump a weekly counter for the
        # daily digest. Never blocks apply.
        try:
            from app.services.adversarial_test_gen import run_adversarial_probes
            adv_report = run_adversarial_probes(candidate)
            if adv_report.get("fragility_score", 0) > 0:
                _record_adversarial_report(candidate, adv_report)
        except Exception as exc:
            log.debug("bugfix_apply: adversarial probe failed (non-fatal): %s", exc)

        from app.services.audit import write_audit_log
        write_audit_log(
            db, actor_type="system", actor_name="bugfix_apply",
            action_type="bugfix_applied", target_type="bugfix",
            target_id=str(candidate.id),
            after_state={"title": candidate.title, "tests_passed": True, "commit": commit_sha},
            status="completed", approval_mode="human_approved",
        )
        db.flush()

        # Propagate resolution to linked support incidents
        _propagate_resolution(db, candidate)

        # Create promotion for remote push
        try:
            from app.services.promotion_pipeline import create_promotion
            create_promotion(db, bugfix_candidate_id=candidate.id, git_commit_sha=commit_sha)
            db.flush()
        except Exception as exc:
            log.warning("bugfix_apply: promotion creation failed (non-fatal): %s", exc)

        log.info("bugfix_apply: SUCCESS id=%d sha=%s title=%s", candidate.id, commit_sha, candidate.title)
        return result

    except Exception as exc:
        if patch_path:
            try:
                _rollback_patch(patch_path)
            except Exception as exc:
                log.warning("bugfix_pipeline: rollback after apply failure failed: %s", exc)
        result.status = "apply_failed"
        result.failure_reason = f"unexpected: {str(exc)[:300]}"
        candidate.status = "apply_failed"
        candidate.failure_reason = result.failure_reason
        db.flush()
        _write_apply_alert(db, candidate, result)
        return result

    finally:
        if patch_path:
            try:
                os.unlink(patch_path)
            except Exception as exc:
                log.warning("bugfix_pipeline: patch file cleanup failed: %s", exc)
        # Release file locks acquired by pre_apply_guard
        if _apply_files:
            try:
                from app.core.pre_apply_guard import release_guard
                release_guard(_apply_files, "bugfix_pipeline")
            except Exception as exc:
                log.warning("bugfix_pipeline: guard release failed: %s", exc)


def _git_commit_patch(candidate: BugFixCandidate) -> str | None:
    """Create a local git commit for the applied patch. Returns SHA or None.

    SECURITY-CRITICAL: we explicitly list the files from the candidate's
    patch_files metadata and `git add` ONLY those. We NEVER do `git add -A`,
    which on 2026-04-11 was observed to sweep an operator's in-flight
    session into a commit labelled "apply bugfix candidate #45693". The
    dirty-tree pre-check in the apply path is the first line of defense;
    this targeted add is the second.
    """
    import json as _json
    import subprocess
    try:
        # Resolve patch files from the candidate — safe parse, fallback empty
        try:
            patch_files = _json.loads(candidate.patch_files or "[]")
        except (ValueError, TypeError):
            patch_files = []

        if not isinstance(patch_files, list) or not patch_files:
            log.warning(
                "bugfix_apply: candidate #%d has no patch_files — refusing to commit "
                "(would require `git add -A` which is forbidden)", candidate.id,
            )
            return None

        # Filter out any path that tries to escape the repo root
        safe_files: list[str] = []
        for f in patch_files:
            if not isinstance(f, str) or not f.strip():
                continue
            if ".." in f or f.startswith("/"):
                log.warning("bugfix_apply: rejecting unsafe path %r", f)
                continue
            safe_files.append(f)

        if not safe_files:
            log.warning("bugfix_apply: candidate #%d has no valid patch_files", candidate.id)
            return None

        # Stage ONLY the patch's files. Any other working-tree changes
        # stay untouched. If the dirty-tree pre-check slipped a race,
        # this explicit file list is the safety net.
        add_result = subprocess.run(
            ["git", "add", "--"] + safe_files,
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=10,
        )
        if add_result.returncode != 0:
            log.warning(
                "bugfix_apply: git add failed for candidate=%d files=%s: %s",
                candidate.id, safe_files, add_result.stderr[:200],
            )
            return None

        # Sanity check: verify that git only staged our requested files.
        # If somehow other files got into the index, abort.
        diff_cached = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=5,
        )
        staged_files = [f.strip() for f in diff_cached.stdout.splitlines() if f.strip()]
        unexpected = set(staged_files) - set(safe_files)
        if unexpected:
            log.error(
                "bugfix_apply: SECURITY — candidate #%d tried to commit unexpected files: %s. "
                "Aborting commit and resetting index.",
                candidate.id, list(unexpected)[:5],
            )
            subprocess.run(["git", "reset", "HEAD"], cwd=_BACKEND_DIR, capture_output=True, timeout=10)
            return None

        # Commit
        msg = f"chore(autofix): apply bugfix candidate #{candidate.id}\n\n{candidate.title}"
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.warning("bugfix_apply: git commit failed: %s", result.stderr[:200])
            return None
        # Get SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=5,
        )
        return sha_result.stdout.strip()[:40] if sha_result.returncode == 0 else None
    except Exception as exc:
        log.warning("bugfix_apply: git commit error: %s", exc)
        return None


def _fail_apply(
    db: Session, candidate: BugFixCandidate, result: ApplyResult,
    reason: str, rolled_back: bool = False,
) -> ApplyResult:
    result.status = "rolled_back" if rolled_back else "apply_failed"
    result.failure_reason = reason
    candidate.status = result.status
    candidate.failure_reason = reason
    db.flush()

    # Record failed patch fingerprint for future dedup
    _record_patch_fingerprint(db, candidate, outcome=result.status, failure_reason=reason)

    _write_apply_alert(db, candidate, result)
    return result


def _rollback_patch(patch_path: str) -> None:
    import subprocess
    subprocess.run(
        ["git", "apply", "-R", patch_path], cwd=_BACKEND_DIR,
        capture_output=True, timeout=10,
    )


def _write_apply_alert(db: Session, candidate: BugFixCandidate, result: ApplyResult) -> None:
    from app.services.alerting import write_alert
    severity = "critical" if result.status == "rolled_back" else "warning"
    write_alert(
        db, severity=severity, source="bugfix_apply",
        alert_type="bugfix_rolled_back" if result.status == "rolled_back" else "bugfix_apply_failed",
        summary=f"Bug fix #{candidate.id} {result.status}: {result.failure_reason}",
        detail={"candidate_id": candidate.id, "title": candidate.title, "reason": result.failure_reason},
    )
    db.flush()
