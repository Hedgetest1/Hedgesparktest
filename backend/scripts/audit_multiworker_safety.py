#!/usr/bin/env python3
"""
audit_multiworker_safety.py — flag module-level state that silently
assumes a single uvicorn worker process.

Runs under preflight to block commits that introduce module-level mutable
dicts/locks in the FastAPI request path without an explicit multi-worker
disposition. The 2026-04-23 scaling pass moved the 4 API-path landmines
(shopify_client rate bucket, orchestrator cooldown, promotion_pipeline
push cooldown, model_config cache) to Redis. This script guards against
new landmines re-appearing.

Scan scope: app/api, app/core, app/services. Module-level assignments only.

Flagging rule (either triggers a fail):

1. **Suspicious runtime-state names** assigned to empty container
   (`{}`, `[]`, `set()`, `dict()`, `list()`, `defaultdict(...)`):
   names matching one of: _cache, _store, _bucket, _buckets, _nonces,
   _counts, _counters, _mem_*, _claims, _rate_*, _local_*, _cooldown*,
   _pending, _queue, _state, _last_*, _recent_*, _seen_*, _sent_*.
   These are almost always per-process runtime state.

2. **Module-level threading/asyncio locks** of any kind:
   threading.Lock(), threading.RLock(), threading.Semaphore(),
   asyncio.Lock(), Lock() (bare import). These CANNOT protect
   cross-process state and give false confidence of correctness.

**Override with annotation on the declaration line or line above:**

    # multi-worker: redis-backed       — Redis primary, this is fallback only
    # multi-worker: accept-degrade      — known per-worker, degradation tolerated
    # multi-worker: constant            — never mutated at runtime
    # multi-worker: redis-mirrored      — Redis dual-write, reconciled on read

Usage:
    ./scripts/audit_multiworker_safety.py            # report
    ./scripts/audit_multiworker_safety.py --strict   # exit 1 on any hit
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text


ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"
SCAN_DIRS = ["api", "core", "services"]

# Name patterns that typically indicate runtime-mutated state (not constants)
_STATE_NAME_PATTERNS = [
    re.compile(r"^_(.*_)?cache$"),
    re.compile(r"^_(.*_)?cache_\w+$"),  # _cache_ts, _cache_lock, etc.
    re.compile(r"^_(.*_)?store$"),
    re.compile(r"^_(.*_)?bucket$"),
    re.compile(r"^_(.*_)?buckets$"),
    re.compile(r"^_(.*_)?nonces?$"),
    re.compile(r"^_(.*_)?counts?$"),
    re.compile(r"^_(.*_)?counters?$"),
    re.compile(r"^_mem_\w+$"),
    re.compile(r"^_(.*_)?claims$"),
    re.compile(r"^_rate_\w+$"),
    re.compile(r"^_local_\w+$"),
    re.compile(r"^_cooldown\w*$"),
    re.compile(r"^_(.*_)?cooldown$"),
    re.compile(r"^_pending\w*$"),
    re.compile(r"^_(.*_)?queue$"),
    re.compile(r"^_(.*_)?state$"),
    re.compile(r"^_last_\w+$"),
    re.compile(r"^_recent_\w+$"),
    re.compile(r"^_seen_\w+$"),
    re.compile(r"^_sent_\w+$"),
    re.compile(r"^_snapshot_\w+$"),
    re.compile(r"^_active_\w+$"),
    re.compile(r"^_fallback_\w+$"),
    re.compile(r"^_\w+_429$"),  # _provider_429 pattern
    re.compile(r"^_\w+_sent$"),  # _alert_sent dedup
    re.compile(r"^_auto_\w+_cooldown$"),
    re.compile(r"^_\w+_last_\w+$"),
    re.compile(r"^_\w+_alert_sent$"),
]

ANNOTATION_RE = re.compile(
    r"#\s*multi-worker:\s*(redis-backed|accept-degrade|constant|redis-mirrored|thread-only)",
    re.IGNORECASE,
)

# Calls that always flag (locks / per-worker synchronization primitives).
# 2026-04-23 retro DA: expanded beyond Lock/RLock/Semaphore to Event,
# Condition, Barrier (all threading primitives that don't share state
# across processes) plus queue.Queue variants — per-worker queues hold
# state that the other 3 uvicorn workers can't observe.
_LOCK_CALL_NAMES = {
    "Lock", "RLock", "Semaphore", "BoundedSemaphore",
    "Event", "Condition", "Barrier",
    "Queue", "LifoQueue", "PriorityQueue", "SimpleQueue",
}
_LOCK_ATTR_TARGETS = {"threading", "asyncio", "queue"}


def _is_empty_container_expr(node: ast.AST) -> bool:
    """True if node is an empty container literal or equivalent call."""
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    if isinstance(node, ast.List) and not node.elts:
        return True
    if isinstance(node, ast.Set) and not node.elts:
        return True
    if isinstance(node, ast.Call):
        fn = node.func
        # dict(), list(), set(), defaultdict(...)
        if isinstance(fn, ast.Name):
            if fn.id in {"dict", "list", "set"}:
                return not node.args and not node.keywords
            if fn.id in {"defaultdict", "OrderedDict", "Counter"}:
                return True  # always runtime-mutable
        if isinstance(fn, ast.Attribute):
            # collections.defaultdict() etc.
            if fn.attr in {"defaultdict", "OrderedDict", "Counter"}:
                return True
    return False


def _is_lock_call(node: ast.AST) -> bool:
    """True if node is a threading/asyncio lock constructor call."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id in _LOCK_CALL_NAMES:
        return True
    if isinstance(fn, ast.Attribute) and fn.attr in _LOCK_CALL_NAMES:
        # threading.Lock() / asyncio.Lock()
        if isinstance(fn.value, ast.Name) and fn.value.id in _LOCK_ATTR_TARGETS:
            return True
    return False


