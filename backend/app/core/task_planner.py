from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PlannedTask:
    task_type: str
    priority: str
    execution_mode: str
    target_area: str
    sandbox_required: bool
    human_approval_required: bool
    reason: str


def plan_task(goal: str, payload: dict[str, Any] | None = None) -> PlannedTask:
    payload = payload or {}
    normalized_goal = (goal or "").strip().lower()

    if "worker" in normalized_goal:
        return PlannedTask(
            task_type="implement_worker_layer",
            priority="high",
            execution_mode="offline",
            target_area="backend/app/workers",
            sandbox_required=True,
            human_approval_required=True,
            reason="Worker-related changes affect background processing and should be validated safely.",
        )

    if "shopify" in normalized_goal:
        return PlannedTask(
            task_type="implement_shopify_app",
            priority="high",
            execution_mode="offline",
            target_area="shopify-app",
            sandbox_required=True,
            human_approval_required=True,
            reason="Shopify app work affects product architecture and merchant-facing integration.",
        )

    if "dashboard" in normalized_goal or "ui" in normalized_goal:
        return PlannedTask(
            task_type="improve_dashboard",
            priority="medium",
            execution_mode="live",
            target_area="dashboard/src",
            sandbox_required=False,
            human_approval_required=True,
            reason="Dashboard changes are user-facing and should be reviewed before release.",
        )

    if "router" in normalized_goal or "ai" in normalized_goal:
        return PlannedTask(
            task_type="improve_ai_orchestration",
            priority="high",
            execution_mode="offline",
            target_area="backend/app/core",
            sandbox_required=True,
            human_approval_required=True,
            reason="AI orchestration changes affect routing, safety, and cost control.",
        )

    if "bug" in normalized_goal or "debug" in normalized_goal or "fix" in normalized_goal:
        return PlannedTask(
            task_type="debug_system",
            priority="high",
            execution_mode="offline",
            target_area="backend/app",
            sandbox_required=True,
            human_approval_required=True,
            reason="Debugging should happen in controlled mode before touching production behavior.",
        )

    return PlannedTask(
        task_type="analyze_project",
        priority="medium",
        execution_mode="offline",
        target_area="/opt/wishspark",
        sandbox_required=True,
        human_approval_required=False,
        reason="Default planning path for broad or unknown goals.",
    )


def plan_to_dict(goal: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan_task(goal=goal, payload=payload)
    return {
        "goal": goal,
        "task_type": plan.task_type,
        "priority": plan.priority,
        "execution_mode": plan.execution_mode,
        "target_area": plan.target_area,
        "sandbox_required": plan.sandbox_required,
        "human_approval_required": plan.human_approval_required,
        "reason": plan.reason,
    }
