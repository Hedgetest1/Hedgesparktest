"""
rule_engine.py — Evaluates merchant_rules against incoming signals (ζ2).

Public API
----------
    evaluate_trigger(db, shop_domain, trigger_signal, payload) -> int
        For every active rule with matching trigger_signal, evaluates
        conditions against payload. On match, runs the whitelisted
        action. Returns the number of rules that fired.

Conditions DSL
--------------
Simple list-of-filters, all AND-ed:
    [{"field": "source", "op": "eq", "value": "google"},
     {"field": "magnitude", "op": "gt", "value": 1000}]

Supported ops: eq, ne, gt, lt, gte, lte, contains, in, regex

Actions DSL
-----------
Whitelisted action types:
  - send_klaviyo_event    → forwards through klaviyo_events
  - notify_slack          → posts via signal_webhooks
  - create_nudge          → calls nudge_engine.create_or_refresh_nudge
  - write_note            → writes an annotation row
  - emit_ops_alert        → writes an ops_alert

Rate-limited: each rule can fire at most `max_per_hour` times.
Audited: every fire writes a row into audit_log with the matched payload.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.merchant_rule import MerchantRule

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("rule_engine")

_SUPPORTED_OPS = {"eq", "ne", "gt", "lt", "gte", "lte", "contains", "in", "regex"}
_ALLOWED_ACTIONS = frozenset(
    ["send_klaviyo_event", "notify_slack", "create_nudge", "write_note", "emit_ops_alert"]
)
_RATE_REDIS_KEY_PREFIX = "hs:rule_rate"


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception as exc:
        log.warning("rule_engine: _redis failed: %s", exc)
        return None


def _rate_allow(rule_id: int, max_per_hour: int) -> bool:
    rc = _redis()
    if rc is None:
        record_silent_return("rule_engine.rate_limit")
        return True  # fail-open on Redis down
    try:
        key = f"{_RATE_REDIS_KEY_PREFIX}:{rule_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        count = rc.incr(key)
        rc.expire(key, 3700)
        return int(count) <= max_per_hour
    except Exception as exc:
        log.warning("rule_engine: _rate_allow failed: %s", exc)
        return True


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _get_field(payload: dict, field: str) -> Any:
    """Dot-path field lookup: 'a.b.c' → payload['a']['b']['c']."""
    cur: Any = payload
    for part in field.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _eval_condition(cond: dict, payload: dict) -> bool:
    field = cond.get("field")
    op = cond.get("op")
    expected = cond.get("value")
    if not field or op not in _SUPPORTED_OPS:
        return False

    actual = _get_field(payload, field)

    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gt":
            return actual is not None and float(actual) > float(expected)
        if op == "lt":
            return actual is not None and float(actual) < float(expected)
        if op == "gte":
            return actual is not None and float(actual) >= float(expected)
        if op == "lte":
            return actual is not None and float(actual) <= float(expected)
        if op == "contains":
            return expected in (actual or "")
        if op == "in":
            return actual in (expected or [])
        if op == "regex":
            return bool(re.search(str(expected), str(actual or "")))
    except (ValueError, TypeError):
        return False
    return False


def _eval_all(conditions: list[dict], payload: dict) -> bool:
    """AND semantics across all conditions."""
    if not conditions:
        return True
    return all(_eval_condition(c, payload) for c in conditions)


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------

def _run_action(
    db: Session,
    shop_domain: str,
    action: dict,
    payload: dict,
    rule: MerchantRule,
) -> tuple[bool, str]:
    """Execute a rule action. Returns (ok, reason)."""
    kind = action.get("type")
    if kind not in _ALLOWED_ACTIONS:
        return False, f"action_not_allowed:{kind}"

    try:
        if kind == "send_klaviyo_event":
            from app.services.klaviyo_events import forward_event_async, ALLOWED_EVENTS
            event_name = action.get("event_name") or "rule_triggered"
            if event_name not in ALLOWED_EVENTS:
                return False, f"klaviyo_event_not_allowed:{event_name}"
            email = action.get("email") or _get_field(payload, "email")
            if not email:
                return False, "klaviyo_no_email"
            forward_event_async(
                shop_domain=shop_domain,
                event_name=event_name,
                email=email,
                properties={"rule_id": rule.id, "rule_name": rule.name, **payload},
            )
            return True, "ok"

        if kind == "notify_slack":
            # Reuse existing signal_webhooks path — it handles Slack URLs
            from app.services.signal_webhooks import send_signal
            send_signal(
                shop_domain=shop_domain,
                event_type=action.get("event_type", "goal_at_risk"),
                payload={"rule_name": rule.name, **payload},
                source="merchant_rule",
            )
            return True, "ok"

        if kind == "create_nudge":
            product_url = action.get("product_url") or _get_field(payload, "product_url")
            if not product_url:
                return False, "nudge_no_product"
            from app.services.nudge_engine import create_or_refresh_nudge
            create_or_refresh_nudge(
                db=db,
                shop_domain=shop_domain,
                product_url=product_url,
                action_type=action.get("nudge_type", "SCARCITY_NUDGE"),
                trigger_source=f"rule:{rule.id}",
                holdout_pct=int(action.get("holdout_pct", 20)),
            )
            return True, "ok"

        if kind == "write_note":
            # Annotations live in Redis (see app.services.annotations).
            # The old path tried to INSERT INTO a Postgres `annotations`
            # table that never existed, silently crashed, and the
            # `write_note` rule action has been dead since launch.
            from datetime import datetime, timezone
            from app.services.annotations import create_annotation
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            create_annotation(
                shop_domain,
                date=today,
                label=(action.get("body") or rule.name)[:120],
                description=f"Automatically authored by rule {rule.id}",
                category=action.get("category", "other"),
                author=f"rule:{rule.id}",
            )
            return True, "ok"

        if kind == "emit_ops_alert":
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity=action.get("severity", "info"),
                source=f"merchant_rule:{rule.id}",
                alert_type=action.get("alert_type", "rule_triggered"),
                summary=f"Rule '{rule.name}' fired for {shop_domain}",
                shop_domain=shop_domain,
                detail={"rule_id": rule.id, "payload": payload},
            )
            return True, "ok"
    except Exception as exc:
        log.warning("rule_engine: action %s failed: %s", kind, exc)
        return False, f"error:{type(exc).__name__}"

    return False, "unknown"


# ---------------------------------------------------------------------------
# Evaluation entrypoint
# ---------------------------------------------------------------------------

def evaluate_trigger(
    db: Session,
    shop_domain: str,
    trigger_signal: str,
    payload: dict,
) -> int:
    """Called when a signal fires. Evaluates all rules for the shop
    with matching trigger_signal. Returns number of rules fired."""
    try:
        rules = (
            db.query(MerchantRule)
            .filter(
                MerchantRule.shop_domain == shop_domain,
                MerchantRule.status == "active",
                MerchantRule.trigger_signal == trigger_signal,
            )
            .limit(50)
            .all()
        )
    except Exception as exc:
        log.warning("rule_engine: query failed: %s", exc)
        return 0

    fired = 0
    for rule in rules:
        conditions = rule.conditions or []
        if not _eval_all(conditions, payload):
            continue
        if not _rate_allow(rule.id, rule.max_per_hour):
            log.info("rule_engine: rate-limited rule #%d", rule.id)
            continue
        ok, reason = _run_action(db, shop_domain, rule.action or {}, payload, rule)
        if ok:
            rule.fired_count = (rule.fired_count or 0) + 1
            rule.last_fired_at = datetime.now(timezone.utc).replace(tzinfo=None)
            fired += 1
            try:
                from app.services.audit import write_audit_log
                write_audit_log(
                    db,
                    actor_type="system",
                    actor_name=f"rule:{rule.id}",
                    action_type="merchant_rule_fired",
                    target_type="merchant_rule",
                    target_id=str(rule.id),
                    status="completed",
                    approval_mode="merchant_authored",
                    shop_domain=shop_domain,
                    metadata={"rule_name": rule.name, "trigger": trigger_signal},
                )
            except Exception as exc:
                log.warning("rule_engine: evaluate_trigger failed: %s", exc)
        else:
            log.info("rule_engine: rule #%d skipped: %s", rule.id, reason)

    try:
        db.flush()
    except Exception as exc:
        log.warning("rule_engine: evaluate_trigger failed: %s", exc)
    return fired
