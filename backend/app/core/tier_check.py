"""
tier_check.py — Execution policy enforcement for autonomous agents.

Given a list of changed file paths, returns the required execution tier
(TIER_0, TIER_1, TIER_2) and blocking reasons.

This is the programmatic enforcement of EXECUTION_POLICY.md.

Public interface:
    check_tier(files) -> TierResult
    enforce_pre_apply(files, patch_diff) -> EnforceResult
    require_frontend_build(files) -> bool
    require_tracker_bump(files) -> bool
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("tier_check")

# ---------------------------------------------------------------------------
# Tier constants
# ---------------------------------------------------------------------------

TIER_0 = 0  # Autonomous — agent may apply with tests passing
TIER_1 = 1  # Propose only — human must approve before apply
TIER_2 = 2  # Human-only — agent must never modify

TIER_LABELS = {TIER_0: "TIER_0", TIER_1: "TIER_1", TIER_2: "TIER_2"}

# ---------------------------------------------------------------------------
# File-to-tier mapping — derived from EXECUTION_POLICY.md
# ---------------------------------------------------------------------------

# TIER_2: absolute protection — agent must never modify
# This is the SINGLE SOURCE OF TRUTH for protected file patterns.
# Other modules (evolution_engine, bugfix_pipeline) import from here.
_TIER_2_PATTERNS: list[str] = [
    "app/core/token_crypto",
    "app/core/merchant_session",
    "app/core/deps.py",
    "app/api/shopify_oauth",
    "app/api/billing",
    "app/api/webhooks.py",
    "app/services/shopify_auth",
    "app/services/order_ingestion",
    "app/services/gdpr_processor",
    "migrations/versions/",
    "migrations/env.py",
    ".env",
    "deploy.sh",
    "EXECUTION_POLICY.md",
]

# SCAN_FORBIDDEN_PATTERNS: used by evolution scanners to skip files that agents
# should never propose changes to. Superset of TIER_2 + governance TIER_1 files.
SCAN_FORBIDDEN_PATTERNS: list[str] = _TIER_2_PATTERNS + [
    "dashboard/",
    "app/services/orchestrator.py",
    "app/models/action_approval",
    "app/services/email_templates.py",
    "app/services/email_orchestrator.py",
    "app/services/email_governance.py",
    "app/services/brand_voice.py",
    "app/core/email.py",
]


def is_forbidden_path(path: str) -> bool:
    """Check if a path matches any forbidden pattern. Used by evolution scanners."""
    normalized = path.lstrip("/")
    for prefix in ("opt/wishspark/backend/", "opt/wishspark/", "backend/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return any(_path_matches_pattern(normalized, p) for p in SCAN_FORBIDDEN_PATTERNS)

# TIER_1: propose only — human approves before apply
_TIER_1_PATTERNS: list[str] = [
    "tracker/",
    "app/services/orchestrator",
    "app/services/bugfix_pipeline",
    "app/services/promotion_pipeline",
    "app/services/evolution_engine",
    "app/services/evolution_converter",
    "app/services/reviewer_layer",
    "app/services/project_brain",
    "app/services/meta_reviewer",
    "app/services/merge_intelligence",
    "app/core/llm_budget",
    "app/core/llm_router",
    "app/core/ai_router",
    "app/core/rate_limit",
    "app/core/alert_delivery",
    "app/core/tier_check",       # self-protection
    "app/core/file_lock",        # self-protection
    "app/core/pre_apply_guard",  # self-protection
    "app/models/",
    "ecosystem.config.js",
]

# TIER_0: everything else in safe zones (with tests passing)
# Explicit safe prefixes for positive matching
_TIER_0_PREFIXES: list[str] = [
    "tests/",
    "docs/",
    "app/services/",
    "app/api/",
    "app/workers/",
    "dashboard/src/",
    "dashboard/public/",
]

# ---------------------------------------------------------------------------
# Dangerous diff patterns — force TIER_2 regardless of file path
# ---------------------------------------------------------------------------

_DANGEROUS_DIFF_PATTERNS: list[str] = [
    "subprocess.call",
    "os.system(",
    "eval(",
    "exec(",
    "__import__(",
    "MERCHANT_TOKEN_ENCRYPTION_KEY",
    "SHOPIFY_API_SECRET",
    "DASHBOARD_API_KEY",
    "SESSION_SECRET",
    "SENTRY_DSN",
    "drop table",
    "truncate table",
    "alter table.*drop",
    "delete from.*where 1",
    "git push --force",
    "git reset --hard",
    "rm -rf",
]

# Maximum files for TIER_0 autonomous apply
MAX_TIER_0_FILES = 5

# Maximum changed lines for TIER_0
MAX_TIER_0_DIFF_LINES = 120


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TierResult:
    """Result of a tier check on a set of files."""
    tier: int
    label: str
    reasons: list[str] = field(default_factory=list)
    affected_domains: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None

    @property
    def is_autonomous(self) -> bool:
        return self.tier == TIER_0 and not self.blocked

    @property
    def requires_approval(self) -> bool:
        return self.tier >= TIER_1

    @property
    def is_forbidden(self) -> bool:
        return self.tier == TIER_2


@dataclass
class EnforceResult:
    """Result of pre-apply enforcement check."""
    allowed: bool
    tier: int
    label: str
    reasons: list[str] = field(default_factory=list)
    requires_frontend_build: bool = False
    requires_tracker_bump: bool = False
    blocked: bool = False
    block_reason: str | None = None


# ---------------------------------------------------------------------------
# Core tier classification
# ---------------------------------------------------------------------------

def _path_matches_pattern(path: str, pattern: str) -> bool:
    """
    Secure path matching — prevents substring bypass attacks.

    Rules:
        - If pattern ends with '/' (directory), use startswith
        - If pattern contains '.', it targets a specific file — match as prefix
          (e.g., "app/core/deps.py" matches exactly, not "app/core/new_deps.py")
        - Otherwise, match as path prefix with boundary check
          (e.g., "app/services/orchestrator" matches "app/services/orchestrator.py"
           and "app/services/orchestrator_core.py" but NOT
           "xapp/services/orchestrator")
    """
    if pattern.endswith("/"):
        # Directory pattern: path must start with it
        return path.startswith(pattern)

    if pattern.endswith(".py") or pattern.endswith(".js") or pattern.endswith(".md") or pattern.endswith(".sh"):
        # Exact file pattern: must match exactly
        return path == pattern

    # Prefix pattern (e.g., "app/core/token_crypto"): path must start with it
    return path.startswith(pattern)


def _classify_file(path: str) -> tuple[int, str]:
    """
    Classify a single file path into its tier.
    Returns (tier, reason).
    """
    # Normalize path — strip leading slashes and common prefixes
    path = path.lstrip("/")
    for prefix in ("opt/wishspark/backend/", "opt/wishspark/", "backend/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    # Check TIER_2 first (highest priority)
    for pattern in _TIER_2_PATTERNS:
        if _path_matches_pattern(path, pattern):
            return TIER_2, f"TIER_2: {path} matches protected pattern '{pattern}'"

    # Check TIER_1
    for pattern in _TIER_1_PATTERNS:
        if _path_matches_pattern(path, pattern):
            return TIER_1, f"TIER_1: {path} matches approval-required pattern '{pattern}'"

    # Check explicit TIER_0 prefixes
    for prefix in _TIER_0_PREFIXES:
        if path.startswith(prefix):
            return TIER_0, f"TIER_0: {path} in safe zone '{prefix}'"

    # Unknown files default to TIER_1 (safe default: propose, don't apply)
    return TIER_1, f"TIER_1: {path} not in any known safe zone — default to approval required"


def _classify_diff(patch_diff: str | None) -> tuple[int, list[str]]:
    """
    Scan diff content for dangerous patterns.
    Returns (tier, reasons).
    """
    if not patch_diff:
        return TIER_0, []

    reasons = []
    diff_lower = patch_diff.lower()

    for pattern in _DANGEROUS_DIFF_PATTERNS:
        try:
            if re.search(pattern, diff_lower, re.IGNORECASE):
                reasons.append(f"TIER_2: dangerous pattern in diff: '{pattern}'")
        except re.error:
            # Fallback to simple substring match if regex fails
            if pattern.lower() in diff_lower:
                reasons.append(f"TIER_2: dangerous pattern in diff: '{pattern}'")

    if reasons:
        return TIER_2, reasons
    return TIER_0, []


def _get_domains(files: list[str]) -> list[str]:
    """Get the set of affected domains using project_brain's classifier."""
    domains = set()
    try:
        from app.services.project_brain import classify_file as brain_classify
        for f in files:
            result = brain_classify(f)
            domains.add(result["domain"])
    except ImportError:
        # Fallback if project_brain not available (e.g., in tests)
        pass
    return sorted(domains)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check_tier(files: list[str]) -> TierResult:
    """
    Given a list of changed file paths, return the required execution tier.

    Rules:
        - Highest tier among all files wins
        - 3+ domains → TIER_1 minimum (cross-domain escalation)
        - >5 files → TIER_1 minimum (blast radius escalation)

    Args:
        files: List of file paths (relative or absolute)

    Returns:
        TierResult with tier, reasons, and blocking info
    """
    if not files:
        return TierResult(tier=TIER_0, label="TIER_0", reasons=["no_files"])

    max_tier = TIER_0
    reasons = []

    for f in files:
        tier, reason = _classify_file(f)
        reasons.append(reason)
        if tier > max_tier:
            max_tier = tier

    # Cross-domain escalation: 3+ domains → TIER_1 minimum
    domains = _get_domains(files)
    if len(domains) >= 3 and max_tier < TIER_1:
        max_tier = TIER_1
        reasons.append(f"TIER_1: cross-domain escalation — {len(domains)} domains affected: {', '.join(domains)}")

    # Blast radius escalation: >5 files → TIER_1 minimum
    if len(files) > MAX_TIER_0_FILES and max_tier < TIER_1:
        max_tier = TIER_1
        reasons.append(f"TIER_1: blast radius escalation — {len(files)} files (max {MAX_TIER_0_FILES} for TIER_0)")

    result = TierResult(
        tier=max_tier,
        label=TIER_LABELS[max_tier],
        reasons=reasons,
        affected_domains=domains,
    )

    # TIER_2 is always blocked for agents
    if max_tier == TIER_2:
        result.blocked = True
        tier2_files = [f for f in files if _classify_file(f)[0] == TIER_2]
        result.block_reason = f"TIER_2 files detected — agent must not modify: {', '.join(tier2_files[:5])}"

    return result


