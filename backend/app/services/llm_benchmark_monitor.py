"""
llm_benchmark_monitor.py — A5 weekly LLM prompt-guardrail drift check.

What this closes
----------------
The `test_llm_propose_bench.py` suite (17 structural tests) encodes
every known hallucination-mode failure the 2026-04-11 prompt-engineering
sprint rejected. It runs on every commit via the regular pytest path.
BUT: refactors inside `bugfix_pipeline._validate_patch_semantics` or
`bugfix_prompt_grounding.*` can silently loosen a guardrail in a way
that still passes the existing module-level tests but lets through new
hallucination shapes the benchmark was designed to catch.

This monitor runs the benchmark SEPARATELY, weekly, against current
code — independent from whatever test-discovery path CI took that
week. If fewer tests pass than the recorded baseline, ops_alert fires.

Zero-cost: the benchmark uses fake LLM stubs. No real API calls, no
budget impact. If later a real-model drift check is wired (Approach 3),
it layers on top without changing this file.

Schedule
--------
Gated once per week — Sunday 04:00-06:00 UTC window. Aggregation
worker calls `run_weekly_check()` on every 5-min cycle; the service
returns early when out-of-window or already-ran-this-week.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("llm_benchmark_monitor")

_BENCH_FILE = "tests/test_llm_propose_bench.py"
_BACKEND_DIR = "/opt/wishspark/backend"
_PYTEST_TIMEOUT_SECONDS = 120

# Sunday (weekday=6) in the 04:00-06:00 UTC window.
_SCHEDULE_WEEKDAY = 6
_SCHEDULE_WINDOW_START_HOUR = 4
_SCHEDULE_WINDOW_END_HOUR = 6

# Once-per-week gate. ISO week: "2026-W16".
_WEEKLY_GATE_KEY = "hs:llm_bench:last_run:{iso_week}"
_WEEKLY_GATE_TTL = 8 * 86400  # 8 days — covers the weekly cycle + buffer

# Per-run history — 8 weeks rolling (newest first via LPUSH).
_HISTORY_KEY = "hs:llm_bench:history"
_HISTORY_MAX_ENTRIES = 8
_HISTORY_TTL = 90 * 86400


def _in_schedule_window(now: datetime) -> bool:
    if now.weekday() != _SCHEDULE_WEEKDAY:
        return False
    return _SCHEDULE_WINDOW_START_HOUR <= now.hour < _SCHEDULE_WINDOW_END_HOUR


def _already_ran_this_week(rc, iso_week: str) -> bool:
    try:
        return bool(rc.get(_WEEKLY_GATE_KEY.format(iso_week=iso_week)))
    except Exception:
        return False


def _mark_ran_this_week(rc, iso_week: str) -> None:
    try:
        rc.setex(_WEEKLY_GATE_KEY.format(iso_week=iso_week), _WEEKLY_GATE_TTL, "1")
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("llm_benchmark_monitor.mark_ran.exception")


# Pytest output pattern: "17 passed in 0.52s" / "3 failed, 14 passed in 0.52s" / "17 passed"
_PASS_COUNT_RE = re.compile(r"(\d+)\s+passed")
_FAIL_COUNT_RE = re.compile(r"(\d+)\s+failed")
_ERROR_COUNT_RE = re.compile(r"(\d+)\s+error")


def _parse_pytest_summary(stdout: str, stderr: str) -> dict:
    """Extract pass / fail / error counts from the combined output."""
    blob = (stdout or "") + "\n" + (stderr or "")
    pass_count = 0
    fail_count = 0
    error_count = 0
    m = _PASS_COUNT_RE.search(blob)
    if m:
        pass_count = int(m.group(1))
    m = _FAIL_COUNT_RE.search(blob)
    if m:
        fail_count = int(m.group(1))
    m = _ERROR_COUNT_RE.search(blob)
    if m:
        error_count = int(m.group(1))
    return {"passed": pass_count, "failed": fail_count, "errored": error_count}


def _run_benchmark_subprocess() -> dict | None:
    """Invoke pytest on the benchmark file. Returns parsed summary dict
    or None on catastrophic failure. Never raises."""
    python_bin = os.path.join(_BACKEND_DIR, "venv", "bin", "python")
    if not os.path.exists(python_bin):
        python_bin = "python3"  # fallback

    try:
        proc = subprocess.run(
            [python_bin, "-m", "pytest", _BENCH_FILE, "-q", "--tb=line"],
            cwd=_BACKEND_DIR,
            capture_output=True,
            timeout=_PYTEST_TIMEOUT_SECONDS,
            text=True,
            env={**os.environ, "APP_ENV": "test"},
        )
    except subprocess.TimeoutExpired:
        log.warning("llm_bench: subprocess timed out after %ds", _PYTEST_TIMEOUT_SECONDS)
        return None
    except Exception as exc:
        log.warning("llm_bench: subprocess launch failed: %s", exc)
        return None

    summary = _parse_pytest_summary(proc.stdout, proc.stderr)
    summary["exit_code"] = proc.returncode
    summary["total"] = summary["passed"] + summary["failed"] + summary["errored"]
    if summary["total"] == 0:
        # Nothing parsed — indicates a pytest infra problem (discovery
        # failure, syntax error in the test file). Distinct from "0
        # passed out of N" which would still report N.
        log.warning(
            "llm_bench: failed to parse pytest output. stdout head=%s stderr head=%s",
            (proc.stdout or "")[:300], (proc.stderr or "")[:300],
        )
        return None
    return summary


def _append_history(rc, run: dict, iso_week: str) -> None:
    """Prepend run to the rolling history, cap at _HISTORY_MAX_ENTRIES."""
    try:
        import json as _json
        entry = _json.dumps({
            "iso_week": iso_week,
            "passed": run["passed"],
            "failed": run["failed"],
            "errored": run["errored"],
            "total": run["total"],
            "exit_code": run["exit_code"],
            "captured_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        })
        pipe = rc.pipeline()
        pipe.lpush(_HISTORY_KEY, entry)
        pipe.ltrim(_HISTORY_KEY, 0, _HISTORY_MAX_ENTRIES - 1)
        pipe.expire(_HISTORY_KEY, _HISTORY_TTL)
        pipe.execute()
    except Exception as exc:
        log.warning("llm_bench: history write failed: %s", exc)


def _load_history(rc) -> list[dict]:
    try:
        import json as _json
        raw_entries = rc.lrange(_HISTORY_KEY, 0, _HISTORY_MAX_ENTRIES - 1) or []
        out = []
        for raw in raw_entries:
            try:
                s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                out.append(_json.loads(s))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _compute_baseline_total(history: list[dict]) -> int | None:
    """Return the maximum passed-count seen in prior weeks as the
    baseline. Max (not mean) because the benchmark is deterministic —
    any week below max is a regression we want to know about."""
    prior = history[1:]  # skip index 0 (today)
    if not prior:
        return None
    totals = [int(h.get("passed", 0)) for h in prior if h.get("passed") is not None]
    return max(totals) if totals else None


def run_weekly_check(db: Session, *, force: bool = False) -> dict:
    """Entry point — called from aggregation_worker every cycle. Gates
    internally. `force=True` bypasses both gates for tests / manual runs."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    iso_year, iso_week_num, _ = now.isocalendar()
    iso_week = f"{iso_year}-W{iso_week_num:02d}"

    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception:
        rc = None

    if not force:
        if not _in_schedule_window(now):
            return {"ran": False, "reason": "outside_window"}
        if rc is not None and _already_ran_this_week(rc, iso_week):
            return {"ran": False, "reason": "already_ran_this_week"}

    t0 = time.time()
    summary = _run_benchmark_subprocess()
    elapsed = round(time.time() - t0, 1)

    if summary is None:
        # Benchmark infra failure itself is an alert — a silently
        # broken monitor is worse than no monitor.
        try:
            from app.services.alerting import write_alert
            # heal-detection: weekly gate via _WEEKLY_GATE_KEY — one alert per ISO week; recovery = next week's run within tolerance (no new alert)
            write_alert(
                db,
                severity="warning",
                source="llm_benchmark_monitor",
                alert_type="llm_benchmark_run_failed",
                summary=f"LLM guardrail benchmark failed to run after {elapsed}s",
                detail={"elapsed_seconds": elapsed, "bench_file": _BENCH_FILE},
            )
        except Exception:
            pass  # SILENT-EXCEPT-OK: alerting failure already logged in alerting.py
        if rc is not None:
            _mark_ran_this_week(rc, iso_week)  # don't thrash next cycle
        return {"ran": True, "status": "subprocess_failed", "elapsed_seconds": elapsed}

    # Load history (includes prior runs), then prepend today's run.
    history = _load_history(rc) if rc is not None else []
    history_with_today = [{
        "iso_week": iso_week,
        "passed": summary["passed"],
        "failed": summary["failed"],
        "errored": summary["errored"],
        "total": summary["total"],
        "exit_code": summary["exit_code"],
    }] + history

    baseline = _compute_baseline_total(history_with_today)
    regression = False
    detail_extra: dict = {}

    if baseline is not None and summary["passed"] < baseline:
        regression = True
        detail_extra = {
            "baseline_passed": baseline,
            "drop": baseline - summary["passed"],
        }

    # Also treat any non-zero fail/error count as a regression — even
    # if the baseline is below current (first run), tests shouldn't fail.
    if summary["failed"] > 0 or summary["errored"] > 0:
        regression = True

    alerts_fired = 0
    if regression:
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="llm_benchmark_monitor",
                alert_type="llm_benchmark_regression",
                summary=(
                    f"LLM guardrail benchmark regression: "
                    f"{summary['passed']}/{summary['total']} passed "
                    f"(failed={summary['failed']}, errored={summary['errored']})"
                    + (f", baseline={baseline}" if baseline is not None else "")
                ),
                detail={
                    **summary,
                    **detail_extra,
                    "iso_week": iso_week,
                    "elapsed_seconds": elapsed,
                },
            )
            alerts_fired = 1
        except Exception as exc:
            log.warning("llm_bench: regression alert write failed: %s", exc)

    if rc is not None:
        _append_history(rc, summary, iso_week)
        _mark_ran_this_week(rc, iso_week)

    log.info(
        "llm_bench: ran in %.1fs passed=%d failed=%d errored=%d "
        "regression=%s alerts=%d",
        elapsed, summary["passed"], summary["failed"], summary["errored"],
        regression, alerts_fired,
    )
    return {
        "ran": True,
        "status": "ok",
        "elapsed_seconds": elapsed,
        "iso_week": iso_week,
        **summary,
        "regression": regression,
        "alerts_fired": alerts_fired,
    }
