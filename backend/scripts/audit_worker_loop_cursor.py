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
# The time-budget-break detector scans worker MAIN loops only — that
# pattern (a `for` + `break` + `.monotonic()`) is a worker-loop idiom;
# scanning all of app/services for it would risk false positives on
# unrelated timed code without catching a real new class.
_WORKER_DIRS = [
    _ROOT / "app" / "workers",
    _ROOT / "app" / "workers" / "tasks",
]
# The unordered-limited-Merchant-scan detector ALSO scans app/services:
# the witnessed §11 miss (`merchant_brain.tick_all_active_merchants`,
# pre-77f3a34) was a worker-INVOKED service function, not a worker
# module. Empirically (2026-05-18) ZERO `query(Merchant)…limit()`
# chains exist in app/services today (77f3a34 fixed the only one) ⟹
# adding this scope is zero-FP now and purely future-regression-
# blocking. This closes the preventer-coverage gap honestly: a
# directory-only extension would NOT have flagged the miss (it has no
# time-budget/break — the existing detector is structurally blind to
# the `.limit(N)` no-cursor class), so a SECOND detector was required,
# not just a wider glob (that would have been theater).
_MERCHANT_SCAN_DIRS = [
    _ROOT / "app" / "workers",
    _ROOT / "app" / "workers" / "tasks",
    _ROOT / "app" / "services",
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


def _query_call_targets_merchant(call: ast.Call) -> bool:
    """True iff this `.query(...)` Call selects the `Merchant` entity:
    `query(Merchant)` (Name) OR `query(Merchant.shop_domain)` /
    `query(Merchant.id, ...)` (Attribute on the Merchant name). Robust
    to import path (keys on the `Merchant` symbol, not a module path)."""
    for arg in call.args:
        if isinstance(arg, ast.Name) and arg.id == "Merchant":
            return True
        if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) \
                and arg.value.id == "Merchant":
            return True
    return False


def _chain_has(limit_call: ast.Call, attr_name: str) -> bool:
    """True iff `.<attr_name>(...)` appears in the dotted-call receiver
    chain of this `.limit(...)` call."""
    node: ast.AST | None = limit_call.func
    while node is not None:
        if isinstance(node, ast.Attribute):
            if node.attr == attr_name:
                return True
            node = node.value
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == attr_name:
                return True
            node = f
        else:
            break
    return False


def _limit_receiver_is_merchant_query(limit_call: ast.Call) -> bool:
    """Walk the dotted-call receiver chain of a `.limit(...)` call
    (`recv.query(Merchant).filter(...).limit(N)`) and report whether a
    `.query(Merchant…)` appears in it AND it is NOT an offset-paginated
    full sweep.

    `.offset(...)` in the same chain is a CLEAR: `query(Merchant)
    .order_by(id).offset(o).limit(BATCH)` inside the standard
    `while True: … offset += BATCH; if not rows: break` loop processes
    EVERY merchant each cycle — offset is itself the cross-batch
    progression, not a per-cycle bounded slice. The tail-starvation
    class is specifically a *fixed-window* `.limit(N)` with no offset
    AND no cursor (the merchant_brain pre-77f3a34 shape). `.order_by`
    is NOT a clear on its own — an ordered-but-cursorless fixed
    `.limit(N)` still re-grinds the same first-N every cycle."""
    if _chain_has(limit_call, "offset"):
        return False  # offset-paginated full sweep — valid coverage
    node: ast.AST | None = limit_call.func  # the Attribute `.limit`
    # Descend the receiver chain: Attribute.value / Call.func.value.
    while node is not None:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "query" \
                    and _query_call_targets_merchant(node):
                return True
            node = func
        elif isinstance(node, ast.Name):
            break
        else:
            break
    # Fallback: any `query(Merchant…)` Call inside the limit expression
    # subtree (covers `query(Merchant)` not in the strict .func chain,
    # e.g. wrapped in a comprehension generator).
    for sub in ast.walk(limit_call):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr == "query" \
                and _query_call_targets_merchant(sub):
            return True
    return False


def _unordered_limited_merchant_scans(fn: ast.AST) -> list[ast.Call]:
    """The §11-miss class (witnessed: `merchant_brain.
    tick_all_active_merchants` pre-77f3a34 —
    `db.query(Merchant).filter(install_status=='active').limit(
    max_shops)` with NO order_by, NO cursor, NO time-budget). Returns
    the offending `.limit(...)` Call nodes in a cursorless function.
    Structurally invisible to `_is_time_budget_break_loop` (no break /
    monotonic) — this is why a wider glob alone was theater."""
    out: list[ast.Call] = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr == "limit" \
                and _limit_receiver_is_merchant_query(n):
            out.append(n)
    return out


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


def _merchant_scan_violations_in(path: Path) -> list[str]:
    """Detector 2 — the unordered-limited-Merchant-scan class (§11
    miss). Scans workers + services (the miss lived in app/services).

    Opt-out is PER-FUNCTION (`# worker-loop-cursor: ok — <reason>`
    inside the function body), NOT file-level: a module like
    agent_worker.py legitimately has both a self-draining transient
    queue (opt-out) AND time-budget loops that MUST stay checked — a
    file-level opt-out would blind the whole module."""
    src = safe_read_text(path)
    if src is None:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scans = _unordered_limited_merchant_scans(fn)
        if not scans:
            continue
        if _function_has_cursor(fn, src):
            continue
        if _OPT_OUT in (ast.get_source_segment(src, fn) or ""):
            continue  # per-function documented opt-out
        for call in scans:
            out.append(
                f"  {path.relative_to(_ROOT)}:{call.lineno} — function "
                f"`{fn.name}` does `query(Merchant…).limit(…)` with NO "
                f"cross-cycle resume cursor. At 10k a per-cycle "
                f"`.limit(N)` over active merchants re-selects the SAME "
                f"arbitrary N every cycle → the tail is systematically "
                f"never ticked (the merchant_brain pre-77f3a34 §11 "
                f"miss: 99% of merchants never brain-ticked at 10k). "
                f"Add the shared cursor (app.workers._rr_cursor: "
                f"load_cursor/rr_slice/next_cursor/save_cursor — see "
                f"merchant_brain.tick_all_active_merchants post-fix), "
                f"or opt out with `# {_OPT_OUT} — <reason>` if this is "
                f"a genuine one-shot bounded sample, not a cyclic "
                f"all-tenant tick."
            )
    return out


def main() -> int:
    violations: list[str] = []

    # Detector 1 — time-budget `break` worker loop (workers only).
    seen: set[Path] = set()
    for d in _WORKER_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.py")):
            if p in seen:
                continue
            seen.add(p)
            violations.extend(_violations_in(p))

    # Detector 2 — unordered-limited Merchant scan (workers + services;
    # the witnessed §11 miss lived in app/services/merchant_brain.py).
    seen2: set[Path] = set()
    for d in _MERCHANT_SCAN_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.py")):
            if p in seen2:
                continue
            seen2.add(p)
            violations.extend(_merchant_scan_violations_in(p))

    if violations:
        print("audit_worker_loop_cursor: FAIL — worker/worker-invoked "
              "loop without a round-robin/keyset resume cursor (the 10k "
              "tail-starvation class, CLAUDE.md §12):")
        print("\n".join(violations))
        return 1
    print("audit_worker_loop_cursor: OK — every time-budget worker loop "
          "AND every query(Merchant…).limit() scan has a cross-cycle "
          "resume cursor (no 10k tail starvation).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
