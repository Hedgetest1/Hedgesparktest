"""Test audit_test_hermeticity false-positive reduction (LOW-03 partial).

Two heuristic improvements pinned here:
  1. `assert row.<attr> is False` (attribute access) — NOT a hermeticity
     risk, it's a column-value check. Must NOT be flagged.
  2. Test bodies that call `uuid.uuid4()` / `time.time_ns()` / similar
     use test-scoped identifiers and cannot collide with prod writes.
     Must NOT be flagged.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_test_hermeticity.py")


def _load_module():
    name = "audit_test_hermeticity_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclass decorator reads sys.modules[cls.__module__] when
    # detecting KW_ONLY — register before exec_module or it crashes.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_fn(src: str) -> ast.AST:
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert len(fns) == 1
    return fns[0]


def test_attribute_check_is_not_flagged_as_hermeticity_risk():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    m = db.query(Merchant).filter_by(shop_domain='s').first()\n"
        "    assert m.billing_active is False\n"
    )
    # Negative-state detector should NOT fire because LHS is m.billing_active
    # (Attribute), not a plain Name or direct query-finalizer call.
    neg, _ = mod._has_negative_state_assertion(fn)
    assert neg is False


def test_query_result_name_is_flagged():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    row = db.query(Merchant).filter_by(shop_domain='s').first()\n"
        "    assert row is None\n"
    )
    neg, reason = mod._has_negative_state_assertion(fn)
    assert neg is True
    assert "is None" in reason


def test_direct_finalizer_is_flagged():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    assert db.query(Merchant).count() == 0\n"
    )
    neg, reason = mod._has_negative_state_assertion(fn)
    assert neg is True
    assert "== 0" in reason


def test_uuid_marker_short_circuits():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    import uuid\n"
        "    unique = uuid.uuid4().hex\n"
        "    row = db.query(X).filter_by(k=unique).first()\n"
        "    assert row is None\n"
    )
    assert mod._uses_uniqueness_marker(fn) is True


def test_time_ns_marker_short_circuits():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    import time\n"
        "    k = f'probe-{time.time_ns()}'\n"
        "    row = db.query(X).filter_by(k=k).first()\n"
        "    assert row is None\n"
    )
    assert mod._uses_uniqueness_marker(fn) is True


def test_plain_literal_does_not_trip_uniqueness_marker():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    row = db.query(X).filter_by(k='literal').first()\n"
        "    assert row is None\n"
    )
    assert mod._uses_uniqueness_marker(fn) is False


def test_positive_assertion_is_not_flagged():
    mod = _load_module()
    fn = _parse_fn(
        "def test_t(db):\n"
        "    row = db.query(X).first()\n"
        "    assert row is not None\n"
    )
    neg, _ = mod._has_negative_state_assertion(fn)
    assert neg is False
