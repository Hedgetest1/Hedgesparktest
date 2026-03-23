"""
action_executor.py — Converts action candidates into executable task records
and manages task lifecycle transitions.

Responsibilities
----------------
1. Build a structured task_payload for each action_type.
   Currently implemented: CRO_FIX.
   Stub structure exists for others so agents can extend without breaking callers.

2. Enforce the one-active-task rule:
   If a pending or executing task already exists for
   (shop_domain, product_url, action_type), return it unchanged instead of
   creating a duplicate.  Terminal states (done, failed, dismissed) are ignored
   — a resolved task can always be re-triggered.

3. Persist the ActionTask row and return it.

4. Manage lifecycle transitions via claim_task(), transition_task(),
   and release_task().

Transition graph
----------------
  pending   → executing   ONLY via claim_task() — atomic SELECT FOR UPDATE
  executing → done        sets completed_at = now()
  executing → failed      sets completed_at = now()
  pending   → dismissed   sets completed_at = now()
  executing → pending     ONLY via release_task() — stale-task recovery

  pending → executing is intentionally absent from _TRANSITIONS.
  executing → pending is intentionally absent from _TRANSITIONS.
  Both are recovery / claim operations with dedicated functions.

  All other (from, to) pairs in transition_task() are rejected.
  updated_at is always set server-side on every write.
  Client timestamps are never trusted.

Result contract validation
--------------------------
For executing → done and executing → failed, result_detail is required and
must be a JSON object with at minimum:

  {
    "outcome":  "PASS" | "PARTIAL" | "FAIL"  (done)
                "ERROR"                       (failed)
    "agent_id": "<string>",
    "summary":  "<string>"
  }

outcome=ERROR is only valid for status=failed.
outcome=PASS|PARTIAL|FAIL is only valid for status=done.
Mismatches are rejected with 422 before any write occurs.

dismissed tasks: no JSON validation — free text is allowed.

Atomic claim design
-------------------
claim_task() issues a SELECT ... FOR UPDATE row lock before inspecting status
and claimed_by.  Under concurrent load:
  - Agent A acquires the lock, sees status=pending, claimed_by=NULL → succeeds.
  - Agent B acquires the lock after A commits, sees status=executing → gets
    conflict="already_claimed" → caller returns 409.
No agent ever sees a false success on a task it does not actually hold.

Stale-task recovery
-------------------
release_task() reverses a claim atomically.  It acquires a SELECT FOR UPDATE
lock, verifies status=executing, then resets the task to pending:
  - status       → pending
  - claimed_by   → NULL   (cleared so next claim records fresh identity)
  - executed_at  → NULL   (cleared so next claim records fresh timestamp)
  - updated_at   → now()
  - result_detail → appended with a structured release note

The release note preserves the previous claimed_by so the stale agent is
identifiable in the audit trail even after the field is cleared.

claimed_by filtering
--------------------
list_tasks() accepts an optional claimed_by filter.  When provided, only tasks
where claimed_by matches exactly are returned.  status and claimed_by filters
are independent and composable (AND semantics).

CRO_FIX payload design
-----------------------
The checklist covers the 7 universal page audit items that apply to every
CRO failure, regardless of which signal fired.

suggested_fixes are ranked by the signal that triggered the candidate:
  HIGH_TRAFFIC_NO_CART   → CTA prominence and friction removal.
  DEAD_TRAFFIC           → First impression and page speed.
  LOW_CONVERSION_ATTENTION → Trust and price presentation.

If multiple signals are present, the highest-priority one governs the fix set.

automation_hint
  "SHOPIFY_THEME_AUDIT"   — inspect theme settings (no writes)
  "SHOPIFY_METAFIELD_SET" — write a merchant-visible recommendation flag
  auto_executable = False means a human must review before the agent acts.

Public interface
----------------
  create_task(db, shop_domain, candidate, triggered_by)         -> (ActionTask, bool)
  claim_task(db, task_id, shop_domain, claimed_by)              -> (ActionTask | None, str | None)
  release_task(db, task_id, shop_domain, reason)                -> (ActionTask | None, str | None)
  transition_task(db, task, new_status, result_detail)          -> ActionTask
  get_task(db, task_id, shop_domain)                            -> ActionTask | None
  list_tasks(db, shop_domain, status, claimed_by, limit)        -> list[ActionTask]
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.action_task import ActionTask

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
STATUS_PENDING    = "pending"
STATUS_EXECUTING  = "executing"
STATUS_DONE       = "done"
STATUS_FAILED     = "failed"
STATUS_DISMISSED  = "dismissed"

_ACTIVE_STATUSES = (STATUS_PENDING, STATUS_EXECUTING)

# ---------------------------------------------------------------------------
# Transition graph
#
# pending → executing and executing → pending are deliberately absent.
# They are handled atomically by claim_task() and release_task() respectively.
#
# Maps (from_status, to_status) → which server-set timestamp column to write.
# ---------------------------------------------------------------------------
_TRANSITIONS: dict[tuple[str, str], str] = {
    (STATUS_EXECUTING, STATUS_DONE):      "completed_at",
    (STATUS_EXECUTING, STATUS_FAILED):    "completed_at",
    (STATUS_PENDING,   STATUS_DISMISSED): "completed_at",
}

# What moves are legal from each status — used to build rejection messages.
_ALLOWED_FROM: dict[str, list[str]] = {}
for (frm, to) in _TRANSITIONS:
    _ALLOWED_FROM.setdefault(frm, []).append(to)

# ---------------------------------------------------------------------------
# Result contract validation
# ---------------------------------------------------------------------------

_OUTCOMES_FOR_DONE   = {"PASS", "PARTIAL", "FAIL"}
_OUTCOMES_FOR_FAILED = {"ERROR"}
_VALIDATED_STATUSES  = (STATUS_DONE, STATUS_FAILED)


def _validate_result_detail(result_detail: Optional[str], new_status: str) -> None:
    """
    Validate the result_detail JSON envelope for agent-written task closures.

    Called by transition_task() for executing → done and executing → failed.
    No-op for all other transitions.

    Rules
    -----
    - result_detail is required for done and failed.
    - Must be a valid JSON string representing an object.
    - Must contain: outcome (str), agent_id (str), summary (str).
    - For status=done:   outcome must be PASS, PARTIAL, or FAIL.
    - For status=failed: outcome must be ERROR.

    Raises ValueError with a precise message on any violation.
    The caller converts ValueError to HTTP 422.
    """
    if new_status not in _VALIDATED_STATUSES:
        return

    if not result_detail:
        raise ValueError(
            f"result_detail is required when closing a task with status={new_status!r}. "
            f"Provide a JSON object with at minimum: outcome, agent_id, summary."
        )

    try:
        data = json.loads(result_detail)
    except (json.JSONDecodeError, TypeError):
        raise ValueError(
            "result_detail must be a valid JSON string. "
            "Provide a JSON object with at minimum: outcome, agent_id, summary."
        )

    if not isinstance(data, dict):
        raise ValueError(
            "result_detail must be a JSON object, not an array or scalar."
        )

    for field in ("outcome", "agent_id", "summary"):
        if field not in data:
            raise ValueError(
                f"result_detail is missing required field: {field!r}."
            )
        if not isinstance(data[field], str):
            raise ValueError(
                f"result_detail.{field!r} must be a string."
            )

    outcome = data["outcome"]

    if new_status == STATUS_DONE:
        if outcome not in _OUTCOMES_FOR_DONE:
            raise ValueError(
                f"result_detail.outcome={outcome!r} is not valid for status=done. "
                f"Allowed: {sorted(_OUTCOMES_FOR_DONE)}. "
                f"Use status=failed for ERROR outcomes."
            )

    elif new_status == STATUS_FAILED:
        if outcome not in _OUTCOMES_FOR_FAILED:
            raise ValueError(
                f"result_detail.outcome={outcome!r} is not valid for status=failed. "
                f"Must be 'ERROR'."
            )


# ---------------------------------------------------------------------------
# CRO_FIX checklist — universal for all CRO failures
# ---------------------------------------------------------------------------

_CRO_CHECKLIST = [
    {
        "id": "page_speed",
        "label": "Page load speed",
        "description": (
            "Test load time on mobile (target < 3 s). Use Shopify speed report "
            "or PageSpeed Insights. Heavy images and unoptimised apps are the "
            "most common culprits."
        ),
    },
    {
        "id": "above_fold_content",
        "label": "Above-the-fold first impression",
        "description": (
            "On mobile, the product image and title must be visible without "
            "scrolling. Check that the hero image loads and is not cropped or "
            "hidden behind a banner."
        ),
    },
    {
        "id": "primary_cta",
        "label": "Add to Cart / Buy Now prominence",
        "description": (
            "The primary action button must be above the fold on desktop and "
            "within one scroll on mobile. It must have high contrast and a "
            "clear, action-oriented label."
        ),
    },
    {
        "id": "product_description",
        "label": "Product description clarity",
        "description": (
            "Lead with benefit, not specification. The first two sentences must "
            "answer: what is it, and why should I care. Walls of text kill "
            "intent; use short paragraphs or bullet points."
        ),
    },
    {
        "id": "social_proof",
        "label": "Social proof visibility",
        "description": (
            "Star rating and review count should appear near the title, not "
            "buried below the fold. If you have zero reviews, add a trust badge "
            "(free returns, secure checkout) as a substitute."
        ),
    },
    {
        "id": "price_presentation",
        "label": "Price framing and anchoring",
        "description": (
            "Is the price presented clearly alongside a compare-at price or "
            "savings callout? Missing price anchoring forces visitors to "
            "question value rather than confirm it."
        ),
    },
    {
        "id": "mobile_layout",
        "label": "Mobile layout integrity",
        "description": (
            "Test on a real mobile device. Check that images resize correctly, "
            "text is readable without pinching, and the Add to Cart button is "
            "not covered by sticky navigation or cookie banners."
        ),
    },
]


# ---------------------------------------------------------------------------
# Signal-specific suggested fixes
# ---------------------------------------------------------------------------

_FIXES_HIGH_TRAFFIC_NO_CART = [
    {
        "priority": 1,
        "fix": "Move the Add to Cart button above the product description",
        "impact": "HIGH",
        "signal": "HIGH_TRAFFIC_NO_CART",
        "rationale": (
            "Visitors are landing but not initiating purchase intent. "
            "The most direct intervention is reducing scroll distance to the CTA."
        ),
    },
    {
        "priority": 2,
        "fix": "Add a sticky Add to Cart bar that follows the user as they scroll",
        "impact": "HIGH",
        "signal": "HIGH_TRAFFIC_NO_CART",
        "rationale": (
            "Eliminates the need to scroll back up to buy. "
            "Shopify themes typically support this as a native option."
        ),
    },
    {
        "priority": 3,
        "fix": "Add social proof near the CTA (review count, star rating, or sold count)",
        "impact": "MEDIUM",
        "signal": "HIGH_TRAFFIC_NO_CART",
        "rationale": (
            "Reduces hesitation at the decision point without requiring any "
            "price change."
        ),
    },
    {
        "priority": 4,
        "fix": "Remove or collapse elements that push the CTA below the fold",
        "impact": "MEDIUM",
        "signal": "HIGH_TRAFFIC_NO_CART",
        "rationale": "Apps, banners, and app widgets are common CTA burial causes.",
    },
]

_FIXES_DEAD_TRAFFIC = [
    {
        "priority": 1,
        "fix": "Audit and compress all product images (target < 200 KB per image)",
        "impact": "HIGH",
        "signal": "DEAD_TRAFFIC",
        "rationale": (
            "Visitors are bouncing before engaging — slow load is the leading cause. "
            "Use WebP format and lazy loading."
        ),
    },
    {
        "priority": 2,
        "fix": "Replace the hero image with a high-contrast lifestyle shot on white background",
        "impact": "MEDIUM",
        "signal": "DEAD_TRAFFIC",
        "rationale": (
            "The first image is the brand's handshake. Unclear or low-quality "
            "images signal low product quality and trigger immediate back-navigation."
        ),
    },
    {
        "priority": 3,
        "fix": "Remove or defer third-party scripts (chat widgets, analytics, pop-ups) from initial load",
        "impact": "HIGH",
        "signal": "DEAD_TRAFFIC",
        "rationale": (
            "Third-party scripts are the primary non-image load time contributor "
            "on Shopify stores."
        ),
    },
    {
        "priority": 4,
        "fix": "Add a concise, benefit-first product headline above the fold",
        "impact": "MEDIUM",
        "signal": "DEAD_TRAFFIC",
        "rationale": (
            "Visitors who do load the page need an immediate value hook. "
            "One sentence answering 'what problem does this solve' is enough."
        ),
    },
]

_FIXES_LOW_CONVERSION_ATTENTION = [
    {
        "priority": 1,
        "fix": "Add a compare-at price (original price crossed out) to anchor perceived value",
        "impact": "HIGH",
        "signal": "LOW_CONVERSION_ATTENTION",
        "rationale": (
            "Visitors are engaging but not converting — price friction is the "
            "leading cause when dwell is present. Anchoring makes the current "
            "price feel like a deal without changing it."
        ),
    },
    {
        "priority": 2,
        "fix": "Add a trust block: secure checkout badge, free returns policy, money-back guarantee",
        "impact": "HIGH",
        "signal": "LOW_CONVERSION_ATTENTION",
        "rationale": (
            "Engaged visitors who don't convert are often blocked by risk "
            "perception. Explicit risk removal (free returns, guarantee) "
            "directly lowers this barrier."
        ),
    },
    {
        "priority": 3,
        "fix": "Add urgency signals: low stock count, limited-time offer, or recent purchase notification",
        "impact": "MEDIUM",
        "signal": "LOW_CONVERSION_ATTENTION",
        "rationale": (
            "For visitors with demonstrated intent (attention), urgency converts "
            "passive interest into active decision-making."
        ),
    },
    {
        "priority": 4,
        "fix": "Rewrite product description to lead with a specific outcome, not a feature list",
        "impact": "MEDIUM",
        "signal": "LOW_CONVERSION_ATTENTION",
        "rationale": (
            "Conversion-stage visitors need emotional confirmation that they are "
            "making the right choice — outcome-focused copy provides this."
        ),
    },
]

_FIXES_GENERIC = [
    {
        "priority": 1,
        "fix": "Audit the full product page against the checklist items above",
        "impact": "MEDIUM",
        "signal": "GENERIC",
        "rationale": "Multiple signals indicate a CRO failure — start with the checklist.",
    },
]

_SIGNAL_FIX_MAP = [
    ("DEAD_TRAFFIC",             _FIXES_DEAD_TRAFFIC),
    ("HIGH_TRAFFIC_NO_CART",     _FIXES_HIGH_TRAFFIC_NO_CART),
    ("LOW_CONVERSION_ATTENTION", _FIXES_LOW_CONVERSION_ATTENTION),
]


def _cro_fix_suggested_fixes(supporting_signals: list[str]) -> list[dict]:
    for signal, fixes in _SIGNAL_FIX_MAP:
        if signal in supporting_signals:
            return fixes
    return _FIXES_GENERIC


# ---------------------------------------------------------------------------
# Payload builders — one per action_type
# ---------------------------------------------------------------------------

def _build_cro_fix_payload(candidate: dict) -> dict:
    supporting_signals = candidate.get("supporting_signals", [])
    return {
        "checklist": _CRO_CHECKLIST,
        "suggested_fixes": _cro_fix_suggested_fixes(supporting_signals),
        "automation_hint": "SHOPIFY_THEME_AUDIT",
        "auto_executable": False,
    }


def _build_scarcity_nudge_payload(candidate: dict) -> dict:
    """
    Build the SCARCITY_NUDGE task payload.

    When triggered by the hot_segment_monitor, the candidate contains live
    segment context (visitor_count, estimated_revenue_window, visitor_ids, etc.).
    That context is surfaced in task_payload.segment_context so agents have
    full visibility without re-querying.

    When triggered manually the segment_context key is omitted (None is stripped).
    """
    visitor_count    = candidate.get("visitor_count")
    revenue_window   = candidate.get("estimated_revenue_window")
    avg_bi           = candidate.get("avg_behavioral_index")
    cvr_estimate     = candidate.get("cvr_estimate")
    calibration      = candidate.get("calibration_state", "unknown")
    trigger_source   = candidate.get("trigger_source")

    checklist = [
        {
            "id": "scarcity_signal",
            "label": "Add scarcity signal to product page",
            "description": (
                "Display low-stock count, 'X people viewing now', or a time-limited "
                "offer near the Add to Cart button.  For Shopify: use theme settings "
                "or a metafield-driven widget to avoid hard-coding."
            ),
        },
        {
            "id": "urgency_copy",
            "label": "Update copy with urgency near the CTA",
            "description": (
                "Add benefit + scarcity in one sentence near the Add to Cart button.  "
                "Example: 'Only 3 left — ships today'.  Specificity outperforms "
                "generic urgency ('Limited stock available')."
            ),
        },
        {
            "id": "social_proof",
            "label": "Add real-time social proof",
            "description": (
                "Show recent purchase notifications or live visitor count if your "
                "theme supports it.  Authenticity matters — avoid fake counters.  "
                "Even review count near the CTA reduces hesitation for engaged visitors."
            ),
        },
    ]

    suggested_fixes = []

    if visitor_count and visitor_count >= 1:
        suggested_fixes.append({
            "priority": 1,
            "fix": (
                f"Display '{visitor_count} people viewing this now' "
                f"near the Add to Cart button"
            ),
            "impact": "HIGH",
            "signal": "HOT_SEGMENT",
            "rationale": (
                f"{visitor_count} hot visitors are active on this product.  "
                "Social proof at this scale creates competitive urgency and "
                "tips high-intent visitors into purchase."
            ),
        })

    if revenue_window and revenue_window >= 1.0:
        suggested_fixes.append({
            "priority": 2,
            "fix": (
                f"Add a limited-time offer to capture the "
                f"${revenue_window:.0f} estimated revenue window"
            ),
            "impact": "HIGH",
            "signal": "HOT_SEGMENT",
            "rationale": (
                "The hot segment's revenue window is open now.  "
                "A time-limited nudge (e.g. 'Free shipping today only') "
                "converts this window into captured revenue before it closes."
            ),
        })

    suggested_fixes.append({
        "priority": len(suggested_fixes) + 1,
        "fix": "Add a low-stock count if inventory is below 10 units",
        "impact": "MEDIUM",
        "signal": "HOT_SEGMENT",
        "rationale": (
            "Inventory scarcity is the strongest urgency signal for "
            "high-intent visitors.  Even a count of 8–10 remaining is "
            "actionable — under 5 converts at a measurably higher rate."
        ),
    })

    # segment_context is included only when this was triggered by the monitor.
    # None values are excluded so the payload stays clean for manual tasks.
    segment_context = None
    if trigger_source == "hot_segment_monitor":
        segment_context = {
            "trigger_source":           trigger_source,
            "visitor_count":            visitor_count,
            "estimated_revenue_window": revenue_window,
            "avg_behavioral_index":     avg_bi,
            "cvr_estimate":             cvr_estimate,
            "calibration_state":        calibration,
        }

    payload = {
        "checklist":        checklist,
        "suggested_fixes":  suggested_fixes,
        "automation_hint":  "SHOPIFY_METAFIELD_SET",
        "auto_executable":  False,
    }
    if segment_context is not None:
        payload["segment_context"] = segment_context

    return payload


def _build_price_test_payload(candidate: dict) -> dict:
    return {"checklist": [], "suggested_fixes": [], "automation_hint": "SHOPIFY_PRICE_COMPARE_AT", "auto_executable": False}


def _build_retarget_payload(candidate: dict) -> dict:
    return {"checklist": [], "suggested_fixes": [], "automation_hint": "SHOPIFY_DISCOUNT_CODE", "auto_executable": False}


def _build_flash_incentive_payload(candidate: dict) -> dict:
    return {"checklist": [], "suggested_fixes": [], "automation_hint": "SHOPIFY_DISCOUNT_CODE", "auto_executable": False}


_PAYLOAD_BUILDERS = {
    "CRO_FIX":              _build_cro_fix_payload,
    "SCARCITY_NUDGE":       _build_scarcity_nudge_payload,
    "PRICE_TEST":           _build_price_test_payload,
    "RETARGET_HOT_TRAFFIC": _build_retarget_payload,
    "FLASH_INCENTIVE":      _build_flash_incentive_payload,
}


def build_payload(action_type: str, candidate: dict) -> dict:
    """Return the structured task_payload for the given action_type."""
    builder = _PAYLOAD_BUILDERS.get(action_type)
    if builder is None:
        raise ValueError(f"No payload builder registered for action_type={action_type!r}")
    return builder(candidate)


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def create_task(
    db: Session,
    shop_domain: str,
    candidate: dict,
    triggered_by: str = "manual",
) -> tuple[ActionTask, bool]:
    """
    Create an ActionTask from a candidate dict.

    Returns (task, created) where created=False means an active task for the
    same (shop_domain, product_url, action_type) already existed and was
    returned instead of creating a duplicate.

    Raises ValueError if action_type is unknown.
    """
    action_type = candidate["action_type"]
    product_url = candidate["product_url"]

    existing = (
        db.query(ActionTask)
        .filter(
            ActionTask.shop_domain == shop_domain,
            ActionTask.product_url == product_url,
            ActionTask.action_type == action_type,
            ActionTask.status.in_(_ACTIVE_STATUSES),
        )
        .first()
    )
    if existing is not None:
        return existing, False

    task_payload = build_payload(action_type, candidate)

    task = ActionTask(
        shop_domain      = shop_domain,
        product_url      = product_url,
        action_type      = action_type,
        status           = STATUS_PENDING,
        triggered_by     = triggered_by,
        claimed_by       = None,
        source_candidate = candidate,
        task_payload     = task_payload,
        expected_loss    = candidate.get("expected_loss"),
        confidence       = candidate.get("confidence"),
        urgency          = candidate.get("urgency"),
        created_at       = datetime.utcnow(),
        updated_at       = datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task, True


def claim_task(
    db: Session,
    task_id: int,
    shop_domain: str,
    claimed_by: str,
) -> tuple[Optional[ActionTask], Optional[str]]:
    """
    Atomically claim a pending task for an agent.

    Uses SELECT FOR UPDATE to acquire a row-level lock before inspecting
    status and claimed_by.  This makes the claim safe under concurrent load:
    only one agent can succeed; all others get a conflict signal.

    Returns:
      (task, None)           — claim succeeded; task is now executing
      (None, "not_found")    — no task with this id belonging to this shop
      (task, "conflict")     — task is no longer pending or already claimed

    The caller maps these to HTTP status codes:
      None       → 404
      "conflict" → 409
    """
    task = (
        db.query(ActionTask)
        .filter(
            ActionTask.id == task_id,
            ActionTask.shop_domain == shop_domain,
        )
        .with_for_update()
        .first()
    )

    if task is None:
        return None, "not_found"

    if task.status != STATUS_PENDING or task.claimed_by is not None:
        return task, "conflict"

    now = datetime.utcnow()
    task.status      = STATUS_EXECUTING
    task.claimed_by  = claimed_by
    task.executed_at = now
    task.updated_at  = now

    db.commit()
    db.refresh(task)
    return task, None


def release_task(
    db: Session,
    task_id: int,
    shop_domain: str,
    reason: Optional[str] = None,
) -> tuple[Optional[ActionTask], Optional[str]]:
    """
    Release a stuck executing task back to pending.

    Intended for operator or watchdog use when an agent has crashed or
    disappeared after claiming a task.  Uses SELECT FOR UPDATE to make the
    reset atomic — safe to call concurrently or from a background sweep.

    On success:
      - status       → pending
      - claimed_by   → NULL  (cleared so next claim records fresh identity)
      - executed_at  → NULL  (cleared so next claim records a fresh timestamp)
      - updated_at   → now()
      - completed_at → unchanged (was NULL; leave it NULL)
      - result_detail → release note appended (see below)

    Release note format
    -------------------
    Appended to result_detail so the previous agent identity is preserved in
    the audit trail even after claimed_by is cleared.

      [RELEASED: <ISO timestamp> | was_claimed_by: <agent_id> | reason: <reason>]

    If result_detail was NULL, the note becomes the entire value.
    If result_detail already had content, a newline separates the entries.
    reason defaults to "manual_release" if omitted.

    Returns:
      (task, None)           — release succeeded; task is now pending
      (None, "not_found")    — no task with this id belonging to this shop
      (task, "conflict")     — task is not currently executing; cannot release

    The caller maps these to HTTP status codes:
      None       → 404
      "conflict" → 409
    """
    task = (
        db.query(ActionTask)
        .filter(
            ActionTask.id == task_id,
            ActionTask.shop_domain == shop_domain,
        )
        .with_for_update()
        .first()
    )

    if task is None:
        return None, "not_found"

    if task.status != STATUS_EXECUTING:
        return task, "conflict"

    now = datetime.utcnow()
    release_reason = reason or "manual_release"
    previous_claimant = task.claimed_by or "unknown"

    release_note = (
        f"[RELEASED: {now.isoformat()} "
        f"| was_claimed_by: {previous_claimant} "
        f"| reason: {release_reason}]"
    )

    if task.result_detail:
        task.result_detail = task.result_detail + "\n" + release_note
    else:
        task.result_detail = release_note

    task.status      = STATUS_PENDING
    task.claimed_by  = None
    task.executed_at = None
    task.updated_at  = now

    db.commit()
    db.refresh(task)
    return task, None


def transition_task(
    db: Session,
    task: ActionTask,
    new_status: str,
    result_detail: Optional[str] = None,
) -> ActionTask:
    """
    Apply a non-claim status transition to an existing task.

    Valid transitions:
      executing → done        sets completed_at
      executing → failed      sets completed_at
      pending   → dismissed   sets completed_at

    pending → executing is NOT handled here — use claim_task() instead.
    executing → pending is NOT handled here — use release_task() instead.
    Attempting either here raises ValueError.

    For done and failed transitions, result_detail is required and must
    satisfy the structured result contract (see _validate_result_detail).
    Validation failures raise ValueError before any write occurs.

    All timestamps are set server-side.  updated_at is always refreshed.
    result_detail is written when provided; an existing value is never cleared
    by passing None.
    """
    key = (task.status, new_status)
    if key not in _TRANSITIONS:
        allowed = _ALLOWED_FROM.get(task.status, [])
        if allowed:
            raise ValueError(
                f"Invalid transition: {task.status!r} → {new_status!r}. "
                f"Allowed from {task.status!r}: {allowed}"
            )
        raise ValueError(
            f"Task is in terminal state {task.status!r} and cannot be transitioned."
        )

    _validate_result_detail(result_detail, new_status)

    now = datetime.utcnow()
    task.status       = new_status
    task.updated_at   = now
    task.completed_at = now

    if result_detail is not None:
        task.result_detail = result_detail

    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: int, shop_domain: str) -> Optional[ActionTask]:
    """Return a single task scoped to the shop, or None."""
    return (
        db.query(ActionTask)
        .filter(ActionTask.id == task_id, ActionTask.shop_domain == shop_domain)
        .first()
    )


def list_tasks(
    db: Session,
    shop_domain: str,
    status: Optional[str] = None,
    claimed_by: Optional[str] = None,
    limit: int = 50,
) -> list[ActionTask]:
    """
    Return tasks for a shop, newest first.

    Filters are independent and composable (AND semantics):
      status     — when provided, restricts to tasks in that status
      claimed_by — when provided, restricts to tasks claimed by that agent
      limit      — caps results; enforced at the service layer (max 50 at API)

    Omitting a filter leaves that dimension unrestricted.
    """
    q = db.query(ActionTask).filter(ActionTask.shop_domain == shop_domain)
    if status:
        q = q.filter(ActionTask.status == status)
    if claimed_by:
        q = q.filter(ActionTask.claimed_by == claimed_by)
    return q.order_by(ActionTask.created_at.desc()).limit(limit).all()
