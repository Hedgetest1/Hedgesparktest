"""Shared TOCTOU-safe I/O helpers for preflight audit scripts.

Born 2026-05-14 after invariant_monitor fired CRITICAL invariant_regression
twice in 24h (audit_cte_missing_comma + audit_tier_cost_literals) on the
same race: `test_audit_data_truth_gate` creates `_test_hardcoded_eur_
DELETE_ME.py` under `app/services/` as a fixture, deletes it at teardown.
A concurrent `invariant_monitor` cycle: `Path.rglob("*.py")` discovers
the file, `path.read_text()` crashes with FileNotFoundError, audit exits
non-zero, alert fires. The bug was latent in 70+ other audits — none had
fired only because of iteration-order / scheduling luck.

Centralizing the defense here:
  * removes 70+ duplicated try/except blocks
  * gives the contract one test surface
  * lets `audit_audit_io_safety.py` enforce coverage by import-presence

The defense is intentionally narrow — we swallow the two race signatures
that are *expected* during normal pytest execution (file deleted,
permission flipped), and propagate every other I/O exception so genuine
disk/encoding faults still surface.
"""
from __future__ import annotations

from pathlib import Path


def safe_read_text(
    path: Path,
    encoding: str = "utf-8",
    errors: str = "ignore",
) -> str | None:
    """Race-safe `Path.read_text` for `rglob`-driven audit scans.

    Returns the file contents on success.

    Returns `None` when the path disappeared or became unreadable
    between discovery (`rglob` yields a path) and read (we open it).
    The two race signatures we swallow are `FileNotFoundError` (file
    deleted by a concurrent test fixture) and `PermissionError` (mode
    flipped — happens when CI workers chmod fixtures during teardown).

    All other exceptions propagate — disk errors, decoding faults,
    OS-level failures should still fail loudly so we don't paper over
    real problems.

    Idiomatic caller pattern (the only correct one):

        for path in root.rglob("*.py"):
            text = safe_read_text(path)
            if text is None:
                continue  # raced with concurrent delete; next cycle re-scans
            ...

    Parameters mirror `Path.read_text` exactly so this is a drop-in
    replacement for the typical `path.read_text(encoding="utf-8",
    errors="ignore")` call site.
    """
    try:
        return path.read_text(encoding=encoding, errors=errors)
    except (FileNotFoundError, PermissionError):
        return None
