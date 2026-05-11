#!/usr/bin/env python3
"""classify_commit_tier.py — classify a commit's diff as TIER_0/1/2.

Mirrors the safety model in CLAUDE.md §10. A commit is classified by
the HIGHEST tier of any file it touches:
- TIER_2: governance files (billing, oauth, token crypto, migrations,
  env, deploy.sh, webhooks, etc.) — never auto-deploy
- TIER_1: self-modification pipeline files + models + tracker JS +
  LLM infra + multi-file refactors (≥6 files)
- TIER_0: everything else — safe for auto-deploy under the Phase 2.0
  elite-stack gate. Phase 1.9.5 wiring auto-deploys TIER_0 only.

Usage:
    ./classify_commit_tier.py                    # classify HEAD commit
    ./classify_commit_tier.py <sha>              # classify specific commit
    ./classify_commit_tier.py --files a.py b.py  # classify a file list

Exit codes (for scripting):
    0   TIER_0 (safe for auto-deploy)
    1   TIER_1 (requires human approval)
    2   TIER_2 (governance, never auto)
    3   script error
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# TIER_2 paths per CLAUDE.md §10. Exact file matches + glob patterns.
# Any file matching any of these = TIER_2.
TIER_2_PATHS = [
    "backend/app/core/token_crypto.py",
    "backend/app/core/merchant_session.py",
    "backend/app/api/shopify_oauth.py",
    "backend/app/api/billing.py",
    "backend/app/core/deps.py",
    "backend/app/api/webhooks.py",
    "backend/app/services/order_ingestion.py",
    "backend/app/services/gdpr_processor.py",
    "ecosystem.config.js",
    ".env",
]
TIER_2_GLOBS = [
    re.compile(r"^migrations/"),
    re.compile(r"^backend/migrations/"),
    re.compile(r"/deploy\.sh$"),
    re.compile(r"^\.env\."),  # .env.production etc.
]

TIER_1_PATHS = [
    "backend/app/services/orchestrator.py",
    "backend/app/services/bugfix_pipeline.py",
    "backend/app/services/promotion_pipeline.py",
    "backend/app/services/reviewer_layer.py",
    "backend/app/services/project_brain.py",
    "backend/app/core/llm_budget.py",
    "backend/app/core/llm_router.py",
]
TIER_1_GLOBS = [
    re.compile(r"^tracker/.*\.js$"),
    re.compile(r"^backend/app/services/orchestrator.*\.py$"),
    re.compile(r"^backend/app/models/"),
]


def classify_path(path: str) -> int:
    """Return the tier (0, 1, 2) for a single path."""
    normalized = path.lstrip("./")
    if normalized in TIER_2_PATHS:
        return 2
    for pat in TIER_2_GLOBS:
        if pat.search(normalized):
            return 2
    if normalized in TIER_1_PATHS:
        return 1
    for pat in TIER_1_GLOBS:
        if pat.search(normalized):
            return 1
    return 0


def changed_files_in_commit(sha: str = "HEAD") -> list[str]:
    """Return the list of files touched by `sha` via `git show --name-only`."""
    out = subprocess.run(
        ["git", "show", "--name-only", "--format=", sha],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def classify_files(paths: list[str]) -> tuple[int, str]:
    """Classify a set of paths. Return (tier, reason)."""
    if not paths:
        return 0, "no files"

    highest = 0
    reasons: list[str] = []
    for p in paths:
        t = classify_path(p)
        if t > highest:
            highest = t
        if t > 0:
            reasons.append(f"  {p} → TIER_{t}")

    # CLAUDE.md §10 also marks "Multi-file refactors (6+ files)" as TIER_1.
    # If after path classification we're still TIER_0 but diff is large,
    # upgrade to TIER_1 as a conservative bound.
    if highest == 0 and len(paths) >= 6:
        highest = 1
        reasons.append(f"  {len(paths)} files touched — multi-file refactor → TIER_1")

    if highest == 0:
        return 0, f"all {len(paths)} file(s) are TIER_0"
    return highest, "\n".join(reasons)


def main(argv: list[str]) -> int:
    if "--files" in argv:
        idx = argv.index("--files")
        paths = argv[idx + 1:]
    elif len(argv) >= 1 and not argv[0].startswith("-"):
        paths = changed_files_in_commit(argv[0])
    else:
        paths = changed_files_in_commit("HEAD")

    if not paths:
        print("classify_commit_tier: no files to classify", file=sys.stderr)
        return 3

    tier, reason = classify_files(paths)
    # `flush=True` guarantees the TIER line lands on stdout BEFORE any
    # stderr writes — critical for downstream `head -1` parsers under
    # `2>&1` redirection (Python's stdout is block-buffered when not a
    # TTY; without flush, stderr lines can interleave first). Born
    # 2026-05-11 Senior+++ close after post_commit_auto_deploy.sh
    # captured stderr-first output and "fell back to manual deploy"
    # on a multi-tier commit.
    print(f"TIER_{tier}", flush=True)
    if tier > 0:
        print(reason, file=sys.stderr, flush=True)
    return tier


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"classify_commit_tier: script error — {exc}", file=sys.stderr)
        sys.exit(3)
