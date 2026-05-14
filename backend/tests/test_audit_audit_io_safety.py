"""Self-test for `scripts/audit_audit_io_safety.py` — proves the
preventer catches the bug class it was born to catch, including the
import-without-use bypass that was the v1→v2 sharpening trigger.

If any of these tests fail, the preventer is silently broken and a
future audit can ship a TOCTOU regression past it. Lock the contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import audit_audit_io_safety as preventer  # noqa: E402


def _scan(text: str) -> list[str]:
    """Run the preventer's AST scanner on a synthetic source string."""
    return preventer._scan_module(text, "<synthetic>")


def test_clean_module_with_no_glob_returns_no_findings():
    src = """
from pathlib import Path

p = Path("/etc/hostname")
text = p.read_text()
"""
    assert _scan(src) == []


def test_glob_loop_with_safe_read_text_is_clean():
    src = """
from pathlib import Path
from _audit_io import safe_read_text

for p in Path('.').rglob('*.py'):
    text = safe_read_text(p)
    if text is None:
        continue
    print(text)
"""
    assert _scan(src) == []


def test_glob_loop_with_explicit_filenotfound_is_clean():
    src = """
from pathlib import Path

for p in Path('.').rglob('*.py'):
    try:
        text = p.read_text()
    except (FileNotFoundError, PermissionError):
        continue
    print(text)
"""
    assert _scan(src) == []


def test_glob_loop_with_raw_read_text_is_flagged():
    """Bug class — must fire."""
    src = """
from pathlib import Path

for p in Path('.').rglob('*.py'):
    text = p.read_text()
    print(text)
"""
    findings = _scan(src)
    assert len(findings) == 1, findings
    assert "p.read_text" in findings[0]


def test_import_without_use_is_flagged_v2_sharpening():
    """The v1→v2 sharpening test: importing safe_read_text but still
    calling raw `.read_text()` at the use-site must be flagged. Born
    2026-05-14 from independent close audit that found
    audit_test_hermeticity.py importing the helper but using raw
    read_text — v1 preventer passed, v2 must catch."""
    src = """
from pathlib import Path
from _audit_io import safe_read_text  # imported but not used at site

for p in Path('.').rglob('*.py'):
    text = p.read_text()  # raw — bug class still latent
    print(text)
"""
    findings = _scan(src)
    assert len(findings) == 1, findings
    assert "p.read_text" in findings[0]


def test_glob_loop_with_open_context_manager_is_flagged():
    """`.open(...)` is the sibling pattern to `.read_text(...)` — same
    race, same defense required."""
    src = """
from pathlib import Path

for p in Path('.').rglob('*.py'):
    with p.open() as f:
        text = f.read()
"""
    findings = _scan(src)
    assert len(findings) == 1, findings
    assert "p.open" in findings[0]


def test_blanket_except_exception_is_NOT_sufficient():
    """`except Exception` swallows everything including FileNotFoundError,
    so it technically covers the race — but it ALSO swallows real bugs
    (AttributeError on a typo, ImportError on a misnamed module, etc.).
    The doctrine requires explicit defense against the named race
    classes, not blanket Exception catch. Locks the doctrine choice."""
    src = """
from pathlib import Path

for p in Path('.').rglob('*.py'):
    try:
        text = p.read_text()
    except Exception:
        continue
    print(text)
"""
    findings = _scan(src)
    assert len(findings) == 1, findings


def test_for_in_sorted_glob_pattern_is_caught():
    """`for x in sorted(p.rglob('*.py'))` — common pattern. The bound
    name `x` must still be tracked back to the glob loop."""
    src = """
from pathlib import Path

for x in sorted(Path('.').rglob('*.py')):
    text = x.read_text()
    print(text)
"""
    findings = _scan(src)
    assert len(findings) == 1, findings


def test_nested_glob_loops_each_tracked_independently():
    """Two glob loops in the same module — both bound names tracked."""
    src = """
from pathlib import Path

for a in Path('/a').rglob('*.py'):
    print(a.read_text())

for b in Path('/b').glob('*.txt'):
    print(b.read_text())
"""
    findings = _scan(src)
    assert len(findings) == 2, findings


def test_real_preventer_runs_clean_against_current_codebase():
    """Integration test: run the actual preventer's main() against the
    real scripts/ directory. After the 2026-05-14 sweep this MUST be
    clean. If it ever regresses, this test fires before invariant_
    monitor does — caught at unit-test time, not at runtime."""
    rc = preventer.main()
    assert rc == 0, "preventer found regressions — see preceding output"
