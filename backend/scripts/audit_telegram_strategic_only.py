#!/usr/bin/env python3
"""audit_telegram_strategic_only.py — Pin the founder direttiva 2026-05-05.

Telegram surfaces ONLY strategic signals (memory state, merchant counts,
RAM, LLM usage, capacity, cost, financial, breach). All operational
alerts (invariant_regression, sentry_regression, slo_*, p95_slow_trend,
circuit_breaker_tripped, pipeline_stall_*, session_anomaly,
llm_safety_*, frontend_error*, onboarding_*, etc.) are handled
autonomously by the brain — they NEVER page the founder.

The audit pins two invariants:

1. `app/services/on_alert_responder.py::_ping_founder_p0` MUST
   call `_is_strategic_alert(alert)` before sending and return False
   if non-strategic. Removing the gate or inverting the predicate
   regresses the founder direttiva.

2. `app/services/system_health_synthesizer.py::send_telegram_signal`
   MUST call `_is_strategic_critical(state)` before sending.
   Removing the gate regresses the founder direttiva.

The check is structural — verify the gate function is defined AND
called before the Telegram send. Source-grep is sufficient; no AST
needed for an invariant this small.

Exit codes:
  0 — clean
  1 — gate missing or send-without-gate path detected
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"

CHECKS = [
    {
        "file": "app/services/on_alert_responder.py",
        "gate_def": "_is_strategic_alert",
        "gate_call_required_in": "_ping_founder_p0",
        "send_marker": "send_message",
        "doctrine": "_TELEGRAM_STRATEGIC_ALLOWLIST",
    },
    {
        "file": "app/services/system_health_synthesizer.py",
        "gate_def": "_is_strategic_critical",
        "gate_call_required_in": "send_telegram_signal",
        "send_marker": "send_message",
        "doctrine": "_STRATEGIC_DIMENSIONS",
    },
]


def _verify(check: dict) -> list[str]:
    """Return list of failure messages (empty = pass)."""
    p = ROOT.parent / check["file"]
    if not p.is_file():
        return [f"{check['file']}: file missing — Telegram path not pinned"]
    text = p.read_text(encoding="utf-8")
    failures: list[str] = []
    if check["gate_def"] not in text:
        failures.append(
            f"{check['file']}: gate function `{check['gate_def']}` not defined"
        )
    if check["doctrine"] not in text:
        failures.append(
            f"{check['file']}: doctrine constant `{check['doctrine']}` "
            "missing — strategic allowlist is the load-bearing definition"
        )
    # Locate gate-call-required function body, ensure gate is called BEFORE
    # any send_message.
    fn_match = re.search(
        rf"def {re.escape(check['gate_call_required_in'])}\b.*?(?=\ndef |\Z)",
        text, re.DOTALL,
    )
    if not fn_match:
        failures.append(
            f"{check['file']}: function `{check['gate_call_required_in']}` "
            "not found — Telegram path moved without updating audit"
        )
        return failures
    body = fn_match.group(0)
    if check["gate_def"] not in body:
        failures.append(
            f"{check['file']}::{check['gate_call_required_in']}: "
            f"strategic gate `{check['gate_def']}(...)` is NOT called — "
            "operational alerts will reach the founder Telegram. This "
            "regresses the founder direttiva 2026-05-05."
        )
    elif check["send_marker"] in body:
        gate_idx = body.index(check["gate_def"])
        send_idx = body.index(check["send_marker"])
        if send_idx < gate_idx:
            failures.append(
                f"{check['file']}::{check['gate_call_required_in']}: "
                f"`{check['send_marker']}` is called BEFORE the gate. "
                "Send must be gated."
            )
    return failures


def main(argv: list[str]) -> int:
    all_failures: list[str] = []
    for check in CHECKS:
        all_failures.extend(_verify(check))

    if all_failures:
        print("❌ Telegram strategic-only gate broken:")
        for f in all_failures:
            print(f"   {f}")
        print()
        print(
            "Founder direttiva 2026-05-05: Telegram surfaces ONLY strategic\n"
            "signals (memory/llm_usage/cost/capacity/financial/breach).\n"
            "Operational alerts handled autonomously by brain. To extend\n"
            "the strategic allowlist, edit on_alert_responder.py\n"
            "_TELEGRAM_STRATEGIC_ALLOWLIST and document in memory."
        )
        return 1

    print(
        f"✅ audit_telegram_strategic_only: 2 Telegram path(s) gated "
        "by strategic-only allowlist — clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
