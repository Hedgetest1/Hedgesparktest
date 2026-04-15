"""
trust_contract.py — Delegated Autonomy / Trust Contract model.

THE killer feature: merchant pre-approves safety bounds, and the system
executes autonomous revenue-optimizing actions within those bounds without
human approval. No competitor SMB product does this.

Model
-----
One row = one active trust contract for a given (merchant, action_type).
Expired/revoked contracts are kept for audit — the `status` column gates
execution.

Contract semantics
------------------
- `max_per_day` / `max_per_week`: hard quotas. Redis counters enforce.
- `discount_floor_pct`: the most negative discount allowed
  (e.g. -10 means prices can drop by up to 10%). Default 0 = no price cut.
- `discount_ceiling_pct`: the highest markup allowed. Default 0 = no markup.
- `confidence_threshold`: min holdout-proven confidence required for the
  action to proceed. Default 0.80.
- `auto_pause_on_drop_pct`: if revenue drops more than X% in the last 24h
  vs the preceding 24h, the contract auto-pauses. Default 15.0.
- `require_holdout`: if True, the action must include a holdout group.
- `scope_type`: 'all' | 'products' | 'collections' | 'tags'
- `scope_values`: JSON list of the referenced entities (when scope_type != 'all')
- `status`: 'active' | 'paused' | 'revoked' | 'expired'

Every auto-execution under a contract is logged in `trust_execution_log`
(separate table) so the merchant can see exactly what the system did on
their behalf, and revoke / tighten at any time.

The `panic_stop()` API revokes every active contract for a merchant
instantly — the single button that rebuilds trust after a surprise.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, Numeric, String, Text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class TrustContract(Base):
    __tablename__ = "trust_contracts"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False, index=True)
    action_type = Column(String, nullable=False)  # SCARCITY_NUDGE | PRICE_TEST | FLASH_INCENTIVE | ...

    # --- Quotas ---
    max_per_day = Column(Integer, nullable=False, default=3)
    max_per_week = Column(Integer, nullable=False, default=10)

    # --- Price bounds (for PRICE_TEST / FLASH_INCENTIVE) ---
    # discount_floor_pct: -10 = allow up to 10% price cut (most aggressive)
    # discount_ceiling_pct: 0 = never allow markup; 5 = allow up to +5%
    discount_floor_pct = Column(Float, nullable=False, default=-5.0)
    discount_ceiling_pct = Column(Float, nullable=False, default=0.0)

    # --- Safety gates ---
    confidence_threshold = Column(Float, nullable=False, default=0.80)  # [0..1]
    auto_pause_on_drop_pct = Column(Float, nullable=False, default=15.0)  # rev drop %
    require_holdout = Column(Boolean, nullable=False, default=True)

    # --- Scope ---
    scope_type = Column(String, nullable=False, default="all")  # all | products | collections | tags
    scope_values = Column(Text, nullable=True)  # JSON array, null for scope_type=all

    # --- Lifecycle ---
    status = Column(String, nullable=False, default="active")  # active | paused | revoked | expired
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)  # 'panic' | 'auto_pause:rev_drop' | 'merchant' | ...

    # --- Metadata ---
    created_by = Column(String, nullable=True)  # merchant email / operator id
    note = Column(String, nullable=True)  # free text

    __table_args__ = (
        Index("ix_trust_contracts_shop_action_status", "shop_domain", "action_type", "status"),
    )


class TrustExecutionLog(Base):
    """
    Immutable log of every auto-execution performed under a trust contract.
    Merchant can query this to see exactly what was done on their behalf.
    One row per (contract, action) execution — never updated, never deleted.
    """
    __tablename__ = "trust_execution_log"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, nullable=False, index=True)
    shop_domain = Column(String, nullable=False, index=True)
    action_type = Column(String, nullable=False)
    target_url = Column(String, nullable=True)  # product/collection affected

    executed_at = Column(DateTime, nullable=False, default=utc_now_naive, index=True)

    # Snapshot of the decision
    confidence = Column(Float, nullable=True)
    discount_pct_applied = Column(Float, nullable=True)
    holdout_pct_applied = Column(Integer, nullable=True)
    params_json = Column(Text, nullable=True)  # full param payload

    # Outcome (filled 48h later by outcome evaluator)
    outcome = Column(String, nullable=True)  # effective | ineffective | inconclusive | pending
    revenue_delta_eur = Column(Numeric(18, 2), nullable=True)
    measured_at = Column(DateTime, nullable=True)