def _has_annotation_near(lines: list[str], line_idx: int) -> bool:
    """Check if a '# multi-worker: ...' annotation is on the line or above.

    Looks up to 6 lines above the declaration, stopping at any non-comment,
    non-blank line. This supports group annotations that decorate a block
    of 2-4 related declarations (common for LLM budget counters etc.).
    """
    # Same line first
    if 0 <= line_idx < len(lines) and ANNOTATION_RE.search(lines[line_idx]):
        return True

    # Walk upward through blank lines and comment lines only. Stop at any
    # other content (assignment, import, def, etc.). Max 6 lines back.
    probe = line_idx - 1
    steps = 0
    while probe >= 0 and steps < 6:
        stripped = lines[probe].strip()
        if not stripped:
            probe -= 1
            steps += 1
            continue
        if stripped.startswith("#"):
            if ANNOTATION_RE.search(stripped):
                return True
            probe -= 1
            steps += 1
            continue
        # hit code — stop
        break
    return False


def _name_is_runtime_state(name: str) -> bool:
    # All-uppercase names are constant convention → skip
    if name.isupper():
        return False
    return any(p.match(name) for p in _STATE_NAME_PATTERNS)


def _extract_target_names(node: ast.AST) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            out.append(node.target.id)
    return out


def _scan_file(py: pathlib.Path) -> list[tuple[int, str, str]]:
    """Return list of (line, kind, message) hits for a single file."""
    src = safe_read_text(py, errors="replace")
    if src is None:
        return []
    lines = src.splitlines()
    try:
        tree = ast.parse(src, filename=str(py))
    except SyntaxError:
        return []

    hits: list[tuple[int, str, str]] = []
    for node in tree.body:  # module scope only
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue

        targets = _extract_target_names(node)
        if not targets:
            continue

        line_idx = node.lineno - 1
        annotated = _has_annotation_near(lines, line_idx)

        # Rule 2: lock constructors are ALWAYS flagged unless annotated
        if _is_lock_call(value):
            if not annotated:
                hits.append((
                    node.lineno,
                    "lock",
                    f"{targets[0]} = <Lock> — cross-process lock unsafe",
                ))
            continue

        # Rule 1: suspicious runtime-state names assigned to empty containers
        if _is_empty_container_expr(value):
            for name in targets:
                if _name_is_runtime_state(name) and not annotated:
                    hits.append((
                        node.lineno,
                        "state",
                        f"{name} = <empty container> — looks like runtime state",
                    ))
    return hits


@telemetered("audit_multiworker_safety")
def main() -> int:
    strict = "--strict" in sys.argv
    all_hits: list[tuple[pathlib.Path, int, str, str]] = []

    for subdir in SCAN_DIRS:
        base = ROOT / subdir
        if not base.exists():
            continue
        for py in sorted(base.rglob("*.py")):
            for line, kind, msg in _scan_file(py):
                all_hits.append((py, line, kind, msg))

    if not all_hits:
        print("  ✓ no unannotated multi-worker hazards in app/api|core|services")
        return 0

    print(f"  ✗ found {len(all_hits)} unannotated multi-worker hazards:")
    for py, line, kind, msg in all_hits:
        rel = py.relative_to(ROOT.parent)
        print(f"    [{kind:5s}] {rel}:{line}  {msg}")
    print()
    print("  Annotate each declaration (or the line above) with one of:")
    print("    # multi-worker: redis-backed     — Redis primary, in-proc fallback")
    print("    # multi-worker: redis-mirrored   — Redis dual-write, reconcile on read")
    print("    # multi-worker: accept-degrade   — known per-worker, tolerated")
    print("    # multi-worker: constant         — populated once, never mutated")
    print("    # multi-worker: thread-only      — process-local threads, cross-process N/A")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
