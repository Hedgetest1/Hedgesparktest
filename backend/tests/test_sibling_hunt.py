"""Test sibling_hunt — Sprint A of CTO-brain pipeline upgrade.

Pins:
  * Signature distillation from unified-diff removed lines
  * Parameter normalization (numbers, short quoted strings)
  * find_hits greps recursively, excludes already-fixed files
  * scan_and_queue creates child candidates with parent FK
  * Feature-flag off by default (pipeline paused pre-merchant)
  * Recursion guard: sibling candidate does NOT spawn more siblings
  * Dedup: second scan doesn't duplicate already-queued children
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.services import sibling_hunt


@pytest.fixture
def enable_sibling_hunt(monkeypatch):
    monkeypatch.setenv("SIBLING_HUNT_ENABLED", "1")
    yield


def test_is_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("SIBLING_HUNT_ENABLED", raising=False)
    assert sibling_hunt.is_enabled() is False


def test_is_enabled_on_via_env(monkeypatch):
    monkeypatch.setenv("SIBLING_HUNT_ENABLED", "1")
    assert sibling_hunt.is_enabled() is True


def test_distill_signature_extracts_removed_lines():
    """Every `-` line (not diff header) becomes a signature."""
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def f():\n"
        '-    return x == 42 and verified_count > 0\n'
        '+    return x == 99 and verified_count >= 1\n'
        " print(x)\n"
    )
    sigs = sibling_hunt.distill_signature(diff)
    assert len(sigs) == 1
    # Signature should match the pattern with ANY number in place of 42
    import re
    assert re.search(sigs[0], "return x == 42 and verified_count > 0")
    assert re.search(sigs[0], "return x == 123 and verified_count > 99")


def test_distill_signature_skips_short_lines():
    """Lines < 20 chars discarded (too noisy to grep)."""
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@\n"
        "-short\n"
        "+long replacement that is long enough\n"
    )
    sigs = sibling_hunt.distill_signature(diff)
    assert sigs == []


def test_distill_signature_normalizes_numbers_and_strings():
    """Numeric literals and short quoted strings parameterized."""
    diff = (
        "--- a/y.py\n"
        "+++ b/y.py\n"
        "@@\n"
        '-    assert count == 42, "expected 42"\n'
        '+    assert count == result, "expected result"\n'
    )
    sigs = sibling_hunt.distill_signature(diff)
    assert len(sigs) == 1
    import re
    # Should match variants with different numbers and strings
    assert re.search(sigs[0], 'assert count == 42, "expected 42"')
    assert re.search(sigs[0], 'assert count == 99, "different msg"')


def test_distill_signature_empty_diff():
    assert sibling_hunt.distill_signature("") == []
    assert sibling_hunt.distill_signature(None) == []


def test_find_hits_greps_real_tree(tmp_path, monkeypatch):
    """Synthetic source tree + known signature finds expected hits."""
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\nassert val is None\n")
    (root / "pkg" / "b.py").write_text("y = 2\nassert other is None\n")
    (root / "pkg" / "c.py").write_text("z = 3\nprint('hi')\n")

    # Monkeypatch BACKEND_ROOT so relative-path calculation works
    monkeypatch.setattr(sibling_hunt, "BACKEND_ROOT", tmp_path)

    sig = r"assert\ \w+\ is\ None"
    hits = sibling_hunt.find_hits(sig, roots=(root,))
    files = sorted(h.file for h in hits)
    assert files == ["src/pkg/a.py", "src/pkg/b.py"]


def test_find_hits_excludes_parent_file(tmp_path, monkeypatch):
    root = tmp_path / "src"
    root.mkdir()
    (root / "already_fixed.py").write_text("assert row is None\n")
    (root / "other.py").write_text("assert row is None\n")
    monkeypatch.setattr(sibling_hunt, "BACKEND_ROOT", tmp_path)

    hits = sibling_hunt.find_hits(
        r"assert\ \w+\ is\ None",
        exclude_files=frozenset(["src/already_fixed.py"]),
        roots=(root,),
    )
    assert len(hits) == 1
    assert hits[0].file == "src/other.py"


def test_scan_and_queue_no_op_when_disabled(db, monkeypatch):
    """Feature flag OFF → empty return, zero children created."""
    monkeypatch.delenv("SIBLING_HUNT_ENABLED", raising=False)
    parent = BugFixCandidate(
        status="applied",
        source_type="ops_alert",
        source_ref="probe:x",
        title="test parent",
        patch_diff="--- a/x.py\n+++ b/x.py\n-    assert x is None\n+    assert x is not None\n",
    )
    db.add(parent)
    db.flush()
    result = sibling_hunt.scan_and_queue(db, parent)
    assert result == []


def test_scan_and_queue_recursion_guard(db, enable_sibling_hunt):
    """A candidate whose source_type is already 'sibling' MUST NOT
    trigger another hunt — prevents runaway chains."""
    parent = BugFixCandidate(
        status="applied",
        source_type="sibling",  # this is already a sibling
        source_ref="sibling:99:foo.py:10",
        title="not-root sibling",
        patch_diff="--- a/x.py\n+++ b/x.py\n-    assert x is None real long pattern here\n",
    )
    db.add(parent)
    db.flush()
    result = sibling_hunt.scan_and_queue(db, parent)
    assert result == []


def test_scan_and_queue_creates_children(db, enable_sibling_hunt, monkeypatch, tmp_path):
    """End-to-end: enabled + real signature + synthetic tree → children."""
    # Synthetic search tree
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.py").write_text("assert row is None  # a\n")
    (root / "b.py").write_text("assert row is None  # b\n")
    (root / "c.py").write_text("unrelated code\n")

    monkeypatch.setattr(sibling_hunt, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(sibling_hunt, "_SEARCH_ROOTS", (root,))

    parent = BugFixCandidate(
        status="applied",
        source_type="ops_alert",
        source_ref="probe:scan",
        title="scan parent",
        # Removed line matches both a.py and b.py
        patch_diff=(
            "--- a/already_fixed.py\n"
            "+++ b/already_fixed.py\n"
            "@@\n"
            "-    assert row is None  # long enough to exceed min length\n"
            "+    assert row is not None  # fixed\n"
        ),
        patch_files=json.dumps(["src/already_fixed.py"]),
    )
    db.add(parent)
    db.flush()

    children_ids = sibling_hunt.scan_and_queue(db, parent)
    assert len(children_ids) == 2
    children = db.query(BugFixCandidate).filter(
        BugFixCandidate.id.in_(children_ids)
    ).all()
    for c in children:
        assert c.source_type == "sibling"
        assert c.parent_candidate_id == parent.id
        assert c.status == "open"
        assert c.source_ref.startswith(f"sibling:{parent.id}:src/")


def test_scan_and_queue_dedups_existing(db, enable_sibling_hunt, monkeypatch, tmp_path):
    """A second scan_and_queue on the same parent doesn't create
    duplicate children (source_ref uniqueness per parent)."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.py").write_text("assert row is None  # a\n")
    monkeypatch.setattr(sibling_hunt, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(sibling_hunt, "_SEARCH_ROOTS", (root,))

    parent = BugFixCandidate(
        status="applied",
        source_type="ops_alert",
        source_ref="probe:dedup",
        title="dedup parent",
        patch_diff=(
            "--- a/x.py\n+++ b/x.py\n@@\n"
            "-    assert row is None  # long enough to exceed min length\n"
        ),
        patch_files=json.dumps(["src/x.py"]),
    )
    db.add(parent)
    db.flush()

    first = sibling_hunt.scan_and_queue(db, parent)
    second = sibling_hunt.scan_and_queue(db, parent)
    assert len(first) == 1
    assert second == []  # second scan finds nothing new to queue
