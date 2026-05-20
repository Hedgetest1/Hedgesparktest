"""
action_executor.py — Converts action candidates into executable task records
and manages task lifecycle transitions.

Responsibilities
----------------
1. Build a structured task_payload for each action_type.
   Implemented: SCARCITY_NUDGE, RETARGET_HOT_TRAFFIC, PRICE_TEST, FLASH_INCENTIVE.
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
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.action_task import ActionTask

# F821 class fix (2026-05-19i): `log` used at ~line 782 (baseline-
# capture except, the sole logger call in the file) but NEVER bound →
# the non-fatal path raised NameError instead of warning. Canonical
# project pattern (revenue_metrics.py:72).
log = logging.getLogger(__name__)

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
# Payload builders — one per action_type
# ---------------------------------------------------------------------------

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
    # Currency may be populated by the candidate producer (hot_segment_monitor)
    # via get_shop_currency. Fallback to USD only when candidate didn't
    # carry it (e.g. legacy producers / synthetic test fixtures).
    currency         = candidate.get("currency") or "USD"

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
        from app.core.currency import format_money
        suggested_fixes.append({
            "priority": 2,
            "fix": (
                f"Add a limited-time offer to capture the "
                f"{format_money(revenue_window, currency)} estimated revenue window"
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
    """
    Build the PRICE_TEST task payload.

    Triggered when price_intelligence signals HIGH_INTENT_PRICE_OPPORTUNITY —
    the product's price is above the market midpoint and visitors are engaging
    deeply but not converting, suggesting price friction is the primary barrier.

    The recommended approach is a compare-at price anchor (not a price cut) as
    the lowest-risk, highest-signal intervention.  A real price reduction is
    the second step only if anchoring fails to lift conversion.
    """
    pi_explanation  = (candidate.get("intelligence_explanation") or "").strip()
    price_position  = candidate.get("price_position", "above_market")
    confidence      = int(candidate.get("confidence_score") or 0)
    product_url     = candidate.get("product_url", "")

    checklist = [
        {
            "id": "anchor_compare_at",
            "label": "Set compare-at price to anchor perceived value",
            "description": (
                "Add a crossed-out 'was' price next to the current price. "
                "The compare-at field in Shopify admin creates this automatically. "
                "Even a 5–10% higher compare-at price significantly improves "
                "conversion by confirming the current price is a deal."
            ),
        },
        {
            "id": "verify_market_position",
            "label": "Verify current price vs market midpoint",
            "description": (
                f"Price intelligence signals position: {price_position} "
                f"(confidence {confidence}%). "
                + (f"Context: {pi_explanation}. " if pi_explanation else "")
                + "Check 3 competitor listings before making any price change."
            ),
        },
        {
            "id": "test_5pct_reduction",
            "label": "Test a 5% price reduction on one variant (if anchoring insufficient)",
            "description": (
                "Use Shopify's built-in price testing or create a duplicate "
                "product variant at the reduced price. Do NOT reduce the original "
                "listing until you have conversion data from the test variant."
            ),
        },
        {
            "id": "monitor_conversion_48h",
            "label": "Monitor conversion rate change over 48 hours",
            "description": (
                "A meaningful price intervention should produce a detectable "
                "conversion rate change within 48 hours if traffic is sufficient. "
                f"Watch the product_url: {product_url} in HedgeSpark's live metrics."
            ),
        },
        {
            "id": "rollback_plan",
            "label": "Prepare rollback plan before any live price change",
            "description": (
                "Record the original price. Set a calendar reminder at 48h. "
                "If conversion does not improve, revert immediately — price "
                "reductions that do not lift conversion erode margin permanently."
            ),
        },
    ]

    suggested_fixes = [
        {
            "priority": 1,
            "fix": "Add a compare-at (was) price to anchor the current price as a deal",
            "impact": "HIGH",
            "signal": "PRICE_TEST",
            "rationale": (
                "Compare-at pricing costs nothing and typically lifts conversion "
                "by 5–15% on products where price anxiety is the primary friction. "
                "It is the lowest-risk first step before any real price change."
            ),
        },
        {
            "priority": 2,
            "fix": "Add a value-reframing line near the price ('Includes X, Y, Z')",
            "impact": "MEDIUM",
            "signal": "PRICE_TEST",
            "rationale": (
                "Visitors hesitating on price often respond to explicit value "
                "itemisation — showing what they get for the price shifts the "
                "mental calculation from 'is it cheap enough' to 'is it worth it'."
            ),
        },
        {
            "priority": 3,
            "fix": "Run a 5% price reduction test on a secondary variant for 7 days",
            "impact": "MEDIUM",
            "signal": "PRICE_TEST",
            "rationale": (
                f"Price intelligence detects {price_position} position at "
                f"{confidence}% confidence. A controlled reduction tests "
                "whether price is truly the barrier before committing to it."
            ),
        },
    ]

    return {
        "checklist":        checklist,
        "suggested_fixes":  suggested_fixes,
        "automation_hint":  "SHOPIFY_PRICE_COMPARE_AT",
        "auto_executable":  False,
        "price_context": {
            "price_position":            price_position,
            "confidence_score":          confidence,
            "intelligence_explanation":  pi_explanation or None,
        },
    }


def _build_retarget_payload(candidate: dict) -> dict:
    """
    Build the RETARGET_HOT_TRAFFIC task payload.

    Triggered when return visitors are engaging with a product repeatedly
    but not converting — signals strong intent without a closing nudge.

    Goal: bring the return visitor back with a personalised re-engagement
    that acknowledges their prior interest without feeling intrusive.
    """
    return_count    = int(candidate.get("return_visitor_count_7d") or 0)
    cart_count      = int(candidate.get("cart_conversions_24h") or 0)
    expected_loss   = float(candidate.get("expected_loss") or 0.0)
    product_url     = candidate.get("product_url", "")

    checklist = [
        {
            "id": "segment_return_visitors",
            "label": "Export return visitor segment to Klaviyo",
            "description": (
                "Use HedgeSpark's /pro/klaviyo/push endpoint to sync the "
                "hot-intent visitor segment to a Klaviyo list.  This creates "
                "a targetable audience without touching your main list."
            ),
        },
        {
            "id": "compose_return_email",
            "label": "Send a 'you left something behind' re-engagement email",
            "description": (
                "Keep it short: product image + headline + single CTA.  "
                "Personalise the subject line with the product name.  "
                "Do NOT include a discount in the first email — that trains "
                "visitors to wait for offers."
            ),
        },
        {
            "id": "activate_return_nudge",
            "label": "Activate a return-visitor nudge on the product page",
            "description": (
                "In HedgeSpark Pro, create a nudge for this product targeting "
                "the return_visitor strategy.  The AI composer will generate "
                "copy grounded in the return-visit signal."
            ),
        },
        {
            "id": "offer_loyalty_incentive",
            "label": "Consider a loyalty reward for the 2nd touch (if email unopened at 48h)",
            "description": (
                "If the first re-engagement email does not convert within 48h, "
                "send a follow-up with a modest loyalty reward (free shipping, "
                "small discount).  Only reveal the incentive on the second touch "
                "to preserve margin on visitors who would have converted anyway."
            ),
        },
    ]

    suggested_fixes = [
        {
            "priority": 1,
            "fix": (
                f"Export the {return_count} return visitor segment from HedgeSpark "
                "to Klaviyo and send a re-engagement email within 24 hours"
            ),
            "impact": "HIGH",
            "signal": "RETARGET_HOT_TRAFFIC",
            "rationale": (
                f"{return_count} visitors have returned to this product multiple times "
                "this week with only {cart_count} cart additions.  These are your "
                "highest-intent non-buyers — the conversion window is open now."
            ),
        },
        {
            "priority": 2,
            "fix": "Activate a return-visitor nudge on this product page in HedgeSpark",
            "impact": "HIGH",
            "signal": "RETARGET_HOT_TRAFFIC",
            "rationale": (
                "A behaviorally-targeted nudge on the product page itself "
                "catches return visitors at the highest-intent moment — when they "
                "are already viewing the product."
            ),
        },
        {
            "priority": 3,
            "fix": (
                f"If email does not convert in 48h, offer a loyalty reward "
                f"to capture the estimated ${expected_loss:.0f} revenue window"
                if expected_loss >= 10.0 else
                "If email does not convert in 48h, offer a small loyalty reward (free shipping)"
            ),
            "impact": "MEDIUM",
            "signal": "RETARGET_HOT_TRAFFIC",
            "rationale": (
                "Second-touch incentives should be revealed only after the "
                "first touch fails — this prevents training all return visitors "
                "to wait for a deal."
            ),
        },
    ]

    return {
        "checklist":       checklist,
        "suggested_fixes": suggested_fixes,
        "automation_hint": "SHOPIFY_DISCOUNT_CODE",
        "auto_executable": False,
        "retarget_context": {
            "return_visitor_count_7d": return_count,
            "cart_conversions_24h":    cart_count,
            "estimated_revenue_loss":  expected_loss,
            "product_url":             product_url,
        },
    }


def _build_flash_incentive_payload(candidate: dict) -> dict:
    """
    Build the FLASH_INCENTIVE task payload.

    Triggered when a live traffic spike is detected and is NOT converting at
    the expected rate — a time-limited offer can capture the revenue window
    before the spike subsides.

    These tasks are inherently time-sensitive.  Agents should prioritise them
    above other task types when claimed_by timestamp is recent.
    """
    views_1h        = int(candidate.get("views_1h") or 0)
    views_24h       = int(candidate.get("views_24h") or 0)
    cart_count      = int(candidate.get("cart_conversions_24h") or 0)
    expected_loss   = float(candidate.get("expected_loss") or 0.0)
    product_url     = candidate.get("product_url", "")

    checklist = [
        {
            "id": "verify_spike_live",
            "label": "Verify the traffic spike is still active before acting",
            "description": (
                f"Current spike: {views_1h} views in the last hour. "
                "Check HedgeSpark's live visitors panel to confirm the spike "
                "has not already subsided.  Flash incentives are only effective "
                "during active traffic — acting after the spike wastes the discount."
            ),
        },
        {
            "id": "create_discount_code",
            "label": "Create a time-limited Shopify discount code (2–4 hours)",
            "description": (
                "Use the Shopify admin or HedgeSpark's /shopify/discount endpoint. "
                "Set a hard expiry — 2 to 4 hours maximum.  Longer durations "
                "train visitors to wait rather than convert during the spike."
            ),
        },
        {
            "id": "surface_on_product_page",
            "label": "Surface the discount near the Add to Cart button immediately",
            "description": (
                "Options in order of speed: (1) activate a HedgeSpark nudge with "
                "the discount code embedded; (2) use a Shopify announcement bar app; "
                "(3) manually update the product description temporarily. "
                "Time-to-live matters — faster surfacing = more captured revenue."
            ),
        },
        {
            "id": "monitor_conversion_real_time",
            "label": "Monitor conversion rate in real time for the duration of the offer",
            "description": (
                f"Product: {product_url}. "
                f"Baseline: {cart_count} cart additions in last 24h. "
                "A successful flash incentive should show a measurable cart "
                "addition lift within 30–60 minutes of activation."
            ),
        },
        {
            "id": "remove_offer_at_expiry",
            "label": "Remove or deactivate the discount at expiry — do not extend",
            "description": (
                "Extensions convert a flash incentive into a permanent discount "
                "in visitors' mental models.  Stick to the announced expiry "
                "even if conversion is still active."
            ),
        },
    ]

    suggested_fixes = [
        {
            "priority": 1,
            "fix": (
                f"Create a 2-hour discount code now and activate a nudge on this product. "
                f"Live spike: {views_1h} views/hour."
            ),
            "impact": "HIGH",
            "signal": "FLASH_INCENTIVE",
            "rationale": (
                f"Traffic is spiking ({views_1h} views/hr, {views_24h} total today) "
                f"with only {cart_count} cart additions. "
                "A time-limited incentive converts transient intent into revenue "
                "before the spike window closes."
            ),
        },
        {
            "priority": 2,
            "fix": "Add an announcement bar or countdown timer to create urgency on the page",
            "impact": "MEDIUM",
            "signal": "FLASH_INCENTIVE",
            "rationale": (
                "Visual urgency reinforces the time-limited nature of the offer. "
                "A countdown timer on the page has been shown to increase flash "
                "sale conversion by 15–20% compared to text-only announcements."
            ),
        },
    ]

    return {
        "checklist":       checklist,
        "suggested_fixes": suggested_fixes,
        "automation_hint": "SHOPIFY_DISCOUNT_CODE",
        "auto_executable": False,
        "spike_context": {
            "views_1h":              views_1h,
            "views_24h":             views_24h,
            "cart_conversions_24h":  cart_count,
            "estimated_revenue_loss": expected_loss,
            "product_url":           product_url,
            "urgency_note": (
                "This task is time-sensitive — verify the spike is still active "
                "before creating a discount code."
            ),
        },
    }


_PAYLOAD_BUILDERS = {
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
        created_at       = datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at       = datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Capture baseline metrics for closed-loop proof-of-impact.
    # This snapshot will be compared after 7 days to measure the action's
    # effect. best-effort: if the baseline capture fails the action still
    # goes through — measurement just degrades to "no baseline available".
    # session-rollback: ok — task already committed at line 767; all callers heal a poisoned session before reuse: api/action_tasks.py:218 request-scoped get_db (teardown closes); segment_monitor_worker.py:422 wraps in try/except + rollback_quiet(db); action_agent._process_task wraps each task in try/except + db.rollback() (action_agent.py:86-92).
    try:
        from app.services.action_proof import capture_baseline
        capture_baseline(
            db=db,
            shop_domain=shop_domain,
            product_url=product_url,
            action_type=action_type,
            action_task_id=task.id,
            signal_type=candidate.get("signal_type") or (candidate.get("supporting_signals") or [None])[0],
            signal_strength=candidate.get("signal_strength"),
        )
        db.commit()
    except Exception as exc:
        log.warning("action_executor: baseline capture failed (non-fatal) task_id=%d: %s", task.id, exc)

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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
