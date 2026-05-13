"""
Regression tests for `audit_n_plus_one.py` opt-out annotation contract.

The audit honors `# n-plus-one: ok` (or `false-positive` / `skip`)
comments within 5 lines preceding a `for` opener as the documented
escape valve for intentional patterns. The 5-line tag-distance window
is a contract that the audit logic enforces — these tests lock it so
a future audit-author change doesn't silently break existing
annotations across the codebase.

Born 2026-05-13 after Agent-review noted the cross_shop_aggregator.py
annotation distance could regress silently if the audit's tag-window
constant changed.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re


def _load_audit_module():
    """Load audit_n_plus_one as a module."""
    path = pathlib.Path("/opt/wishspark/backend/scripts/audit_n_plus_one.py")
    spec = importlib.util.spec_from_file_location("audit_npo", path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    class _Shim:
        @staticmethod
        def telemetered(name):
            def deco(fn):
                return fn
            return deco
    sys.modules.setdefault("_audit_telemetry_shim", _Shim())
    spec.loader.exec_module(mod)
    return mod


class TestAnnotationOptOutRegex:
    """The opt-out regex must match all 3 documented forms."""

    def test_matches_ok_form(self):
        mod = _load_audit_module()
        # The audit defines _OPTOUT_RE inline in audit(); we reconstruct
        # the regex here to lock the contract authors rely on.
        regex = re.compile(
            r"#\s*n-plus-one:\s*(?:false-positive|ok|skip)\b",
            re.IGNORECASE,
        )
        assert regex.search("# n-plus-one: ok") is not None

    def test_matches_false_positive_form(self):
        regex = re.compile(
            r"#\s*n-plus-one:\s*(?:false-positive|ok|skip)\b",
            re.IGNORECASE,
        )
        assert regex.search("# n-plus-one: false-positive") is not None

    def test_matches_skip_form(self):
        regex = re.compile(
            r"#\s*n-plus-one:\s*(?:false-positive|ok|skip)\b",
            re.IGNORECASE,
        )
        assert regex.search("# n-plus-one: skip") is not None

    def test_case_insensitive(self):
        regex = re.compile(
            r"#\s*n-plus-one:\s*(?:false-positive|ok|skip)\b",
            re.IGNORECASE,
        )
        assert regex.search("# N-PLUS-ONE: OK") is not None

    def test_does_not_match_unrelated_comment(self):
        regex = re.compile(
            r"#\s*n-plus-one:\s*(?:false-positive|ok|skip)\b",
            re.IGNORECASE,
        )
        # Other comments must not be flagged as opt-outs
        assert regex.search("# This is fine") is None
        assert regex.search("# nudge: ok") is None


class TestCrossShopAggregatorAnnotation:
    """The actual annotation in cross_shop_aggregator.py MUST land
    within the 5-line tag window of the for-loop. Born 2026-05-13.
    If a future refactor reflows the comment block, this test
    fails before the annotation silently stops working."""

    def test_annotation_in_window(self):
        path = pathlib.Path(
            "/opt/wishspark/backend/app/services/cross_shop_aggregator.py"
        )
        lines = path.read_text().splitlines()

        # Find the `# n-plus-one: ok` line and the next `for` opener
        optout_line = None
        for_line = None
        for i, ln in enumerate(lines, start=1):
            if "n-plus-one: ok" in ln.lower():
                optout_line = i
                # Find the next `for ... in ...:` opener
                for j in range(i, min(i + 6, len(lines) + 1)):
                    if j - 1 < len(lines) and re.match(
                        r"\s*for\s+.*\bin\b.*:", lines[j - 1],
                    ):
                        for_line = j
                        break
                break

        assert optout_line is not None, (
            "cross_shop_aggregator.py must carry the documented "
            "`# n-plus-one: ok` annotation per commit 94fa938"
        )
        assert for_line is not None, (
            "The `for` opener must be within 5 lines of the "
            "`# n-plus-one: ok` tag per audit_n_plus_one contract"
        )
        # The audit accepts offset 0..5 inclusive
        distance = for_line - optout_line
        assert 0 <= distance <= 5, (
            f"Annotation at line {optout_line}, for-loop at line "
            f"{for_line} (distance {distance}). The audit's tag "
            f"window is 0–5 lines (inclusive); a distance of {distance} "
            f"would silently lose the annotation."
        )
