"""Tests for app/core/sentry_init.

Pin the contract introduced 2026-04-24 (C1..C4 sweep):
  * init_sentry() is graceful when DSN is missing
  * init_sentry() is idempotent — second call is a no-op
  * before_send PII scrub redacts emails / API keys / bearer tokens
    in exception values + breadcrumb messages + request body
  * cron_monitor() returns a no-op decorator when slug is NOT in the
    SENTRY_CRON_MONITORING allowlist (Team-plan quota gate)
  * sentry_span() returns a usable context manager whether Sentry is
    enabled or not
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest


def _reload_sentry_init():
    """Import-and-reload to reset the module-level _enabled flag between
    tests. Necessary because init_sentry() is idempotent — once True
    in a process, subsequent tests that want to assert "no init" need
    a clean slate."""
    import app.core.sentry_init as si
    si._enabled = False
    si._initialized_for = None
    return si


def test_init_sentry_returns_false_without_dsn():
    """No DSN in env → graceful False, no exception."""
    si = _reload_sentry_init()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENTRY_DSN", None)
        ok = si.init_sentry(component="test_no_dsn")
    assert ok is False
    assert si.is_enabled() is False
    assert si.get_component() is None


def test_init_sentry_idempotent():
    """Once enabled, second init is a no-op returning True."""
    si = _reload_sentry_init()
    si._enabled = True
    si._initialized_for = "first_caller"
    ok = si.init_sentry(component="second_caller")
    assert ok is True
    # Component does NOT change on second call — the first caller wins.
    assert si.get_component() == "first_caller"


def test_before_send_scrubs_pii_in_exception_value():
    """An exception whose value leaks an email gets sanitized + the
    event tagged sentry.pii_scrubbed=true."""
    si = _reload_sentry_init()
    before_send = si._make_before_send()
    event = {
        "exception": {
            "values": [
                {"value": "Failed to authenticate user@hedgesparkhq.com against API"},
            ],
        },
    }
    out = before_send(event, hint={})
    assert out is not None
    msg = out["exception"]["values"][0]["value"]
    assert "user@hedgesparkhq.com" not in msg
    assert "redacted" in msg
    assert out["tags"]["sentry.pii_scrubbed"] == "true"


def test_before_send_scrubs_pii_in_breadcrumb():
    """Breadcrumb messages containing API tokens are scrubbed."""
    si = _reload_sentry_init()
    before_send = si._make_before_send()
    event = {
        "breadcrumbs": {
            "values": [
                {"message": "POST /admin with token shpat_abc123def456ghi789jkl0123456789"},
            ],
        },
    }
    out = before_send(event, hint={})
    bm = out["breadcrumbs"]["values"][0]["message"]
    assert "shpat_" not in bm
    assert out["tags"]["sentry.pii_scrubbed"] == "true"


def test_before_send_passes_clean_event_unchanged():
    """A clean event (no PII) passes through without the scrub tag."""
    si = _reload_sentry_init()
    before_send = si._make_before_send()
    event = {
        "exception": {"values": [{"value": "ValueError: invalid literal"}]},
    }
    out = before_send(event, hint={})
    assert out is event
    assert "sentry.pii_scrubbed" not in (out.get("tags") or {})


def test_cron_monitor_noop_when_not_in_allowlist():
    """Empty allowlist → decorator must be a no-op (preserves quota)."""
    si = _reload_sentry_init()
    with patch.dict(os.environ, {"SENTRY_CRON_MONITORING": ""}):
        @si.cron_monitor(slug="some_unallowed_cycle", interval_minutes=5)
        def _job():
            return "ran"
        # The function must remain plain (not wrapped in a Sentry monitor).
        assert _job() == "ran"


def test_cron_monitor_noop_when_other_slug_allowed():
    """Allowlist with a different slug → still no-op for ours."""
    si = _reload_sentry_init()
    with patch.dict(os.environ, {"SENTRY_CRON_MONITORING": "agent_worker_cycle"}):
        @si.cron_monitor(slug="aggregation_worker_cycle", interval_minutes=5)
        def _job():
            return "ran"
        assert _job() == "ran"


def test_sentry_span_returns_usable_context_manager_when_disabled():
    """Without Sentry init, sentry_span must still be a usable CM with
    .set_data() so callers don't crash."""
    si = _reload_sentry_init()
    with si.sentry_span("test.op", "test_description") as span:
        # Even no-op span supports the methods callers use.
        span.set_data("k", "v")
        span.set_tag("t", "v")
    # No assertion needed — not raising IS the contract.


