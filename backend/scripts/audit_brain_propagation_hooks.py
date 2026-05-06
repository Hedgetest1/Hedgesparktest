#!/usr/bin/env python3
"""audit_brain_propagation_hooks.py — Pin §21.6 brain extension.

The autonomous brain pipeline runs WITHOUT the founder when
active (TIER_0 patches auto-apply). Founder direttiva 2026-05-06:
the macchia d'olio + triple-DA + preventer-wiring + curiosity
mandate apply equally to brain, not only to interactive Claude.

The brain is currently DORMANT pre-merchants (per
`project_pipeline_closed_until_merchants.md`). When it transitions
to active (first paying merchant lands), all 5 hooks below MUST
be in place — the autonomous CTO must operate with the same
discipline as the human one.

This audit runs at preflight + invariant_monitor. It is INFO-ONLY
while the pipeline is dormant; it flips to BLOCKING (`--strict`)
when the pipeline reopens. The reopening procedure must run
this audit and fix any RED hook before flipping the kill switch.

Hooks audited:

  1. **Sibling sweep** — `bugfix_pipeline._run_sibling_sweep` +
     `_post_apply_retro_check` exist and wire into propose/apply.
  2. **Triple-DA for TIER_0** — adversarial_reviewer runs for
     all tier classes, not just TIER_1+.
  3. **Preventer wiring after fix** — auto-generated regression
     test + invariant_monitor entry alongside applied patch.
  4. **Tool-spawn capability** — brain can dispatch parallel
     scans / invoke skills / web-search during investigation.
  5. **Semantic ramification** — call-graph / data-flow checks
     beyond syntactic file/line count.

For each hook, the audit looks for marker functions, flags, or
explicit `# brain-hook: <name>` annotations in the source.

# invariant-eligible: true
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

_BUGFIX_PIPELINE = APP / "services" / "bugfix_pipeline.py"
_ADVERSARIAL = APP / "services" / "adversarial_reviewer.py"
_AGENT_WORKER = APP / "workers" / "agent_worker.py"


def _check_sibling_sweep() -> tuple[bool, str]:
    """Hook #1: sibling sweep wired in propose + post-apply."""
    if not _BUGFIX_PIPELINE.is_file():
        return (False, "bugfix_pipeline.py not found")
    text = _BUGFIX_PIPELINE.read_text()
    has_propose = bool(re.search(r"def\s+_run_sibling_sweep\b", text))
    has_post = bool(re.search(r"def\s+_post_apply_retro_check\b", text))
    if has_propose and has_post:
        return (True, "_run_sibling_sweep + _post_apply_retro_check both present")
    missing = []
    if not has_propose:
        missing.append("_run_sibling_sweep")
    if not has_post:
        missing.append("_post_apply_retro_check")
    return (False, f"missing: {', '.join(missing)}")


def _check_triple_da_for_tier_0() -> tuple[bool, str]:
    """Hook #2: adversarial_reviewer runs for TIER_0 patches.

    Currently bugfix_pipeline.py:3902 has `if tier != PATCH_TIER_0:`
    skipping the call. This hook checks for the explicit annotation
    `# brain-hook: tier_0-triple-da` OR the absence of the skip
    pattern."""
    if not _BUGFIX_PIPELINE.is_file():
        return (False, "bugfix_pipeline.py not found")
    text = _BUGFIX_PIPELINE.read_text()
    if "# brain-hook: tier_0-triple-da" in text:
        return (True, "explicit hook annotation found")
    # Detect the legacy skip pattern
    skip_pattern = re.compile(
        r"if\s+tier\s*!=\s*PATCH_TIER_0\s*:\s*\n[^\n]*adversarial",
        re.IGNORECASE,
    )
    if skip_pattern.search(text):
        return (False, "TIER_0 skips adversarial_reviewer (line ~3902)")
    return (False, "no explicit `# brain-hook: tier_0-triple-da` annotation")


