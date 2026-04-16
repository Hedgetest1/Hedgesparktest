"""
merchant_rule.py — Low-code rule builder (ζ2).

Merchants define:
    IF <signal> [AND <filter>] THEN <action>

Examples:
  IF cart_abandoned AND visitor_returning AND source=google
    THEN send_klaviyo_event(name="cart_watch")

  IF rars_spike AND magnitude>1000
    THEN notify_slack(channel="revenue-alerts")

  IF high_intent_abandon AND product_category=electronics
    THEN create_nudge(type=scarcity, holdout=30)

Evaluation
----------
Rules evaluated by `rule_engine.py` every time a matching signal type
is produced. Idempotent, rate-limited per rule, audited.

Storage
-------
One row per rule. Conditions + action stored as JSONB for flexibility.
Status: draft | active | paused | disabled.

Safety
------
Rule actions are restricted to a whitelist (send_klaviyo_event,
notify_slack, create_nudge, write_note) — merchant cannot run
arbitrary code. The rule_engine picks the handler from the whitelist.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class MerchantRule(Base):
    __tablename__ = "merchant_rules"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)

    name = Column(String(200), nullable=False)

    # Trigger — the signal type that starts evaluation
    # e.g. 'cart_abandoned', 'rars_spike', 'goal_at_risk', 'high_intent_abandon'
    trigger_signal = Column(String(64), nullable=False)

    # Conditions — JSONB list of simple filters:
    # [{"field": "source", "op": "eq", "value": "google"},
    #  {"field": "magnitude", "op": "gt", "value": 1000}]
    # Supported ops: eq, ne, gt, lt, gte, lte, contains, in
    conditions = Column(JSONB, nullable=False, default=list, server_default="'[]'")

    # Action — JSONB dict with 'type' + params
    # {"type": "send_klaviyo_event", "event_name": "cart_watch"}
    # {"type": "notify_slack", "channel": "revenue-alerts"}
    # {"type": "create_nudge", "nudge_type": "scarcity", "holdout_pct": 30}
    # {"type": "write_note", "body": "Watch this one"}
    action = Column(JSONB, nullable=False)

    status = Column(String(16), nullable=False, default="draft", server_default="draft")  # draft|active|paused|disabled

    # Rate limiting — max fires per hour per rule (prevents runaway loops)
    max_per_hour = Column(Integer, nullable=False, default=30, server_default="30")

    # Statistics
    fired_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_fired_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()", onupdate=utc_now_naive)
    created_by = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_merchant_rules_shop_status", "shop_domain", "status"),
        Index("ix_merchant_rules_shop_trigger", "shop_domain", "trigger_signal"),
    )
