#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of worker module source — code structure, not
#   runtime state. Commit-stage-only by nature.
"""audit_worker_loop_cursor.py — structural preventer (10k).

Born 2026-05-17. `aggregation_worker.store_metrics` (and its sibling
`intelligence_worker`) had a per-shop loop bounded by a TIME-budget
`break` but NO round-robin / keyset cursor. At 10k merchants the budget
exhausted after the first N shops in iteration order and the loop
re-started from the SAME head every cycle, so the iteration-order tail
was *systematically never reached* — store_metrics/SIP/execution never
refreshed for it AND it was never prewarmed, so it had no sticky
last-known-good for the 4th-tier cold-build admission to shed to (the
load-bearing premise under the 2026-05-16f 41%-cliff fix collapsed at
10k). CLAUDE.md §12 already mandated "Worker loops over all shops have a
time budget + Redis cursor for round-robin resume" in PROSE — this makes
it MECHANICAL so the next time-budget worker loop cannot ship cursorless.

The contract: in a worker module, a `for` loop that contains a `break`
guarded by a TIME budget (a `.monotonic()` comparison or a
`*_TIME_BUDGET*` / `*_budget_seconds*` name) MUST also carry a
cross-cycle resume cursor in the same function — one of:
  • a call into the shared helper `app.workers._rr_cursor`
    (load_cursor / rr_slice / next_cursor / save_cursor),
  • the segment_monitor `_batch_for_cycle` / `_load_cursor` pattern,
  • a keyset cursor (`tuple_(...) >` row comparison, or a SQL literal
    containing `> (:cursor` / `find_active_products_batch`),
  • or any cursor load/save helper (`_load_*cursor` / `_save_*cursor`).

A loop whose iteration is genuinely bounded cross-cycle by other means
(an iteration-count circuit breaker, a fixed tiny literal set) opts out
with a `# worker-loop-cursor: ok — <reason>` comment in the same module.

FAIL (exit 1) if a time-budget `break` loop exists with neither a cursor
signal in its function nor the opt-out comment.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_io import safe_read_text  # noqa: E402  TOCTOU-safe read

_ROOT = Path(__file__).resolve().parent.parent
_WORKER_DIRS = [
    _ROOT / "app" / "workers",
    _ROOT / "app" / "workers" / "tasks",
]
_OPT_OUT = "worker-loop-cursor: ok"

# Cursor-presence signals (any one in the same function clears the loop).
_CURSOR_NAME_RE = re.compile(
    r"(?i)(load_cursor|save_cursor|rr_slice|next_cursor|_batch_for_cycle"
    r"|_rr_load|_rr_save|_rr_slice|_rr_next|_load_\w*cursor"
    r"|_save_\w*cursor|find_active_products_batch)"
)
_TIME_BUDGET_NAME_RE = re.compile(r"(?i)(time_budget|budget_seconds|cycle_time_budget)")
_KEYSET_LITERAL_RE = re.compile(r">\s*\(:cursor|cursor_shop|cursor_product")


def _names_in(node: ast.AST) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, ast.alias):
            out.append(n.asname or n.name)
    return out


def _is_time_budget_break_loop(loop: ast.For) -> bool:
    """True if the loop body contains a `break` and a TIME-budget signal
    (a `.monotonic()` call or a *_time_budget* name) — i.e. the loop
    yields on elapsed wall time, the class that needs a resume cursor.
    An iteration-COUNT circuit breaker (retention `_run_batched`) has no
    monotonic/time-budget signal, so it correctly does NOT match."""
    has_break = any(isinstance(n, ast.Break) for n in ast.walk(loop))
    if not has_break:
        return False
    for n in ast.walk(loop):
        if isinstance(n, ast.Attribute) and n.attr == "monotonic":
            return True
        if isinstance(n, ast.Name) and _TIME_BUDGET_NAME_RE.search(n.id):
            return True
    return False


def _function_has_cursor(fn: ast.AST, src: str) -> bool:
    # Name/attr/alias signal anywhere in the function subtree.
    for ident in _names_in(fn):
        if _CURSOR_NAME_RE.search(ident):
            return True
    # ImportFrom the shared helper inside the function.
    for n in ast.walk(fn):
        if isinstance(n, ast.ImportFrom) and n.module \
                and n.module.endswith("_rr_cursor"):
            return True
        # Keyset row-value comparison: tuple_(...) <op> tuple_(...).
        if isinstance(n, ast.Compare):
            for sub in [n.left, *n.comparators]:
                if isinstance(sub, ast.Call):
                    fname = getattr(sub.func, "id", None) \
                        or getattr(sub.func, "attr", None)
                    if fname == "tuple_":
                        return True
        # Keyset SQL literal.
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and _KEYSET_LITERAL_RE.search(n.value):
            return True
    return False


def _violations_in(path: Path) -> list[str]:
    src = safe_read_text(path)
    if src is None:
        return []
    if _OPT_OUT in src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        budget_loops = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.For) and _is_time_budget_break_loop(n)
        ]
        if not budget_loops:
            continue
        if _function_has_cursor(fn, src):
            continue
        for loop in budget_loops:
            out.append(
                f"  {path.relative_to(_ROOT)}:{loop.lineno} — function "
                f"`{fn.name}` has a TIME-budget `break` loop with NO "
                f"cross-cycle resume cursor. At 10k the budget exhausts "
                f"after the first N items and the loop re-grinds the same "
                f"head every cycle → the tail is systematically never "
                f"reached. Add the shared cursor "
                f"(app.workers._rr_cursor: load_cursor/rr_slice/"
                f"next_cursor/save_cursor) or the keyset pattern "
                f"(find_active_products_batch), or opt out with "
                f"`# {_OPT_OUT} — <reason>` if the loop is bounded "
                f"cross-cycle by other means."
            )
    return out


def main() -> int:
    seen: set[Path] = set()
    violations: list[str] = []
    for d in _WORKER_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.py")):
            if p in seen:
                continue
            seen.add(p)
            violations.extend(_violations_in(p))
    if violations:
        print("audit_worker_loop_cursor: FAIL — time-budget worker loop "
              "without a round-robin/keyset resume cursor (the 10k "
              "tail-starvation class, CLAUDE.md §12):")
        print("\n".join(violations))
        return 1
    print("audit_worker_loop_cursor: OK — every time-budget worker loop "
          "has a cross-cycle resume cursor (no 10k tail starvation).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
