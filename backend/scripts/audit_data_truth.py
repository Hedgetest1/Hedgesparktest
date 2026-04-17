#!/usr/bin/env python3
"""
audit_data_truth.py — CTO-grade data truth audit.

Encodes the bug-hunting patterns that manual code review finds:
  1. Money aggregations without currency filter
  2. Division without zero guard
  3. Hardcoded currency symbols ($, €, £)
  4. CVR/rate calculations from independent populations
  5. Statistical claims without significance tests
  6. Timezone-unsafe date bucketing

Each check is a grep-based pattern with known false-positive suppression.
Returns structured JSON results that the bugfix pipeline can consume.

Usage:
    ./venv/bin/python scripts/audit_data_truth.py          # human-readable
    ./venv/bin/python scripts/audit_data_truth.py --json   # machine-readable
    ./venv/bin/python scripts/audit_data_truth.py --strict # exit 1 if findings
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BACKEND_DIR / "app"
REPO_ROOT = BACKEND_DIR.parent
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"

# Known false positives (file:pattern pairs that have been manually verified)
_ALLOWLIST: set[str] = set()

# Files whose purpose IS currency/detection — scanning them is noise.
# These files are either:
#   - the centralized currency helper module (app/core/currency.py)
#   - regex-based PII / governance detection (must contain € and $ literals)
#   - a private _SYMBOLS mapper (the legitimate source of truth)
_FILE_ALLOWLIST: set[str] = {
    "app/core/currency.py",
    "app/services/response_guardrails.py",
    "app/services/evolution_bet_governance.py",
}

# Per-line allowlist for narrow, manually-verified false positives.
# Format: "<rel_path>:<lineno>" → one-line justification.
_LINE_ALLOWLIST: dict[str, str] = {
    # _SYMBOLS mapper — legacy local copy of the currency helper, safe.
    "app/services/revenue_triggers.py:271": "_SYMBOLS mapper dict is the currency source, not a hardcoded symbol",
    # LLM internal budget is €5/mo by policy; "€" is correct unit for LLM spend.
    "app/services/scaling_intelligence.py:368": "LLM budget is €-denominated by policy (CLAUDE.md §8.1)",
    "app/services/scaling_intelligence.py:369": "LLM budget is €-denominated by policy (CLAUDE.md §8.1)",
    # Shopify webhook payload already includes currency — this is the default
    # for a payload that explicitly lacks it, not a merchant-facing display.
    "app/services/order_ingestion.py:274": "Shopify webhook default — ingestion-layer safety net, not display",
    "app/services/pnl_engine.py:162": "Exception-path fallback after get_shop_currency() raises — defensive",
    "app/services/storefront_preview.py:156": "Pre-signup demo: currency unknown; narrative explicitly labels 'in your store's currency'",
    # ─── stats_claim_without_significance allowlist (manually verified) ──
    "app/api/playbook.py:219": "Peer-network aggregate: avg_lift computed from SQL AVG((treatment_cvr - control_cvr) / control_cvr); per-row holdout is by construction, per-row significance is upstream in autonomous_actions writes (nudge_measurement).",
    "app/api/playbook.py:220": "Peer-network aggregate: best_lift over holdout-measured rows — same source as avg_lift on line 219.",
    "app/services/conversion_service.py:144": "expected_uplift is a PREDICTED value from a heuristic model; the key name explicitly says 'expected' not 'measured'. Not a claim about post-hoc lift.",
    "app/services/share_engine.py:239": "share_engine consumes pre-measured holdout data from proof_engine — the message correctly says 'measured with holdout testing' because the lift is already holdout-measured by the time it reaches this file.",
    # ─── Pydantic default-value allowlist (response_model `currency: str = "USD"`) ───
    # These are default-currency literals on Pydantic response models.
    # They are NOT hardcoded display currencies — the service layer
    # overrides them with the shop's actual currency before serializing.
    # The default is a safety net for cases where the service fails to
    # resolve (e.g. brand-new merchant with no orders yet).
    "app/api/mta.py:46": "Pydantic response_model default; service overrides with shop currency",
    "app/api/mta.py:57": "Pydantic response_model default; service overrides with shop currency",
    "app/api/benchmarks.py:55": "Pydantic response_model default; service overrides with shop currency",
}

# Per-line fallbacks that are DEFENSIVE (e.g. `get_shop_currency(db, shop) or "USD"`).
# These are a safety net, not a hardcoded currency — the shop currency is
# resolved at runtime; the "USD" is only the last-resort fallback for a
# shop that has neither primary_currency nor order history.
_DEFENSIVE_FALLBACK_RE = re.compile(
    r'(get_shop_currency|_dominant_currency|payload\.get\(["\']currency["\']\))\s*\([^)]*\)\s*or\s*["\']USD["\']',
)

# Pydantic model field declarations like `currency: str = "USD"` are
# response-model DEFAULTS — the service layer always overrides them
# with the actual shop currency. Flagging these as hardcoded is a
# false positive (the default is a safety net for cold-start cases).
# Matches: `currency: str = "USD"` / `currency: str | None = "USD"` /
# `currency: Optional[str] = "USD"`.
_PYDANTIC_FIELD_DEFAULT_RE = re.compile(
    r'^\s*currency\s*:\s*[\w\[\]| ,]+\s*=\s*["\']USD["\']',
    re.IGNORECASE,
)

# Exception-handler fallback — `currency = "USD"` inside an `except`
# block is the catch-all when the real lookup raises. We look back
# a few lines to see if the enclosing context is an `except` handler.
_EXCEPTION_FALLBACK_LINE_RE = re.compile(
    r'^\s*currency\s*=\s*["\']USD["\']\s*$',
    re.IGNORECASE,
)


@dataclass
class Finding:
    check: str
    file: str
    line: int
    code: str
    severity: str  # "critical", "warning", "info"
    explanation: str


def _scan_files() -> list[tuple[Path, list[str]]]:
    """Load all .py files under app/ with their lines."""
    results = []
    for py in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        try:
            lines = py.read_text().splitlines()
            results.append((py, lines))
        except Exception:
            continue
    return results


def _allowlisted(filepath: str, lineno: int) -> bool:
    key = f"{filepath}:{lineno}"
    return key in _ALLOWLIST or key in _LINE_ALLOWLIST


# ---------------------------------------------------------------------------
# Check 1: Money aggregations without currency filter
# ---------------------------------------------------------------------------

_MONEY_AGG_RE = re.compile(
    r"SUM\s*\(\s*(total_price|amount|revenue|refund_amount|price|revenue_delta)",
    re.IGNORECASE,
)
# A legit currency control is a SQL or Python pattern, not the word
# "currency" in a comment or docstring. We accept any of:
#   - `:currency` bind param (parameterized filter)
#   - `currency =` / `currency IS NULL` / `currency IN (` (WHERE filter)
#   - `GROUP BY ... currency` (aggregation partitioned by currency)
#   - `SELECT currency` / `SELECT COALESCE(currency` (column in output)
#   - a Python kwarg literal like `"currency": currency`
# Comments are stripped BEFORE this check so the word "currency" in
# an explanatory comment ("# no currency filter yet") cannot mask a
# real regression (the pre-2026-04-17 behavior that this audit gate
# exists to prevent).
_CURRENCY_GUARD_RE = re.compile(
    r":currency\b|"
    r"\bcurrency\s*(=|IS\s+NULL|IS\s+NOT\s+NULL|IN\s*\()|"
    r"\bGROUP\s+BY\b[^\n]*\bcurrency\b|"
    r"\bSELECT\b[^\n]*\bcurrency\b|"
    r"COALESCE\s*\(\s*currency\b|"
    r"['\"]currency['\"]\s*:",
    re.IGNORECASE,
)


_SQL_CONTEXT_RE = re.compile(
    r"\b(SELECT|FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY)\b",
    re.IGNORECASE,
)


def check_money_aggregation(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Ignore comment descriptions that happen to mention SUM(...)
            if stripped.startswith(("#", "//", "*")):
                continue
            if _MONEY_AGG_RE.search(line) and not _CURRENCY_GUARD_RE.search(line):
                # Require a SQL keyword nearby. Without this, the audit
                # flags prose like "Returns: covered_revenue: SUM(price ×
                # quantity) over matched line items" inside docstrings.
                # Real SQL queries always have SELECT/FROM/WHERE within
                # a few lines of the SUM().
                narrow_ctx = "\n".join(lines[max(0, i - 6):i + 10])
                if not _SQL_CONTEXT_RE.search(narrow_ctx):
                    continue
                # Columns suffixed with _eur are already currency-normalized
                # (the ingestion pipeline converts to EUR at write-time via
                # `order_ingestion._normalize_to_eur`). Aggregating them is
                # SAFE because every row is already in the same unit.
                if re.search(r"SUM\s*\(\s*\w*_eur\b", line, re.IGNORECASE):
                    continue
                # Check the enclosing SQL block (25 lines before, 30 after).
                # A SUM() can be 20+ lines from its WHERE clause when the
                # query has CTEs, GROUP BY, ORDER BY, LIMIT, etc.
                #
                # Strip Python comments first — the word "currency" in
                # an explanatory comment ("# no currency filter yet")
                # previously produced a false negative. We only trust
                # SQL/kwarg patterns, not prose.
                raw = lines[max(0, i - 26):i + 30]
                stripped_ctx = [
                    re.sub(r"#.*$", "", ln) for ln in raw
                    if not ln.lstrip().startswith("#")
                ]
                context = "\n".join(stripped_ctx)
                if not _CURRENCY_GUARD_RE.search(context):
                    if not _allowlisted(rel, i):
                        findings.append(Finding(
                            check="money_aggregation_no_currency",
                            file=rel, line=i, code=stripped,
                            severity="critical",
                            explanation="SUM/AVG on money column without currency filter — "
                                        "multi-currency merchants will see mixed totals",
                        ))
    return findings


# ---------------------------------------------------------------------------
# Check 2: Hardcoded currency symbols
# ---------------------------------------------------------------------------

_HARDCODED_CURRENCY_RE = re.compile(
    r'''["']\$\d|["']€|["']£|currency\s*=\s*["']USD["']|["']USD["']\s*$''',
)


def check_hardcoded_currency(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        if rel in _FILE_ALLOWLIST:
            continue
        # Track triple-quote docstring regions so we skip description text.
        in_docstring = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            triple_count = stripped.count('"""') + stripped.count("'''")
            if triple_count == 1:
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if _HARDCODED_CURRENCY_RE.search(line):
                # Suppress known safe patterns
                if "no_data_response" in line or "placeholder" in line.lower():
                    continue
                if "test" in line.lower() or "example" in line.lower():
                    continue
                # Defensive "currency = ... or 'USD'" fallbacks are safe
                if _DEFENSIVE_FALLBACK_RE.search(line):
                    continue
                # Pydantic response-model field defaults (`currency: str = "USD"`)
                # are overridden by the service layer — not a hardcode.
                if _PYDANTIC_FIELD_DEFAULT_RE.match(line):
                    continue
                # Exception-handler fallback: `currency = "USD"` on its own
                # line AFTER an `except` clause within 4 lines is the
                # defensive catch-all for a failed get_shop_currency() call.
                if _EXCEPTION_FALLBACK_LINE_RE.match(line):
                    preceding = "\n".join(lines[max(0, i - 5):i])
                    if re.search(r"except\s+Exception", preceding):
                        continue
                if not _allowlisted(rel, i):
                    findings.append(Finding(
                        check="hardcoded_currency",
                        file=rel, line=i, code=stripped,
                        severity="warning",
                        explanation="Hardcoded currency symbol or 'USD' — "
                                    "EUR/GBP merchants will see wrong currency",
                    ))
    return findings


