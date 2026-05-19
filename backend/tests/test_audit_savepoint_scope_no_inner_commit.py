"""Ground-truth contract tests for the semantic preventer
scripts/audit_savepoint_scope_no_inner_commit.py (born 2026-05-19c).

The audit is the STATIC counterpart to savepoint_scope's runtime
self-enforcing guard — it must, at preflight:
  1. DETECT a `with savepoint_scope(db):` whose body calls (directly
     or transitively via a bare-name function) a helper that issues a
     full db.commit() — the exact d15ada0 #1 class that silently
     regressed Klaviyo Pro-push.
  2. NOT false-positive on method-name collisions (`.add`/`.get`/
     `.first` resolving to unrelated top-level defs that commit) — the
     nudge_compose_task:70 FP that the Name-only resolver fixed.
  3. Be non-vacuous: fail if zero savepoint sites are discovered.
  4. Honor the `# savepoint-scope: commits-own-session` opt-out.

Tests build a synthetic app/ tree and point the audit's module
globals at it (the audit exposes _APP/_DATABASE_PY/_MIN_SITES exactly
so it is testable — a testable preventer is part of the jewel).
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

A = importlib.import_module("scripts.audit_savepoint_scope_no_inner_commit")


def _mk(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


@pytest.fixture()
def _app(tmp_path, monkeypatch):
    app = tmp_path / "app"
    # minimal stand-in for the primitive's home file (excluded by audit)
    _mk(app, "core/database.py", "def savepoint_scope(s):\n    yield\n")
    monkeypatch.setattr(A, "_APP", app)
    monkeypatch.setattr(A, "_DATABASE_PY", (app / "core/database.py").resolve())
    monkeypatch.setattr(A, "_MIN_SITES", 1)
    return app


def test_detects_transitive_committing_helper(_app):
    """d15ada0 #1 shape: savepoint wraps a bare-name call to a helper
    that commits → MUST fail."""
    _mk(_app, "x/bad.py", (
        "from app.core.database import savepoint_scope\n"
        "def committing_helper(db):\n"
        "    db.add(1)\n"
        "    db.commit()\n"
        "def run(db, items):\n"
        "    for it in items:\n"
        "        try:\n"
        "            with savepoint_scope(db):\n"
        "                committing_helper(db)\n"
        "        except Exception:\n"
        "            continue\n"
    ))
    assert A.main() == 1  # detected


def test_no_false_positive_on_method_name_collision(_app):
    """The nudge_compose_task:70 FP: the savepoint body only does
    method calls (`db.query(...).first()`, `obj.add(...)`) whose bare
    attr names collide with an UNRELATED top-level `def add()` that
    commits its OWN session. Must NOT flag."""
    _mk(_app, "y/unrelated.py", (
        "def add(x):\n"           # collides with `.add(` method name
        "    from app.core.database import savepoint_scope  # noqa\n"
        "    s = object()\n"
        "    return x\n"
    ))
    _mk(_app, "y/clean.py", (
        "from app.core.database import savepoint_scope\n"
        "def run(db, rows):\n"
        "    for r in rows:\n"
        "        try:\n"
        "            with savepoint_scope(db):\n"
        "                obj = db.query(r).first()\n"
        "                obj.add(1)\n"          # method call, name 'add'
        "                db.flush()\n"
        "        except Exception:\n"
        "            continue\n"
    ))
    assert A.main() == 0  # method-name collision must NOT FP


def test_vacuous_when_no_sites(_app, monkeypatch):
    monkeypatch.setattr(A, "_MIN_SITES", 1)
    _mk(_app, "z/nosite.py", "def f():\n    return 1\n")
    assert A.main() == 1  # 0 sites < floor → vacuous → fail-loud


def test_opt_out_comment_honored(_app):
    _mk(_app, "w/optout.py", (
        "from app.core.database import savepoint_scope\n"
        "def committing_helper(db):\n"
        "    db.commit()\n"
        "def run(db, items):\n"
        "    for it in items:\n"
        "        try:\n"
        "            with savepoint_scope(db):  # savepoint-scope: commits-own-session — synthetic test\n"
        "                committing_helper(db)\n"
        "        except Exception:\n"
        "            continue\n"
    ))
    # opt-out suppresses this site → 0 sites counted → vacuity floor
    # (1) trips → rc 1, but NOT a violation. Assert it's the vacuity
    # path (no violation printed), not a detection.
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = A.main()
    out = buf.getvalue()
    assert rc == 1 and "vacuous" in out and "wrapping a committing body" not in out
