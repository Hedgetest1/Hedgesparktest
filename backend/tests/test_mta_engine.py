"""Tests for mta_engine (β2) — multi-touch attribution models."""
from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from app.services.mta_engine import (
    Journey,
    Touch,
    _model_first_touch,
    _model_last_touch,
    _model_linear,
    _model_time_decay,
    _model_position_based,
)


def _j(touches: list[tuple[str, int]], rev: float = 100.0) -> Journey:
    """Build a Journey from (source, days_before_purchase) tuples."""
    now = datetime(2026, 4, 12, 12, 0, 0)
    j = Journey(
        visitor_id="v1",
        order_id="o1",
        revenue=rev,
        purchase_at=now,
        touches=[
            Touch(source=src, campaign=None, ts=now - timedelta(days=d))
            for src, d in touches
        ],
    )
    return j


class TestFirstTouch:
    def test_single_source(self):
        j = _j([("google", 3)])
        assert _model_first_touch(j) == {"google": 1.0}

    def test_multiple_sources(self):
        j = _j([("google", 10), ("meta", 5), ("direct", 0)])
        # First touch is the earliest (largest days_before)
        # Touches are in order of iteration, so first is google
        assert _model_first_touch(j) == {"google": 1.0}


class TestLastTouch:
    def test_last_wins(self):
        j = _j([("google", 10), ("meta", 5), ("direct", 0)])
        assert _model_last_touch(j) == {"direct": 1.0}


class TestLinear:
    def test_equal_split(self):
        j = _j([("google", 10), ("meta", 5), ("direct", 0)])
        credits = _model_linear(j)
        assert abs(sum(credits.values()) - 1.0) < 1e-6
        for source in credits:
            assert abs(credits[source] - 1.0 / 3.0) < 1e-6

    def test_single_touch(self):
        j = _j([("google", 5)])
        assert _model_linear(j) == {"google": 1.0}


class TestTimeDecay:
    def test_recent_weighted_higher(self):
        j = _j([("google", 14), ("direct", 0)])  # 14 days vs 0 days
        credits = _model_time_decay(j)
        assert credits["direct"] > credits["google"]
        assert abs(sum(credits.values()) - 1.0) < 1e-6

    def test_single_touch(self):
        j = _j([("google", 5)])
        assert _model_time_decay(j) == {"google": 1.0}


class TestPositionBased:
    def test_two_touches_equal_split(self):
        j = _j([("google", 10), ("direct", 0)])
        credits = _model_position_based(j)
        assert abs(credits["google"] - 0.5) < 1e-6
        assert abs(credits["direct"] - 0.5) < 1e-6

    def test_three_touches_u_shape(self):
        j = _j([("google", 10), ("meta", 5), ("direct", 0)])
        credits = _model_position_based(j)
        # First + last get 40% each, middle gets 20%
        assert abs(credits["google"] - 0.40) < 1e-6
        assert abs(credits["meta"] - 0.20) < 1e-6
        assert abs(credits["direct"] - 0.40) < 1e-6
        assert abs(sum(credits.values()) - 1.0) < 1e-6

    def test_four_touches(self):
        j = _j([("google", 14), ("meta", 10), ("email", 5), ("direct", 0)])
        credits = _model_position_based(j)
        # First 40, last 40, two middles split 20 → 10 each
        assert abs(credits["google"] - 0.40) < 1e-6
        assert abs(credits["direct"] - 0.40) < 1e-6
        assert abs(credits["meta"] - 0.10) < 1e-6
        assert abs(credits["email"] - 0.10) < 1e-6
