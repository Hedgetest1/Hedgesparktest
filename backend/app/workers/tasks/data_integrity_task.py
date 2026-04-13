"""
data_integrity_task.py — Semantic drift probe.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Runs every 6h
(cheap, but iterates N merchants, so we don't want it every 5-min cycle).
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("worker.aggregation.data_integrity")

_INTERVAL_S = 6 * 3_600  # 6 hours
_last_run: float | None = None


def should_run() -> bool:
    if _last_run is None:
        return True
    return (time.monotonic() - _last_run) >= _INTERVAL_S


def mark_done() -> None:
    global _last_run
    _last_run = time.monotonic()


def run() -> None:
    """
    Sweep active merchants and flag semantic drift: attribution collapse,
    order collapse, AOV drift, nudge lift decay. Findings are written to
    ops_alerts and picked up by bugfix_pipeline.run_bug_triage Rule 6.
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        from app.services.data_integrity_probe import run_probe
        result = run_probe(db)
        db.commit()
        if result.findings:
            _log.info(
                "data_integrity_probe: checks=%d findings=%d errors=%d",
                result.checks_run, len(result.findings), len(result.errors),
            )
    except Exception as exc:
        _log.warning("data_integrity_probe: error (non-fatal): %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
