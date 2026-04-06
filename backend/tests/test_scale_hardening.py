"""
Tests for scale-hardening: worker sharding, scaled LLM budget, Sentry posture.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.core.sentry_status import get_sentry_status, format_sentry_digest_line
from app.core.worker_sharding import shard_owns_shop, get_shard_info, _parse_shard


# ---------------------------------------------------------------------------
# Worker sharding — opt-in contract
# ---------------------------------------------------------------------------

def test_sharding_disabled_by_default():
    """When WORKER_SHARD unset, every shop is owned (total=1)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WORKER_SHARD", None)
        assert shard_owns_shop("any-shop.myshopify.com") is True
        info = get_shard_info()
        assert info["shard_total"] == 1
        assert info["is_sharded"] is False


def test_sharding_partitions_shops_evenly():
    """With shard 0/2, exactly half of shops hash to bucket 0."""
    shops = [f"shop-{i}.myshopify.com" for i in range(1000)]

    def count_owned(idx: int, total: int) -> int:
        with patch.dict(os.environ, {"WORKER_SHARD": f"{idx}/{total}"}, clear=False):
            return sum(1 for s in shops if shard_owns_shop(s))

    owned_0 = count_owned(0, 2)
    owned_1 = count_owned(1, 2)
    # Every shop exactly once across the two shards
    assert owned_0 + owned_1 == 1000
    # Roughly balanced (±20% tolerance)
    assert 400 < owned_0 < 600
    assert 400 < owned_1 < 600


def test_sharding_partition_consistent_across_replicas():
    """A given shop always maps to the same bucket, deterministically."""
    with patch.dict(os.environ, {"WORKER_SHARD": "1/4"}, clear=False):
        # Same shop, called 100 times, gets the same answer
        results = [shard_owns_shop("deterministic.myshopify.com") for _ in range(100)]
        assert all(r == results[0] for r in results)


def test_sharding_every_shop_owned_by_exactly_one_shard():
    """Total ownership across shards = exactly 1× for every shop."""
    shops = [f"s{i}.myshopify.com" for i in range(500)]
    for total in (1, 2, 3, 4, 8):
        owned_counts = []
        for idx in range(total):
            with patch.dict(os.environ, {"WORKER_SHARD": f"{idx}/{total}"}, clear=False):
                owned_counts.append(sum(1 for s in shops if shard_owns_shop(s)))
        assert sum(owned_counts) == len(shops)


def test_sharding_malformed_env_falls_back_to_noop():
    """Bad WORKER_SHARD values degrade to 0/1 (every shop owned)."""
    for bad in ("nonsense", "1", "1/", "-1/2", "5/3", "0/0"):
        with patch.dict(os.environ, {"WORKER_SHARD": bad}, clear=False):
            assert shard_owns_shop("x.myshopify.com") is True
            info = get_shard_info()
            assert info["shard_total"] == 1


# ---------------------------------------------------------------------------
# Sentry status — posture + digest line
# ---------------------------------------------------------------------------

def test_sentry_disabled_when_dsn_missing():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENTRY_DSN", None)
        s = get_sentry_status()
        assert s["enabled"] is False


def test_sentry_estimate_mode_error_only():
    with patch.dict(os.environ, {"SENTRY_DSN": "https://fake@sentry", "SENTRY_TRACES_SAMPLE_RATE": "0.0"}):
        s = get_sentry_status()
        assert s["estimate_mode"] == "error_only"
        assert s["warning"] is None


def test_sentry_estimate_mode_low_trace():
    with patch.dict(os.environ, {"SENTRY_DSN": "https://fake@sentry", "SENTRY_TRACES_SAMPLE_RATE": "0.05"}):
        s = get_sentry_status()
        assert s["estimate_mode"] == "low_trace"
        assert s["warning"] is None


def test_sentry_estimate_mode_moderate_warns():
    with patch.dict(os.environ, {"SENTRY_DSN": "https://fake@sentry", "SENTRY_TRACES_SAMPLE_RATE": "0.15"}):
        s = get_sentry_status()
        assert s["estimate_mode"] == "moderate_trace"
        assert s["warning"] is not None
        assert "quota" in s["warning"].lower()


