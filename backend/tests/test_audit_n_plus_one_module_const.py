"""Test audit_n_plus_one's wave-6 extension that exempts
`for x in MODULE_CONST:` loops where MODULE_CONST is bound at module
scope to an inline list/tuple/set literal of ≤10 elements.

Closes the brutal-DA gap: the audit script was extended without test
coverage at wave-6 (commit 412c6ef). A regression in
_resolve_name_to_collection_len could silently introduce false-
negatives (real N+1s now exempted) — these tests prevent that.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_n_plus_one.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_n_plus_one", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _parse(src: str) -> tuple[ast.For, ast.Module]:
    """Parse source, return (first For node, module)."""
    tree = ast.parse(src)
    for_node = next(n for n in ast.walk(tree) if isinstance(n, ast.For))
    return for_node, tree


# ---------------------------------------------------------------------------
# Inline literal exemption (pre-existing behavior — guard against regression)
# ---------------------------------------------------------------------------

def test_inline_list_literal_under_10_is_exempt():
    mod = _load_module()
    for_node, tree = _parse('for x in [1, 2, 3]:\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is True


def test_inline_list_literal_over_10_is_not_exempt():
    mod = _load_module()
    eleven = ", ".join(str(i) for i in range(11))
    for_node, tree = _parse(f'for x in [{eleven}]:\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is False


def test_inline_tuple_literal_is_exempt():
    mod = _load_module()
    for_node, tree = _parse('for x in (1, 2, 3):\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is True


# ---------------------------------------------------------------------------
# Wave-6 extension: module-level Name bound to small literal IS exempt
# ---------------------------------------------------------------------------

def test_module_const_tuple_under_10_is_exempt():
    """The wave-6 win: `_WINDOWS = (a, b, c)` at module scope; loop
    iterating over `_WINDOWS` should be exempt the same way an inline
    tuple literal is."""
    mod = _load_module()
    for_node, tree = _parse(
        '_WINDOWS = ("a", "b", "c")\n'
        'for x in _WINDOWS:\n'
        '    pass\n'
    )
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is True


def test_module_const_list_under_10_is_exempt():
    mod = _load_module()
    for_node, tree = _parse(
        'TABLES = ["t1", "t2", "t3", "t4"]\n'
        'for x in TABLES:\n'
        '    pass\n'
    )
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is True


def test_module_const_over_10_is_not_exempt():
    """If the module-level constant has >10 elements, exemption does
    NOT apply (treat as potentially unbounded)."""
    mod = _load_module()
    elts = ", ".join(f'"t{i}"' for i in range(11))
    for_node, tree = _parse(
        f'TABLES = ({elts})\n'
        'for x in TABLES:\n'
        '    pass\n'
    )
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is False


def test_function_local_name_is_not_resolved():
    """The exemption applies ONLY to MODULE-level constants. A function-
    local list bound to the same name should NOT be exempted (would be
    false-negative — local lists can be dynamically extended)."""
    mod = _load_module()
    src = (
        'def f():\n'
        '    LOCAL = ["a", "b", "c"]\n'
        '    for x in LOCAL:\n'
        '        pass\n'
    )
    tree = ast.parse(src)
    # The For node lives inside the function body, not module scope
    for_node = next(n for n in ast.walk(tree) if isinstance(n, ast.For))
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is False


def test_no_module_scope_means_no_exemption():
    """If module_scope is not provided, Name iteration cannot be
    resolved → no exemption (safe default — flag as potential N+1)."""
    mod = _load_module()
    for_node, _ = _parse('for x in TABLES:\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=None) is False


def test_undefined_name_no_exemption():
    """If the Name has no binding at module scope, no exemption."""
    mod = _load_module()
    for_node, tree = _parse('for x in UNDEFINED:\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is False


# ---------------------------------------------------------------------------
# Pre-existing behavior preserved: range exemptions still work
# ---------------------------------------------------------------------------

def test_range_with_small_literal_exempt():
    mod = _load_module()
    for_node, tree = _parse('for x in range(5):\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is True


def test_range_with_large_literal_not_exempt():
    mod = _load_module()
    for_node, tree = _parse('for x in range(100):\n    pass\n')
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is False


def test_range_len_module_const_exempt():
    """Pre-existing: `for x in range(len(KNOWN_IDS))` exempted when
    KNOWN_IDS is module-level small constant."""
    mod = _load_module()
    for_node, tree = _parse(
        'KNOWN_IDS = ("a", "b", "c")\n'
        'for x in range(len(KNOWN_IDS)):\n'
        '    pass\n'
    )
    assert mod.loop_is_small_literal(for_node.iter, module_scope=tree) is True
