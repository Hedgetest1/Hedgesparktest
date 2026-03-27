"""Tests for LLM model routing + explicit provider fallback policy."""
from unittest.mock import patch as mock_patch

from app.core.llm_router import select_model, get_provider_policy, SONNET, OPUS, SONNET_OPENAI


# ---------------------------------------------------------------------------
# Model selection (unchanged logic)
# ---------------------------------------------------------------------------

def test_default_is_sonnet():
    sel = select_model(module="bugfix_proposal")
    assert sel.model == SONNET
    assert sel.escalation is False


def test_orchestrator_always_sonnet():
    sel = select_model(module="orchestrator", patch_risk_tier=2, file_count=10, is_complex=True)
    assert sel.model == SONNET
    assert "orchestrator" in sel.reason


def test_high_tier_gets_opus():
    sel = select_model(module="bugfix_proposal", patch_risk_tier=1)
    assert sel.model == OPUS


def test_multi_file_gets_opus():
    sel = select_model(module="bugfix_proposal", file_count=4)
    assert sel.model == OPUS


def test_complex_flag_gets_opus():
    sel = select_model(module="bugfix_proposal", is_complex=True)
    assert sel.model == OPUS


def test_previous_failure_escalates():
    sel = select_model(module="bugfix_proposal", previous_failed=True)
    assert sel.model == OPUS
    assert sel.escalation is True


def test_single_file_low_tier_stays_sonnet():
    sel = select_model(module="bugfix_proposal", patch_risk_tier=0, file_count=1)
    assert sel.model == SONNET


def test_no_provider_returns_none():
    sel = select_model(anthropic_available=False, openai_available=False)
    assert sel.model == "none"
    assert sel.provider == "none"


def test_always_returns_valid():
    for kwargs in [
        {},
        {"module": "orchestrator"},
        {"module": "bugfix_proposal", "patch_risk_tier": 2, "file_count": 5},
        {"previous_failed": True},
        {"anthropic_available": False, "openai_available": False},
    ]:
        sel = select_model(**kwargs)
        assert sel.provider is not None
        assert sel.model is not None
        assert sel.reason is not None
        assert isinstance(sel.escalation, bool)
        assert isinstance(sel.max_tokens, int)


# ---------------------------------------------------------------------------
# Explicit provider fallback policy
# ---------------------------------------------------------------------------

def test_no_anthropic_fallback_disabled_returns_none():
    """No Anthropic + fallback disabled → model=none, no call."""
    import app.core.llm_router as router
    orig_pref, orig_fb = router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK
    try:
        router.PREFERRED_PROVIDER = "anthropic"
        router.ALLOW_FALLBACK = False
        sel = select_model(anthropic_available=False, openai_available=True)
        assert sel.provider == "none"
        assert sel.model == "none"
        assert "fallback_disabled" in sel.reason
    finally:
        router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK = orig_pref, orig_fb


def test_no_anthropic_fallback_enabled_uses_openai():
    """No Anthropic + fallback enabled → OpenAI selected."""
    import app.core.llm_router as router
    orig_pref, orig_fb = router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK
    try:
        router.PREFERRED_PROVIDER = "anthropic"
        router.ALLOW_FALLBACK = True
        sel = select_model(module="bugfix_proposal", anthropic_available=False, openai_available=True)
        assert sel.provider == "openai"
        assert sel.model == SONNET_OPENAI
    finally:
        router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK = orig_pref, orig_fb


def test_preferred_openai_no_anthropic_fallback():
    """Preferred=openai, OpenAI unavailable, fallback disabled → none."""
    import app.core.llm_router as router
    orig_pref, orig_fb = router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK
    try:
        router.PREFERRED_PROVIDER = "openai"
        router.ALLOW_FALLBACK = False
        sel = select_model(anthropic_available=True, openai_available=False)
        assert sel.provider == "none"
        assert "fallback_disabled" in sel.reason
    finally:
        router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK = orig_pref, orig_fb


def test_no_multi_hop_fallback():
    """Even with fallback enabled, only one fallback provider is tried."""
    import app.core.llm_router as router
    orig_pref, orig_fb = router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK
    try:
        router.PREFERRED_PROVIDER = "anthropic"
        router.ALLOW_FALLBACK = True
        # Both unavailable — fallback enabled but no provider works
        sel = select_model(anthropic_available=False, openai_available=False)
        assert sel.provider == "none"
        assert sel.model == "none"
    finally:
        router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK = orig_pref, orig_fb


def test_preferred_available_no_fallback_needed():
    """Preferred provider available → no fallback logic triggered."""
    import app.core.llm_router as router
    orig_pref, orig_fb = router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK
    try:
        router.PREFERRED_PROVIDER = "anthropic"
        router.ALLOW_FALLBACK = False
        sel = select_model(anthropic_available=True, openai_available=True)
        assert sel.provider == "anthropic"
    finally:
        router.PREFERRED_PROVIDER, router.ALLOW_FALLBACK = orig_pref, orig_fb


def test_provider_policy_visibility():
    """get_provider_policy returns current settings."""
    policy = get_provider_policy()
    assert "preferred_provider" in policy
    assert "allow_fallback" in policy
    assert "last_blocked_reason" in policy
