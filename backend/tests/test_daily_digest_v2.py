"""Tests for streamlined daily digest — scannable in 3 seconds.

Locks the shape: headline + revenue + merchants + pipeline + attention
(only if truly needed) + footer.

Hermeticity note
----------------
build_daily_digest reads from several module-level singletons beyond
the `db` argument: Redis for `hs:system_health` cache, Redis for RARS
history keys, synthesize_health() for degraded-system detection, and
llm_budget.get_usage_summary for cost rollups. A MagicMock()'d db is
not enough to make the test deterministic — any of those globals may
return "degraded" or "critical" based on real prod state, which flips
the string assertions on the digest output.

Each test below uses `_hermetic_digest_mocks()` to patch every
external dependency, guaranteeing a deterministic "healthy + zero
state" baseline.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.services import telegram_agent as ta


@contextmanager
def _hermetic_digest_mocks():
    """Patch every non-db dependency of build_daily_digest so the
    digest output is a function of ONLY the mocked db argument.

    Returns a MagicMock for the `db` argument pre-configured to
    return zero/empty for every execute/fetchone/fetchall/scalar."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0

    healthy_health = {
        "overall_status": "ok",
        "dimensions": [],
        "grade": "A",
        "score": 100,
    }
    healthy_synth = MagicMock()
    healthy_synth.to_dict.return_value = healthy_health

    # Reset button cache before each test so the assertion on
    # callback_data is a function of THIS test's output, not leftover
    # state from whichever other test happened to run first.
    ta._digest_buttons_cache.clear()

    with patch("app.core.redis_client.cache_get", return_value=healthy_health), \
         patch("app.services.system_health_synthesizer.synthesize_health",
               return_value=healthy_synth), \
         patch("app.core.redis_client._client", return_value=None), \
         patch("app.core.llm_budget.get_usage_summary", return_value={
             "month_spend_eur": 0.0,
             "month_cap_eur": 10.0,
             "module_spend_7d": {},
         }):
        yield db


def test_digest_returns_string_with_header():
    """Smoke: build runs against a fully-mocked DB without crashing."""
    with _hermetic_digest_mocks() as db:
        msg = ta.build_daily_digest(db)
    assert "*Daily Digest*" in msg
    assert "all systems running" in msg.lower() or "OK" in msg


def test_digest_no_approve_buttons_for_tier0_or_tier1():
    """Zero TIER_0/1 buttons in the cache."""
    with _hermetic_digest_mocks() as db:
        ta.build_daily_digest(db)

    # Buttons cache must be empty when no TIER_2 is pending
    flat = [b for row in ta._digest_buttons_cache for b in row]
    callback_data = [b.get("callback_data", "") for b in flat]
    assert not any("/bugfix_approve" in c for c in callback_data)
    assert not any("/bugfix_apply" in c for c in callback_data)
    assert not any("/approve" in c for c in callback_data)


def test_digest_includes_pipeline_section():
    with _hermetic_digest_mocks() as db:
        db.execute.return_value.scalar.return_value = 5
        msg = ta.build_daily_digest(db)
    assert "Pipeline" in msg
    assert "fixes shipped" in msg


def test_digest_renders_tier2_review_section_when_present():
    """A TIER_2 candidate must produce a TIER_2 attention line."""
    with _hermetic_digest_mocks() as db:
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
    with _hermetic_digest_mocks() as db:
        msg = ta.build_daily_digest(db)
    assert "Needs you" not in msg
