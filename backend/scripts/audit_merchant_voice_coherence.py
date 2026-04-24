#!/usr/bin/env python3
"""audit_merchant_voice_coherence.py — enforce Spark voice on merchant surfaces.

Part of the HedgeSpark merchant-facing coherence preventer
(/docs/HEDGESPARK_MERCHANT_COHERENCE_SPEC.md §5).

v1 scope — 4 rules, 2 blocking + 2 warning:

    Rule 1 BLOCKING — forbidden pricing phrases (CLAUDE.md §3)
        anywhere in dashboard source.
    Rule 3 BLOCKING — third-person narration in Spark-surface files
        (dashboard + chat_voice + spark_voice + merchant_chatbot +
        chatbot_llm_fallback).
    Rule 2 WARNING — jargon tokens without a plain-English gloss in
        the same string. Heuristic; v2 promotes to blocking after
        retrofit.
    Rule 4 WARNING — personality anti-pattern (emojis in prose copy,
        multiple exclamation marks).

Not scoped by v1:
- Email templates (governed by brand_voice.py, Andrea-voice).
- Landing hero/pricing page copy (founder territory for some strings;
  pricing forbidden phrases still enforced codebase-wide).
- Sentence-length audit (max 12 words in H1/CTA) — context-heavy,
  deferred to v2.

Exit codes:
    0  clean (warnings may be present)
    1  blocking rule triggered
    2  script error

Single source of truth for constants: app/services/spark_voice.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from app.services.spark_voice import (  # type: ignore
        JARGON_TOKENS,
        PRICING_FORBIDDEN_PHRASES,
        THIRD_PERSON_PATTERNS,
    )
except ImportError as e:  # pragma: no cover
    print(f"audit_merchant_voice_coherence: cannot import spark_voice ({e})", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Scope — which files each rule scans
# ---------------------------------------------------------------------------

# Rule 1 (pricing) — scan every merchant-facing dashboard source file.
PRICING_SCAN_ROOTS = [
    REPO_ROOT / "dashboard" / "src" / "app",
]

# Rule 3 (third-person in Spark surface) — dashboard + Spark backend files
# (not email templates — those use brand_voice with an Andrea-narrator).
SPARK_SURFACE_ROOTS = [
    REPO_ROOT / "dashboard" / "src" / "app" / "app",
    REPO_ROOT / "dashboard" / "src" / "app" / "components",
]
SPARK_SURFACE_FILES = [
    REPO_ROOT / "backend" / "app" / "services" / "chat_voice.py",
    REPO_ROOT / "backend" / "app" / "services" / "spark_voice.py",
    REPO_ROOT / "backend" / "app" / "services" / "merchant_chatbot.py",
    REPO_ROOT / "backend" / "app" / "services" / "chatbot_llm_fallback.py",
]

# Extensions to scan
SCAN_EXTS = {".tsx", ".ts", ".py"}

# Paths to always skip
SKIP_DIR_NAMES = {"node_modules", ".next", "__pycache__", ".git", "tests", "scripts"}


# ---------------------------------------------------------------------------
# Emoji regex (Rule 4 warning). Excludes the functional tokens ✓ ⚠ →
# used in UI affordances, per coherence spec §3.
# ---------------------------------------------------------------------------

EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"  # dingbats (excluding the allowlist below)
    "]+",
    flags=re.UNICODE,
)

# Functional tokens always allowed (not counted as emoji violations)
EMOJI_ALLOWLIST = {"✓", "⚠", "→", "←", "↑", "↓", "•", "·"}


# ---------------------------------------------------------------------------
# Third-person pattern compilation
# ---------------------------------------------------------------------------

_THIRD_PERSON_RE = re.compile(
    "|".join(THIRD_PERSON_PATTERNS),
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pricing pattern compilation
# ---------------------------------------------------------------------------

_PRICING_RE = re.compile(
    "|".join(re.escape(p) for p in PRICING_FORBIDDEN_PHRASES),
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Jargon gloss heuristic: if the jargon token appears on a line and
# no gloss hint word appears within the same line (±120 chars buffer
# for JSX), treat as unglossed.
# ---------------------------------------------------------------------------

JARGON_GLOSS_HINTS: dict[str, tuple[str, ...]] = {
    "CVR": ("conversion",),
    "COGS": ("cost of goods", "costs"),
    "CAC": ("acquisition cost",),
    "ARPC": ("per customer", "what each customer spends"),
    "MRR": ("monthly recurring",),
    "ARR": ("annual recurring",),
    "LTV": ("lifetime value", "over time", "what each customer spends"),
    "AOV": ("order value",),
    "ROAS": ("return on ad",),
    "attribution window": ("days back", "lookback", "window"),
    "cohort": ("group of",),
    "p-value": ("confidence", "statistical"),
    "holdout": ("control group",),
    "confidence interval": ("range of",),
}

# Compile a word-boundary regex per jargon token
_JARGON_REGEXES = {
    token: re.compile(rf"\b{re.escape(token)}\b", flags=re.IGNORECASE)
    for token in JARGON_TOKENS
}


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------


def _iter_files(roots: list[Path]) -> list[Path]:
    """Return all files under the given roots with scannable extensions."""
    out: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix in SCAN_EXTS:
                out.append(root)
            continue
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in SCAN_EXTS:
                continue
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            out.append(p)
    return out


def _is_ignored_line(line: str) -> bool:
    """Lines that are clearly code, not copy — skip all rules on them."""
    stripped = line.strip()
    # Empty / comments
    if not stripped or stripped.startswith(("//", "/*", "*", "*/", "#")):
        return True
    # Import / export / type-only lines
    if stripped.startswith(("import ", "export ", "from ", "type ", "interface ")):
        return True
    # Pure assignments to tuples of color hex / classes
    if re.match(r"^\w+\s*[=:]\s*['\"][#a-zA-Z0-9_./\-:]+['\"]\s*[,;]?\s*$", stripped):
        return True
    # TypeScript / Python type paths, dict keys, endpoint paths
    # e.g. paths["/pro/cohorts/ltv/products"]["get"]…
    if "paths[" in stripped or 'paths["' in stripped:
        return True
    # URL/route strings used as arguments (not merchant copy)
    if re.search(r"['\"`]/[a-z][a-z0-9_/.\-]+['\"`]", stripped) and "content=" not in stripped:
        # Lines whose only quoted strings look like URL paths are API calls, not copy.
        quoted_strings = re.findall(r"['\"`]([^'\"`]+)['\"`]", stripped)
        if quoted_strings and all(
            q.startswith("/") or q.startswith("http") or "://" in q or q.endswith(".json") for q in quoted_strings
        ):
            return True
    # Short variable declarations with numeric literals, no prose
    #   const aov = 50;    let arr: number[] = [];    aov: resolvedAov,
    if re.match(r"^(?:const|let|var)?\s*\w+\s*[=:]\s*[\d\[\]\{\}\w.,\s|<>()!?-]+$", stripped):
        # Only treat as ignored if no string literal with spaces is present
        if not re.search(r"['\"`][^'\"`]*\s[^'\"`]*['\"`]", stripped):
            return True
    # Function / arrow function signatures with parameters typed as identifiers
    #   (arr: readonly BehSegment[]) =>
    if re.match(r".*\([^)]*:\s*\w", stripped) and "=>" in stripped and stripped.count('"') < 2:
        return True
    # JSDoc-like @param lines
    if stripped.startswith("*") or stripped.startswith("@param"):
        return True
    return False


_BANG_BANG_RE = re.compile(r"!!\w")
_WORD_EXCL_RE = re.compile(r"\w!!\w")  # word!!word (still code-ish)


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def check_pricing(files: list[Path]) -> list[str]:
    """Rule 1 BLOCKING — forbidden pricing phrases."""
    findings: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), start=1):
            if _is_ignored_line(line):
                continue
            m = _PRICING_RE.search(line)
            if m:
                rel = path.relative_to(REPO_ROOT)
                findings.append(f"{rel}:{ln}  PRICING  {m.group()!r}  →  line: {line.strip()[:120]}")
    return findings


def check_third_person(files: list[Path]) -> list[str]:
    """Rule 3 BLOCKING — third-person narration on Spark surfaces."""
    findings: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), start=1):
            if _is_ignored_line(line):
                continue
            m = _THIRD_PERSON_RE.search(line)
            if m:
                # Skip: documentation/comments that REFERENCE these patterns
                # (e.g. the spark_voice THIRD_PERSON_PATTERNS tuple itself)
                if "THIRD_PERSON_PATTERNS" in line or "# " in line[: max(0, m.start())]:
                    continue
                rel = path.relative_to(REPO_ROOT)
                findings.append(f"{rel}:{ln}  THIRD_PERSON  {m.group()!r}  →  line: {line.strip()[:120]}")
    return findings


def check_jargon_unglossed(files: list[Path]) -> list[str]:
    """Rule 2 WARNING — jargon tokens without a gloss hint on the same line."""
    findings: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), start=1):
            if _is_ignored_line(line):
                continue
            line_lower = line.lower()
            for token, regex in _JARGON_REGEXES.items():
                m = regex.search(line)
                if not m:
                    continue
                hints = JARGON_GLOSS_HINTS.get(token, ())
                if any(hint.lower() in line_lower for hint in hints):
                    continue  # glossed on same line — OK
                # Skip: if the token is part of a longer word or identifier
                # (e.g. CVR in "uCVRSignal") — word-boundary regex already
                # handles this, so any match here is a real candidate.
                rel = path.relative_to(REPO_ROOT)
                findings.append(
                    f"{rel}:{ln}  JARGON:{token}  →  line: {line.strip()[:120]}"
                )
    return findings


def check_personality(files: list[Path]) -> list[str]:
    """Rule 4 WARNING — emojis in prose + multi-exclamation."""
    findings: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), start=1):
            if _is_ignored_line(line):
                continue
            # Emoji (excluding allowlist + icon-system value assignments)
            for m in EMOJI_RE.finditer(line):
                if m.group() in EMOJI_ALLOWLIST:
                    continue
                # Skip import/require lines
                if "import" in line or "require(" in line:
                    continue
                # Skip icon-system value assignments: icon: "🔥" / emoji: "🧭"
                # These are visual UI tokens, not prose emojis.
                if re.search(r"\b(icon|emoji)\s*:\s*['\"]", line):
                    continue
                if re.search(r"\b(icon|emoji)=['\"{]", line):
                    continue
                rel = path.relative_to(REPO_ROOT)
                findings.append(
                    f"{rel}:{ln}  EMOJI  {m.group()!r}  →  line: {line.strip()[:120]}"
                )
            # Multi-exclamation in PROSE only — skip TS double-negation `!!x`
            if "!!" in line:
                # Strip TS double-negation artifacts: !!variable, !!x.y, !!{expr}
                # We only care if "!!" appears inside a string literal
                string_hits = re.findall(r"['\"`]([^'\"`]*!!+[^'\"`]*)['\"`]", line)
                if string_hits:
                    rel = path.relative_to(REPO_ROOT)
                    findings.append(
                        f"{rel}:{ln}  MULTI_EXCL  →  line: {line.strip()[:120]}"
                    )
    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@telemetered("audit_merchant_voice_coherence")
def main() -> int:
    pricing_files = _iter_files(PRICING_SCAN_ROOTS)
    spark_files = _iter_files(SPARK_SURFACE_ROOTS) + [
        p for p in SPARK_SURFACE_FILES if p.exists()
    ]

    # BLOCKING rules
    pricing_hits = check_pricing(pricing_files)
    third_person_hits = check_third_person(spark_files)

    # WARNING rules
    jargon_hits = check_jargon_unglossed(spark_files)
    personality_hits = check_personality(spark_files)

    exit_code = 0

    if pricing_hits:
        print(
            f"BLOCKING — {len(pricing_hits)} forbidden pricing phrase(s):",
            file=sys.stderr,
        )
        for h in pricing_hits:
            print(f"  {h}", file=sys.stderr)
        exit_code = 1

    if third_person_hits:
        print(
            f"BLOCKING — {len(third_person_hits)} third-person narration hit(s) "
            f"in Spark-surface files:",
            file=sys.stderr,
        )
        for h in third_person_hits:
            print(f"  {h}", file=sys.stderr)
        exit_code = 1

    if jargon_hits:
        print(
            f"WARNING — {len(jargon_hits)} jargon token(s) without on-line "
            f"gloss (will be blocking in v2):"
        )
        # Cap the warning list at 50 so preflight stays readable
        for h in jargon_hits[:50]:
            print(f"  {h}")
        if len(jargon_hits) > 50:
            print(f"  ... and {len(jargon_hits) - 50} more")

    if personality_hits:
        print(
            f"WARNING — {len(personality_hits)} personality anti-pattern(s) "
            f"(will be blocking in v2):"
        )
        for h in personality_hits[:50]:
            print(f"  {h}")
        if len(personality_hits) > 50:
            print(f"  ... and {len(personality_hits) - 50} more")

    if exit_code == 0 and not (jargon_hits or personality_hits):
        print(
            f"audit_merchant_voice_coherence: OK — "
            f"{len(pricing_files)} pricing files + {len(spark_files)} spark-surface files scanned, "
            f"no violations"
        )
    elif exit_code == 0:
        warnings_n = len(jargon_hits) + len(personality_hits)
        print(
            f"audit_merchant_voice_coherence: OK with {warnings_n} warning(s) — "
            f"blocking rules clean"
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
