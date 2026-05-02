#!/usr/bin/env python3
"""Intent-bag Jaccard threshold auto-calibrator (Gap A).

Phase J ships with `_INTENT_SIMILARITY_THRESHOLD = 0.70` chosen
empirically with NO real corpus. As PatchFingerprint accumulates real
fail/recur outcomes, that magic number must be re-tuned on actual
data.

This script reads the last N days of PatchFingerprint rows that have a
stored `intent_bag` (ctx-json) AND an `outcome`. For every pair of
failed patches in the same `affected_domain`, it computes Jaccard
similarity. It then SCANS thresholds {0.50, 0.55, 0.60, 0.65, 0.70,
0.75, 0.80, 0.85, 0.90} and reports precision/recall at each:

  precision_at_t = pairs flagged AND known-related / pairs flagged
  recall_at_t    = pairs flagged AND known-related / known-related pairs

"Known related" = both pairs have outcome != applied AND failure_reason
overlaps semantically (heuristic: ≥1 shared word in failure_reason).

Output
------
Writes a recommendation memo to
  /root/.claude/projects/-opt-wishspark/memory/intent_threshold_calibration_<date>.md

Includes:
  - sample size (N pairs)
  - per-threshold precision / recall / F1
  - recommended threshold (max F1, break ties with highest precision)
  - confidence note (sample size <30 -> "low confidence; keep 0.70")

Currently the corpus is small (pre-merchant), so the SCAFFOLD ships
green-but-uninformative. Once paying merchants generate real outcomes,
this script becomes the auto-tuner.

Usage
-----
    python3 scripts/intent_threshold_calibrator.py
    python3 scripts/intent_threshold_calibrator.py --days 30 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "/opt/wishspark"
sys.path.insert(0, f"{REPO}/backend")

MEMORY_DIR = Path("/root/.claude/projects/-opt-wishspark/memory")

_DEFAULT_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


def _load_corpus(days: int) -> list[dict]:
    """Pull recent PatchFingerprint rows with intent_bag stored in
    failure_reason JSON. Returns list of dicts: {id, domain,
    outcome, bag (frozenset), failure_reason}."""
    try:
        from app.core.database import SessionLocal
        from app.models.patch_fingerprint import PatchFingerprint
    except Exception as exc:
        print(f"calibrator: import failed ({exc}) — empty corpus")
        return []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    out: list[dict] = []
    db = SessionLocal()
    try:
        rows = (
            db.query(PatchFingerprint)
            .filter(PatchFingerprint.created_at >= cutoff)
            .all()
        )
        for r in rows:
            payload = None
            try:
                payload = json.loads(r.failure_reason or "{}")
            except Exception:
                pass
            if not isinstance(payload, dict):
                continue
            bag = payload.get("intent_bag")
            if not isinstance(bag, list) or not bag:
                continue
            out.append({
                "id": r.bugfix_candidate_id,
                "domain": r.affected_domain,
                "outcome": r.outcome,
                "bag": frozenset(str(t) for t in bag),
                "failure_reason": str(r.failure_reason or "")[:200],
            })
    finally:
        db.close()
    return out


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _is_related(a: dict, b: dict) -> bool:
    """Heuristic: same affected_domain + at least one shared word in
    failure_reason (skip generic stop-words)."""
    if a["domain"] != b["domain"]:
        return False
    aw = set(w for w in a["failure_reason"].lower().split() if len(w) > 4)
    bw = set(w for w in b["failure_reason"].lower().split() if len(w) > 4)
    return bool(aw & bw)


def _evaluate(corpus: list[dict], thresholds: list[float]) -> list[dict]:
    """For each threshold t, compute pairs flagged + true related +
    precision + recall + F1."""
    failed = [r for r in corpus if r["outcome"] in (
        "rolled_back", "apply_failed", "tests_failed", "test_timeout"
    )]
    pairs = []
    for i in range(len(failed)):
        for j in range(i + 1, len(failed)):
            a, b = failed[i], failed[j]
            if a["domain"] != b["domain"]:
                continue
            sim = _jaccard(a["bag"], b["bag"])
            related = _is_related(a, b)
            pairs.append((sim, related))

    results: list[dict] = []
    total_related = sum(1 for _, r in pairs if r)
    for t in thresholds:
        flagged = [p for p in pairs if p[0] >= t]
        n_flagged = len(flagged)
        n_true = sum(1 for _, r in flagged if r)
        precision = n_true / n_flagged if n_flagged else 0.0
        recall = n_true / total_related if total_related else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) else 0.0
        )
        results.append({
            "threshold": t,
            "flagged": n_flagged,
            "true_positive": n_true,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        })
    return results


def _recommend(results: list[dict], sample_size: int, current: float) -> dict:
    if sample_size < 30:
        return {
            "recommendation": current,
            "reason": (
                f"low sample size ({sample_size} pairs) — keep "
                f"current threshold {current}; calibrator output "
                "is informational only until corpus grows."
            ),
        }
    best = max(results, key=lambda r: (r["f1"], r["precision"]))
    return {
        "recommendation": best["threshold"],
        "reason": (
            f"max F1 = {best['f1']} at threshold {best['threshold']} "
            f"(precision={best['precision']}, recall={best['recall']}). "
            f"Sample size {sample_size} pairs (sufficient for tuning)."
        ),
    }


def _format_memo(results: list[dict], rec: dict, days: int, sample_size: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# Intent-bag threshold calibration — {today} (last {days}d)",
        "",
        "Auto-generated by `backend/scripts/intent_threshold_calibrator.py`.",
        "Corpus: PatchFingerprint rows with stored `intent_bag` in "
        "`failure_reason` JSON.",
        "",
        f"## Sample",
        f"- Failed-patch pairs in same affected_domain: **{sample_size}**",
        "",
        "## Per-threshold metrics",
        "| Threshold | Flagged | True+ | Precision | Recall | F1 |",
        "|-----------|---------|-------|-----------|--------|-----|",
    ]
    for r in results:
        lines.append(
            f"| {r['threshold']:.2f} | {r['flagged']} | "
            f"{r['true_positive']} | {r['precision']:.3f} | "
            f"{r['recall']:.3f} | {r['f1']:.3f} |"
        )
    lines.extend([
        "",
        "## Recommendation",
        f"- Recommended threshold: **{rec['recommendation']}**",
        f"- Reason: {rec['reason']}",
        "",
        "_To apply: edit `_INTENT_SIMILARITY_THRESHOLD` in "
        "`backend/app/services/bugfix_pipeline.py` and run pytest._",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus = _load_corpus(args.days)
    failed_pairs = [
        (i, j)
        for i in range(len(corpus))
        for j in range(i + 1, len(corpus))
        if corpus[i]["domain"] == corpus[j]["domain"]
    ]
    sample_size = len(failed_pairs)

    results = _evaluate(corpus, _DEFAULT_THRESHOLDS)

    # Read current threshold from bugfix_pipeline source
    current = 0.70
    try:
        from app.services.bugfix_pipeline import _INTENT_SIMILARITY_THRESHOLD
        current = float(_INTENT_SIMILARITY_THRESHOLD)
    except Exception:
        pass

    rec = _recommend(results, sample_size, current)
    memo = _format_memo(results, rec, args.days, sample_size)

    if args.dry_run:
        sys.stdout.write(memo)
        return 0

    if not MEMORY_DIR.is_dir():
        sys.stdout.write(memo)
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = MEMORY_DIR / f"intent_threshold_calibration_{today}.md"
    try:
        out_path.write_text(memo)
    except Exception as exc:
        print(f"calibrator: write failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: calibration memo written to {out_path}")
    print(
        f"  corpus_size={len(corpus)} pairs={sample_size} "
        f"recommendation={rec['recommendation']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
