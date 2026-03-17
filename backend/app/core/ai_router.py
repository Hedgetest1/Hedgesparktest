from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AIRouteDecision:
    engine: str
    mode: str
    sandbox_required: bool
    human_approval_required: bool
    reason: str


def route_ai_task(task_type: str, payload: dict[str, Any] | None = None) -> AIRouteDecision:
    payload = payload or {}

    micro_tasks = {
        "intent_classification",
        "event_labeling",
        "signal_extraction",
        "product_tagging",
        "simple_summary",
    }

    insight_tasks = {
        "store_report",
        "opportunity_summary",
        "pricing_suggestion",
        "competitor_analysis",
        "conversion_insight",
        "weekly_brief",
    }

    agent_tasks = {
        "strategy_simulation",
        "code_debugging",
        "architecture_analysis",
        "code_generation",
        "automation_planning",
        "implement_next_step",
    }

    if task_type in micro_tasks:
        return AIRouteDecision(
            engine="micro",
            mode="live",
            sandbox_required=False,
            human_approval_required=False,
            reason="Fast low-cost classification task",
        )

    if task_type in insight_tasks:
        return AIRouteDecision(
            engine="insight",
            mode="live",
            sandbox_required=False,
            human_approval_required=False,
            reason="Structured analytical task for user-facing insights",
        )

    if task_type in agent_tasks:
        return AIRouteDecision(
            engine="agent",
            mode="offline",
            sandbox_required=True,
            human_approval_required=True,
            reason="Complex task requiring controlled execution",
        )

    return AIRouteDecision(
        engine="micro",
        mode="live",
        sandbox_required=False,
        human_approval_required=False,
        reason="Fallback route for unknown task",
    )


def route_to_dict(task_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    decision = route_ai_task(task_type=task_type, payload=payload)
    return {
        "task_type": task_type,
        "engine": decision.engine,
        "mode": decision.mode,
        "sandbox_required": decision.sandbox_required,
        "human_approval_required": decision.human_approval_required,
        "reason": decision.reason,
    }