def enforce_pre_apply(
    files: list[str],
    patch_diff: str | None = None,
) -> EnforceResult:
    """
    Full pre-apply enforcement check. Call this before writing any patch to disk.

    Checks:
        1. File tier classification
        2. Diff content scanning for dangerous patterns
        3. Blast radius limits
        4. Cross-domain escalation
        5. Frontend build requirement
        6. Tracker version bump requirement

    Args:
        files: List of file paths the patch will modify
        patch_diff: The actual diff content (optional, for content scanning)

    Returns:
        EnforceResult with allowed/blocked status and requirements
    """
    # Step 1: File-based tier check
    tier_result = check_tier(files)
    max_tier = tier_result.tier
    reasons = list(tier_result.reasons)

    # Step 2: Diff content scanning
    if patch_diff:
        diff_tier, diff_reasons = _classify_diff(patch_diff)
        if diff_tier > max_tier:
            max_tier = diff_tier
        reasons.extend(diff_reasons)

        # Check diff size for TIER_0
        if max_tier == TIER_0:
            diff_lines = len([
                line for line in patch_diff.split("\n")
                if line.startswith("+") or line.startswith("-")
            ])
            if diff_lines > MAX_TIER_0_DIFF_LINES:
                max_tier = TIER_1
                reasons.append(
                    f"TIER_1: diff too large for TIER_0 — {diff_lines} lines "
                    f"(max {MAX_TIER_0_DIFF_LINES})"
                )

    # Step 3: Determine requirements
    needs_frontend_build = require_frontend_build(files)
    needs_tracker_bump = require_tracker_bump(files)

    # Build result
    allowed = max_tier == TIER_0
    blocked = max_tier == TIER_2

    result = EnforceResult(
        allowed=allowed,
        tier=max_tier,
        label=TIER_LABELS[max_tier],
        reasons=reasons,
        requires_frontend_build=needs_frontend_build,
        requires_tracker_bump=needs_tracker_bump,
        blocked=blocked,
    )

    if blocked:
        tier2_files = [f for f in files if _classify_file(f)[0] == TIER_2]
        result.block_reason = (
            f"BLOCKED: TIER_2 files detected — agent must not modify: "
            f"{', '.join(tier2_files[:5])}"
        )

    return result


