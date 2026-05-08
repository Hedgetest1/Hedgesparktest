from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import sessionmaker

from app.core.ai_router import route_to_dict
from app.core.task_planner import plan_to_dict
from app.core.database import engine
from app.core.deps import require_operator
from app.sandbox.sandbox_executor import (
    create_sandbox_run,
    update_sandbox_status,
    inspect_sandbox_run,
)
from app.api.dashboard import (
    _build_summary,
    _build_top_products,
    _build_price_intelligence,
    _build_market_lookup,
    _build_ai_recommended_actions,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# /agent/* is operator-only diagnostic surface (project-context, daily-brief
# across all merchants, sandbox runner). Pre-2026-05-08 this router shipped
# UNGATED — every endpoint readable by anyone hitting api.hedgesparkhq.com.
# Daily-brief leaks cross-merchant summary + top products + price intel,
# project-context discloses internal architecture files, sandbox endpoints
# accept arbitrary payload. The router-level dependency gates EVERY route
# (current + future) behind X-API-Key, fail-closed when DASHBOARD_API_KEY
# unset (returns 503 from require_operator).
router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    dependencies=[Depends(require_operator)],
)

BASE_PATH = Path("/opt/wishspark")
DOCS_PATH = BASE_PATH / "docs"
BACKEND_APP_PATH = BASE_PATH / "backend" / "app"
SANDBOX_PATH = BASE_PATH / "sandbox"


def _read_text_file(path: Path) -> str:
    if not path.exists():
        return f"[missing] {path}"
    return path.read_text(encoding="utf-8")


