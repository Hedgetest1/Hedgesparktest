#!/usr/bin/env python3
"""LLM-based adversarial post-commit review (Gap C, semantic layer).

Complements post_commit_independent_review.py (Phase L deterministic).
That script catches structural lies (claim-file mismatch, test-count
without test-touched). This one catches SEMANTIC lies — fragile fixes
claimed robust, missing edge-case handling, hidden side-effects the
deterministic check cannot reach.

Cost-aware design
-----------------
Each invocation costs ~€0.02-0.05 (Sonnet 4.6, ~3K input + ~500 output).
At 1k merchants generating ~5 TIER_1+ commits/week => ~€0.50/week =
~€2/month. Negligible against the €500 hard cap.

Gates (all must pass for the LLM to be called):
  1. ADVERSARIAL_REVIEW_LLM env var == "1" (default OFF; founder
     opt-in. Keeps the budget impact discoverable + reversible).
  2. classify_commit_tier returns TIER_1 or TIER_2 (skip TIER_0
     commits — too noisy, low signal-per-euro).
  3. llm_budget.check_budget("adversarial_review") returns ok.

Output
------
LLM findings -> ops_alert(alert_type="adversarial_review_finding").
The bugfix_pipeline.run_bug_triage Rule recognises this alert type
and creates a candidate (visibility-only — humans review, no
auto-apply).

Invocation
----------
Wired into post_commit_auto_deploy.sh AFTER the deterministic
Phase L review. Non-blocking. Failure here never fails the deploy.

Usage
-----
    python3 scripts/post_commit_llm_adversarial_review.py [--sha HEAD]
    python3 scripts/post_commit_llm_adversarial_review.py --sha HEAD --force
       (skip the env-var gate; useful for manual review)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = "/opt/wishspark"
BACKEND = f"{REPO}/backend"
sys.path.insert(0, BACKEND)


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", REPO, *args], stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _commit_msg(sha: str) -> str:
    return _git("log", "-1", "--pretty=%B", sha)


def _commit_diff(sha: str, max_chars: int = 12000) -> str:
    raw = _git("show", "--no-color", sha)
    return raw[:max_chars]


def _classify_tier(sha: str) -> int:
    """Return 0/1/2 per backend/scripts/classify_commit_tier."""
    try:
        out = subprocess.check_output(
            [
                f"{BACKEND}/venv/bin/python",
                f"{BACKEND}/scripts/classify_commit_tier.py", sha,
            ],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        # classify_commit_tier prints "TIER_N" on stdout
        for n in (2, 1, 0):
            if f"TIER_{n}" in out:
                return n
    except Exception:
        pass
    return 0


_PROMPT_TEMPLATE = """You are a brutal external CTO reviewing a single git \
commit. The author of this commit is an autonomous coding agent that \
already self-reviewed. Your job is the SECOND review — find what the \
agent's self-review missed.

Look specifically for:
1. UNVERIFIABLE CLAIMS — the message asserts something the diff does NOT \
prove (e.g. "fix is robust" when the change is a bare try/except).
2. HIDDEN SIDE EFFECTS — additions that touch DB, HTTP, or external \
state without proper rollback / rate-limit / fail-closed handling.
3. EDGE CASES MISSED — error paths that aren't tested, None inputs not \
guarded, race conditions in shared state.
4. SCOPE CREEP — files changed that weren't justified by the commit \
subject (smuggled-in changes).
5. MIRAGE TESTS — tests added that don't actually verify the claimed \
behavior (e.g. assert True in disguise, mock-only tests with no real \
code path exercised).

Output a JSON object EXACTLY in this shape (no prose around it):
{{
  "verdict": "clean" | "issues_found",
  "findings": [
    {{
      "category": "unverifiable_claim" | "hidden_side_effect" | "edge_case_missed" | "scope_creep" | "mirage_test",
      "severity": "low" | "medium" | "high",
      "summary": "one sentence describing the issue",
      "file": "path/to/file or null",
      "evidence": "specific quote from the diff or message"
    }}
  ]
}}

If the commit is genuinely clean, return verdict="clean" with empty findings.

---
COMMIT MESSAGE:
{message}

---
DIFF (truncated to 12KB):
{diff}
---

Return only the JSON object.
"""


def _call_anthropic(prompt: str, max_tokens: int = 1024) -> str | None:
    """Direct Anthropic SDK call. Returns response text or None on
    any failure (network, auth, parse). Bounded at max_tokens."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate all text blocks
        parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "".join(parts) or None
    except Exception:
        return None


def _parse_findings(raw: str) -> dict | None:
    """Best-effort JSON extraction from the LLM response."""
    if not raw:
        return None
    # Strip code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    # Try to find the first { and last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = cleaned[start:end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return None


def _write_alert(sha: str, parsed: dict) -> None:
    findings = parsed.get("findings") or []
    if not findings:
        return
    try:
        from app.core.database import SessionLocal
        from app.services.alerting import write_alert
    except Exception:
        return
    severity_max = "low"
    rank = {"low": 1, "medium": 2, "high": 3}
    for f in findings:
        sev = (f.get("severity") or "low").lower()
        if rank.get(sev, 0) > rank.get(severity_max, 0):
            severity_max = sev
    db = SessionLocal()
    try:
        write_alert(
            db,
            severity="warning" if severity_max != "high" else "critical",
            source=f"post_commit_llm_review:{sha}",
            alert_type="adversarial_review_finding",
            summary=(
                f"LLM adversarial review on {sha} found {len(findings)} "
                f"{'issue' if len(findings) == 1 else 'issues'} "
                f"(max severity: {severity_max})"
            ),
            detail={
                "sha": sha,
                "verdict": parsed.get("verdict", "issues_found"),
                "findings": findings,
            },
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _budget_ok() -> bool:
    try:
        from app.core.llm_budget import check_budget
        ok, _ = check_budget("adversarial_review")
        return ok
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--force", action="store_true",
                    help="skip the ADVERSARIAL_REVIEW_LLM env-var gate")
    args = ap.parse_args()

    if not args.force:
        if os.getenv("ADVERSARIAL_REVIEW_LLM", "").strip() != "1":
            print("post_commit_llm_review: skip — ADVERSARIAL_REVIEW_LLM != 1")
            return 0

    sha = _git("rev-parse", args.sha).strip()
    if not sha:
        print(f"post_commit_llm_review: cannot resolve {args.sha}")
        return 0

    tier = _classify_tier(sha)
    if tier == 0 and not args.force:
        print(f"post_commit_llm_review: skip — TIER_{tier} (low-signal)")
        return 0

    if not _budget_ok() and not args.force:
        print("post_commit_llm_review: skip — LLM budget exhausted")
        return 0

    msg = _commit_msg(sha)
    diff = _commit_diff(sha)
    prompt = _PROMPT_TEMPLATE.format(message=msg, diff=diff)
    raw = _call_anthropic(prompt)
    if not raw:
        print("post_commit_llm_review: skip — anthropic call returned empty")
        return 0

    parsed = _parse_findings(raw)
    if not parsed:
        print("post_commit_llm_review: skip — could not parse JSON response")
        return 0

    findings = parsed.get("findings") or []
    print(
        f"post_commit_llm_review: {sha} verdict={parsed.get('verdict','?')} "
        f"findings={len(findings)}"
    )
    for f in findings:
        print(
            f"  [{f.get('severity','?')}] {f.get('category','?')}: "
            f"{f.get('summary','?')}"
        )
    if findings:
        _write_alert(sha, parsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
