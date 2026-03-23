"""
action_tasks.py — Action Execution API

POST /actions/execute
    Create an action task from a candidate produced by /actions/candidates/pro.
    If an active task already exists for the same (shop, product, action_type),
    the existing task is returned with created=false.

GET /actions/tasks
    List action tasks for the shop.  Filters are optional and composable:
      ?status=<pending|executing|done|failed|dismissed>
      ?claimed_by=<agent_id>
      ?limit=<int>   default 50, max 50

PATCH /actions/tasks/{task_id}
    Transition a task to a new status.

    Claiming (pending → executing):
      Requires claimed_by in the body.  Uses SELECT FOR UPDATE internally —
      safe for concurrent agents.  Returns 409 if the task is no longer
      pending or was already claimed by another agent.

    All other transitions (executing → done|failed, pending → dismissed):
      claimed_by body field is ignored.  Invalid transitions return 422.

    Cross-tenant access returns 404 in all cases.

POST /actions/tasks/{task_id}/release
    Release a stuck executing task back to pending.

    For use by operators or watchdog scripts when an agent has crashed or
    disappeared after claiming a task.  Accepts an optional reason string.
    Returns 409 if the task is not currently executing.
    Returns 404 if the task does not belong to the authenticated shop.

GET /actions/tasks/{task_id}
    Single task detail.  Returns 404 if the task does not belong to the shop.

All routes are Pro-only (require_pro_plan enforces plan + API key + shop domain).

Design notes
------------
The execute endpoint accepts the full candidate dict in the request body under
the key "candidate".  This keeps the contract stable — callers pass exactly
what /actions/candidates/pro returned, no field picking required.

The shop_domain is taken from the Pro plan dependency, not from the request
body, to prevent cross-tenant task creation or transition.

task_payload is included in all responses so clients can render the checklist
and suggested_fixes without a second round-trip.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_plan
from app.services.action_executor import (
    STATUS_EXECUTING,
    claim_task,
    create_task,
    get_task,
    list_tasks,
    release_task,
    transition_task,
)

router = APIRouter(prefix="/actions", tags=["actions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    candidate: dict
    triggered_by: str = "manual"


class TransitionRequest(BaseModel):
    status: str
    claimed_by: Optional[str] = None
    result_detail: Optional[str] = None


class ReleaseRequest(BaseModel):
    reason: Optional[str] = None


def _task_to_dict(task) -> dict:
    return {
        "id":               task.id,
        "shop_domain":      task.shop_domain,
        "product_url":      task.product_url,
        "action_type":      task.action_type,
        "status":           task.status,
        "triggered_by":     task.triggered_by,
        "claimed_by":       task.claimed_by,
        "source_candidate": task.source_candidate,
        "task_payload":     task.task_payload,
        "expected_loss":    task.expected_loss,
        "confidence":       task.confidence,
        "urgency":          task.urgency,
        "created_at":       task.created_at.isoformat() if task.created_at else None,
        "updated_at":       task.updated_at.isoformat() if task.updated_at else None,
        "executed_at":      task.executed_at.isoformat() if task.executed_at else None,
        "completed_at":     task.completed_at.isoformat() if task.completed_at else None,
        "result_detail":    task.result_detail,
    }


# ---------------------------------------------------------------------------
# POST /actions/execute
# ---------------------------------------------------------------------------

@router.post("/execute")
def execute_action(
    body: ExecuteRequest,
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Convert a candidate into an action task.

    Accepts the full candidate dict from /actions/candidates/pro.
    The shop_domain is derived from the Pro plan auth dependency — callers
    cannot create tasks for other shops.

    Returns the created (or existing active) task.  The `created` field
    indicates whether a new task was written (true) or a deduplication
    hit occurred (false).
    """
    candidate = body.candidate

    for field in ("action_type", "product_url"):
        if field not in candidate:
            raise HTTPException(
                status_code=422,
                detail=f"candidate is missing required field: {field!r}",
            )

    try:
        task, created = create_task(
            db=db,
            shop_domain=shop,
            candidate=candidate,
            triggered_by=body.triggered_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "created": created,
        "task":    _task_to_dict(task),
    }


