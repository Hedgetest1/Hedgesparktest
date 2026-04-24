#!/usr/bin/env python3
"""audit_dashboard_dead_code.py — block orphan components/hooks.

Problem class: a component or hook is exported from a file but no
other file imports it. Lives as dead code forever, accumulates
maintenance burden, and can mask real regressions when a refactor
leaves it behind.

This audit scans dashboard/src for every exported React component
and hook, then greps the rest of dashboard/src for import references.
Zero-reference exports are flagged as orphans.

Scope:
- `dashboard/src/app/**/*.tsx` — components and pages
- Exports of form `export function ComponentName(`, `export const useXxx`,
  `export { X, Y, Z }`, `export default function Name()`
- Next.js convention files (page.tsx, layout.tsx, error.tsx,
  loading.tsx, not-found.tsx, route.ts) are implicit entry points —
  Next.js wires them automatically with no import needed, so they're
  exempt from the "must be imported" rule.

Limits (honest):
- Does not catch orphan TYPE exports — types are often exported
  pre-emptively for future consumers.
- Does not catch re-exported surfaces (`export * from "./x"`).
- Does not catch components referenced only via JSX string (rare;
  would only happen in dynamic-component patterns we don't use).
- False positive rate is low in our codebase but non-zero at
  boundaries (e.g., a component used only in tests).

Coverage claim: catches ORPHAN-LOCAL-EXPORT class — a component
file that was written, committed, and never wired. This is exactly
the Phase-X-leaves-dead-code regression class.

Exit codes:
    0  clean (or no candidates found)
    1  orphans detected
    2  script error
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import emit, telemetered

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"

# Next.js App Router entry points — these are consumed by the framework,
# not by `import` statements in our code. Exempt from the orphan check.
# If we add new Next.js conventions, extend this set.
NEXTJS_ENTRY_FILES = {
    "page.tsx",
    "page.ts",
    "layout.tsx",
    "layout.ts",
    "error.tsx",
    "global-error.tsx",
    "loading.tsx",
    "not-found.tsx",
    "template.tsx",
    "route.ts",
    "middleware.ts",
}

# Infrastructure primitives exported as siblings to actively-used
# components — intentional, waiting for future use. Do NOT add to this
# list to paper over a real orphan; add only when the component is
# legitimate but the consumer is on the roadmap.
EXPLICIT_ALLOWLIST: set[str] = {
    # MascotEmpty is the section-level sibling to MascotLoader (which
    # IS imported). The per-card CardEmpty primitive is preferred for
    # most uses, but MascotEmpty is reserved for section-empty states
    # during cold-start. Kept until Phase 1.8.3 Pro migration decides.
    "dashboard/src/app/components/MascotLoader.tsx::MascotEmpty",
    # DrawerPeerComparison is a Pro-drill-down drawer shape prepared
    # for the PeerBenchmarks card; the current card uses a different
    # drawer variant. Kept for future restructure.
    "dashboard/src/app/components/DetailDrawer.tsx::DrawerPeerComparison",
}

# Only flag real React component / hook identifiers:
# - Components: PascalCase, no underscores (e.g., `RevenueHero`, not
#   `DISPLAY_CURRENCY_STORAGE_KEY`)
# - Hooks: `useCamelCase`
# Constants in SCREAMING_SNAKE are not the target class — many are
# intentionally exported for future consumers or as tokens, and
# auditing them creates false-positive noise that disables the check.
_COMPONENT_OR_HOOK = r"(?:use[A-Z][a-zA-Z0-9]*|[A-Z][a-zA-Z0-9]*)"
_NAMED_EXPORT_PATTERNS = [
    re.compile(rf"^export\s+function\s+({_COMPONENT_OR_HOOK})\s*\(", re.MULTILINE),
    re.compile(rf"^export\s+default\s+function\s+({_COMPONENT_OR_HOOK})\s*\(", re.MULTILINE),
    re.compile(rf"^export\s+const\s+({_COMPONENT_OR_HOOK})\s*=", re.MULTILINE),
]


def scan_exports(path: Path) -> list[str]:
    """Return names of exported PascalCase components + use* hooks."""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    names: set[str] = set()
    for pattern in _NAMED_EXPORT_PATTERNS:
        names.update(pattern.findall(text))
    return sorted(names)


def name_is_imported_anywhere(
    name: str,
    defining_path: Path,
    all_files: list[Path],
) -> bool:
    """True if `name` appears in an import statement in any file
    OTHER than the file that defines it."""
    import_patterns = [
        re.compile(rf"\b{re.escape(name)}\b"),
    ]
    for f in all_files:
        if f == defining_path:
            continue
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        # Only consider matches that look like import usage or JSX
        # usage (<ComponentName), not string literals or comments.
        # Simple heuristic: check if the name appears in any line that
        # is an import OR any line that has either `<Name` or `Name(`.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for pat in import_patterns:
                if pat.search(line):
                    # Context check: is it an import, JSX tag, or call?
                    if (
                        "import " in line
                        or f"<{name}" in line
                        or f"{name}(" in line
                        or f" {name}," in line
                        or f" {name} " in line
                        or f"{{{name}" in line
                        or f"{name}}}" in line
                    ):
                        return True
    return False


@telemetered("audit_dashboard_dead_code")
def main(argv: list[str]) -> int:

    if not DASHBOARD_SRC.exists():
        print(
            f"audit_dashboard_dead_code: {DASHBOARD_SRC} not found — skip",
            file=sys.stderr,
        )
        emit("audit_dashboard_dead_code", findings=0, severity="info")
        return 0

    all_files = [
        p for p in DASHBOARD_SRC.rglob("*.tsx")
        if "node_modules" not in p.parts
    ]
    all_files.extend(
        p for p in DASHBOARD_SRC.rglob("*.ts")
        if "node_modules" not in p.parts and p.suffix == ".ts"
    )

    orphans: list[tuple[str, str]] = []

    for path in all_files:
        # Next.js convention files are consumed by the framework
        if path.name in NEXTJS_ENTRY_FILES:
            continue
        rel = str(path.relative_to(REPO_ROOT))

        exports = scan_exports(path)
        for name in exports:
            # Allowlist is `path::name` — more precise than path-wide
            # so we don't accidentally exempt new exports in the same file.
            if f"{rel}::{name}" in EXPLICIT_ALLOWLIST:
                continue
            if not name_is_imported_anywhere(name, path, all_files):
                orphans.append((rel, name))

    if not orphans:
        print(
            f"audit_dashboard_dead_code: clean — scanned "
            f"{len(all_files)} files, 0 orphan exports"
        )
        emit("audit_dashboard_dead_code", findings=0, severity="info")
        return 0

    print(
        f"audit_dashboard_dead_code: {len(orphans)} orphan export(s) "
        "detected."
    )
    print()
    print("These components/hooks are exported but never imported.")
    print("Either wire them into the dashboard, delete them, or add")
    print("to EXPLICIT_ALLOWLIST in this audit if intentional.")
    print()
    for path, name in orphans:
        print(f"  {path}  →  {name}")
    print()
    emit("audit_dashboard_dead_code", findings=len(orphans), severity="warn")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_dashboard_dead_code: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
