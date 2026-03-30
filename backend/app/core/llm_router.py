"""
llm_router.py — Deterministic model selection + explicit provider fallback.

Every LLM call must use select_model() to determine which model to use.

Provider policy (from env):
    LLM_PREFERRED_PROVIDER:  anthropic | openai  (default: anthropic)
    LLM_ALLOW_FALLBACK:      true | false        (default: false)

If preferred provider is unavailable and fallback is disabled:
    → provider="none", model="none", no call attempted

No multi-hop fallback. No implicit provider switching.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("llm_router")

# Model definitions
SONNET = "claude-sonnet-4-20250514"
OPUS = "claude-opus-4-20250514"

SONNET_OPENAI = "gpt-4o-mini"
OPUS_OPENAI = "gpt-4o"

# Provider policy (read once at import, restart to change)
PREFERRED_PROVIDER = os.getenv("LLM_PREFERRED_PROVIDER", "anthropic").strip().lower()
ALLOW_FALLBACK = os.getenv("LLM_ALLOW_FALLBACK", "false").strip().lower() == "true"

_last_blocked_reason: str = ""


@dataclass
class ModelSelection:
    provider: str = "anthropic"
    model: str = SONNET
    reason: str = "default_sonnet"
    escalation: bool = False
    max_tokens: int = 512


def get_provider_policy() -> dict:
    """Return current provider policy for visibility endpoints."""
    return {
        "preferred_provider": PREFERRED_PROVIDER,
        "allow_fallback": ALLOW_FALLBACK,
        "last_blocked_reason": _last_blocked_reason,
    }


def select_model(
    *,
    module: str = "default",
    patch_risk_tier: int | None = None,
    file_count: int = 1,
    previous_failed: bool = False,
    is_complex: bool = False,
    anthropic_available: bool = True,
    openai_available: bool = True,
) -> ModelSelection:
    """
    Select LLM model based on context + explicit provider policy.
    """
    global _last_blocked_reason
    sel = ModelSelection()

    # --- Provider resolution with explicit fallback policy ---
    provider = _resolve_provider(anthropic_available, openai_available)
    if provider == "none":
        sel.provider = "none"
        sel.model = "none"
        sel.reason = _last_blocked_reason
        return sel

    sel.provider = provider

    # --- Read base model from persistent config ---
    base_model = _get_persistent_model(module, provider)

    # Orchestrator: use configured model (no escalation)
    if module == "orchestrator":
        sel.model = base_model
        sel.reason = "orchestrator_configured"
        sel.max_tokens = 512
        return sel

    # Escalation: configured model failed → try Opus once
    if previous_failed:
        sel.model = OPUS if provider == "anthropic" else SONNET_OPENAI
        sel.reason = "escalation_from_failure"
        sel.escalation = True
        sel.max_tokens = 2048
        log.info("llm_router: escalating to Opus — previous call failed")
        return sel

    # High complexity → Opus
    needs_opus = False
    reasons = []

    if patch_risk_tier is not None and patch_risk_tier >= 1:
        needs_opus = True
        reasons.append(f"tier_{patch_risk_tier}")

    if file_count >= 3:
        needs_opus = True
        reasons.append(f"multi_file_{file_count}")

    if is_complex:
        needs_opus = True
        reasons.append("explicitly_complex")

    if needs_opus:
        sel.model = OPUS if provider == "anthropic" else SONNET_OPENAI
        sel.reason = f"opus_selected: {','.join(reasons)}"
        sel.max_tokens = 2048
        return sel

    # Default: use configured base model
    sel.model = base_model
    sel.reason = "configured_default"
    sel.max_tokens = 2048 if module == "bugfix_proposal" else 512
    return sel


def _resolve_provider(anthropic_available: bool, openai_available: bool) -> str:
    """
    Resolve which provider to use based on explicit policy.

    Rules:
        1. Try preferred provider first
        2. If unavailable and ALLOW_FALLBACK=true → use other provider
        3. If unavailable and ALLOW_FALLBACK=false → return "none"
        4. No multi-hop — exactly one fallback attempt
    """
    global _last_blocked_reason

    providers = {
        "anthropic": anthropic_available,
        "openai": openai_available,
    }

    # Preferred available → use it
    if providers.get(PREFERRED_PROVIDER):
        _last_blocked_reason = ""
        return PREFERRED_PROVIDER

    # Preferred not available
    if not ALLOW_FALLBACK:
        _last_blocked_reason = f"preferred_{PREFERRED_PROVIDER}_unavailable_fallback_disabled"
        log.info("llm_router: %s unavailable, fallback disabled — no LLM call", PREFERRED_PROVIDER)
        return "none"

    # Fallback enabled — try the other provider (exactly one)
    fallback = "openai" if PREFERRED_PROVIDER == "anthropic" else "anthropic"
    if providers.get(fallback):
        _last_blocked_reason = ""
        log.info("llm_router: %s unavailable, falling back to %s", PREFERRED_PROVIDER, fallback)
        return fallback

    # Neither available
    _last_blocked_reason = "no_provider_available"
    return "none"


def _get_persistent_model(module: str, provider: str) -> str:
    """Read active model from persistent DB config. Falls back to constants."""
    try:
        from app.services.model_config import get_active_model
        config = get_active_model(module)
        if config.get("provider") == provider and config.get("model"):
            return config["model"]
    except Exception:
        pass
    # Fallback to constants if DB unavailable or provider mismatch
    return SONNET if provider == "anthropic" else SONNET_OPENAI
