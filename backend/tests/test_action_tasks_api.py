"""
Regression tests for /actions/tasks API — Sentry incident #38 (2026-04-11).

Root cause of the incident: ActionTaskRow declared `result_detail: dict | None`
but the underlying DB column is `Text` and contains free-form strings (not JSON).
Pydantic raised ResponseValidationError on any response containing a task with
a non-null result_detail, breaking the endpoint for Pro merchants.

These tests lock in the correct shape so a future refactor cannot silently
re-introduce the regression.
"""
from __future__ import annotations

from app.api.action_tasks import ActionTaskRow, ActionTasksListResponse


def test_action_task_row_accepts_string_result_detail():
    """result_detail must accept plain strings — matches the DB reality."""
    row = ActionTaskRow(
        id=1,
        shop_domain="test.myshopify.com",
        action_type="cta_audit",
        status="done",
        result_detail="CRO audit complete — CTA moved above fold.",
    )
    assert row.result_detail == "CRO audit complete — CTA moved above fold."


def test_action_task_row_accepts_null_result_detail():
    row = ActionTaskRow(
        id=1,
        shop_domain="test.myshopify.com",
        action_type="cta_audit",
        status="pending",
        result_detail=None,
    )
    assert row.result_detail is None


def test_action_task_row_rejects_dict_result_detail():
    """Guard: result_detail should NOT accept dicts anymore. This prevents
    a future refactor from reverting the type back to dict|None (which was
    the bug) without updating the DB schema."""
    import pydantic
    try:
        ActionTaskRow(
            id=1,
            shop_domain="test.myshopify.com",
            action_type="cta_audit",
            status="done",
            result_detail={"not": "supported"},  # type: ignore[arg-type]
        )
        # If we get here the type is too permissive — flag it
        raise AssertionError("ActionTaskRow should not silently accept a dict for result_detail")
    except pydantic.ValidationError:
        pass  # expected — str|None rejects dict


def test_action_tasks_list_response_round_trips_real_shape(client, auth_a, db, merchant_a):
    """End-to-end regression: hit the endpoint with a task whose result_detail
    is a plain string, verify it serializes without ResponseValidationError."""
    from app.models.action_task import ActionTask
    from datetime import datetime, timezone

    t = ActionTask(
        shop_domain=merchant_a.shop_domain,
        product_url="/products/test",
        action_type="cta_audit",
        status="done",
        triggered_by="manual",
        claimed_by="agent_1",
        source_candidate={"score": 0.8},
        task_payload={"target": "hero_cta"},
        expected_loss=120.0,
        confidence=0.92,
        urgency=0.85,  # Float per ORM schema — not a string
        result_detail="CRO audit complete — CTA moved above fold, sticky bar added.",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(t)
    db.flush()
    db.commit()

    resp = client.get("/actions/tasks", cookies=auth_a)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert "tasks" in body
    assert isinstance(body["total"], int)
    # Find the task we created and check its shape
    ours = [row for row in body["tasks"] if row["id"] == t.id]
    assert len(ours) == 1
    assert isinstance(ours[0]["result_detail"], str)
    assert "CRO audit complete" in ours[0]["result_detail"]