def _check_preventer_wiring_after_fix() -> tuple[bool, str]:
    """Hook #3: brain auto-generates regression test + invariant_
    monitor entry alongside applied patch."""
    if not _BUGFIX_PIPELINE.is_file():
        return (False, "bugfix_pipeline.py not found")
    text = _BUGFIX_PIPELINE.read_text()
    if "# brain-hook: preventer-after-fix" in text:
        return (True, "explicit hook annotation found")
    # Look for any function name suggesting auto-test-generation
    has_gen = bool(re.search(
        r"def\s+(?:generate_regression_test|wire_preventer_after|"
        r"_attach_preventer)\b",
        text,
    ))
    if has_gen:
        return (True, "preventer-generation function found")
    return (False, (
        "no preventer-wiring-after-fix mechanism — patch applies "
        "without regression test or invariant_monitor entry"
    ))


def _check_tool_spawn_capability() -> tuple[bool, str]:
    """Hook #4: brain can dispatch parallel scans / skills / web-
    search during investigation."""
    if not _BUGFIX_PIPELINE.is_file():
        return (False, "bugfix_pipeline.py not found")
    text = _BUGFIX_PIPELINE.read_text()
    if "# brain-hook: tool-spawn" in text:
        return (True, "explicit hook annotation found")
    has_brain_tool = bool(re.search(
        r"\bBrainTool\b|brain_dispatch\b|spawn_investigation\b",
        text,
    ))
    if has_brain_tool:
        return (True, "BrainTool/brain_dispatch interface found")
    return (False, (
        "brain runs linearly — no parallel scan / skill invocation / "
        "web-search dispatch interface"
    ))


def _check_semantic_ramification() -> tuple[bool, str]:
    """Hook #5: call-graph / data-flow checks beyond syntactic file
    count and forbidden-path detection."""
    if not _BUGFIX_PIPELINE.is_file():
        return (False, "bugfix_pipeline.py not found")
    text = _BUGFIX_PIPELINE.read_text()
    if "# brain-hook: semantic-ramification" in text:
        return (True, "explicit hook annotation found")
    has_callgraph = bool(re.search(
        r"call_graph\b|data_flow\b|hot_path_check\b|"
        r"_check_hot_path\b|semantic_ramification\b",
        text,
    ))
    if has_callgraph:
        return (True, "semantic-ramification check found")
    has_syntactic = bool(re.search(
        r"touches_self_healing_pipeline\b|files\s*>\s*8\b|"
        r"lines\s*>\s*200\b",
        text,
    ))
    if has_syntactic:
        return (
            False,
            "only syntactic ramification checks (file/line count, "
            "forbidden paths) — no call-graph/data-flow analysis",
        )
    return (False, "no ramification checks found")


_HOOKS = [
    ("sibling-sweep", _check_sibling_sweep),
    ("triple-DA TIER_0", _check_triple_da_for_tier_0),
    ("preventer-wiring", _check_preventer_wiring_after_fix),
    ("tool-spawn", _check_tool_spawn_capability),
    ("semantic-ramification", _check_semantic_ramification),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true",
        help="Block when any hook missing. Use BEFORE pipeline reopens.",
    )
    args = parser.parse_args()

    findings: list[tuple[str, str]] = []
    passing: list[str] = []
    for name, check in _HOOKS:
        ok, msg = check()
        if ok:
            passing.append(f"{name}: {msg}")
        else:
            findings.append((name, msg))

    print(
        f"audit_brain_propagation_hooks: {len(passing)}/{len(_HOOKS)} hooks "
        f"present"
    )
    for line in passing:
        print(f"  ✓ {line}")
    for name, msg in findings:
        print(f"  ✗ {name}: {msg}")

    if not findings:
        print("All §21.6 brain hooks intact — autonomous CTO discipline at parity with interactive.")
        return 0

    print(
        "\nReason: §21.6 (CLAUDE.md + feedback_founder_2026_05_06_top1_cto_"
        "mandate.md). The autonomous brain runs WITHOUT the founder; missing\n"
        "hooks mean the brain operates with weaker discipline than interactive\n"
        "Claude — direct violation of founder direttiva 2026-05-06.\n"
        "\nPipeline status: dormant pre-merchants. Hooks must be GREEN before\n"
        "the pipeline reopens (first paying merchant lands).\n"
        "\nRun with --strict before flipping the pipeline kill-switch on."
    )
    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