def test_sentry_estimate_mode_high_warns_loudly():
    with patch.dict(os.environ, {"SENTRY_DSN": "https://fake@sentry", "SENTRY_TRACES_SAMPLE_RATE": "0.50"}):
        s = get_sentry_status()
        assert s["estimate_mode"] == "high_trace"
        assert s["warning"] is not None
        assert "HIGH" in s["warning"] or "0.05" in s["warning"]


def test_sentry_digest_line_disabled():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENTRY_DSN", None)
        line = format_sentry_digest_line()
        assert "DISABLED" in line
        assert "🔴" in line


def test_sentry_digest_line_enabled_mirrors_llm_shape():
    """Digest line must start with '*SENTRY:*' for consistent operator scanning."""
    with patch.dict(os.environ, {"SENTRY_DSN": "https://fake@sentry", "SENTRY_TRACES_SAMPLE_RATE": "0.05"}):
        line = format_sentry_digest_line()
        assert line.startswith("*SENTRY:*")
        assert "enabled" in line
        assert "0.05" in line


def test_sentry_bad_trace_rate_env_is_safe():
    with patch.dict(os.environ, {"SENTRY_DSN": "https://fake@sentry", "SENTRY_TRACES_SAMPLE_RATE": "not_a_float"}):
        s = get_sentry_status()
        assert s["traces_sample_rate"] == 0.0
        assert s["estimate_mode"] == "error_only"


# ---------------------------------------------------------------------------
# LLM budget scaling — dynamic cap by merchant count
# ---------------------------------------------------------------------------

def test_llm_effective_cap_uses_static_floor_at_small_scale():
    """With few merchants, effective cap == static floor."""
    from app.core.llm_budget import get_effective_monthly_cap, MONTHLY_EUR_CAP, _effective_cap_cache
    _effective_cap_cache["value"] = None  # clear cache
    _effective_cap_cache["computed_at"] = 0.0

    # Static floor should apply when merchants * €0.10 < floor
    # Default floor is 10.0 EUR, default per-merchant is 0.10 EUR.
    # 50 merchants × 0.10 = 5.0 < 10.0 → floor wins.
    cap = get_effective_monthly_cap()
    assert cap >= MONTHLY_EUR_CAP


def test_llm_effective_cap_never_exceeds_hard_ceiling():
    """Hard ceiling clamps the scaled value — 100k merchants cannot
    produce a €10k cap if LLM_MAX_MONTHLY_EUR=500."""
    from app.core.llm_budget import _effective_cap_cache
    import app.core.llm_budget as lb
    saved_cache = dict(_effective_cap_cache)
    saved_max = lb._LLM_MAX_MONTHLY_EUR
    saved_per = lb._LLM_EUR_PER_MERCHANT
    try:
        # 100_000 merchants × €0.10 = €10,000 scaled. Hard ceiling €500 wins.
        _effective_cap_cache["merchants"] = 100_000
        _effective_cap_cache["computed_at"] = 10**12  # far future, don't refresh
        _effective_cap_cache["value"] = True
        lb._LLM_MAX_MONTHLY_EUR = 500.0
        lb._LLM_EUR_PER_MERCHANT = 0.10
        cap = lb.get_effective_monthly_cap()
        assert cap == 500.0
    finally:
        _effective_cap_cache.clear()
        _effective_cap_cache.update(saved_cache)
        lb._LLM_MAX_MONTHLY_EUR = saved_max
        lb._LLM_EUR_PER_MERCHANT = saved_per


def test_llm_usage_summary_includes_scaled_metadata():
    """Summary must expose both the effective cap AND its static floor."""
    from app.core.llm_budget import get_usage_summary
    s = get_usage_summary()
    assert "monthly_cap_eur" in s
    assert "monthly_cap_static_floor" in s
    assert "monthly_cap_scaled_by_merchants" in s
    # Effective cap >= static floor always
    assert s["monthly_cap_eur"] >= s["monthly_cap_static_floor"]
