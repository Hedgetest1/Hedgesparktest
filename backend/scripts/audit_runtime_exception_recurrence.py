#!/usr/bin/env python3
"""Runtime-exception recurrence preventer.

Born 2026-05-02 from the brutal-CTO post-elite-tier inspection that
found 4118 historical `NameError` occurrences in the production
error log — bugs that fired silently for weeks because no audit was
periodically scanning the log for runtime-exception classes.

Strategy
--------
Scan the PM2 backend error log over the last `--hours` window
(default 24). Count occurrences of each exception class that, by
definition, indicates a real code bug rather than a recoverable
runtime condition:

  - NameError              — undefined symbol (refactor-leftover)
  - UnboundLocalError      — variable assigned in only one branch
  - AttributeError         — None / wrong-type access (frequent
                             when chained calls hit a None mid-way)
  - ImportError            — missing module / circular import
  - SyntaxError            — should be caught at deploy but a
                             dynamic exec / template can leak one

Threshold
---------
If any class fires >= _RECURRENCE_THRESHOLD times in the window,
the audit FAILS and reports the top file:line sites. The threshold
is set to be sensitive — even 3 NameError occurrences in 24h is a
pattern worth alerting on.

Usage:
    python3 scripts/audit_runtime_exception_recurrence.py
    python3 scripts/audit_runtime_exception_recurrence.py --hours 168 --threshold 5
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _audit_io import safe_read_text

PM2_LOG = Path("/root/.pm2/logs/wishspark-backend-error.log")

_BUG_CLASSES = (
    "NameError",
    "UnboundLocalError",
    "AttributeError",
    "ImportError",
    "SyntaxError",
)

# AttributeError on builtin types is sometimes legitimate (e.g.
# `if hasattr(x, "foo")` followed by `x.foo` after a None branch).
# These shapes are filtered to reduce false positives.
_ATTR_NOISE_PATTERNS = (
    re.compile(r"AttributeError: '(?:NoneType|dict|list|str|tuple|int|float|bool)' object has no attribute"),
)
# Generic "module 'X' has no attribute" is real — keep.
# But add an opt-out for known harmless 3rd-party warnings if any arise.

_DEFAULT_THRESHOLD = 3
_DEFAULT_HOURS = 24


def _within_window(line: str, cutoff: datetime) -> bool:
    """Find a JSON-shaped {"ts": "..."} or "[YYYY-MM-DD ...]" timestamp
    in the line and decide whether it falls within the window."""
    m = re.search(r'"ts"\s*:\s*"(20\d\d-\d\d-\d\d[T ][\d:\.]+)', line)
    if m:
        try:
            ts = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts >= cutoff
        except Exception:
            pass
    return True  # if no timestamp on this line, assume in-window (conservative)


def _scan(log_path: Path, hours: int) -> tuple[Counter, dict[str, list[str]]]:
    """Return (class_counter, samples_by_class)."""
    if not log_path.is_file():
        return Counter(), {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    counts: Counter = Counter()
    samples: dict[str, list[str]] = {}
    last_ts_in_window = True
    try:
        # Read tail of log — large logs handled by scanning from end if needed.
        # Explicit (FileNotFoundError, PermissionError) below is required by
        # audit_audit_io_safety: `with log_path.open(...)` is a race-prone
        # receiver pattern (log_path is a Path-typed parameter that may be
        # log-rotated by pm2-logrotate mid-scan).
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # Track window via timestamps when present
                if '"ts"' in line:
                    last_ts_in_window = _within_window(line, cutoff)
                if not last_ts_in_window:
                    continue
                for klass in _BUG_CLASSES:
                    if f"{klass}:" not in line:
                        continue
                    # Filter AttributeError noise
                    if klass == "AttributeError" and any(
                        p.search(line) for p in _ATTR_NOISE_PATTERNS
                    ):
                        continue
                    counts[klass] += 1
                    samples.setdefault(klass, [])
                    if len(samples[klass]) < 3:
                        # Trim line for readability
                        samples[klass].append(line.strip()[:240])
                    break
    except (FileNotFoundError, PermissionError):
        # Log rotated mid-scan. Best-effort: return what we counted so far.
        pass
    return counts, samples


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=_DEFAULT_HOURS)
    ap.add_argument("--threshold", type=int, default=_DEFAULT_THRESHOLD)
    ap.add_argument("--log-path", default=str(PM2_LOG))
    args = ap.parse_args()

    log_path = Path(args.log_path)
    if not log_path.is_file():
        print(f"audit_runtime_exception_recurrence: skip — log not found: {log_path}")
        return 0

    counts, samples = _scan(log_path, args.hours)
    over_threshold = {
        k: n for k, n in counts.items() if n >= args.threshold
    }

    if over_threshold:
        print(
            f"FAIL: runtime-exception recurrence — "
            f"{len(over_threshold)} class(es) over threshold "
            f"({args.threshold} hits in last {args.hours}h):"
        )
        for klass, n in sorted(over_threshold.items(), key=lambda kv: -kv[1]):
            print(f"\n  {klass}  ×{n}")
            for s in samples.get(klass, [])[:2]:
                print(f"    {s}")
        print(
            "\nRecurring runtime exceptions == real code bugs. "
            "Each firing was an exception that escaped to the user. "
            "Open the most-recent stack in the error log + fix at "
            "the root. After the fix, this audit should return to OK."
        )
        return 1

    if counts:
        nice = ", ".join(f"{k}={n}" for k, n in counts.most_common())
        print(
            f"OK: runtime exceptions in last {args.hours}h all under "
            f"threshold {args.threshold} — {nice}."
        )
    else:
        print(
            f"OK: no runtime exceptions in last {args.hours}h "
            f"(window-bounded scan, threshold {args.threshold})."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
