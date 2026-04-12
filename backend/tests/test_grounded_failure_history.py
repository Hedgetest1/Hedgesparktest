"""Tests for C1 — grounded failure history in LLM prompt."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.services.bugfix_prompt_grounding import extract_recent_failures


def _fp(date_str: str, outcome: str, reason: str):
    fp = MagicMock()
    fp.created_at = datetime.fromisoformat(date_str).replace(tzinfo=None)
    fp.outcome = outcome
    fp.failure_reason = reason
    return fp


def test_returns_empty_when_no_classification():
    db = MagicMock()
    assert extract_recent_failures(db, affected_domain=None, source_type=None) == ""


def test_returns_empty_when_no_history():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    result = extract_recent_failures(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert result == ""


def test_renders_failures_in_prompt_format():
    db = MagicMock()
    rows = [
        _fp("2026-04-01T10:00:00", "rolled_back", "caused regression in evolution_engine"),
        _fp("2026-03-28T15:30:00", "apply_failed", "git apply rejected: corrupt patch line 88"),
        _fp("2026-03-22T09:15:00", "tests_failed", "test_evolution_decision: assertion error"),
    ]
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows

    result = extract_recent_failures(
        db, affected_domain="evolution", source_type="ops_alert",
    )

    assert "Prior failed attempts" in result
    assert "DO NOT" in result.upper() or "do not" in result.lower()
    assert "2026-04-01" in result
    assert "rolled_back" in result
    assert "caused regression" in result
    assert "2026-03-28" in result
    assert "corrupt patch" in result


def test_truncates_long_failure_reason():
    db = MagicMock()
    long_reason = "x" * 500
    rows = [_fp("2026-04-01T10:00:00", "apply_failed", long_reason)]
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    result = extract_recent_failures(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    # Per-failure cap is 200 chars
    assert "x" * 201 not in result


def test_query_failure_returns_empty():
    db = MagicMock()
    db.query.side_effect = RuntimeError("db down")
    result = extract_recent_failures(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert result == ""
