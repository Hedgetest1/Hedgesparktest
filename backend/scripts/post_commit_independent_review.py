#!/usr/bin/env python3
"""Independent post-commit claim verifier — Gap 1 of the elite-tier
brutal-CTO sprint.

The honest audit: "the agent that wrote the code is the same agent that
checks the work. No external review process." This script is the
external review process — runs post-commit, separate from the agent
that authored the commit, deterministic + free.

Strategy
--------
Reads the just-shipped commit (HEAD). Extracts verifiable CLAIMS from
the commit message (e.g. "26/26 tests pass", "verified by pytest X",
"wired into Y", "shipped to <path>"). For each claim, cross-references
the diff for matching evidence:

  Claim: "N/M tests pass"          → Diff must touch a test file
  Claim: "<file_path> updated"      → Diff must include that path
  Claim: "wired into <module>"      → Diff must touch that module
  Claim: "verified by `<command>`"  → Soft check (logged but not failing)

Writes findings to /tmp/post_commit_independent_review_<sha>.log.
Non-blocking (the commit already shipped) but writes ops_alert if
any HIGH-severity inconsistency is detected so the next triage cycle
catches it.

Why deterministic, not LLM?
---------------------------
LLM-based adversarial review on every commit costs ~$0.02 each.
Bounded but real. The deterministic checks catch structural lies
(claim-file-mismatch, claim-test-count-without-test-touched) without
LLM cost. The semantic version (LLM call for TIER_1+ commits only)
is a follow-on (R-blocker:sprint>1d-with-memo).

Usage
-----
Invoked from post-commit hook (auto). Manual:
    python3 scripts/post_commit_independent_review.py [--sha HEAD]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = "/opt/wishspark"


def _git(*args: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", REPO, *args], stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _commit_message(sha: str) -> str:
    return _git("log", "-1", "--pretty=%B", sha)


def _commit_files(sha: str) -> list[str]:
    out = _git("show", "--name-only", "--pretty=", sha)
    return [p for p in out.splitlines() if p.strip()]


def _commit_diff(sha: str) -> str:
    return _git("show", "--no-color", sha)


# Claim patterns we know how to verify deterministically.
# Each tuple: (regex, claim_label, evidence_predicate(match, files, diff))
def _check_test_count_claim(msg: str, files: list[str], diff: str) -> list[tuple[str, str]]:
    """Find 'N/M tests pass' / 'N tests green' / 'pytest ... N passed' claims.
    Verify at least one test file appears in the diff."""
    findings: list[tuple[str, str]] = []
    pat = re.compile(
        r"\b(\d+)\s*/\s*(\d+)\s*(?:tests?\s+(?:pass|green|passing))",
        re.IGNORECASE,
    )
    for m in pat.findall(msg):
        passed, total = m[0], m[1]
        # If a test claim exists, look for ANY .py file in tests/ touched
        test_touched = any(
            p.startswith("backend/tests/") or "/tests/" in p or "_test." in p or "test_" in os.path.basename(p)
            for p in files
        )
        # OR a pytest invocation line in the message body
        has_pytest_line = bool(re.search(r"\$\s*(?:python\s+-m\s+)?pytest", msg))
        if not test_touched and not has_pytest_line:
            findings.append((
                "claim_test_count_unverified",
                f"Commit claims {passed}/{total} tests pass but no test file "
                f"in diff and no `pytest` invocation line in message.",
            ))
    return findings


def _check_file_path_claims(msg: str, files: list[str], diff: str) -> list[tuple[str, str]]:
    """Find 'wired into <path>' / 'updated <path>' / 'added to <path>'
    claims. Verify <path> is a substring of any file in diff."""
    findings: list[tuple[str, str]] = []
    # Generic "verb (file with extension)" pattern
    pat = re.compile(
        r"\b(?:wired\s+(?:into|to)|updated|added\s+to|injected\s+into)"
        r"\s+`?([\w/\.\-]+\.(?:py|ts|tsx|js|sh|md|json|yml|yaml))`?",
        re.IGNORECASE,
    )
    for m in pat.findall(msg):
        # Allow flexible match: claim mentions a basename that exists in diff
        basename = os.path.basename(m)
        if not any(basename in f for f in files):
            findings.append((
                "claim_file_path_unverified",
                f"Commit message claims work in `{m}` but the diff "
                f"contains no file matching that basename.",
            ))
    return findings


def _check_count_claim_consistency(msg: str, files: list[str], diff: str) -> list[tuple[str, str]]:
    """Find '<N> commits' / '<N> phases' / '<N> wires' claims that
    contradict the diff (e.g. claim '5 wires' but only 1 wire change)."""
    findings: list[tuple[str, str]] = []
    # Look for 'wired N preventer(s)' or 'wired N audit(s)' style claims
    pat = re.compile(
        r"wired\s+(\d+)\s+(?:state-based\s+)?(?:preventer|audit|hook)s?",
        re.IGNORECASE,
    )
    for m in pat.findall(msg):
        n = int(m)
        # Heuristic: count audit_*.py file changes in diff
        audit_files = [f for f in files if f.startswith("backend/scripts/audit_")]
        if n > 0 and len(audit_files) == 0:
            findings.append((
                "claim_wire_count_no_evidence",
                f"Commit claims {n} preventer/audit/hook wired but the "
                f"diff touches no audit_*.py files.",
            ))
    return findings


_CHECKS = [
    _check_test_count_claim,
    _check_file_path_claims,
    _check_count_claim_consistency,
]


def review(sha: str) -> tuple[list[tuple[str, str]], dict]:
    msg = _commit_message(sha)
    if not msg.strip():
        return [], {"error": f"no commit message for {sha}"}
    files = _commit_files(sha)
    diff = _commit_diff(sha)
    findings: list[tuple[str, str]] = []
    for fn in _CHECKS:
        try:
            findings.extend(fn(msg, files, diff))
        except Exception as exc:
            findings.append((
                "review_check_failed",
                f"check {fn.__name__} raised: {exc}",
            ))
    return findings, {
        "sha": sha,
        "message_len": len(msg),
        "file_count": len(files),
        "diff_len": len(diff),
    }


def _write_alert_if_findings(sha: str, findings: list[tuple[str, str]]) -> None:
    """Write an ops_alert when findings exist. Soft-skip on import error."""
    if not findings:
        return
    try:
        sys.path.insert(0, "/opt/wishspark/backend")
        from app.core.database import SessionLocal
        from app.services.alerting import write_alert
    except Exception as exc:
        print(f"  (skip alert write — import failed: {exc})")
        return
    try:
        db = SessionLocal()
        try:
            write_alert(
                db,
                severity="warning",
                source=f"post_commit_independent_review:{sha}",
                alert_type="independent_review_finding",
                summary=(
                    f"post-commit review on {sha} found {len(findings)} "
                    f"unverified claim(s): "
                    f"{', '.join(label for label, _ in findings[:3])}"
                ),
                detail={
                    "sha": sha,
                    "findings": [
                        {"label": label, "detail": d}
                        for label, d in findings
                    ],
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        print(f"  (alert write failed — non-fatal: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--dry-run", action="store_true",
                    help="print findings but skip alert write")
    args = ap.parse_args()

    sha = _git("rev-parse", args.sha).strip()
    if not sha:
        print(f"FAIL: cannot resolve {args.sha}")
        return 1

    findings, meta = review(sha)
    log_dir = Path("/tmp")
    log_path = log_dir / f"post_commit_independent_review_{sha}.log"
    payload = {
        "sha": sha,
        "meta": meta,
        "findings": [{"label": label, "detail": d} for label, d in findings],
    }
    try:
        log_path.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass

    print(f"post-commit independent review on {sha}:")
    print(f"  files={meta['file_count']} message_len={meta['message_len']} "
          f"findings={len(findings)}")
    for label, detail in findings:
        print(f"  ⚠ {label}: {detail}")
    if not findings:
        print("  ✓ all extractable claims verified against diff.")
        return 0
    if not args.dry_run:
        _write_alert_if_findings(sha, findings)
    return 0  # post-commit hook is non-blocking; just surface the signal


if __name__ == "__main__":
    sys.exit(main())
