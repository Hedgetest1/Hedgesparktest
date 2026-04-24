"""sibling_hunt — post-apply pipeline phase that finds sibling occurrences
of a just-fixed bug pattern and queues them as child BugFixCandidates.

Sprint A of the CTO-brain pipeline upgrade (see
`memory/project_cto_brain_pipeline_gap.md`). Closes the long-observed
"1 reported bug → 3-4 hidden siblings" ratio (empirical from April 2026
hunts) by making sibling discovery an AUTOMATIC pipeline phase, not
a manual step.

Architecture
------------
After the pipeline applies a BugFixCandidate's patch, `scan_and_queue`
runs:

  1. Distill a bug signature from the unified-diff patch — the removed
     lines normalized into a regex (parameters → `\\w+`).
  2. Grep the codebase (excluding the already-fixed file) for matches.
  3. For each hit, create a child BugFixCandidate with
     `parent_candidate_id = original.id`, `source_type = "sibling"`,
     and `source_ref = "sibling:{parent.id}:{file}:{line}"`.
  4. Deduplicate: skip hits where an open/applied candidate already
     exists with the same source_ref.

Safety
------
- Capped at `_MAX_SIBLINGS_PER_PARENT` (default 15) to prevent runaway.
- Feature-flagged by `SIBLING_HUNT_ENABLED` env var (default `"0"`).
  Pipeline is paused pre-merchant — the flag stays off until explicit
  re-open.
- Recursion guard: a candidate whose source_type is already "sibling"
  does NOT trigger another sibling hunt.
- Pattern minimum length: signatures < 20 chars are discarded (too
  noisy to grep accurately).

Public API
----------
  scan_and_queue(db, parent) -> list[int]     # returns child IDs
  distill_signature(patch_diff) -> list[str]  # removed-line patterns
  find_hits(signature, exclude_file) -> list[(file, line)]

All functions are pure / side-effect-audited — no ops_alert spam, no
LLM calls. Deterministic and cheap.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.silent_fallback import record_silent_return
from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("sibling_hunt")

BACKEND_ROOT = Path("/opt/wishspark/backend")

# Directories we grep for sibling patterns. Test and script sources are
# included because many bug classes (hermeticity assertions, dedup-key
# literals) live there.
_SEARCH_ROOTS = (
    BACKEND_ROOT / "app",
    BACKEND_ROOT / "tests",
    BACKEND_ROOT / "scripts",
)

_MAX_SIBLINGS_PER_PARENT = int(os.getenv("SIBLING_HUNT_MAX_PER_PARENT", "15"))
# Minimum length AFTER comment-strip. 15 chars keeps substantive
# patterns (`assert row is None` = 18) but skips noise (`pass`,
# `return True`, `if x:`).
_MIN_SIGNATURE_LEN = 15

# Tokens replaced with `\w+` to match minor variations (variable names,
# numbers, etc.). Conservative: only swap obvious parameterization
# points so signature stays specific to the pattern.
_PARAM_NORMALIZATION_PATTERNS = (
    # numeric literals (ids, counts) → `\d+` regex (any length, catches
    # both `42` and `0` so signatures don't pin a specific sentinel value)
    (re.compile(r"\b\d+\b"), r"\d+"),
    # quoted strings (test literals, log messages) → `"[^"]*"` regex
    (re.compile(r'"[^"]{3,40}"'), r'"[^"]*"'),
    (re.compile(r"'[^']{3,40}'"), r"'[^']*'"),
)


@dataclass
class SiblingHit:
    file: str           # relative to BACKEND_ROOT
    line: int           # 1-indexed
    matched_line: str   # source line (trimmed)
    signature: str      # which distilled signature matched


def is_enabled() -> bool:
    """Feature flag. Pipeline is paused pre-merchant — keep off by
    default. Enable via `SIBLING_HUNT_ENABLED=1`."""
    return os.getenv("SIBLING_HUNT_ENABLED", "0").lower() in ("1", "true", "yes")


_TRAILING_COMMENT_RE = re.compile(r"\s+#[^\"']*$|\s+//[^\"']*$")


def _strip_trailing_comment(body: str) -> str:
    """Drop trailing `# ...` or `// ...` comments — they make signatures
    over-specific. Only strip when the `#` / `//` is preceded by
    whitespace (so a `#` inside a string literal isn't mistaken for
    a comment)."""
    return _TRAILING_COMMENT_RE.sub("", body).rstrip()


def distill_signature(patch_diff: str | None) -> list[str]:
    """Extract regex patterns from the removed lines of a unified diff.

    Signature rule: each removed line (`^-` prefix, but not `---`
    header) becomes a regex where numeric literals and short quoted
    strings are normalized. Trailing comments are stripped (they make
    signatures over-specific — file A's `# comment A` would never
    match file B's `# comment B`). Lines shorter than
    `_MIN_SIGNATURE_LEN` are discarded.

    Returns an ORDERED list of unique signatures (preserves diff
    order so the "most important" removed line appears first).
    """
    if not patch_diff:
        return []
    signatures: list[str] = []
    seen: set[str] = set()
    for raw in patch_diff.splitlines():
        if not raw.startswith("-"):
            continue
        if raw.startswith("---"):
            continue  # diff file-header
        body = raw[1:].rstrip()
        body = body.lstrip("\t ")
        body = _strip_trailing_comment(body)
        if len(body) < _MIN_SIGNATURE_LEN:
            continue
        signature = _build_regex_from_body(body)
        if signature in seen:
            continue
        seen.add(signature)
        signatures.append(signature)
    return signatures


def _build_regex_from_body(body: str) -> str:
    """Tokenize body into LITERAL and PARAMETER chunks; escape literals,
    keep parameter chunks as regex. Conservative normalization:
    - multi-digit numbers → `\\d+`
    - 3-40 char quoted strings → `"[^"]*"` / `'[^']*'`
    """
    tokens: list[tuple[str, str]] = []  # (kind, text)
    remaining = body
    # Greedy alternation: find earliest match of ANY parameter pattern.
    while remaining:
        best_match: tuple[int, re.Match, str] | None = None
        for pat, regex_replacement in _PARAM_NORMALIZATION_PATTERNS:
            m = pat.search(remaining)
            if m is None:
                continue
            if best_match is None or m.start() < best_match[0]:
                best_match = (m.start(), m, regex_replacement)
        if best_match is None:
            tokens.append(("literal", remaining))
            break
        start, m, regex_replacement = best_match
        if start > 0:
            tokens.append(("literal", remaining[:start]))
        tokens.append(("regex", regex_replacement))
        remaining = remaining[m.end():]
    return "".join(
        re.escape(text) if kind == "literal" else text
        for kind, text in tokens
    )


def find_hits(
    signature: str,
    exclude_files: frozenset[str] = frozenset(),
    roots: tuple[Path, ...] | None = None,
    max_hits: int = 50,
) -> list[SiblingHit]:
    """Grep every `.py` file under the given roots for the signature.
    Excludes files in `exclude_files` (relative-to-BACKEND_ROOT paths)
    and caps at `max_hits`. `roots` defaults to module-level
    `_SEARCH_ROOTS` — resolved at call time, NOT at def time, so tests
    that monkeypatch `_SEARCH_ROOTS` take effect."""
    if not signature:
        return []
    if roots is None:
        roots = _SEARCH_ROOTS
    try:
        compiled = re.compile(signature)
    except re.error as exc:
        log.warning("sibling_hunt: bad regex %s: %s", signature[:60], exc)
        return []
    out: list[SiblingHit] = []
    for root in roots:
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            rel = str(py.relative_to(BACKEND_ROOT))
            if rel in exclude_files:
                continue
            try:
                text = py.read_text(errors="ignore")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    out.append(SiblingHit(
                        file=rel,
                        line=lineno,
                        matched_line=line.strip()[:200],
                        signature=signature,
                    ))
                    if len(out) >= max_hits:
                        return out
    return out


def scan_and_queue(db: Session, parent: BugFixCandidate) -> list[int]:
    """Run the full sibling-hunt phase for a just-applied candidate.

    Returns: list of child candidate IDs created (possibly empty).

    Safety:
      - No-op when feature flag off.
      - No-op when parent.source_type is already "sibling" (recursion
        guard).
      - Dedup against existing open/applied candidates with matching
        source_ref.
    """
    if not is_enabled():
        record_silent_return("sibling_hunt.disabled")
        return []
    if parent.source_type == "sibling":
        record_silent_return("sibling_hunt.recursion_guard")
        return []
    if not parent.patch_diff:
        record_silent_return("sibling_hunt.no_patch_diff")
        return []

    signatures = distill_signature(parent.patch_diff)
    if not signatures:
        record_silent_return("sibling_hunt.no_signatures")
        return []

    # Files touched by the parent are excluded — we already fixed them.
    exclude_files: set[str] = set()
    try:
        if parent.patch_files:
            exclude_files.update(json.loads(parent.patch_files))
    except Exception as exc:  # SILENT-EXCEPT-OK: malformed patch_files JSON falls back to empty exclude list; sibling hunt still runs (just won't skip the parent file)
        log.warning(
            "sibling_hunt: patch_files JSON parse failed for parent=%d: %s",
            parent.id, exc,
        )

    all_hits: list[SiblingHit] = []
    for sig in signatures:
        hits = find_hits(sig, exclude_files=frozenset(exclude_files))
        all_hits.extend(hits)
        if len(all_hits) >= _MAX_SIBLINGS_PER_PARENT:
            all_hits = all_hits[:_MAX_SIBLINGS_PER_PARENT]
            break

    if not all_hits:
        return []

    # Dedup against already-queued siblings + existing candidates with
    # same source_ref so repeated runs don't pile duplicates.
    existing_refs = {
        r[0] for r in db.query(BugFixCandidate.source_ref).filter(
            or_(
                BugFixCandidate.parent_candidate_id == parent.id,
                BugFixCandidate.source_type == "sibling",
            ),
        ).all()
    }

    created_ids: list[int] = []
    for hit in all_hits:
        source_ref = f"sibling:{parent.id}:{hit.file}:{hit.line}"
        if source_ref in existing_refs:
            continue
        child = BugFixCandidate(
            status="open",
            source_type="sibling",
            source_ref=source_ref,
            title=f"Sibling of #{parent.id}: same pattern in {hit.file}",
            summary=(
                f"Sibling-hunt match for parent candidate #{parent.id}.\n"
                f"Matched line: `{hit.matched_line}`\n"
                f"Signature: `{hit.signature[:160]}`"
            ),
            parent_candidate_id=parent.id,
            affected_domain=parent.affected_domain,
            evidence_source=parent.evidence_source or "pre_merchant",
        )
        db.add(child)
        db.flush()  # populate child.id
        created_ids.append(child.id)

    db.commit()
    log.info(
        "sibling_hunt: parent=%d created %d sibling(s)",
        parent.id, len(created_ids),
    )
    return created_ids


__all__ = [
    "is_enabled",
    "distill_signature",
    "find_hits",
    "scan_and_queue",
    "SiblingHit",
]