# ---------------------------------------------------------------------------
# GET /actions/tasks
# ---------------------------------------------------------------------------

@router.get("/tasks")
def list_action_tasks(
    status: Optional[str] = None,
    claimed_by: Optional[str] = None,
    limit: int = 50,
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    List action tasks for the shop, newest first.

    Optional query params:
      ?status=pending|executing|done|failed|dismissed
      ?claimed_by=<agent_id>
      ?limit=<int>   default 50, max 50

    Filters are composable — both may be provided simultaneously (AND).
    Omitting a filter leaves that dimension unrestricted.
    """
    limit = min(limit, 50)
    tasks = list_tasks(
        db=db,
        shop_domain=shop,
        status=status,
        claimed_by=claimed_by,
        limit=limit,
    )
    return {
        "shop_domain": shop,
        "total":       len(tasks),
        "tasks":       [_task_to_dict(t) for t in tasks],
    }


# ---------------------------------------------------------------------------
# PATCH /actions/tasks/{task_id}
# ---------------------------------------------------------------------------

@router.patch("/tasks/{task_id}")
def transition_action_task(
    task_id: int,
    body: TransitionRequest,
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Apply a status transition to an action task.

    Claiming (status=executing):
      claimed_by is required.  The claim is atomic — safe for concurrent agents.
      Returns 409 if the task was already claimed or is no longer pending.
      Returns 404 if the task does not belong to the authenticated shop.

    Other transitions:
      executing → done        result_detail required (structured JSON)
      executing → failed      result_detail required (structured JSON)
      pending   → dismissed   result_detail optional (free text)

      Returns 422 for invalid transitions or malformed result_detail.
      Returns 404 for cross-tenant access.
    """
    if body.status == STATUS_EXECUTING:
        if not body.claimed_by:
            raise HTTPException(
                status_code=422,
                detail="claimed_by is required when claiming a task (status=executing).",
            )

        task, conflict = claim_task(
            db=db,
            task_id=task_id,
            shop_domain=shop,
            claimed_by=body.claimed_by,
        )

        if conflict == "not_found":
            raise HTTPException(status_code=404, detail="Task not found.")
        if conflict == "conflict":
            raise HTTPException(
                status_code=409,
                detail="Claim failed: task is no longer pending or has already been claimed.",
            )

        return _task_to_dict(task)

    task = get_task(db=db, task_id=task_id, shop_domain=shop)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    try:
        task = transition_task(
            db=db,
            task=task,
            new_status=body.status,
            result_detail=body.result_detail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return _task_to_dict(task)


# ---------------------------------------------------------------------------
# POST /actions/tasks/{task_id}/release
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/release")
def release_action_task(
    task_id: int,
    body: ReleaseRequest,
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Release a stuck executing task back to pending.

    For use by operators or watchdog scripts when an agent has crashed or
    disappeared after claiming a task.  The release is atomic — safe to call
    concurrently or from a background sweep.

    On success:
      - status is reset to pending
      - claimed_by and executed_at are cleared
      - a release note is appended to result_detail preserving the
        previous agent identity and the supplied reason

    Returns 404 if the task does not belong to the authenticated shop.
    Returns 409 if the task is not currently executing.
    """
    task, conflict = release_task(
        db=db,
        task_id=task_id,
        shop_domain=shop,
        reason=body.reason,
    )

    if conflict == "not_found":
        raise HTTPException(status_code=404, detail="Task not found.")
    if conflict == "conflict":
        raise HTTPException(
            status_code=409,
            detail="Release failed: task is not currently executing.",
        )

    return _task_to_dict(task)


# ---------------------------------------------------------------------------
# GET /actions/tasks/{task_id}
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}")
def get_action_task(
    task_id: int,
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Return a single action task by ID.

    Scoped to the authenticated shop — returns 404 if the task belongs
    to a different shop (same as not found, prevents enumeration).
    """
    task = get_task(db=db, task_id=task_id, shop_domain=shop)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return _task_to_dict(task)
