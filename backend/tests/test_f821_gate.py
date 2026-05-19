"""Contract test for the undefined-name (F821) preflight gate
(born 2026-05-19i).

The §21 sweep measured 9 LIVE latent NameError bugs in app/ (log×4
[error-path-firing — the exact recurring Sentry class], deque×2
[broken forward-ref annotations], linked / compute_decision /
shop_domain [scoping]). The fictional smoke harness + low-data prod
had hidden them; `name X is not defined` crashes the first request
to reach that path, with 1 merchant or 10k. Instances are fixed; the
preflight gate `pyflakes app/ scripts/ | grep -i 'undefined name'`
locks the CLASS.

This pins the gate's MECHANISM (cheap, deterministic — not a
re-scan of 474 files, which the preflight step itself already does):
pyflakes is installed AND emits the exact `undefined name` string the
gate greps for on an undefined name, and stays silent on a clean
snippet. If pyflakes vanished from the venv or changed its message
format, the gate would silently pass everything — this catches that.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


def _pyflakes(src: str) -> str:
    """Run the venv pyflakes over a snippet written to a temp FILE —
    the exact invocation the preflight gate uses (`pyflakes <paths>`,
    not stdin: `pyflakes -` is unreliable across versions)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "snippet.py"
        p.write_text(textwrap.dedent(src))
        return subprocess.run(
            [sys.executable, "-m", "pyflakes", str(p)],
            capture_output=True,
            text=True,
        ).stdout


def test_pyflakes_is_installed_and_runnable():
    r = subprocess.run(
        [sys.executable, "-m", "pyflakes", "--version"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, "pyflakes must be installed (requirements-dev.txt)"


def test_undefined_name_emits_the_grepped_string():
    """The gate greps for 'undefined name'. pyflakes MUST emit exactly
    that on an undefined name (the log/deque/shop_domain class)."""
    out = _pyflakes(
        """
        def f():
            return log.warning("boom")  # 'log' never bound — F821
        """
    )
    assert "undefined name" in out.lower(), (
        f"pyflakes must flag undefined 'log' with 'undefined name' "
        f"(the gate's grep token). got: {out!r}"
    )
    assert "log" in out


def test_clean_snippet_has_no_undefined_name():
    out = _pyflakes(
        """
        import logging
        log = logging.getLogger(__name__)

        def f():
            return log.warning("ok")
        """
    )
    assert "undefined name" not in out.lower(), (
        f"a correctly-bound name must NOT be flagged (no false gate). "
        f"got: {out!r}"
    )


def test_forward_ref_annotation_undefined_is_caught():
    """The deque×2 class: a name only in a quoted annotation that is
    in NO scope is still F821 (cosmetic at runtime, real if hints
    resolve) — the gate must catch this shape too."""
    out = _pyflakes(
        """
        X: "deque[int]" = {}   # 'deque' imported nowhere
        """
    )
    assert "undefined name" in out.lower()
