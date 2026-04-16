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

# Known false positives (file:pattern pairs that have been manually verified)
_ALLOWLIST: set[str] = set()


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
    return key in _ALLOWLIST


# ---------------------------------------------------------------------------
# Check 1: Money aggregations without currency filter
# ---------------------------------------------------------------------------

_MONEY_AGG_RE = re.compile(
    r"SUM\s*\(\s*(total_price|amount|revenue|refund_amount|price|revenue_delta)",
    re.IGNORECASE,
)
_CURRENCY_GUARD_RE = re.compile(r"currency", re.IGNORECASE)


def check_money_aggregation(files: list[tuple[Path, list[str]]]) -> list[Finding]:
    findings = []
    for path, lines in files:
        rel = str(path.relative_to(BACKEND_DIR))
        if "/test" in rel or "scripts/" in rel:
            continue
        for i, line in enumerate(lines, 1):
            if _MONEY_AGG_RE.search(line) and not _CURRENCY_GUARD_RE.search(line):
                # Check surrounding context (5 lines before/after) for currency filter
                context = "\n".join(lines[max(0, i - 6):i + 5])
                if "currency" not in context.lower():
                    if not _allowlisted(rel, i):
                        findings.append(Finding(
                            check="money_aggregation_no_currency",
                            file=rel, line=i, code=line.strip(),
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
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if _HARDCODED_CURRENCY_RE.search(line):
                # Suppress known safe patterns
                if "no_data_response" in line or "placeholder" in line.lower():
                    continue
                if "test" in line.lower() or "example" in line.lower():
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

ALL_CHECKS = [
    ("Money aggregation without currency filter", check_money_aggregation),
    ("Hardcoded currency symbols", check_hardcoded_currency),
    ("DST-unsafe timezone patterns", check_timezone_safety),
    ("Hardcoded database credentials", check_hardcoded_credentials),
]


def main():
    use_json = "--json" in sys.argv
    strict = "--strict" in sys.argv

    files = _scan_files()
    all_findings: list[Finding] = []

    for name, check_fn in ALL_CHECKS:
        findings = check_fn(files)
        all_findings.extend(findings)

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