# ---------------------------------------------------------------------------
# Check 3: Unsafe timezone patterns (double AT TIME ZONE)
# ---------------------------------------------------------------------------

_DOUBLE_TZ_RE = re.compile(
    r"AT\s+TIME\s+ZONE\s+['\"]UTC['\"]\s+AT\s+TIME\s+ZONE",
    re.IGNORECASE,
)


def check_timezone_safety(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        for i, line in enumerate(lines, 1):
            if _DOUBLE_TZ_RE.search(line):
                if not _allowlisted(rel, i):
                    findings.append(Finding(
                        check="double_timezone_conversion",
                        file=rel, line=i, code=line.strip(),
                        severity="warning",
                        explanation="Double AT TIME ZONE conversion is DST-unsafe — "
                                    "use date_trunc('day', col AT TIME ZONE :tz) instead",
                    ))
    return findings


# ---------------------------------------------------------------------------
# Check 3b: Division by zero without guard
# ---------------------------------------------------------------------------
#
# Python's `/` raises ZeroDivisionError on 0; `//` same. A `/ count`
# or `/ len(rows)` without a prior guard silently crashes the endpoint
# when the denominator is 0 — and our endpoints often have `try/except
# Exception` wrappers that swallow the crash and return empty payloads.
#
# The pattern we catch: `<token> / count` / `<token> / len(...)` /
# `<token> / total` where the line has NO `if ... > 0` or `or 1` guard
# in the preceding 3 lines. False positives are acceptable — the fix
# is to add an explicit guard, which is free.

_DIV_SUSPECT_RE = re.compile(
    r"/\s*(count|total|n|len\([^)]+\)|len_[a-z_]+|size|sample_size)\b",
    re.IGNORECASE,
)
# A guard specifically on the denominator variable. We look for any of:
#   - `if <denom>` / `if <denom> > 0` / `if <denom> >= 1`
#   - `if not <denom>` / `if <denom> == 0`  (early-return pattern)
#   - `max(<denom>, 1)`
#   - `<denom> or 1` / `<denom> or 0.0001`
# The denominator name is captured from the division and interpolated
# into a guard regex so we only accept guards that actually cover THIS
# specific variable, not some unrelated `if other_var > 0`.
_DIV_GENERIC_GUARD_RE = re.compile(
    r"\bmax\s*\(\s*\w+\s*,\s*[01]\s*\)",
    re.IGNORECASE,
)


def check_division_by_zero(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        # Track triple-quote docstring regions so we skip prose mentions
        # of `x / total` inside module/class/function docstrings.
        in_docstring = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            triple_count = stripped.count('"""') + stripped.count("'''")
            if triple_count == 1:
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            # Skip comments
            if stripped.startswith(("#", "//", "*", ":param", ":return")):
                continue
            # Strip trailing inline comment — `# 0..1, fraction hits / total`
            # is prose even when the code before the # is valid Python.
            code_only = re.sub(r"\s+#.*$", "", line)
            if "/" not in code_only:
                continue
            m = _DIV_SUSPECT_RE.search(code_only)
            if not m:
                continue
            denom = m.group(1).lower()
            # Ternary guard on same line e.g. `x / n if n else 0`
            if re.search(r"\bif\s+\w+\b[^#\n]*\belse\b", code_only):
                continue
            # Preceding 25-line window: look for a guard specifically on
            # the denominator variable captured by the regex. Generic
            # early-return patterns count (`if not rows: return` guards
            # `total = len(rows)` at module scope).
            ctx = "\n".join(lines[max(0, i - 26):i])
            # Build a denom-aware guard pattern. For `len(rows)` the
            # denom is "len(rows)" — the inner variable is what we
            # actually need to check against.
            inner_var = denom
            len_match = re.match(r"len\(([^)]+)\)", denom)
            if len_match:
                inner_var = len_match.group(1).strip()
            denom_guards = [
                # `if <denom> == 0` / `if <denom> > 0` / `if total <= 0`
                # (also matches inside an `or` clause of a compound guard).
                rf"\b{re.escape(inner_var)}\s*(==|>=?|<=?|!=)\s*\d",
                # `if not <denom>` / `if <denom>:` / `if <denom> and ...`
                rf"\bif\s+not\s+{re.escape(inner_var)}\b",
                rf"\bif\s+{re.escape(inner_var)}\s*[=:><)\s]",
                rf"\b{re.escape(inner_var)}\s+or\s+[0-9]",
                # `total = <expr> or 1` — the LHS var is guarded by the
                # `or <truthy>` pattern on the RHS. Common Python idiom.
                rf"\b{re.escape(inner_var)}\s*=[^=\n]*\bor\s+[0-9]",
                # Also accept: `if not <plural-of-inner>` where inner is a
                # collection name (e.g. `if not rows` guards `len(rows)`)
            ]
            if any(re.search(p, ctx, re.IGNORECASE) for p in denom_guards):
                continue
            if _DIV_GENERIC_GUARD_RE.search(ctx):
                continue
            # Skip SQL string literals (NULLIF handles div-by-zero in SQL)
            if "NULLIF" in line.upper() or "nullif" in line.lower():
                continue
            if not _allowlisted(rel, i):
                findings.append(Finding(
                    check="division_by_zero_unguarded",
                    file=rel, line=i, code=stripped[:120],
                    severity="warning",
                    explanation=(
                        "Division by a potentially-zero denominator without a "
                        "preceding guard. Use `x / n if n else 0` or "
                        "`x / max(n, 1)` to avoid silent endpoint 500s."
                    ),
                ))
    return findings


# ---------------------------------------------------------------------------
# Check 3c: Statistical claims without significance test
# ---------------------------------------------------------------------------
#
# Any code that writes "lift_pct", "uplift", "+X%", "improvement" into
# a merchant-facing payload must back the claim with a significance
# test. The product promise (§0 CLAUDE.md) is "no false claims, ever"
# — every +€X recovered is holdout-measured with p<0.05. The audit
# catches string constructions that look like uplift claims in files
# that don't import a significance helper (z_test, chi_square, etc.).

_CLAIM_RE = re.compile(
    r'["\']\s*\+?\s*\{[^}]*lift|'
    r'["\']\s*\+\{[^}]*pct|'
    r'lift_pct.*:\s*round\(|'
    r'uplift.*:\s*round\(',
    re.IGNORECASE,
)
# Require ACTUAL use of a significance primitive — not just a prose
# mention in a docstring. Each pattern below is a concrete call site
# or import, so the word "significance" in a comment can't silence the
# audit.
_SIGNIFICANCE_TOKENS = re.compile(
    # Accept both `z_test(` and `_z_test(` (underscore prefix for
    # private helpers). Word boundary alone doesn't help because `_`
    # is a word character in regex — leading `\b` would NOT match
    # between `_` and `z`. We allow an optional `_` before the name.
    r"(?<![a-zA-Z])_?z_test\s*\(|"
    r"(?<![a-zA-Z])_?chi_square\s*\(|"
    r"\bp_value\s*[=<>]|"
    r"(?<![a-zA-Z])_?confidence_interval\s*\(|"
    r"(?<![a-zA-Z])_?margin_of_error\s*\(|"
    r"\bholdout_active\b|"
    r"\bsignificance\s*[:=]|"
    r"from\s+scipy\.stats|"
    r"import\s+statsmodels",
    re.IGNORECASE,
)


def check_stats_claims(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        # Fast path: if the whole file contains a significance token,
        # every claim inside it is presumed backed.
        whole = "\n".join(lines)
        if _SIGNIFICANCE_TOKENS.search(whole):
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", '"""', "'''")):
                continue
            if _CLAIM_RE.search(line):
                if not _allowlisted(rel, i):
                    findings.append(Finding(
                        check="stats_claim_without_significance",
                        file=rel, line=i, code=stripped[:120],
                        severity="warning",
                        explanation=(
                            "Merchant-facing claim (lift_pct/uplift) in a file "
                            "that doesn't import any significance primitive "
                            "(z_test/chi_square/p_value). §0 CLAUDE.md: no "
                            "false claims, every +€X recovered must be "
                            "holdout-measured with p<0.05."
                        ),
                    ))
    return findings


# ---------------------------------------------------------------------------
# Check 3d: CVR computed from independent populations
# ---------------------------------------------------------------------------
#
# A common silent bug: `cvr = purchases / visitors` where `purchases`
# comes from `shop_orders` (paid customers — may not even be tracked
# visitors) and `visitors` comes from `events` (anonymous). The two
# populations are disjoint, so dividing produces a number that has no
# real meaning. The correct shape is:
#   purchases-among-tracked-visitors / tracked-visitors
#
# Heuristic: flag any assignment like `cvr = ... purchases ... / ...
# visitors` where both source variables come from different SELECT
# statements (can't analyze statically; flag for manual review).

_CVR_PATTERN_RE = re.compile(
    r"(cvr|conversion_rate|conversion_pct)\s*=\s*[^=]*\b"
    r"(purchases|converted|buyers|customers)\b[^=]*/[^=]*\b"
    r"(visitors|sessions|unique_visitors)\b",
    re.IGNORECASE,
)
_CVR_SAFE_TOKEN_RE = re.compile(
    r"converted_visitors|tracked_visitors|visitor_purchase_sessions|"
    r"JOIN\s+[^\n]*visitor",
    re.IGNORECASE,
)


def check_cvr_independent_populations(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", '"""', "'''")):
                continue
            if _CVR_PATTERN_RE.search(line):
                # Skip when the same module uses the safe JOIN pattern
                ctx = "\n".join(lines[max(0, i - 20):i + 20])
                if _CVR_SAFE_TOKEN_RE.search(ctx):
                    continue
                if not _allowlisted(rel, i):
                    findings.append(Finding(
                        check="cvr_independent_populations",
                        file=rel, line=i, code=stripped[:120],
                        severity="warning",
                        explanation=(
                            "CVR computed from purchases / visitors where the "
                            "two populations may be disjoint (shop_orders has "
                            "buyers who may never have been tracked visitors). "
                            "Use converted_visitors from the events→vps→orders "
                            "JOIN so numerator and denominator share a cohort."
                        ),
                    ))
    return findings


# ---------------------------------------------------------------------------
# Check 4: Hardcoded DB credentials
# ---------------------------------------------------------------------------

_CRED_RE = re.compile(
    r'password\s*=\s*["\'][^"\']+["\']|'
    r'user\s*=\s*["\'][^"\']+["\'].*password',
    re.IGNORECASE,
)


def check_hardcoded_credentials(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel or ".env" in rel:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _CRED_RE.search(line):
                if "environ" in line or "os.getenv" in line or "config" in line.lower():
                    continue
                if not _allowlisted(rel, i):
                    findings.append(Finding(
                        check="hardcoded_credentials",
                        file=rel, line=i, code=stripped[:80] + "..." if len(stripped) > 80 else stripped,
                        severity="critical",
                        explanation="Hardcoded database credentials in source code",
                    ))
    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Check 5: Frontend hardcoded currency — TSX/TS files under dashboard/src/
# ---------------------------------------------------------------------------
#
# The backend data-truth audit is strict: currency drift on the server is
# blocked at commit time. But merchants see the dashboard, not the server.
# A card that hardcodes `€${amount}` ships wrong symbols to every non-EUR
# shop regardless of backend correctness. This check closes the gap.
#
# Pattern: look for €, £, ¥, $-prefixed money literals in .ts/.tsx under
# dashboard/src/. Exempt the central currency module itself (it IS the
# symbol table), FX-rate tables, fallback branches inside `catch` blocks
# in the central helper, and storybook/snapshot fixtures.
#
# Findings are WARNING severity (reportable but non-blocking) because
# pre-existing violations need migration one component at a time; making
# it critical would block every commit until the full sweep lands.

_FRONTEND_CURRENCY_RE = re.compile(r'[`"\']€|[`"\']£|[`"\']¥|\$\{')
_FRONTEND_SKIP_FILES: set[str] = {
    # The central currency module defines the symbol lookup — allowed.
    "app/lib/currency.ts",
    # Formatters without money rendering (other format helpers).
    "app/app/_lib/formatters.ts",
}


def _scan_frontend_files() -> list[tuple[Path, list[str]]]:
    """Load .ts/.tsx files under dashboard/src/ with their lines."""
    results = []
    if not DASHBOARD_SRC.exists():
        return results
    for ext in ("*.ts", "*.tsx"):
        for f in sorted(DASHBOARD_SRC.rglob(ext)):
            p = str(f)
            if "node_modules" in p or "_generated" in p or ".next" in p:
                continue
            try:
                lines = f.read_text().splitlines()
                results.append((f, lines))
            except Exception:
                continue
    return results


# Narrower money-literal regex. Three cases we want to flag:
#   1. A non-ASCII currency symbol (€, £, ¥) — almost certainly money
#      since none of these appear in UI text for non-money purposes.
#   2. A `"$N"` / `'$N'` literal where N is a digit — hardcoded dollar
#      amount outside a template literal.
#   3. A `` `$${expr}` `` pattern — double-dollar prefix in a template
#      literal is the idiom for a hardcoded $ sign followed by a variable.
# Template-literal variable interpolation `${foo}` without a currency
# prefix is NOT money and must NOT be flagged.
_FRONTEND_MONEY_LITERAL_RE = re.compile(
    r'€|£|¥|'
    r'["\']\$\d|'
    r'\$\$\{',
)


def check_frontend_hardcoded_currency(
    files: list[tuple[Path, list[str]]],
) -> list[Finding]:
    findings: list[Finding] = []
    for path, lines in files:
        rel = str(path.relative_to(DASHBOARD_SRC.parent))
        # Normalize windows-style separators just in case
        rel = rel.replace("\\", "/")
        # Strip leading "src/" so allowlist keys match the dashboard layout
        rel_short = rel[4:] if rel.startswith("src/") else rel
        if rel_short in _FRONTEND_SKIP_FILES:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip JS/TS comments + JSDoc lines
            if stripped.startswith(("//", "/*", "*")):
                continue
            # Skip import lines
            if stripped.startswith(("import ", "export ")) and "from " in stripped:
                continue
            if _FRONTEND_MONEY_LITERAL_RE.search(line):
                # Skip regex-like lines (they don't actually render)
                if "regex" in line.lower() or "/g" in line or "/i" in line:
                    continue
                # Skip lines that reference the central helper (they ARE
                # routing through formatDisplayMoney).
                if "formatDisplayMoney" in line or "createMoneyFormatter" in line:
                    continue
                findings.append(Finding(
                    check="frontend_hardcoded_currency",
                    file=f"dashboard/{rel}",
                    line=i,
                    code=stripped[:120],
                    severity="warning",
                    explanation=(
                        "Hardcoded currency literal in dashboard — non-EUR merchants "
                        "may see the wrong symbol. Route through "
                        "formatDisplayMoney() from lib/currency.ts instead."
                    ),
                ))
    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    ("Money aggregation without currency filter", check_money_aggregation),
    ("Hardcoded currency symbols", check_hardcoded_currency),
    ("DST-unsafe timezone patterns", check_timezone_safety),
    ("Hardcoded database credentials", check_hardcoded_credentials),
    ("Division by zero without guard", check_division_by_zero),
    ("Statistical claims without significance", check_stats_claims),
    ("CVR from independent populations", check_cvr_independent_populations),
]


def main():
    use_json = "--json" in sys.argv
    strict = "--strict" in sys.argv

    files = _scan_files()
    all_findings: list[Finding] = []

    for name, check_fn in ALL_CHECKS:
        findings = check_fn(files)
        all_findings.extend(findings)

    # Frontend sweep — separate file set. Findings are WARNING-level so
    # --strict does not block on pre-existing violations; the gate is
    # still observable (non-zero findings count) to drive migration.
    frontend_files = _scan_frontend_files()
    if frontend_files:
        all_findings.extend(check_frontend_hardcoded_currency(frontend_files))

    if use_json:
        print(json.dumps([asdict(f) for f in all_findings], indent=2))
    else:
        if not all_findings:
            print("✅ No data truth issues found")
        else:
            critical = [f for f in all_findings if f.severity == "critical"]
            warnings = [f for f in all_findings if f.severity == "warning"]
            print(f"\n{'🔴' if critical else '🟡'} {len(all_findings)} finding(s): "
                  f"{len(critical)} critical, {len(warnings)} warning\n")
            for f in all_findings:
                icon = "🔴" if f.severity == "critical" else "🟡"
                print(f"  {icon} {f.file}:{f.line}")
                print(f"    {f.check}: {f.explanation}")
                print(f"    Code: {f.code[:100]}")
                print()

    if strict and any(f.severity == "critical" for f in all_findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
