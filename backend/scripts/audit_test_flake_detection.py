#!/usr/bin/env python3
"""audit_test_flake_detection.py — run the test suite N times, flag
tests whose result varies (PASSED in one run, FAILED in another).

Why
---
2026-04-25: founder caught `test_public_transparency::test_cache_write_on
_miss` flaking intermittently. The pipeline's existing audits cannot see
"sometimes passes, sometimes fails" — their signal is single-execution
(preflight runs once). Flake detection requires multi-run comparison.

This audit is the missing capability. It runs `pytest --co -q` to
discover tests, then runs the full suite N times (default 3), captures
per-test pass/fail, and reports any tests with mixed results.

Heavy by design — ~3 × suite_wall = ~6-10min at 2800 tests. NOT in
preflight. Operator / CI-nightly run:

    ./audit_test_flake_detection.py           # 3 runs, warn-only
    ./audit_test_flake_detection.py --runs 5  # more runs
    ./audit_test_flake_detection.py --strict  # exit 1 on any flake

Classification
--------------
For each test:
  * stable-pass:  passed in every run
  * stable-fail:  failed in every run (real bug, not flake)
  * flake:        mixed — sometimes pass, sometimes fail
  * skipped/missing: not executed in some run (test collection drift)

The `flake` bucket is the signal the founder asked the pipeline to
catch. Telemetry via @telemetered so /ops/audit-telemetry shows
flake trend over time when run on a schedule.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, "/opt/wishspark/backend")

from _audit_telemetry_shim import telemetered

BACKEND_ROOT = pathlib.Path("/opt/wishspark/backend")
TESTS_DIR = BACKEND_ROOT / "tests"
VENV_PYTHON = BACKEND_ROOT / "venv" / "bin" / "python"

_RESULT_LINE_RE = re.compile(
    r"^(tests/[^\s]+)::([^\s]+)\s+(PASSED|FAILED|ERROR|SKIPPED)\s*$"
)


@dataclass
class TestResult:
    nodeid: str  # "tests/test_foo.py::test_bar"
    runs: list[str]  # per-run status: PASSED / FAILED / ERROR / SKIPPED / MISSING


def _run_once(run_idx: int) -> dict[str, str]:
    """Return {nodeid: status}. Uses -v to get per-test result lines
    on stdout in a parseable shape."""
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "pytest", "tests/", "-v", "--tb=no", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=str(BACKEND_ROOT),
        capture_output=True, text=True, timeout=600,
    )
    out: dict[str, str] = {}
    for line in (result.stdout + result.stderr).splitlines():
        m = _RESULT_LINE_RE.match(line.strip())
        if m:
            file_path, test_name, status = m.groups()
            nodeid = f"{file_path}::{test_name}"
            out[nodeid] = status
    return out


def _classify(results: list[dict[str, str]]) -> dict[str, TestResult]:
    """Merge N run results into per-test trajectories."""
    all_nodes: set[str] = set()
    for r in results:
        all_nodes.update(r.keys())

    out: dict[str, TestResult] = {}
    for node in all_nodes:
        runs = [r.get(node, "MISSING") for r in results]
        out[node] = TestResult(nodeid=node, runs=runs)
    return out


def _bucket(tr: TestResult) -> str:
    statuses = set(tr.runs)
    if "MISSING" in statuses:
        return "missing"
    if statuses == {"PASSED"}:
        return "stable-pass"
    if statuses == {"SKIPPED"}:
        return "skipped"
    if statuses == {"FAILED"} or statuses == {"ERROR"}:
        return "stable-fail"
    # Mixed — includes PASSED + FAILED (classic flake) or any other mix
    if "PASSED" in statuses and ("FAILED" in statuses or "ERROR" in statuses):
        return "flake"
    return "other"


@telemetered("audit_test_flake_detection")
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--help", "-h", action="store_true")
    args = parser.parse_args(argv)

    if args.help:
        print(__doc__)
        return 0

    if not VENV_PYTHON.is_file():
        print(f"audit_test_flake_detection: {VENV_PYTHON} missing",
              file=sys.stderr)
        return 2

    print(f"audit_test_flake_detection: running suite {args.runs}× "
          f"(~{args.runs * 3}min)…", file=sys.stderr)

    all_results: list[dict[str, str]] = []
    for i in range(args.runs):
        print(f"  run {i + 1}/{args.runs}…", file=sys.stderr)
        all_results.append(_run_once(i))

    classified = _classify(all_results)
    buckets: dict[str, list[TestResult]] = defaultdict(list)
    for tr in classified.values():
        buckets[_bucket(tr)].append(tr)

    payload = {
        "runs": args.runs,
        "total_tests_seen": len(classified),
        "stable_pass": len(buckets["stable-pass"]),
        "stable_fail": len(buckets["stable-fail"]),
        "skipped": len(buckets["skipped"]),
        "missing": len(buckets["missing"]),
        "flake": len(buckets["flake"]),
        "other": len(buckets["other"]),
        "flake_list": [
            {"nodeid": tr.nodeid, "runs": tr.runs}
            for tr in sorted(buckets["flake"], key=lambda x: x.nodeid)
        ],
        "missing_list": [
            {"nodeid": tr.nodeid, "runs": tr.runs}
            for tr in sorted(buckets["missing"], key=lambda x: x.nodeid)
        ],
    }

    if args.as_json:
        print(json.dumps(payload, indent=2))
        return 1 if args.strict and (buckets["flake"] or buckets["stable-fail"]) else 0

    print("# Flake detection report\n")
    print(f"Runs:          {payload['runs']}")
    print(f"Tests seen:    {payload['total_tests_seen']}")
    print(f"Stable pass:   {payload['stable_pass']}")
    print(f"Stable fail:   {payload['stable_fail']}")
    print(f"Skipped:       {payload['skipped']}")
    print(f"Missing:       {payload['missing']}")
    print(f"**Flakes:**    {payload['flake']}")
    print()

    if buckets["flake"]:
        print(f"## {len(buckets['flake'])} flaky test(s)\n")
        for tr in sorted(buckets["flake"], key=lambda x: x.nodeid):
            print(f"  {tr.nodeid}")
            print(f"    runs: {' · '.join(tr.runs)}")
        print()
        print("Fix: investigate each flaky test — usually state pollution "
              "between tests or dependency on prod DB/Redis state.")
    else:
        print("✅ No flaky tests detected across runs")

    if buckets["stable-fail"]:
        print(f"\n## {len(buckets['stable-fail'])} stable-fail test(s) — REAL bugs\n")
        for tr in sorted(buckets["stable-fail"], key=lambda x: x.nodeid):
            print(f"  {tr.nodeid}")

    return 1 if args.strict and (buckets["flake"] or buckets["stable-fail"]) else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"audit_test_flake_detection: script error — {exc}",
              file=sys.stderr)
        sys.exit(2)
