#!/usr/bin/env python3
"""audit_dashboard_a11y.py — static a11y pattern scanner for the dashboard.

Catches the two violation classes that axe-core flagged on /app, /app?as=lite,
and /app/pro during the F6 sprint (2026-04-25):

1. **icon-only buttons without an accessible name**
   Pattern: `<button ...><svg ...>...</svg></button>` with NO `aria-label`,
   NO `aria-labelledby`, NO `title`, NO visible text content.
   axe rule: button-name (CRITICAL).

2. **low-contrast small text**
   Pattern: a className containing both `text-slate-500` (or `text-slate-600`)
   AND a small font-size class — `text-[Npx]` where N < 14, OR `text-xs`
   (12px), OR `text-[10px]`/`text-[11px]`/`text-[12px]`/`text-[13px]` /
   `text-[10.5px]`/`text-[11.5px]`/`text-[13.5px]`.
   On dark composited backgrounds, slate-500 (#62748e) and slate-600
   (#45556c) drop below the WCAG AA 4.5:1 minimum for normal text.
   axe rule: color-contrast (SERIOUS).

Mode:
- Default: warn-only — print violations, exit 0. The dashboard has
  ~80 sites of pattern (2) at session 2026-04-25 close, so blocking
  would force a sweeping visual change. Use as a leading indicator
  to drive incremental fixes, not as a hard gate.
- `--strict`: exit 1 when any violation found. Flip on once the
  baseline is reduced to 0.

Scope: dashboard/src/**/*.tsx — that's where merchant-facing UI lives.

False positives accepted:
- Pattern 1: a button with screen-reader-only text via `<span class="sr-only">`
  is not detected as accessible. Rare in this codebase; can be added to
  EXPLICIT_ALLOWLIST if needed.
- Pattern 2: a `text-slate-500` on a card with explicitly LIGHT background
  may pass axe at runtime even when this static check flags it. Re-run
  axe (`npm run e2e:a11y`) to confirm before fixing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _audit_telemetry_shim import emit, telemetered
from _audit_io import safe_read_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src" / "app"

# ----------------------------------------------------------------------------
# Pattern 1 — icon-only buttons without accessible name
# ----------------------------------------------------------------------------
# A button is icon-only if its body contains ONLY an <svg ...> with no
# text content. We scan multi-line button blocks and check for the
# absence of aria-label / aria-labelledby / title attributes.

_BUTTON_BLOCK = re.compile(
    r"<button\b([^>]*)>"      # opening tag — group 1: attributes
    r"(.*?)"                  # body — group 2 (non-greedy)
    r"</button>",
    re.DOTALL,
)
# Inside the body, after stripping comments + svg subtrees, what remains
# should be empty for an "icon-only" button.
_SVG_BLOCK = re.compile(r"<svg\b.*?</svg>", re.DOTALL)
_JSX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")
_TEMPLATE_VAR = re.compile(r"\{[^}]*\}")  # {iconJSX}, {label}, etc.

_HAS_TEXT_LITERAL = re.compile(r">\s*[A-Za-z]")


def _has_accessible_name(attrs: str, body_after_svg: str) -> bool:
    """Return True if the button has an accessible name surface."""
    if "aria-label=" in attrs or "aria-labelledby=" in attrs:
        return True
    # `title=` is weak (not always announced) but still gives axe a
    # name source — accepted for now to keep false positives low.
    if "title=" in attrs:
        return True
    # Visible text content (after stripping SVG, comments, template
    # vars) means the button has its own label. Crude heuristic: any
    # alphanumeric character outside a JSX expression.
    body_stripped = body_after_svg.strip()
    if not body_stripped:
        return False
    # A bare {...} expression — could be {label} which is a name. Be
    # generous: if anything non-empty remains after stripping <svg>,
    # comments, and pure whitespace, treat as accessible.
    return bool(body_stripped)


def find_icon_only_buttons(file: Path) -> list[tuple[int, str]]:
    """Yield (line_no, snippet) for icon-only buttons missing aria-label."""
    text = safe_read_text(file)
    if text is None:
        return []
    findings: list[tuple[int, str]] = []
    for match in _BUTTON_BLOCK.finditer(text):
        attrs, body = match.group(1), match.group(2)
        # Strip svgs + comments to see what TEXT remains in the body.
        body_no_svg = _SVG_BLOCK.sub("", body)
        body_no_comments = _JSX_COMMENT.sub("", body_no_svg)
        body_no_template = _TEMPLATE_VAR.sub("", body_no_comments)
        body_clean = _WHITESPACE.sub("", body_no_template)
        if body_clean:
            # Has visible text outside SVG — accessible by content.
            continue
        # Body is svg-only OR svg + JSX expressions only. Need explicit
        # accessible name on the button itself.
        if _has_accessible_name(attrs, body_no_svg):
            continue
        line_no = text[: match.start()].count("\n") + 1
        snippet = match.group(0).split("\n")[0][:120]
        findings.append((line_no, snippet))
    return findings


# ----------------------------------------------------------------------------
# Pattern 2 — low-contrast small text
# ----------------------------------------------------------------------------
# Match a className value containing BOTH a low-contrast slate token
# AND a small font-size token. Single-quoted, double-quoted, and
# backtick className values are all in scope.

# Match `text-slate-500`, `text-slate-600`, AND opacity-modified
# variants like `text-slate-500/50`. Opacity modifiers always REDUCE
# contrast on dark backgrounds (the foreground composites toward the
# bg luminance), so slate-500/N is at least as bad as slate-500. We
# don't include slate-400/N because plain slate-400 already passes
# 4.5:1 on near-black, and the /N variants are edge-case enough that
# blanket-flagging them creates false positives on lighter cards.
_LOW_CONTRAST_SLATE = re.compile(
    r"(?<!\S)text-slate-(?:500|600)(?:/\d+)?(?!\S)"
)
# Match any small-font token: text-xs, OR text-[N(.5)?px] for any
# integer N <= 13. Matters because axe samples contrast at the actual
# rendered font size; <14px is "regular text" requiring 4.5:1, while
# >=14px bold or >=18px regular drops to 3:1. Earlier regex hard-coded
# 10/11/12/13 + half-step variants and missed unusual values like
# `text-[9.5px]`, leaving page.tsx:358 a false-negative gap.
_SMALL_FONT_SIZE = re.compile(
    r"(?<!\S)text-(?:xs"                                 # text-xs ≡ 12px
    r"|\[(?:[1-9]|1[0-3])(?:\.\d+)?px\])(?!\S)"          # text-[Npx], N=1..13
)
_CLASSNAME_VALUE = re.compile(
    r'className\s*=\s*(?:'
    r'"([^"]+)"'              # double-quoted
    r"|'([^']+)'"             # single-quoted
    r"|`([^`]+)`"             # backtick template
    r")",
    re.DOTALL,
)


def find_low_contrast_small_text(file: Path) -> list[tuple[int, str]]:
    """Yield (line_no, classes) for slate-500/600 + small-font className."""
    text = safe_read_text(file)
    if text is None:
        return []
    findings: list[tuple[int, str]] = []
    for m in _CLASSNAME_VALUE.finditer(text):
        classes = m.group(1) or m.group(2) or m.group(3) or ""
        # Skip dynamic tail that uses ${...} expressions if no slate-* literal
        if not (_LOW_CONTRAST_SLATE.search(classes) and _SMALL_FONT_SIZE.search(classes)):
            continue
        line_no = text[: m.start()].count("\n") + 1
        findings.append((line_no, classes.strip()[:160]))
    return findings


# ----------------------------------------------------------------------------
# Pattern 3 — inline-style low-contrast hex (#64748b slate-500, #45556c slate-600)
# ----------------------------------------------------------------------------
# className-based palette tokens are the dominant pattern, but a handful
# of cards use inline `style={{ color: "#64748b" }}` — those bypass the
# Tailwind class scan above and were the silent gap caught by the
# 2026-04-25 night devil's-advocate run. Match the literal hex strings
# directly. The 4-byte short forms (`#64f` etc.) are not in scope; we
# match the canonical 6-hex form Tailwind v4 emits.

_INLINE_LOW_HEX = re.compile(
    r'color\s*:\s*["\'](?:#64748b|#45556c)["\']',
    re.IGNORECASE,
)


def find_inline_low_contrast(file: Path) -> list[tuple[int, str]]:
    """Yield (line_no, snippet) for inline-style slate-500/600 hex usage."""
    text = safe_read_text(file)
    if text is None:
        return []
    findings: list[tuple[int, str]] = []
    for m in _INLINE_LOW_HEX.finditer(text):
        line_no = text[: m.start()].count("\n") + 1
        findings.append((line_no, m.group(0)))
    return findings


# ----------------------------------------------------------------------------
# Allowlist — known intentional sites where the pattern is benign.
# Each entry: relative path from REPO_ROOT + ":" + line.
# ----------------------------------------------------------------------------
EXPLICIT_ALLOWLIST: set[str] = set()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on any finding (default: warn-only, exit 0)",
    )
    args = parser.parse_args(argv)

    if not DASHBOARD_SRC.exists():
        print(f"audit_dashboard_a11y: dashboard src not found at {DASHBOARD_SRC}")
        return 0  # not a hard failure — repo layout drift would be caught elsewhere

    files = sorted(DASHBOARD_SRC.rglob("*.tsx"))
    icon_button_findings: list[tuple[Path, int, str]] = []
    low_contrast_findings: list[tuple[Path, int, str]] = []
    inline_findings: list[tuple[Path, int, str]] = []

    for f in files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        for line, snip in find_icon_only_buttons(f):
            if f"{rel}:{line}" in EXPLICIT_ALLOWLIST:
                continue
            icon_button_findings.append((f, line, snip))
        for line, classes in find_low_contrast_small_text(f):
            if f"{rel}:{line}" in EXPLICIT_ALLOWLIST:
                continue
            low_contrast_findings.append((f, line, classes))
        for line, snip in find_inline_low_contrast(f):
            if f"{rel}:{line}" in EXPLICIT_ALLOWLIST:
                continue
            inline_findings.append((f, line, snip))

    total = len(icon_button_findings) + len(low_contrast_findings) + len(inline_findings)
    severity = "warn" if total > 0 else None
    emit(
        "audit_dashboard_a11y",
        findings=total,
        severity=severity,
    )

    if total == 0:
        print(
            "audit_dashboard_a11y: clean — 0 icon-only buttons missing names, "
            "0 low-contrast small-text classNames, 0 inline-style low-contrast hex"
        )
        return 0

    print(
        f"audit_dashboard_a11y: {total} findings "
        f"({len(icon_button_findings)} icon-only buttons, "
        f"{len(low_contrast_findings)} low-contrast small text, "
        f"{len(inline_findings)} inline-style low-contrast hex)"
    )
    print()

    if icon_button_findings:
        print(f"=== icon-only buttons missing aria-label / title ({len(icon_button_findings)}) ===")
        for f, line, snip in icon_button_findings[:25]:
            rel = f.relative_to(REPO_ROOT).as_posix()
            print(f"  {rel}:{line} — {snip}")
        if len(icon_button_findings) > 25:
            print(f"  ... and {len(icon_button_findings) - 25} more")
        print()

    if inline_findings:
        print(f"=== inline-style low-contrast hex (color:#64748b / #45556c) ({len(inline_findings)}) ===")
        for f, line, snip in inline_findings[:25]:
            rel = f.relative_to(REPO_ROOT).as_posix()
            print(f"  {rel}:{line} — {snip}")
        if len(inline_findings) > 25:
            print(f"  ... and {len(inline_findings) - 25} more")
        print()

    if low_contrast_findings:
        print(f"=== low-contrast small text (slate-500/600 + ≤13px) ({len(low_contrast_findings)}) ===")
        # Group by file for readability.
        from collections import defaultdict

        by_file: dict[Path, list[tuple[int, str]]] = defaultdict(list)
        for f, line, classes in low_contrast_findings:
            by_file[f].append((line, classes))
        for f in sorted(by_file.keys()):
            rel = f.relative_to(REPO_ROOT).as_posix()
            entries = by_file[f]
            print(f"  {rel} ({len(entries)}):")
            for line, classes in entries[:5]:
                print(f"    :{line} — {classes}")
            if len(entries) > 5:
                print(f"    ... and {len(entries) - 5} more in this file")
        print()

    if args.strict:
        return 1
    return 0


@telemetered("audit_dashboard_a11y")
def _entrypoint(argv: list[str]) -> int:
    return main(argv)


if __name__ == "__main__":
    sys.exit(_entrypoint(sys.argv[1:]))
