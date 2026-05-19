"""Contract test for the jsonb COALESCE-scalar class (born 2026-05-19,
§21 sweep a7de1ee12382855f5).

GROUND TRUTH: `chatbot_llm_fallback.py:256` had
`jsonb_array_elements(COALESCE(so.line_items,'[]'::jsonb))`. COALESCE
guards SQL-NULL but NOT a JSON SCALAR (`"x"`/`123`/`true`) →
jsonb_array_elements still panics "cannot extract elements from a
scalar". Worse: `audit_jsonb_array_length_guard`'s bare-identifier
regex never matched the COALESCE-wrapped arg, so the call was treated
as ABSENT (unscanned), not unguarded → it shipped green. (prod
shop_orders.line_items is 100% array today → the code site was
LATENT, not firing; this locks the audit so a FUTURE scalar-wrapped
site is caught.)

This pins Class 3: a function-wrapped (COALESCE/NULLIF/jsonb_strip_
nulls) jsonb_array_* arg with no jsonb_typeof='array' guard nearby is
flagged; the canonical CASE-WHEN-jsonb_typeof form is NOT (no false
positive on the safe pattern).
"""
from __future__ import annotations

import importlib
from pathlib import Path

A = importlib.import_module("scripts.audit_jsonb_array_length_guard")

_TESTS_DIR = Path(__file__).resolve().parent  # under BACKEND → relative_to ok

_COALESCE_BAD = (
    'SQL = """\n'
    "SELECT line->>'t' FROM shop_orders so,\n"
    "  jsonb_array_elements(COALESCE(so.line_items, '[]'::jsonb)) AS line\n"
    "WHERE so.shop_domain = :s\n"
    '"""\n'
)
_CASE_GOOD = (
    'SQL = """\n'
    "SELECT line->>'t' FROM shop_orders so,\n"
    "  jsonb_array_elements(\n"
    "      CASE WHEN jsonb_typeof(so.line_items) = 'array'\n"
    "           THEN so.line_items ELSE '[]'::jsonb END\n"
    "  ) AS line\n"
    '"""\n'
)
_NULLIF_BAD = (
    'SQL = """\n'
    "SELECT jsonb_array_length(NULLIF(t.items, 'null'::jsonb))\n"
    "FROM t WHERE t.x = :x\n"
    '"""\n'
)


def _scan(src: str) -> list[str]:
    p = _TESTS_DIR / "_jsonb_class_fixture_tmp.py"
    p.write_text(src)
    try:
        return A._scan_file(p)
    finally:
        p.unlink(missing_ok=True)


def test_regex_matches_coalesce_not_case():
    """The new wrapped-arg regex must match COALESCE/NULLIF but NOT
    the canonical CASE form (else it would false-flag the safe shape)."""
    assert A._JSONB_ARRAY_WRAPPED_RE.search(
        "jsonb_array_elements(COALESCE(so.line_items, '[]'::jsonb))"
    )
    assert A._JSONB_ARRAY_WRAPPED_RE.search(
        "jsonb_array_length(NULLIF(t.items, 'null'::jsonb))"
    )
    assert not A._JSONB_ARRAY_WRAPPED_RE.search(
        "jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items)="
        "'array' THEN so.line_items ELSE '[]'::jsonb END)"
    )


def test_coalesce_wrapped_no_typeof_is_flagged():
    findings = _scan(_COALESCE_BAD)
    assert any("guards SQL-NULL but NOT a JSON scalar" in f for f in findings), (
        f"COALESCE-wrapped jsonb_array_elements without typeof guard "
        f"must be flagged (the class). got: {findings}"
    )


def test_nullif_wrapped_no_typeof_is_flagged():
    findings = _scan(_NULLIF_BAD)
    assert any("guards SQL-NULL but NOT a JSON scalar" in f for f in findings)


def test_canonical_case_form_is_NOT_flagged():
    """The exact fix applied to chatbot_llm_fallback:256 — the
    scalar-safe canonical shape — must produce ZERO findings (no
    false positive on the pattern we tell people to use)."""
    findings = _scan(_CASE_GOOD)
    assert findings == [], f"canonical CASE form must be clean, got: {findings}"
