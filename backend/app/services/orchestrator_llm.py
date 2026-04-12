"""
orchestrator_llm.py — LLM-driven decision layer for the orchestrator.

Sends structured system context + action registry to Claude (or OpenAI
fallback) and parses the response into validated action proposals.

This module is READ-ONLY / PROPOSAL MODE — it does NOT execute actions.
The orchestrator decides whether to execute based on ORCHESTRATOR_MODE.

Strict parsing:
    - Only actions in ACTION_REGISTRY are accepted
    - Unknown actions are discarded with a log
    - Response must be valid JSON
    - Max proposals capped

Model preference:
    1. Anthropic Claude (ANTHROPIC_API_KEY)
    2. OpenAI GPT-4o-mini (OPENAI_API_KEY) as fallback
    3. None → returns empty proposals (deterministic mode continues)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("orchestrator.llm")

_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_TIMEOUT = 15.0
_MAX_PROPOSALS = 5

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the autonomous operations agent for HedgeSpark, a Shopify commerce intelligence SaaS.

Your role: read system state, identify problems, and propose safe remediation actions.

RULES:
- Only propose actions from the AVAILABLE ACTIONS list below
- Each proposal must include: action (exact name), target (specific identifier), reason (one sentence)
- Be conservative — only propose actions when there is clear evidence of a problem
- Never propose more than 5 actions
- Never propose the same action+target twice
- If the system looks healthy, return an empty actions list

OUTCOME INTELLIGENCE:
- The "Action Outcomes" section shows recent success rates per action type
- Prefer actions with success_rate >= 60%
- Avoid repeating actions with success_rate < 30% unless conditions have clearly changed
- If an action shows repeated no_effect for the same target, do not propose it again
- When success_rate is unknown (no data), use normal conservative judgment

RESPONSE FORMAT (strict JSON):
{
  "assessment": "One sentence summary of system state",
  "actions": [
    {"action": "action_name", "target": "target_id", "reason": "Why this action"}
  ]
}

If no actions are needed:
{"assessment": "System is healthy, no actions required", "actions": []}
"""


# ---------------------------------------------------------------------------
# Proposal type
# ---------------------------------------------------------------------------

@dataclass
class LLMProposal:
    action: str
    target: str
    reason: str
    valid: bool = True


@dataclass
class LLMDecisionResult:
    assessment: str = ""
    proposals: list[LLMProposal] = None
    raw_response: str = ""
    model_used: str = ""
    error: str | None = None

    def __post_init__(self):
        if self.proposals is None:
            self.proposals = []


# ---------------------------------------------------------------------------
# Main decision function
# ---------------------------------------------------------------------------

