"""Tests for B4 — TIER_2 weekly batch review."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.services import telegram_agent as ta


def _row(cid, title, domain="billing", confidence=75, risk="medium", verdict="refine"):
    r = MagicMock()
    # Tuple-like access used by build_tier2_weekly_review
    r.__getitem__ = lambda self, idx: {
        0: cid, 1: title, 2: domain, 3: datetime.now(),
        4: confidence, 5: risk, 6: verdict,
    }[idx]
    return r


def test_empty_when_no_candidates():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    msg, buttons = ta.build_tier2_weekly_review(db)
    assert msg == ""
    assert buttons == []


def test_renders_candidates_with_risk_emojis():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        _row(101, "Rotate session JWT signing key", domain="auth", risk="high"),
        _row(102, "Add encryption to access_token", domain="billing", risk="low"),
    ]
    msg, buttons = ta.build_tier2_weekly_review(db)

    assert "TIER" in msg and "Weekly Review" in msg
    assert "2 TIER" in msg
    assert "#101" in msg
    assert "#102" in msg
    assert "Rotate session JWT" in msg
    assert "Add encryption to access_token" in msg
    assert "auth" in msg
    assert "billing" in msg


def test_buttons_include_batch_actions_with_all_ids():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        _row(201, "fix one"),
        _row(202, "fix two"),
        _row(203, "fix three"),
    ]
    msg, buttons = ta.build_tier2_weekly_review(db)
    assert len(buttons) == 1  # one row
    row = buttons[0]
    approve_btn = next(b for b in row if "approve" in b["text"].lower())
    reject_btn = next(b for b in row if "reject" in b["text"].lower())
    assert "201,202,203" in approve_btn["callback_data"]
    assert "201,202,203" in reject_btn["callback_data"]
    assert "(3)" in approve_btn["text"]


def test_dashboard_link_present():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [_row(301, "fix")]
    msg, _ = ta.build_tier2_weekly_review(db)
    assert "dashboard" in msg.lower()
    assert "tier2" in msg.lower()


def test_send_returns_false_when_nothing_pending():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    assert ta.send_tier2_weekly_review(db) is False