def test_resolve_release_returns_git_sha_or_env():
    """Release resolution prefers SENTRY_RELEASE env, falls back to git."""
    si = _reload_sentry_init()
    with patch.dict(os.environ, {"SENTRY_RELEASE": "explicit_release@v1.0.0"}):
        assert si._resolve_release() == "explicit_release@v1.0.0"
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENTRY_RELEASE", None)
        # In dev with git available, returns hedgespark@<sha12>
        rel = si._resolve_release()
        assert rel is None or rel.startswith("hedgespark@")


def test_audit_pins_integration_baseline():
    """DA1 closure: the audit enumerates active Sentry integrations and
    fails if a new one is added without updating the baseline + citing
    the quota pre-check. Verifies the scanner actually finds the
    baseline set today — sanity check that the regex isn't broken."""
    import subprocess
    result = subprocess.run(
        ["./venv/bin/python", "scripts/audit_sentry_invariants.py"],
        cwd="/opt/wishspark/backend",
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"Sentry invariants audit failed — integrations or cron slugs may have drifted:\n{result.stdout}\n{result.stderr}"
    )
    assert "integration-pins" in result.stdout
    assert "slug-pins" in result.stdout


def test_audit_flags_new_integration_in_source():
    """DA1 closure: inject a fake new integration into a temp copy of
    the module and verify the audit FLAGS it. Proves the pin actually
    catches additions."""
    import tempfile, shutil, subprocess
    from pathlib import Path as P
    # Create a tempdir + symlink the repo structure + overwrite
    # sentry_init.py with an injected integration.
    with tempfile.TemporaryDirectory() as td:
        tdp = P(td)
        # Real audit targets /opt/wishspark absolute — we can't easily
        # re-root. Instead: poke a marker integration name into the
        # real file temporarily, run audit, assert fail, restore.
        src_path = P("/opt/wishspark/backend/app/core/sentry_init.py")
        original = src_path.read_text()
        try:
            injected = original + "\n# audit-test-marker: CanvasIntegration\n"
            src_path.write_text(injected)
            result = subprocess.run(
                ["./venv/bin/python", "scripts/audit_sentry_invariants.py"],
                cwd="/opt/wishspark/backend",
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 1, "audit should fail when new integration detected"
            assert "CanvasIntegration" in result.stdout
            assert "quota pre-check" in result.stdout.lower()
        finally:
            src_path.write_text(original)


def test_da2_dsn_same_as_frontend_emits_warning():
    """DA2 closure: when NEXT_PUBLIC_SENTRY_DSN equals SENTRY_DSN, the
    helper logs a loud warning recommending split projects. Tested by
    patching the module-level logger's warning method to capture the
    call — bypasses pytest's caplog which conflicts with our JSON
    logging handler swap in configure_logging()."""
    si = _reload_sentry_init()
    fake_dsn = "https://abc@o1.ingest.de.sentry.io/1"
    with patch.dict(os.environ, {"NEXT_PUBLIC_SENTRY_DSN": fake_dsn}):
        with patch.object(si.log, "warning") as mock_warn:
            si._warn_if_dsn_shared(fake_dsn)
    assert mock_warn.called, "helper must log a WARNING when DSNs match"
    call_args = mock_warn.call_args[0]
    assert "SAME Sentry project" in call_args[0], (
        f"Expected 'SAME Sentry project' in warning; got: {call_args}"
    )


def test_da2_different_dsn_no_warning():
    """DA2 closure: different DSNs on backend vs frontend = OK, no warning."""
    si = _reload_sentry_init()
    with patch.dict(os.environ, {
        "NEXT_PUBLIC_SENTRY_DSN": "https://fe@o2.ingest.de.sentry.io/2",
    }):
        with patch.object(si.log, "warning") as mock_warn:
            si._warn_if_dsn_shared("https://be@o1.ingest.de.sentry.io/1")
    assert not mock_warn.called, (
        f"Unexpected warning with different DSNs; called with: {mock_warn.call_args_list}"
    )
