"""audit_telemetry — record per-audit fire-rate + coverage trends over time.

Purpose
-------
The 49 audit scripts in `backend/scripts/audit_*.py` block preflight on
structural drift. They tell us POINT-IN-TIME that a pattern regressed.
They do NOT tell us "this audit fires 3x/week while that one has been
silent for 6 months" or "coverage dropped from 98% to 94% over the past
30 days" — the slow-drift class. This module is that missing sink.

Storage
-------
One Redis HASH per audit, keyed by YYYY-MM-DD day:

    hs:audit_telemetry:{audit_name}     # HASH
        fields:
            "YYYY-MM-DD" → "runs|findings|severity"
        TTL: 90 days (refreshed on every write)

Example record for 2026-04-24:
    hs:audit_telemetry:audit_bundle_budget
        "2026-04-24" → "12|0|info"      (12 runs, 0 findings, last severity info)
        "2026-04-23" → "8|1|warn"       (1 finding was raised on this day)

Why HASH per audit instead of a flat key per (audit, day)?
- 49 audits × 90 days = 4410 keys with flat layout.
- HASH keeps it 49 keys with O(1) field access and one TTL refresh.
- Matches `hs:ns_cal:obs:{shop}` pattern in night_shift_calibration.

Fallback contract
-----------------
Same as redis_client: Redis down → record_silent_return + return False.
The audits must continue to work when Redis is unavailable (e.g., local
dev without redis-cli running). Never raise from the writer.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("audit_telemetry")

_PREFIX = "hs:audit_telemetry"
_TTL_SECONDS = 90 * 24 * 3600  # 90 days

# Severity tokens — keep short and ordered so string sorting is stable.
_VALID_SEVERITY = {"info", "warn", "critical"}


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _today_key() -> str:
    return date.today().isoformat()


def _key(audit_name: str) -> str:
    return f"{_PREFIX}:{audit_name}"


def _parse_cell(raw: str | bytes | None) -> dict | None:
    """Parse "runs|findings|severity" cell. Returns None on any parse failure."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    try:
        return {
            "runs": int(parts[0]),
            "findings": int(parts[1]),
            "severity": parts[2],
        }
    except (ValueError, TypeError):
        return None


def record_run(
    audit_name: str,
    findings: int,
    severity: str = "info",
) -> bool:
    """Record one audit execution. Idempotent within the same day:
    a second call on the same day increments `runs` and OVERWRITES
    `findings` + `severity` with the latest values (since findings
    are deterministic snapshots, not cumulative events).

    Returns True if the write landed in Redis, False on any fallback
    path (Redis down, validation failure, etc.).
    """
    if not audit_name or not isinstance(audit_name, str):
        return False
    if severity not in _VALID_SEVERITY:
        severity = "info"
    try:
        findings_int = int(findings)
    except (TypeError, ValueError):
        return False
    if findings_int < 0:
        findings_int = 0

    rc = _redis()
    if rc is None:
        record_silent_return("audit_telemetry.record_run")
        return False

    try:
        key = _key(audit_name)
        day = _today_key()
        existing = _parse_cell(rc.hget(key, day))
        runs_today = (existing["runs"] + 1) if existing else 1
        cell = f"{runs_today}|{findings_int}|{severity}"
        rc.hset(key, day, cell)
        rc.expire(key, _TTL_SECONDS)
        return True
    except Exception as exc:
        log.warning("audit_telemetry: record_run failed for %s: %s",
                    audit_name, exc)
        return False


def read_audit_history(audit_name: str, days: int = 30) -> list[dict]:
    """Return list of per-day records for the last `days` days, ordered
    oldest → newest. Missing days are NOT padded — the list is sparse."""
    if not audit_name or days <= 0:
        return []

    rc = _redis()
    if rc is None:
        record_silent_return("audit_telemetry.read_history")
        return []

    try:
        key = _key(audit_name)
        raw_map = rc.hgetall(key)
    except Exception as exc:
        log.warning("audit_telemetry: read_audit_history failed for %s: %s",
                    audit_name, exc)
        return []

    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    out: list[dict] = []
    for day_raw, cell_raw in raw_map.items():
        day = day_raw.decode("utf-8") if isinstance(day_raw, bytes) else day_raw
        if day < cutoff:
            continue
        parsed = _parse_cell(cell_raw)
        if parsed is None:
            continue
        parsed["day"] = day
        out.append(parsed)
    out.sort(key=lambda r: r["day"])
    return out


def read_all_audits(days: int = 7) -> dict[str, dict]:
    """Summary for every audit with at least one recorded run in the
    last `days` days. Map shape:

        {
            "audit_bundle_budget": {
                "runs": 12, "findings_total": 1, "findings_last": 1,
                "last_severity": "warn", "last_day": "2026-04-23",
                "days_seen": 2,
            },
            ...
        }
    """
    rc = _redis()
    if rc is None:
        record_silent_return("audit_telemetry.read_all")
        return {}

    try:
        cursor = 0
        keys: list[str] = []
        while True:
            cursor, chunk = rc.scan(
                cursor=cursor, match=f"{_PREFIX}:*", count=100
            )
            for k in chunk:
                k_str = k.decode("utf-8") if isinstance(k, bytes) else k
                keys.append(k_str)
            if cursor == 0:
                break
    except Exception as exc:
        log.warning("audit_telemetry: read_all scan failed: %s", exc)
        return {}

    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    out: dict[str, dict] = {}
    for key in keys:
        audit_name = key[len(_PREFIX) + 1:]
        try:
            raw_map = rc.hgetall(key)
        except Exception:
            continue

        total_runs = 0
        total_findings = 0
        last_day = ""
        last_findings = 0
        last_severity = "info"
        days_seen = 0

        for day_raw, cell_raw in raw_map.items():
            day = day_raw.decode("utf-8") if isinstance(day_raw, bytes) else day_raw
            if day < cutoff:
                continue
            parsed = _parse_cell(cell_raw)
            if parsed is None:
                continue
            total_runs += parsed["runs"]
            total_findings += parsed["findings"]
            days_seen += 1
            if day > last_day:
                last_day = day
                last_findings = parsed["findings"]
                last_severity = parsed["severity"]

        if total_runs == 0:
            continue

        out[audit_name] = {
            "runs": total_runs,
            "findings_total": total_findings,
            "findings_last": last_findings,
            "last_severity": last_severity,
            "last_day": last_day,
            "days_seen": days_seen,
        }

    return out


__all__ = ["record_run", "read_audit_history", "read_all_audits"]
