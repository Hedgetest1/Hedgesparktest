"""Tests for B3 — hard quarantine for repeated failure families."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services import bugfix_prompt_grounding as bpg


def _candidate(domain="evolution", source_type="ops_alert", patch_files=None):
    c = MagicMock()
    c.affected_domain = domain
    c.source_type = source_type
    c.patch_files = patch_files
    c.context_json = None
    return c


def test_quarantine_below_threshold_returns_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 3
    is_q, reason = bpg.check_family_quarantine(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert is_q is False
    assert "3" in reason


def test_quarantine_at_threshold_blocks():
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 5
    is_q, reason = bpg.check_family_quarantine(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert is_q is True
    assert "5 failures" in reason


def test_quarantine_above_threshold_blocks():
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 12
    is_q, reason = bpg.check_family_quarantine(
        db, affected_domain="evolution", source_type="ops_alert",
    )
    assert is_q is True


def test_quarantine_no_classification_passes():
    db = MagicMock()
    is_q, reason = bpg.check_family_quarantine(
        db, affected_domain=None, source_type=None,
    )
    assert is_q is False
    assert reason == "no_family_classification"


def test_quarantine_operator_cleared_overrides():
    """An operator who runs clear_quarantine() should bypass the check."""
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 100  # tons of failures

    fake_redis = MagicMock()
    fake_redis.get.return_value = "1"  # cleared flag set
    with patch.object(bpg, "_client", return_value=fake_redis, create=True):
        # Patch the deeper import path used inside check_family_quarantine
        with patch("app.core.redis_client._client", return_value=fake_redis):
            is_q, reason = bpg.check_family_quarantine(
                db, affected_domain="evolution", source_type="ops_alert",
            )
    assert is_q is False
    assert reason == "operator_cleared"


def test_preflight_quarantine_blocks_candidate():
    """Preflight wired with db must reject quarantined candidates."""
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 8

    cand = _candidate()
    ok, reason = bpg.preflight_ground_candidate(cand, db=db)
    assert ok is False
    assert "quarantined_family" in reason


def test_preflight_without_db_skips_quarantine():
    """Backwards-compat: callers that don't pass db get the old behavior."""
    cand = _candidate()
    ok, reason = bpg.preflight_ground_candidate(cand)  # no db
    assert ok is True


def test_get_quarantined_families_returns_groups():
    db = MagicMock()
    fake_rows = [
        ("evolution", "ops_alert", 7),
        ("billing", "sentry_incident", 6),
    ]
    chain = db.query.return_value.filter.return_value.group_by.return_value.having.return_value
    chain.all.return_value = fake_rows
    result = bpg.get_quarantined_families(db)
    assert len(result) == 2
    assert result[0]["domain"] == "evolution"
    assert result[0]["failure_count"] == 7


def test_clear_quarantine_writes_redis_flag():
    fake_redis = MagicMock()
    with patch("app.core.redis_client._client", return_value=fake_redis):
        ok = bpg.clear_quarantine("evolution", "ops_alert", ttl_days=14)
    assert ok is True
    fake_redis.setex.assert_called_once()
    args, kwargs = fake_redis.setex.call_args
    assert "evolution" in args[0]
    assert "ops_alert" in args[0]
    assert args[1] == 14 * 24 * 3600
