#!/usr/bin/env python3
"""audit_llm_model_version_freshness.py — block stale LLM model strings.

Problem class
-------------
LLM model identifiers drift over time:
  - `claude-sonnet-4-20250514` → superseded by `claude-sonnet-4-6`
  - `claude-opus-4-20250514`   → superseded by `claude-opus-4-7`
  - `gpt-4-turbo-2024-04-09`   → legacy OpenAI naming
  - future: `mistral-*`, `gemini-*`, etc.

CLAUDE.md operating principle: "default to the latest and most
capable Claude models". Same rule applies to any LLM provider — a
stale model string is either quietly-suboptimal (still served) or a
silent failure (retired).

This audit maintains per-provider CANONICAL_MODELS allowlists. Any
model identifier in `app/` with a recognizable provider prefix
(claude-, gpt-, mistral-, gemini-) that is NOT in the allowlist
trips the audit.

DA-2 hardening (2026-04-23): expanded from Claude-only regex to
multi-provider prefix matching, so adding a new provider (e.g.
Mistral) tomorrow doesn't bypass the audit — the new provider's
model strings will still be flagged as unknown until added to the
appropriate CANONICAL set.

Why manual CANONICAL_MODELS sync (not auto-polling Anthropic API)
-----------------------------------------------------------------
A follow-up DA flagged "manual CLAUDE.md sync is stale-prone vs
auto-polling Anthropic's model list". Evaluation: manual sync is
INTENTIONALLY safer than auto-polling.

  1. Auto-polling would silently adopt a model Anthropic has
     deprecated + schedule-for-retirement but still serves —
     supply-chain risk (retirement announcement misses, we auto-
     pick it up, then the model is pulled out from under us).
  2. Release cadence is quarterly-ish (Sonnet 4 → 4.6 → 4.7 over
     ~6 months). Human-in-the-loop review of release notes is
     fast and catches retirement warnings that the API list
     doesn't surface.
  3. This audit FAILS PREFLIGHT on any unknown prefix+model combo,
     so a dev who adds a new model string without updating
     CANONICAL_MODELS is blocked at commit. The gate is the
     preflight, not the poll.

Decision: manual sync is top-1 for our supply-chain threat model.
Logged to ledger as [LLM-03] with that rationale.

Canonical lists (2026-04-23)
----------------------------
  Anthropic: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001
  OpenAI:    gpt-4o, gpt-4o-mini

Exit code
---------
  0 — clean
  1 — stale/unknown model string found (--strict)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

# Keep in sync with CLAUDE.md model lineup + providers actually wired
# in llm_router / nudge_composer / bugfix_pipeline today.
CANONICAL_MODELS: dict[str, set[str]] = {
    "anthropic": {
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    },
    "openai": {
        "gpt-4o",
        "gpt-4o-mini",
    },
    # Future providers: add their prefix below + a set of canonical
    # model strings. The audit will start flagging stale strings
    # immediately.
    # "mistral": {"mistral-large-latest", ...},
    # "google":  {"gemini-2.5-pro", ...},
}

# Prefix → provider routing. Any string with one of these prefixes is
# compared against the matching CANONICAL set; other string shapes
# (e.g. "text-embedding-3") pass through unchecked.
_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("mistral-", "mistral"),
    ("gemini-", "google"),
]

# Files that legitimately reference LEGACY model strings as historical
# aliases — cost-attribution tables, migration breadcrumbs, etc. They
# opt out of the audit via a per-file path allowlist.
LEGACY_ALIAS_ALLOWLIST = {
    # cost table keeps old keys so historical llm_daily_usage rows
    # still resolve a cost when the summary walks Redis counters.
    str((APP_DIR / "services" / "system_summary.py").relative_to(REPO_ROOT)),
    # Redis-counter-key compatibility: llm_budget's _COST_PER_1K_TOKENS
    # maps model-name → cost. Pre-upgrade Redis rows are keyed on the
    # old strings; dropping them would break cost rollup on historical
    # periods. Each legacy entry is annotated in source.
    str((APP_DIR / "core" / "llm_budget.py").relative_to(REPO_ROOT)),
}

# Any quoted string that starts with a known provider prefix is a
# candidate model identifier. Non-prefix strings (library names,
# header keys) are skipped.
_MODEL_CANDIDATE_RE = re.compile(
    r'["\']((?:' + "|".join(p for p, _ in _PROVIDER_PREFIXES) + r')[a-z0-9\-.]+)["\']'
)


def _resolve_provider(model: str) -> str | None:
    for prefix, provider in _PROVIDER_PREFIXES:
        if model.startswith(prefix):
            return provider
    return None


def _scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        src = path.read_text()
    except Exception:
        return findings

    for i, line in enumerate(src.splitlines(), start=1):
        # Skip comments (cheap approximation — doesn't handle inline)
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for m in _MODEL_CANDIDATE_RE.finditer(line):
            model = m.group(1)
            provider = _resolve_provider(model)
            if provider is None:
                # No known prefix — not a model string we audit.
                continue
            canonical = CANONICAL_MODELS.get(provider, set())
            if model in canonical:
                continue
            findings.append((i, f"{model} (provider={provider})"))
    return findings


@telemetered("audit_llm_model_version_freshness")
def main() -> int:
    strict = "--strict" in sys.argv
    violations: list[tuple[Path, int, str]] = []

    if not APP_DIR.is_dir():
        print(f"✗ app dir missing: {APP_DIR}")
        return 1 if strict else 0

    for py_path in sorted(APP_DIR.rglob("*.py")):
        rel = str(py_path.relative_to(REPO_ROOT))
        if rel in LEGACY_ALIAS_ALLOWLIST:
            continue
        for lineno, model in _scan_file(py_path):
            violations.append((py_path, lineno, model))

    if violations:
        print(f"✗ LLM model freshness — {len(violations)} stale references:")
        for path, lineno, model in violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{lineno}  {model}")
        print()
        print("Canonical lineup:")
        for provider, models in sorted(CANONICAL_MODELS.items()):
            print(f"  {provider}: {sorted(models)}")
        print()
        print("Update the hardcoded string OR add the file to")
        print("LEGACY_ALIAS_ALLOWLIST in this audit if it's intentionally")
        print("keeping the legacy string (e.g. cost-attribution table).")
        return 1 if strict else 0

    total = sum(len(s) for s in CANONICAL_MODELS.values())
    providers = len(CANONICAL_MODELS)
    print(f"✓ every LLM model reference matches the canonical lineup "
          f"— {total} canonical entries across {providers} providers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
