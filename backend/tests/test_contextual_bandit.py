"""Tests for contextual_bandit (δ1) — Thompson Sampling nudge selection."""
from __future__ import annotations

import random
import pytest

from app.services.contextual_bandit import (
    select_variant,
    record_outcome,
    make_context,
    get_arm_stats,
    _tod_bucket,
    _context_key,
)


SHOP = "test-bandit-suite.myshopify.com"
VARIANTS = ["high_interest", "social_proof", "return_visitor", "engagement_depth"]


@pytest.fixture(autouse=True)
def _cleanup():
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            for k in rc.keys(f"hs:bandit:{SHOP}:*"):
                rc.delete(k)
    except Exception:
        pass
    yield
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            for k in rc.keys(f"hs:bandit:{SHOP}:*"):
                rc.delete(k)
    except Exception:
        pass


class TestContext:
    def test_make_context_defaults(self):
        ctx = make_context()
        assert "device" in ctx and "source" in ctx and "category" in ctx and "tod" in ctx

    def test_make_context_normalizes(self):
        ctx = make_context(device="Mobile", source="GOOGLE", category="Apparel")
        assert ctx["device"] == "mobile"
        assert ctx["source"] == "google"
        assert ctx["category"] == "apparel"

    def test_tod_bucket(self):
        from datetime import datetime, timezone
        assert _tod_bucket(datetime(2026, 4, 12, 3, 0, tzinfo=timezone.utc)) == "night"
        assert _tod_bucket(datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc)) == "morning"
        assert _tod_bucket(datetime(2026, 4, 12, 15, 0, tzinfo=timezone.utc)) == "afternoon"
        assert _tod_bucket(datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc)) == "evening"


class TestSelection:
    def test_single_variant_returns_it(self):
        ctx = make_context(device="desktop")
        assert select_variant(SHOP, ctx, ["only_one"]) == "only_one"

    def test_empty_variants_raises(self):
        with pytest.raises(ValueError):
            select_variant(SHOP, make_context(), [])

    def test_cold_start_covers_all_variants(self):
        """With no training, Thompson should visit every variant over time."""
        ctx = make_context(device="desktop", source="direct")
        seen = set()
        rng = random.Random(7)
        for _ in range(200):
            seen.add(select_variant(SHOP, ctx, VARIANTS, rng=rng))
        assert seen == set(VARIANTS)

    def test_convergence_to_winner(self):
        """After enough positive evidence, winner dominates."""
        ctx = make_context(device="mobile", source="google", category="apparel")
        # Strong signal for social_proof
        for _ in range(80):
            record_outcome(SHOP, ctx, "social_proof", success=True)
        for _ in range(10):
            record_outcome(SHOP, ctx, "social_proof", success=False)
        # Weak signal for the others
        for v in ["high_interest", "return_visitor", "engagement_depth"]:
            for _ in range(40):
                record_outcome(SHOP, ctx, v, success=False)
            for _ in range(5):
                record_outcome(SHOP, ctx, v, success=True)

        rng = random.Random(42)
        picks = [select_variant(SHOP, ctx, VARIANTS, rng=rng) for _ in range(300)]
        winner_count = picks.count("social_proof")
        # Should pick winner >85% of the time
        assert winner_count / 300 > 0.85, f"winner_rate={winner_count/300}"

    def test_uniform_strategy(self):
        ctx = make_context()
        rng = random.Random(1)
        picks = [select_variant(SHOP, ctx, VARIANTS, strategy="uniform", rng=rng) for _ in range(400)]
        # Uniform should give ~25% each
        for v in VARIANTS:
            count = picks.count(v)
            assert 60 < count < 140, f"{v}={count}"


class TestOutcomeRecording:
    def test_success_increments_alpha(self):
        ctx = make_context(device="desktop")
        record_outcome(SHOP, ctx, "v1", success=True)
        stats = get_arm_stats(SHOP, ctx, "v1")
        assert stats["successes"] == 1
        assert stats["alpha"] == 2.0
        assert stats["beta"] == 1.0

    def test_failure_increments_beta(self):
        ctx = make_context(device="desktop")
        record_outcome(SHOP, ctx, "v1", success=False)
        stats = get_arm_stats(SHOP, ctx, "v1")
        assert stats["successes"] == 0
        assert stats["pulls"] == 1
        assert stats["beta"] == 2.0

    def test_stats_reflect_many_trials(self):
        ctx = make_context(device="mobile")
        for _ in range(30):
            record_outcome(SHOP, ctx, "v1", success=True)
        for _ in range(10):
            record_outcome(SHOP, ctx, "v1", success=False)
        stats = get_arm_stats(SHOP, ctx, "v1")
        assert stats["pulls"] == 40
        assert stats["successes"] == 30
        assert 0.7 < stats["est_success_rate"] < 0.8


class TestContextIsolation:
    def test_different_contexts_dont_share_state(self):
        ctx_mobile = make_context(device="mobile")
        ctx_desktop = make_context(device="desktop")
        for _ in range(20):
            record_outcome(SHOP, ctx_mobile, "v1", success=True)
        stats_mobile = get_arm_stats(SHOP, ctx_mobile, "v1")
        stats_desktop = get_arm_stats(SHOP, ctx_desktop, "v1")
        assert stats_mobile["pulls"] == 20
        assert stats_desktop["pulls"] == 0
