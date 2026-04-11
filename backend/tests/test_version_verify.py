"""
Tests for the process-version snapshot used by deploy_gate to verify
pm2 restarts actually loaded the new code.

This is the regression guard against the 2026-04-11 silent-stale-deploy
incident where the backend ran for 91 hours on an old codebase while
the tests were passing against the new code on disk.
"""
from __future__ import annotations

import os


def test_version_module_captures_git_sha_at_import():
    from app.core.version import GIT_SHA, get_version_info
    info = get_version_info()
    assert "git_sha" in info
    assert "git_sha_short" in info
    assert "process_started_at" in info
    assert "pid" in info
    # SHA should either be a 40-char hex string or the fallback 'unknown'
    assert info["git_sha"] == "unknown" or len(info["git_sha"]) == 40
    assert info["pid"] == os.getpid()


def test_version_is_immutable_across_calls():
    """Calling get_version_info() twice returns the exact same SHA —
    proving it was captured once at import, not on every call."""
    from app.core.version import get_version_info
    a = get_version_info()
    b = get_version_info()
    assert a["git_sha"] == b["git_sha"]
    assert a["process_started_at"] == b["process_started_at"]


def test_system_health_exposes_version(client):
    """GET /system/health must include a `version` block with git_sha."""
    resp = client.get("/system/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "version" in body, "/system/health did not expose version"
    assert "git_sha" in body["version"]


def test_deploy_gate_postdeploy_rejects_sha_mismatch(monkeypatch):
    """deploy_gate.run_postdeploy must fail when reported sha != disk sha."""
    import app.core.version  # noqa: F401 — ensure module is importable

    # We patch the deploy_gate helpers to simulate a version mismatch.
    import scripts.deploy_gate as dg

    # Fake health returns "different-sha-abcdef"
    monkeypatch.setattr(
        dg, "_curl_health",
        lambda: (True, {"status": "ok", "version": {"git_sha": "deadbeef" * 5}}),
    )
    # Fake pm2 restart counts (no crash loops)
    monkeypatch.setattr(dg, "_pm2_restart_counts", lambda: {})
    # Fake current_commit returns a different SHA
    monkeypatch.setattr(dg, "_current_commit", lambda: "abc" * 13 + "a")

    # Avoid state-dir side effects
    monkeypatch.setattr(dg, "_ensure_state_dir", lambda: None)
    monkeypatch.setattr(
        dg, "_STATE_DIR",
        type("FakeDir", (), {
            "__truediv__": lambda self, name: type("FakeFile", (), {
                "read_text": lambda s: "{}",
                "exists": lambda s: False,
            })(),
        })(),
    )

    # Run postdeploy WITHOUT auto_rollback — should return 1 on mismatch
    exit_code = dg.run_postdeploy(auto_rollback=False)
    assert exit_code == 1, "version mismatch must block postdeploy"


def test_deploy_gate_postdeploy_accepts_matching_sha(monkeypatch):
    """When reported == disk SHA, postdeploy passes."""
    import scripts.deploy_gate as dg

    matching = "cafebabe" * 5
    monkeypatch.setattr(
        dg, "_curl_health",
        lambda: (True, {"status": "ok", "version": {"git_sha": matching}}),
    )
    monkeypatch.setattr(dg, "_pm2_restart_counts", lambda: {})
    monkeypatch.setattr(dg, "_current_commit", lambda: matching)
    monkeypatch.setattr(dg, "_ensure_state_dir", lambda: None)
    monkeypatch.setattr(
        dg, "_STATE_DIR",
        type("FakeDir", (), {
            "__truediv__": lambda self, name: type("FakeFile", (), {
                "read_text": lambda s: "{}",
                "exists": lambda s: False,
            })(),
        })(),
    )
    exit_code = dg.run_postdeploy(auto_rollback=False)
    assert exit_code == 0


def test_deploy_gate_skips_verify_if_unknown_sha_reported(monkeypatch):
    """If the running process reports version.git_sha='unknown' we warn
    but do not hard-fail the deploy — backward compat with older builds
    that don't expose the version endpoint yet."""
    import scripts.deploy_gate as dg

    monkeypatch.setattr(
        dg, "_curl_health",
        lambda: (True, {"status": "ok", "version": {"git_sha": "unknown"}}),
    )
    monkeypatch.setattr(dg, "_pm2_restart_counts", lambda: {})
    monkeypatch.setattr(dg, "_current_commit", lambda: "abc" * 13 + "b")
    monkeypatch.setattr(dg, "_ensure_state_dir", lambda: None)
    monkeypatch.setattr(
        dg, "_STATE_DIR",
        type("FakeDir", (), {
            "__truediv__": lambda self, name: type("FakeFile", (), {
                "read_text": lambda s: "{}",
                "exists": lambda s: False,
            })(),
        })(),
    )
    exit_code = dg.run_postdeploy(auto_rollback=False)
    assert exit_code == 0, "unknown SHA must downgrade to warn, not fail"
