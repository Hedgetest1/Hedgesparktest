"""Locks the contract of `scripts/_audit_io.safe_read_text`.

The helper centralizes TOCTOU defense for all `audit_*.py` scripts that
do `rglob → read_text`. If any of these tests fail, every audit using
the helper is silently broken; protect them aggressively.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `scripts/` importable the same way preflight runs them.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _audit_io import safe_read_text  # noqa: E402


def test_returns_text_on_healthy_read(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("hello world", encoding="utf-8")
    assert safe_read_text(p) == "hello world"


def test_returns_none_when_file_missing(tmp_path):
    p = tmp_path / "never_created.py"
    assert safe_read_text(p) is None


def test_returns_none_on_permission_error(tmp_path, monkeypatch):
    p = tmp_path / "x.py"
    p.write_text("never read")

    def boom(self, *args, **kwargs):
        raise PermissionError("simulated chmod race")

    monkeypatch.setattr(Path, "read_text", boom)
    assert safe_read_text(p) is None


def test_propagates_unexpected_oserror(tmp_path, monkeypatch):
    p = tmp_path / "x.py"
    p.write_text("never read")

    def boom(self, *args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(OSError):
        safe_read_text(p)


def test_propagates_unicode_decode_error(tmp_path):
    """The default `errors="ignore"` swallows decode errors, but if a
    caller asks for strict decoding and the file is malformed we MUST
    propagate so the bug is visible — not silently mapped to None."""
    p = tmp_path / "x.py"
    p.write_bytes(b"\xff\xfe invalid utf-8 \xff")
    with pytest.raises(UnicodeDecodeError):
        safe_read_text(p, errors="strict")


def test_idiomatic_caller_loop_skips_disappeared_files(tmp_path):
    """Reproduces the documented caller pattern under a realistic mix
    of healthy + missing files — locks the end-to-end contract."""
    a = tmp_path / "a.py"
    a.write_text("alpha")
    b = tmp_path / "b.py"
    b.write_text("bravo")
    c_ghost = tmp_path / "ghost.py"  # never written

    collected: list[str] = []
    for path in [a, c_ghost, b]:
        text = safe_read_text(path)
        if text is None:
            continue
        collected.append(text)

    assert collected == ["alpha", "bravo"]


def test_default_kwargs_match_pathlib_signature(tmp_path):
    """Drop-in compatibility: the default kwargs (utf-8 + errors=ignore)
    must match the encoding profile that the audits historically used,
    so migration is mechanical and never changes behavior."""
    p = tmp_path / "x.py"
    p.write_bytes("café\n".encode("utf-8"))
    # default behavior — utf-8 decoding, errors ignored
    assert safe_read_text(p) == "café\n"