def claude_decision(
    context: str,
    action_registry: dict[str, tuple],
) -> LLMDecisionResult:
    """
    Send system context to an LLM and parse proposed actions.

    Returns LLMDecisionResult with validated proposals.
    Returns empty proposals (no error) if no API key is configured.
    """
    if not _ANTHROPIC_KEY and not _OPENAI_KEY:
        return LLMDecisionResult(
            assessment="No LLM API key configured — skipping AI decision layer",
            error="no_api_key",
        )

    # Budget guard
    from app.core.llm_budget import check_budget, record_usage, record_blocked, get_max_tokens
    allowed, reason = check_budget("orchestrator")
    if not allowed:
        record_blocked("orchestrator", reason)
        return LLMDecisionResult(assessment=f"Budget blocked: {reason}", error=f"budget:{reason}")

    # Build the action descriptions for the prompt
    # Registry entries are (function, description, tier) — extract description safely
    def _get_desc(entry):
        return entry[1] if len(entry) >= 2 else "No description"
    action_desc = "\n".join(
        f"  - {name}: {_get_desc(entry)}" for name, entry in action_registry.items()
    )

    user_message = f"""## Current System State

{context}

## Available Actions
{action_desc}

Analyze the system state and propose actions if needed. Return strict JSON."""

    # Runtime PII guard — refuse to send anything that looks like
    # merchant contact info, customer email, JWT, or provider API key.
    try:
        from app.core.llm_pii_guard import assert_clean, LLMPayloadViolation
        assert_clean(user_message, context="orchestrator")
    except LLMPayloadViolation as exc:
        log.error("orchestrator_llm: %s", exc)
        return LLMDecisionResult(
            assessment="LLM call blocked by PII guard",
            error="llm_pii_guard_block",
            model_used="none",
        )
    except Exception:
        pass

    # Route model selection
    from app.core.llm_router import select_model
    sel = select_model(
        module="orchestrator",
        anthropic_available=bool(_ANTHROPIC_KEY),
        openai_available=bool(_OPENAI_KEY),
    )

    raw = ""
    model = sel.model

    if sel.provider == "anthropic" and _ANTHROPIC_KEY:
        raw, model = _call_anthropic(user_message, model=sel.model, max_tokens=sel.max_tokens)
    if not raw and _OPENAI_KEY:
        if sel.provider == "anthropic":
            log.info("orchestrator_llm: anthropic failed, fallback=openai")
        raw, model = _call_openai(user_message, model=sel.model if sel.provider == "openai" else "gpt-4o-mini", max_tokens=sel.max_tokens)

    if raw:
        record_usage("orchestrator", tokens_used=len(raw) // 4, provider=sel.provider, model=model)

    if not raw:
        return LLMDecisionResult(
            assessment="LLM call failed or returned empty",
            error="llm_call_failed",
            model_used=model,
        )

    # Parse and validate
    return _parse_response(raw, action_registry, model)


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def _call_anthropic(user_message: str, model: str = "claude-sonnet-4-20250514", max_tokens: int = 512) -> tuple[str, str]:
    """Call Anthropic Claude API. Handles 429 with backoff. Returns (response_text, model_name)."""
    from app.core.llm_budget import is_provider_backed_off, record_429

    if is_provider_backed_off("anthropic"):
        log.info("orchestrator_llm: Anthropic backed off (429 cooldown)")
        return "", model

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": _ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            return text, model
        if resp.status_code == 429:
            record_429("anthropic")
            return "", model
        log.warning("orchestrator_llm: Anthropic returned %d", resp.status_code)
        return "", model
    except Exception as exc:
        log.warning("orchestrator_llm: Anthropic call failed: %s", type(exc).__name__)
        return "", model


def _call_openai(user_message: str, model: str = "gpt-4o-mini", max_tokens: int = 512) -> tuple[str, str]:
    """Call OpenAI API as fallback. Handles 429 with backoff. Returns (response_text, model_name)."""
    from app.core.llm_budget import is_provider_backed_off, record_429

    if is_provider_backed_off("openai"):
        log.info("orchestrator_llm: OpenAI backed off (429 cooldown)")
        return "", model

    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_OPENAI_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return text, model
        if resp.status_code == 429:
            record_429("openai")
            return "", model
        log.warning("orchestrator_llm: OpenAI returned %d", resp.status_code)
        return "", model
    except Exception as exc:
        log.warning("orchestrator_llm: OpenAI call failed: %s", type(exc).__name__)
        return "", model


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(
    raw: str,
    action_registry: dict[str, tuple],
    model: str,
) -> LLMDecisionResult:
    """
    Parse LLM JSON response into validated proposals.

    Strictly validates:
        - Response is valid JSON
        - Each action exists in ACTION_REGISTRY
        - Target is a non-empty string
        - No duplicates
        - Max _MAX_PROPOSALS
    """
    result = LLMDecisionResult(raw_response=raw[:2000], model_used=model)

    try:
        # Extract JSON from potential markdown code blocks
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        result.error = f"json_parse_error: {exc}"
        log.warning("orchestrator_llm: failed to parse JSON: %s", exc)
        return result

    result.assessment = data.get("assessment", "")
    raw_actions = data.get("actions", [])

    if not isinstance(raw_actions, list):
        result.error = "actions_not_a_list"
        return result

    seen: set[str] = set()
    for item in raw_actions[:_MAX_PROPOSALS]:
        if not isinstance(item, dict):
            continue

        action = str(item.get("action", "")).strip()
        target = str(item.get("target", "")).strip()
        reason = str(item.get("reason", "")).strip()

        # Validate action exists in registry
        if action not in action_registry:
            log.info("orchestrator_llm: rejected unknown action=%s", action)
            result.proposals.append(LLMProposal(
                action=action, target=target, reason=reason, valid=False,
            ))
            continue

        # Validate target is non-empty
        if not target:
            log.info("orchestrator_llm: rejected empty target for action=%s", action)
            result.proposals.append(LLMProposal(
                action=action, target=target, reason=reason, valid=False,
            ))
            continue

        # Dedup
        key = f"{action}::{target}"
        if key in seen:
            continue
        seen.add(key)

        result.proposals.append(LLMProposal(
            action=action, target=target, reason=reason, valid=True,
        ))

    valid_count = sum(1 for p in result.proposals if p.valid)
    invalid_count = sum(1 for p in result.proposals if not p.valid)
    log.info(
        "orchestrator_llm: model=%s assessment=%s proposals=%d valid=%d invalid=%d",
        model, result.assessment[:80], len(result.proposals), valid_count, invalid_count,
    )

    return result
