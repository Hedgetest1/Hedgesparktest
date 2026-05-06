"""Smoke tests for app.services.brain_tool — the autonomous brain's
tool-spawn dispatcher (CLAUDE.md §21.6 hook #4 + §21.7).

Coverage:
  * BrainTool import + factory
  * parallel_grep returns hits across the app/ tree
  * invoke_audit returns a structured response for an existing script
  * web_search stub correctly returns [] (R-blocker:infra-spend)
  * spawn_investigation aggregates grep + audits

These run hermetically (no DB / Redis / HTTP). The grep + subprocess
calls hit the real backend tree but are bounded by per-call timeouts.
"""
from app.services.brain_tool import (
    BrainTool,
    brain_dispatch,
    spawn_investigation,
)


def test_brain_dispatch_returns_brain_tool():
    t = brain_dispatch()
    assert isinstance(t, BrainTool)


def test_parallel_grep_returns_hits_for_real_pattern():
    t = brain_dispatch()
    # `class BrainTool` exists exactly once in app/services/brain_tool.py
    out = t.parallel_grep([r"class\s+BrainTool"], scope="app/")
    assert isinstance(out, dict)
    hits = out.get(r"class\s+BrainTool", [])
    assert any("brain_tool.py" in h for h in hits), (
        f"expected at least one brain_tool.py hit, got: {hits!r}"
    )


def test_parallel_grep_handles_empty_patterns():
    t = brain_dispatch()
    assert t.parallel_grep([]) == {}


def test_parallel_grep_rejects_path_escape_scope():
    t = brain_dispatch()
    # ".." in scope should be refused → empty dict
    assert t.parallel_grep([r"x"], scope="../etc") == {}


def test_invoke_audit_existing_script():
    t = brain_dispatch()
    res = t.invoke_audit("audit_brain_propagation_hooks.py")
    assert "exit_code" in res and "stdout" in res and "stderr" in res
    # The audit should currently exit 0 (5/5 hooks)
    assert res["exit_code"] == 0
    assert "5/5 hooks present" in res["stdout"]


def test_invoke_audit_missing_script():
    t = brain_dispatch()
    res = t.invoke_audit("does_not_exist_audit.py")
    assert res["exit_code"] is None
    assert res["stderr"] == "missing"


def test_invoke_audit_rejects_path_separator():
    t = brain_dispatch()
    res = t.invoke_audit("../etc/passwd")
    assert res["exit_code"] is None
    assert res["stderr"] == "bad_name"


def test_web_search_returns_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("BRAIN_WEB_SEARCH_PROVIDER", raising=False)
    t = brain_dispatch()
    assert t.web_search("anything") == []


def test_web_search_empty_for_empty_query():
    t = brain_dispatch()
    assert t.web_search("") == []


def test_spawn_investigation_aggregates():
    out = spawn_investigation(
        patterns=[r"class\s+BrainTool"],
        audits=["audit_brain_propagation_hooks.py"],
    )
    assert "grep" in out and "audits" in out
    assert any(
        "brain_tool.py" in h
        for h in out["grep"].get(r"class\s+BrainTool", [])
    )
    assert (
        out["audits"]["audit_brain_propagation_hooks.py"]["exit_code"] == 0
    )