def require_frontend_build(files: list[str]) -> bool:
    """Check if any changed file requires a frontend build verification."""
    for f in files:
        normalized = f.lstrip("/")
        for prefix in ("/opt/wishspark/", ""):
            if normalized.startswith(prefix + "dashboard/"):
                return True
            cleaned = normalized
            for p in ("/opt/wishspark/backend/", "/opt/wishspark/", "backend/"):
                if cleaned.startswith(p):
                    cleaned = cleaned[len(p):]
            if cleaned.startswith("dashboard/"):
                return True
    return False


def require_tracker_bump(files: list[str]) -> bool:
    """Check if any changed file requires a TRACKER_VERSION bump."""
    for f in files:
        normalized = f.lstrip("/")
        for prefix in ("/opt/wishspark/", ""):
            path = normalized[len(prefix):] if normalized.startswith(prefix) else normalized
            if path.startswith("tracker/") and path.endswith(".js"):
                return True
    return False


# ---------------------------------------------------------------------------
# Convenience: check from patch_files JSON (bugfix_pipeline format)
# ---------------------------------------------------------------------------

def check_tier_from_json(patch_files_json: str | None) -> TierResult:
    """
    Convenience wrapper for bugfix_pipeline integration.
    Accepts the JSON-encoded file list stored on BugFixCandidate.patch_files.
    """
    if not patch_files_json:
        return TierResult(tier=TIER_1, label="TIER_1", reasons=["no_patch_files"])
    try:
        files = json.loads(patch_files_json)
        if not isinstance(files, list):
            return TierResult(tier=TIER_1, label="TIER_1", reasons=["invalid_files_format"])
        return check_tier(files)
    except (json.JSONDecodeError, ValueError):
        return TierResult(tier=TIER_1, label="TIER_1", reasons=["invalid_json"])


def enforce_from_candidate(patch_files_json: str | None, patch_diff: str | None) -> EnforceResult:
    """
    Convenience wrapper for bugfix_pipeline integration.
    Accepts the fields from a BugFixCandidate row.
    """
    files = []
    if patch_files_json:
        try:
            files = json.loads(patch_files_json)
            if not isinstance(files, list):
                files = []
        except (json.JSONDecodeError, ValueError):
            pass
    return enforce_pre_apply(files, patch_diff)
