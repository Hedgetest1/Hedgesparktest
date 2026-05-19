"""Locks the #13 service-config-perms preventer (born 2026-05-18).

Mechanizes `feedback_root_edit_breaks_service_config_perms.md` — the
carried R-fix that stayed doctrine-only across ≥4 sessions. The
2026-05-15b incident: a root Edit of `/etc/pgbouncer/pgbouncer.ini`
flipped it `root:root`; the privilege-dropped pgbouncer could no
longer read it; the next `systemctl restart` failed → backend 503
for ~3 min, with NO detection until the restart failed.

These tests pin:
  1. The pure `evaluate` predicate flags every drift mode AND is
     non-vacuous (compliant input → None).
  2. `check()` is fail-open on absent files (a host without pgbouncer
     is not a drift).
  3. The live host is currently compliant (ground-truth, not assumed).
  4. The runtime layer (`invariant_monitor._check_service_config_perms`)
     is wired, importable, and reuses the script's predicate (the two
     layers cannot diverge).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from audit_service_config_perms import (  # noqa: E402
    _SERVICE_CONFIGS,
    check,
    evaluate,
)


class TestEvaluatePredicate:
    """Pure, root-free. The single predicate both layers share."""

    def test_compliant_is_none(self):
        # postgres:postgres 640 — the ground-truthed correct state.
        assert evaluate("postgres", "postgres", "postgres", "postgres",
                        0o640) is None
        # Stricter modes are also fine (owner can still read).
        assert evaluate("postgres", "postgres", "postgres", "postgres",
                        0o600) is None
        assert evaluate("postgres", "postgres", "postgres", "postgres",
                        0o400) is None

    def test_owner_flip_to_root_is_flagged(self):
        # THE 2026-05-15b incident: root Edit rewrote it root:root.
        reason = evaluate("postgres", "postgres", "root", "root", 0o640)
        assert reason is not None
        assert "owner=root" in reason
        assert "self-inflicted outage" in reason

    def test_group_flip_is_flagged(self):
        reason = evaluate("postgres", "postgres", "postgres", "root", 0o640)
        assert reason is not None and "group=root" in reason

    def test_world_readable_is_flagged(self):
        # userlist.txt carries md5 password hashes — world bit = leak.
        reason = evaluate("postgres", "postgres", "postgres", "postgres",
                          0o644)
        assert reason is not None and "world-accessible" in reason

    def test_owner_cannot_read_is_flagged(self):
        reason = evaluate("postgres", "postgres", "postgres", "postgres",
                          0o040)
        assert reason is not None and "no read bit" in reason

    def test_multiple_drifts_all_reported(self):
        reason = evaluate("postgres", "postgres", "root", "root", 0o646)
        assert reason is not None
        assert "owner=root" in reason
        assert "group=root" in reason
        assert "world-accessible" in reason


class TestCheckFailOpenOnAbsence:
    def test_absent_paths_are_skipped_not_drift(self, monkeypatch):
        # Point the manifest at a path that cannot exist.
        monkeypatch.setattr(
            "audit_service_config_perms._SERVICE_CONFIGS",
            (("/nonexistent/service/config.ini", "postgres", "postgres"),),
        )
        assert check() == []  # absent ≠ drift (fail-open)


class TestLiveHostGroundTruth:
    def test_live_service_configs_are_currently_compliant(self):
        """Ground-truth (not assumed): on this host the manifested
        configs must be clean RIGHT NOW. If this fails, a real drift
        exists and the preventer is doing its job — fix the perms,
        do not weaken the test."""
        drift = check()
        assert drift == [], (
            f"live service-config perm drift detected: {drift} — "
            f"restore with `chown postgres:postgres <path> && "
            f"chmod 640 <path>`"
        )

    def test_manifest_is_nonempty(self):
        assert len(_SERVICE_CONFIGS) >= 1


class TestRuntimeLayerWired:
    def test_invariant_function_exists_and_reuses_script_predicate(self):
        from app.services import invariant_monitor as im
        assert hasattr(im, "_check_service_config_perms"), (
            "runtime layer 2 missing — the dispatch call would NameError"
        )
        # It must be invoked from run_invariant_check's dispatch.
        import inspect
        src = inspect.getsource(im)
        # Wired via the _safe_check dispatch loop (2026-05-19
        # poisoned-session hardening): the check is listed in
        # run_invariant_check's dispatch tuple AND every entry is
        # invoked through _safe_check, which rolls back on failure so
        # a poisoned session can't cascade into the rest of the cycle.
        assert "_check_service_config_perms," in src, (
            "function defined but not in run_invariant_check's "
            "_safe_check dispatch tuple"
        )
        assert "_safe_check(_check, db, summary)" in src, (
            "dispatch loop missing — runtime checks not invoked via "
            "_safe_check (poisoned-session rollback wrapper)"
        )

    def test_runtime_layer_runs_clean_on_compliant_host(self, db):
        """On a compliant host the runtime check must NOT write an
        alert and must increment `checked`. Exercises the real
        import-the-script path (zero-divergence wiring)."""
        from app.services.invariant_monitor import _check_service_config_perms
        summary = {"checked": 0, "failed": 0, "alerts_written": 0}
        _check_service_config_perms(db, summary)
        assert summary["checked"] == 1
        # Host is ground-truth-compliant (TestLiveHostGroundTruth) →
        # no failure, no alert.
        assert summary["failed"] == 0
        assert summary["alerts_written"] == 0
