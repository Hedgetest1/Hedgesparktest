"""Tests for streamlined daily digest — scannable in 3 seconds.

Locks the shape: headline + revenue + merchants + pipeline + attention
(only if truly needed) + footer.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services import telegram_agent as ta


def test_digest_returns_string_with_header():
    """Smoke: build runs against a fully-mocked DB without crashing."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0
    msg = ta.build_daily_digest(db)
    assert "*Daily Digest*" in msg
    assert "all systems running" in msg.lower() or "OK" in msg


def test_digest_no_approve_buttons_for_tier0_or_tier1():
    """Zero TIER_0/1 buttons in the cache."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0

    ta._digest_buttons_cache.clear()
    ta.build_daily_digest(db)

    # Buttons cache must be empty when no TIER_2 is pending
    flat = [b for row in ta._digest_buttons_cache for b in row]
    callback_data = [b.get("callback_data", "") for b in flat]
    assert not any("/bugfix_approve" in c for c in callback_data)
    assert not any("/bugfix_apply" in c for c in callback_data)
    assert not any("/approve" in c for c in callback_data)


def test_digest_includes_pipeline_section():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 5
    msg = ta.build_daily_digest(db)
    assert "Pipeline" in msg
    assert "fixes shipped" in msg


def test_digest_renders_tier2_review_section_when_present():
    """A TIER_2 candidate must produce a TIER_2 attention line."""
    db = MagicMock()

    def _execute(sql, *args, **kwargs):
        result = MagicMock()
        sql_str = str(sql).lower() if hasattr(sql, "__str__") else ""
        if "patch_risk_tier = 2" in sql_str and "patch_proposed" in sql_str:
            result.scalar.return_value = 3
            result.fetchall.return_value = []
        else:
            result.fetchall.return_value = []
            result.scalar.return_value = 0
        result.fetchone.return_value = (0, 0)
        return result

    db.execute.side_effect = _execute
    msg = ta.build_daily_digest(db)
    assert "TIER" in msg
    assert "review" in msg.lower()
    # Still no Telegram action button
    flat = [b for row in ta._digest_buttons_cache for b in row]
    assert not any("/approve" in str(b.get("callback_data", "")) for b in flat)


def test_digest_attention_section_only_when_needed():
    """No attention section when everything is healthy."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0
    msg = ta.build_daily_digest(db)
    assert "Needs you" not in msg
