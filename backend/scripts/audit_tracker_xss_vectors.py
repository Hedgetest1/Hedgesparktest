#!/usr/bin/env python
"""
audit_tracker_xss_vectors.py — preflight invariant.

Catches XSS vectors in storefront tracker JS (`tracker/*.js`). Tracker
runs inside merchant browsers across thousands of stores — a single
unsafe pattern is catastrophic class-wide.

Why it's a bug class
--------------------
HedgeSpark's storefront tracker is HedgeSpark-controlled JavaScript
embedded via `<script src=".../tracker.js">` on millions of merchant
storefronts. Any of these patterns enables a tracker→storefront XSS:

  - eval(<dynamic>)            — arbitrary code execution
  - new Function(<dynamic>)    — same threat as eval
  - setTimeout(<string>, ...)  — string-form executes via eval
  - setInterval(<string>, ...) — same
  - innerHTML = <dynamic>      — DOM-based XSS if dynamic includes user input
  - document.write(<dynamic>)  — DOM-based XSS
  - outerHTML = <dynamic>      — DOM-based XSS

Static literals (`setTimeout(myFunc, 100)` with named function ref)
are fine. DYNAMIC strings are the threat.

What this audits
----------------
Walks `tracker/*.js` and flags every match of the dangerous patterns.
Skips:
  - Comments (// or /* ... */)
  - String literals containing "eval" as a label, like
    `errorType: 'eval_failed'` (matched by surrounding context)
  - innerHTML/outerHTML/document.write only when assigned a
    static string literal (no template literals, no concatenation)

Exempt-list: documented safe usages with `// XSS-AUDIT-OK: <reason>`
inline comment.

Usage
-----
    ./venv/bin/python scripts/audit_tracker_xss_vectors.py
    ./venv/bin/python scripts/audit_tracker_xss_vectors.py --json
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def deco(fn):
            return fn
        return deco


REPO_ROOT = pathlib.Path("/opt/wishspark")
TRACKER_DIR = REPO_ROOT / "tracker"

# Strict patterns — every match is a finding unless XSS-AUDIT-OK on same line.
_DANGEROUS_PATTERNS = [
    # eval(...) — any usage with non-trivial input.
    (re.compile(r"""\beval\s*\("""), "eval() — arbitrary code execution vector"),
    # new Function(...) — same threat.
    (re.compile(r"""\bnew\s+Function\s*\("""), "new Function() — arbitrary code execution"),
    # setTimeout/setInterval with STRING first arg (not a function ref).
    (re.compile(r"""\bsetTimeout\s*\(\s*['"`]"""), "setTimeout(string) — executes via eval"),
    (re.compile(r"""\bsetInterval\s*\(\s*['"`]"""), "setInterval(string) — executes via eval"),
    # document.write / document.writeln — DOM-based XSS class.
    (re.compile(r"""\bdocument\.writeln?\s*\("""), "document.write — DOM-based XSS vector"),
]

# innerHTML/outerHTML — flag only if RHS is a template literal OR concat.
# Static-string assignment (innerHTML = '<div>x</div>') is acceptable —
# the developer's literal output, no user input. Dynamic content is the
# threat (innerHTML = `<div>${userInput}</div>`).
_INNER_OUTER_RE = re.compile(
    r"""(?P<lhs>(?:inner|outer)HTML)\s*=\s*(?P<rhs>[^;\n]+)""",
)
_DYNAMIC_RHS_RE = re.compile(
    r"""(?:`[^`]*\$\{|`[^`]*`\s*\+|[A-Za-z_][A-Za-z0-9_]*\s*\+)"""
)

# Exempt marker — same line OR comment block immediately before.
_EXEMPT_RE = re.compile(r"""//\s*XSS-AUDIT-OK\s*:""")
# Skip comment lines + multi-line comment blocks.
_LINE_COMMENT_RE = re.compile(r"""^\s*//""")


def _line_is_exempt(text: str, line_idx: int) -> bool:
    lines = text.splitlines()
    if line_idx >= len(lines):
        return False
    if _EXEMPT_RE.search(lines[line_idx]):
        return True
    # Check line above.
    if line_idx > 0 and _EXEMPT_RE.search(lines[line_idx - 1]):
        return True
    return False


def _is_in_string_literal(text: str, pos: int) -> bool:
    """Quick heuristic — is pos inside a string literal? (Naive — checks
    if the character is preceded by an unescaped quote on the same line
    that hasn't been closed.)"""
    line_start = text.rfind("\n", 0, pos) + 1
    line_chunk = text[line_start:pos]
    # Count unescaped quotes of each type on this line up to `pos`.
    for q in ('"', "'", "`"):
        cnt = 0
        i = 0
        while i < len(line_chunk):
            if line_chunk[i] == "\\" and i + 1 < len(line_chunk):
                i += 2
                continue
            if line_chunk[i] == q:
                cnt += 1
            i += 1
        if cnt % 2 == 1:
            return True
    return False


@telemetered("audit_tracker_xss_vectors")
def audit() -> int:
    findings: list[dict] = []
    for js_file in TRACKER_DIR.glob("*.js"):
        try:
            text = js_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Strict patterns
        for pattern, msg in _DANGEROUS_PATTERNS:
            for m in pattern.finditer(text):
                lineno = text[: m.start()].count("\n") + 1
                if _is_in_string_literal(text, m.start()):
                    continue
                if _line_is_exempt(text, lineno - 1):
                    continue
                # Skip line comments
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_to_match = text[line_start:m.start()]
                if "//" in line_to_match.split('"')[0].split("'")[0]:
                    continue
                findings.append({
                    "file": str(js_file.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "vector": msg,
                    "code": text.splitlines()[lineno - 1].strip()[:120],
                })
        # innerHTML/outerHTML — flag only if dynamic
        for m in _INNER_OUTER_RE.finditer(text):
            rhs = m.group("rhs").strip()
            # Skip pure static strings: 'foo' or "foo" or `foo` (no ${})
            if (
                (rhs.startswith("'") and rhs.endswith("'") and "${" not in rhs)
                or (rhs.startswith('"') and rhs.endswith('"') and "${" not in rhs)
                or (rhs.startswith("`") and rhs.endswith("`") and "${" not in rhs)
            ):
                continue
            # If RHS has dynamic markers (${, +, var ref) → flag
            if _DYNAMIC_RHS_RE.search(rhs) or "${" in rhs:
                lineno = text[: m.start()].count("\n") + 1
                if _line_is_exempt(text, lineno - 1):
                    continue
                findings.append({
                    "file": str(js_file.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "vector": f"{m.group('lhs')} = <dynamic> — DOM-based XSS",
                    "code": text.splitlines()[lineno - 1].strip()[:120],
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print(f"✓ no XSS vectors in tracker/*.js")
            return 0
        print(f"✗ {len(findings)} XSS vector(s) in storefront tracker JS:")
        for f in findings:
            print(f"  • {f['file']}:{f['line']}  [{f['vector']}]")
            print(f"    {f['code']}")
        print()
        print("Tracker JS executes inside MERCHANT browsers across thousands of")
        print("stores — XSS = catastrophic class-wide. Replace dynamic eval/Function/")
        print("setTimeout-string with named-function refs. Replace innerHTML=<dynamic>")
        print("with textContent or createElement+appendChild. If genuinely safe (e.g.,")
        print("HTML you generate from your own escaped helper), add inline comment:")
        print('    // XSS-AUDIT-OK: <reason>')

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