def _list_python_modules(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(
        str(p.relative_to(BASE_PATH))
        for p in path.rglob("*.py")
        if p.is_file()
    )


def _top_action(actions):
    if not actions:
        return None
    return sorted(actions, key=lambda x: x.get("confidence", 0), reverse=True)[0]


def _top_price(prices):
    if not prices:
        return None
    return sorted(prices, key=lambda x: x.get("confidence_score", 0), reverse=True)[0]


def _list_sandbox_runs() -> list[dict[str, Any]]:
    if not SANDBOX_PATH.exists():
        return []

    runs = []
    for path in sorted(SANDBOX_PATH.iterdir(), reverse=True):
        if not path.is_dir():
            continue

        status = "unknown"
        status_file = path / "status.txt"
        if status_file.exists():
            status = status_file.read_text(encoding="utf-8").strip()

        runs.append(
            {
                "run_id": path.name,
                "status": status,
                "sandbox_path": str(path),
            }
        )

    return runs[:20]


@router.get("/daily-brief")
def daily_brief():
    db = SessionLocal()
    try:
        summary = _build_summary(db)
        products = _build_top_products(db)
        prices = _build_price_intelligence(db)
        market = _build_market_lookup(db)

        ai_actions = _build_ai_recommended_actions(
            top_products=products,
            price_intelligence=prices,
            market_lookup=market,
        )

        top_action = _top_action(ai_actions)
        top_price = _top_price(prices)

        priorities = []
        ai_routes = []
        ai_plans = []

        if top_action:
            priorities.append(
                {
                    "type": "ACTION",
                    "product": top_action.get("product_name"),
                    "action": top_action.get("recommended_action"),
                    "confidence": top_action.get("confidence"),
                }
            )
            ai_routes.append(
                route_to_dict(
                    task_type="opportunity_summary",
                    payload=top_action,
                )
            )
            ai_plans.append(
                plan_to_dict(
                    goal="analyze product opportunity",
                    payload=top_action,
                )
            )

        if top_price:
            priorities.append(
                {
                    "type": "PRICE",
                    "product": top_price.get("product_name"),
                    "action": top_price.get("recommended_price_action"),
                    "confidence": top_price.get("confidence_score"),
                }
            )
            ai_routes.append(
                route_to_dict(
                    task_type="pricing_suggestion",
                    payload=top_price,
                )
            )
            ai_plans.append(
                plan_to_dict(
                    goal="analyze pricing strategy",
                    payload=top_price,
                )
            )

        if products:
            best = sorted(
                products, key=lambda x: x.get("avg_intent_score", 0), reverse=True
            )[0]
            priorities.append(
                {
                    "type": "PRODUCT",
                    "product": best.get("product_name"),
                    "action": "FOCUS_THIS_PRODUCT",
                    "confidence": best.get("avg_intent_score"),
                }
            )
            ai_routes.append(
                route_to_dict(
                    task_type="conversion_insight",
                    payload=best,
                )
            )
            ai_plans.append(
                plan_to_dict(
                    goal="analyze conversion opportunity",
                    payload=best,
                )
            )

        priorities = priorities[:3]

        return {
            "agent_name": "WishSpark Agent",
            "summary": summary,
            "priorities": priorities,
            "ai_routing": ai_routes,
            "ai_plans": ai_plans,
            "top_action": top_action,
            "top_price_signal": top_price,
        }
    finally:
        db.close()


@router.get("/scan-project")
def scan_project() -> dict[str, Any]:
    return {
        "project": "WishSpark",
        "root": str(BASE_PATH),
        "context_sources": {
            "agent_rules": "AGENTS.md",
            "current_state": "docs/CURRENT_STATE.md",
            "next_steps": "docs/NEXT_STEPS.md",
            "server_context": "SERVER_CONTEXT.md",
            "auto_context": "docs/AUTO_CONTEXT.md",
        },
        "backend_api_modules": _list_python_modules(BACKEND_APP_PATH / "api"),
        "backend_service_modules": _list_python_modules(BACKEND_APP_PATH / "services"),
        "backend_core_modules": _list_python_modules(BACKEND_APP_PATH / "core"),
    }


@router.get("/project-context")
def project_context() -> dict[str, str]:
    return {
        "agent_rules": _read_text_file(BASE_PATH / "AGENTS.md"),
        "current_state": _read_text_file(DOCS_PATH / "CURRENT_STATE.md"),
        "next_steps": _read_text_file(DOCS_PATH / "NEXT_STEPS.md"),
        "server_context": _read_text_file(BASE_PATH / "SERVER_CONTEXT.md"),
        "auto_context": _read_text_file(DOCS_PATH / "AUTO_CONTEXT.md"),
    }


@router.get("/analyze-backend")
def analyze_backend() -> dict[str, Any]:
    api_modules = _list_python_modules(BACKEND_APP_PATH / "api")
    service_modules = _list_python_modules(BACKEND_APP_PATH / "services")
    core_modules = _list_python_modules(BACKEND_APP_PATH / "core")
    model_modules = _list_python_modules(BACKEND_APP_PATH / "models")

    return {
        "project": "WishSpark",
        "backend_entrypoint": "backend/app/main.py",
        "counts": {
            "api_modules": len(api_modules),
            "service_modules": len(service_modules),
            "core_modules": len(core_modules),
            "model_modules": len(model_modules),
        },
        "api_modules": api_modules,
        "service_modules": service_modules,
        "core_modules": core_modules,
        "model_modules": model_modules,
        "recommended_next_focus": "Connect dashboard to sandbox and agent outputs",
    }


@router.get("/implement-next-step")
def implement_next_step() -> dict[str, Any]:
    next_steps = _read_text_file(DOCS_PATH / "NEXT_STEPS.md")
    return {
        "status": "planning_only",
        "message": "This endpoint does not modify code yet. It returns the recommended next implementation target.",
        "recommended_task": "Connect dashboard to sandbox and agent outputs",
        "source": "docs/NEXT_STEPS.md",
        "next_steps": next_steps,
    }


@router.post("/sandbox/create")
def sandbox_create(payload: dict[str, Any]) -> dict[str, Any]:
    goal = payload.get("goal", "generic_task")
    return create_sandbox_run(goal=goal, payload=payload)


@router.post("/sandbox/{run_id}/status")
def sandbox_set_status(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status", "updated")
    return update_sandbox_status(run_id=run_id, status=status)


@router.get("/sandbox/{run_id}")
def sandbox_inspect(run_id: str) -> dict[str, Any]:
    return inspect_sandbox_run(run_id=run_id)


@router.get("/sandbox-runs")
def sandbox_runs() -> dict[str, Any]:
    runs = _list_sandbox_runs()
    return {
        "count": len(runs),
        "runs": runs,
    }
