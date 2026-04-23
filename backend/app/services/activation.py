"""
activation.py — Server-side merchant activation stage classifier.

Deterministic classification based on real data.
No AI, no heuristics — purely fact-based.

Public interface:
    classify_activation(db, shop_domain) -> dict

Stages:
    0  installed      — merchant row exists, install_status = active
    1  tracking       — tracker script registered (script_tag_id present)
    2  receiving      — events table has rows for this shop
    3  intelligence   — opportunity_signals has active rows
    4  digest_ready   — has contact_email + enough data for digest
    5  proof_ready    — action_snapshots has delta_computed rows

Used by:
    - digest content adaptation (richer content at higher stages)
    - upgrade timing (trigger at stage 3+)
    - churn risk detection (stage regression)
    - merchant ops views (support triage)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger(__name__)


def classify_activation(db: Session, shop_domain: str) -> dict:
    """
    Return the activation stage and metadata for a merchant.

    Always returns a dict. Never raises.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    result = {
        "shop_domain": shop_domain,
        "stage": 0,
        "stage_name": "unknown",
        "details": {},
        "classified_at": now.isoformat() + "Z",
    }

    try:
        merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
        if not merchant or merchant.install_status != "active":
            result["stage_name"] = "not_installed"
            return result

        # Stage 0: installed
        result["stage"] = 0
        result["stage_name"] = "installed"
        result["details"]["has_token"] = bool(merchant.access_token)
        result["details"]["has_email"] = bool(merchant.contact_email)
        result["details"]["plan"] = merchant.plan or "lite"

        # Stage 1: tracking
        if merchant.script_tag_id:
            result["stage"] = 1
            result["stage_name"] = "tracking"

        # Stage 2: receiving data
        event_count = _count(db, "SELECT COUNT(*)::int FROM events WHERE shop_domain = :shop LIMIT 1", shop_domain)
        if event_count > 0:
            result["stage"] = 2
            result["stage_name"] = "receiving"
            result["details"]["event_count"] = event_count

        # Stage 3: intelligence active
        signal_count = _count(db, """
            SELECT COUNT(*)::int FROM opportunity_signals
            WHERE shop_domain = :shop AND expires_at >= NOW()
        """, shop_domain)
        if signal_count > 0:
            result["stage"] = 3
            result["stage_name"] = "intelligence"
            result["details"]["signal_count"] = signal_count

        # Stage 4: digest ready
        has_email = bool(merchant.contact_email)
        order_count = _count(db, "SELECT COUNT(*)::int FROM shop_orders WHERE shop_domain = :shop", shop_domain)
        if has_email and order_count > 0:
            result["stage"] = max(result["stage"], 4)
            result["stage_name"] = "digest_ready"
            result["details"]["order_count"] = order_count

        # Stage 5: proof ready
        proof_count = _count(db, """
            SELECT COUNT(*)::int FROM action_snapshots
            WHERE shop_domain = :shop AND delta_computed = true
        """, shop_domain)
        if proof_count > 0:
            result["stage"] = 5
            result["stage_name"] = "proof_ready"
            result["details"]["proof_count"] = proof_count

    except Exception as exc:
        log.warning("activation: classification failed shop=%s: %s", shop_domain, exc)

    return result


def _count(db: Session, query: str, shop: str) -> int:
    try:
        row = db.execute(text(query), {"shop": shop}).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
