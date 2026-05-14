#!/usr/bin/env python3
"""Audit: founder Telegram daily digest stays operator-scope.

Born 2026-05-07 closing the 2-day-old "Revenue at risk this week"
regression. The founder's daily digest leaked merchant-aggregate
revenue / RARS / AOV / proven-savings / per-merchant churn into a
message addressed to them as if they were a merchant. Per CLAUDE.md
§0 ("no false claims") and the explicit founder feedback verbatim
"Mi prendi per il culo? Io Founder che ricevo reveneu at risk come
fossi un merchant?!", that content does NOT belong on a CTO digest.

This audit blocks regressions by parsing
`app/services/telegram_agent.py::build_daily_digest` and failing if
any forbidden merchant-aggregate symbol re-appears inside the
function body.

Forbidden symbols (drift = merchant-style content leaking back):
  - `shop_orders`             → per-merchant revenue table
  - `total_price`             → per-merchant revenue
  - `rars_history`            → per-merchant Revenue-at-Risk
  - `get_weekly_proven_savings`  → per-merchant holdout savings
  - `compute_churn_report`    → per-merchant churn predictor

Operator metadata that IS allowed:
  - merchant install COUNT (network state)
  - bugfix_candidates counts (pipeline state)
  - LLM budget (operator metric)
  - ops_alerts critical counts (operator action queue)
  - compliance_score (operator metric)

Wired into preflight + invariant_monitor.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from _audit_io import safe_read_text

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "app" / "services" / "telegram_agent.py"
FUNCTION_NAME = "build_daily_digest"

FORBIDDEN_NAMES = {
    "shop_orders",
    "total_price",
    "rars_history",
    "get_weekly_proven_savings",
    "compute_churn_report",
}


def _function_source(path: Path, fn: str) -> str:
    src = safe_read_text(path)
    if src is None:
        return ""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            return ast.get_source_segment(src, node) or ""
    return ""


def main() -> int:
    if not TARGET.is_file():
        print(f"❌ target missing: {TARGET}")
        return 1
    body = _function_source(TARGET, FUNCTION_NAME)
    if not body:
        print(
            f"⚠️  {FUNCTION_NAME} not found in {TARGET.name} — function "
            "renamed or removed; update this audit."
        )
        return 1

    # Strip comment lines (lateral-change-evidence audit also strips
    # comment-only lines from commit msgs, same convention here — a
    # historical comment mentioning `shop_orders` is not a leak).
    code_lines = [
        ln for ln in body.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)

    hits: dict[str, int] = {}
    for sym in FORBIDDEN_NAMES:
        n = code.count(sym)
        if n:
            hits[sym] = n

    if not hits:
        print(
            f"✅ {FUNCTION_NAME} stays operator-scope — no forbidden "
            "merchant-aggregate symbol in function body."
        )
        return 0

    print(
        f"❌ FAIL — {FUNCTION_NAME} contains forbidden merchant-aggregate "
        "symbol(s):"
    )
    for sym, n in hits.items():
        print(f"   {sym}  (×{n})")
    print()
    print(
        "Per CLAUDE.md §0 (no false claims) + founder feedback 2026-05-07,\n"
        "the founder's daily Telegram digest must NOT contain merchant-\n"
        "aggregate revenue / at-risk / churn framing. Move the new\n"
        "content to merchant_digest.py (per-merchant, operator-filtered)\n"
        "or to a separate /network admin command.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
