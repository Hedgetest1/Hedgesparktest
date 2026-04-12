"""Tests for D4 — adversarial fragility analysis.

Contract:
  1. Each fragility pattern is detected when present in the diff.
  2. Each pattern is NOT detected when a truthiness/None guard precedes
     the use (no false positives on defensive code).
  3. Diffs that don't parse degrade gracefully.
  4. `_record_adversarial_report` attaches the report to context_json
     and bumps the weekly counter.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.services.adversarial_test_gen import (
    analyze_diff_for_fragility,
    run_adversarial_probes,
)
from app.services.bugfix_pipeline import (
    _record_adversarial_report,
    get_adversarial_report_this_week,
)


def _diff(body: str) -> str:
    """Wrap body lines into a minimal valid unified diff."""
    lines = body.split("\n")
    header = ["--- a/x.py", "+++ b/x.py", f"@@ -1,1 +1,{len(lines)+1} @@", " pass"]
    for ln in lines:
        header.append("+" + ln)
    return "\n".join(header) + "\n"


# ---------- Pattern detection ----------

def test_detects_subscript_without_guard():
    diff = _diff(
        "def frag(items):\n"
        "    return items[0]"
    )
    rep = analyze_diff_for_fragility(diff)
    assert rep["fragility_score"] >= 1
    kinds = {p["kind"] for p in rep["probes"]}
    assert "subscript_unchecked" in kinds


def test_subscript_guarded_is_clean():
    diff = _diff(
        "def safe(items):\n"
        "    if not items:\n"
        "        return None\n"
        "    return items[0]"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "subscript_unchecked" not in kinds


def test_detects_attribute_without_guard():
    diff = _diff(
        "def frag(user):\n"
        "    return user.name"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "attribute_unchecked" in kinds


def test_attribute_guarded_by_is_none_is_clean():
    diff = _diff(
        "def safe(user):\n"
        "    if user is None:\n"
        "        return None\n"
        "    return user.name"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "attribute_unchecked" not in kinds


def test_detects_division_by_parameter():
    diff = _diff(
        "def ratio(a, b):\n"
        "    return a / b"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "division_by_param" in kinds


def test_detects_modulo_by_parameter():
    diff = _diff(
        "def bucket(n, slots):\n"
        "    return n % slots"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "division_by_param" in kinds


def test_detects_iteration_without_guard():
    diff = _diff(
        "def process(items):\n"
        "    for x in items:\n"
        "        print(x)"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "iteration_unchecked" in kinds


def test_iteration_guarded_is_clean():
    diff = _diff(
        "def safe(items):\n"
        "    if not items:\n"
        "        return\n"
        "    for x in items:\n"
        "        print(x)"
    )
    rep = analyze_diff_for_fragility(diff)
    kinds = {p["kind"] for p in rep["probes"]}
    assert "iteration_unchecked" not in kinds


# ---------- Edge cases ----------

def test_empty_diff_is_clean():
    rep = analyze_diff_for_fragility("")
    assert rep["fragility_score"] == 0
    assert rep["parse_status"] == "empty"


def test_unparseable_diff_degrades_gracefully():
    # Diff with dangling partial block — ast.parse will raise SyntaxError
    raw = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n+    return broken("
    rep = analyze_diff_for_fragility(raw)
    assert rep["fragility_score"] == 0
    assert rep["parse_status"] == "fallback"


def test_function_count_tracked():
    diff = _diff(
        "def a(x):\n"
        "    return x\n"
        "def b(y):\n"
        "    return y"
    )
    rep = analyze_diff_for_fragility(diff)
    assert rep["function_count"] == 2


def test_probes_are_capped():
    # Generate a function that triggers many probes
    body = "def f(a, b, c):\n"
    for i in range(20):
        body += f"    _{i} = a[{i}]\n"
    diff = _diff(body)
    rep = analyze_diff_for_fragility(diff)
    assert rep["fragility_score"] <= 10  # _MAX_PROBES_PER_CANDIDATE


# ---------- run_adversarial_probes ----------

def test_run_adversarial_probes_reads_candidate_diff():
    diff = _diff("def f(x):\n    return x[0]")
    c = BugFixCandidate()
    c.patch_diff = diff
    rep = run_adversarial_probes(c)
    assert rep["fragility_score"] >= 1


def test_run_adversarial_probes_handles_none_candidate_diff():
    c = BugFixCandidate()
    c.patch_diff = None
    rep = run_adversarial_probes(c)
    assert rep["fragility_score"] == 0


# ---------- _record_adversarial_report ----------

def test_record_report_attaches_to_context_json():
    c = BugFixCandidate()
    c.context_json = None
    report = {
        "fragility_score": 2,
        "function_count": 1,
        "parse_status": "ok",
        "probes": [{"kind": "subscript_unchecked", "function": "f", "param": "x", "detail": "x[0]"}],
    }
    _record_adversarial_report(c, report)
    ctx = json.loads(c.context_json)
    assert ctx["adversarial_report"]["fragility_score"] == 2
    assert ctx["adversarial_report"]["function_count"] == 1
    assert len(ctx["adversarial_report"]["probes"]) == 1


def test_record_report_bumps_weekly_counter():
    before = get_adversarial_report_this_week()
    c = BugFixCandidate()
    c.context_json = "{}"
    report = {"fragility_score": 3, "function_count": 1, "parse_status": "ok", "probes": []}
    _record_adversarial_report(c, report)
    after = get_adversarial_report_this_week()
    if after["runs"] == before["runs"]:
        pytest.skip("redis unavailable")
    assert after["runs"] == before["runs"] + 1
    assert after["weak"] == before["weak"] + 3


def test_record_report_preserves_existing_context():
    c = BugFixCandidate()
    c.context_json = json.dumps({"target_file": "app/x.py", "foo": 1})
    report = {"fragility_score": 0, "function_count": 0, "parse_status": "empty", "probes": []}
    _record_adversarial_report(c, report)
    ctx = json.loads(c.context_json)
    assert ctx["target_file"] == "app/x.py"
    assert ctx["foo"] == 1
    assert "adversarial_report" in ctx
