"""
nudge_compose_task.py — AI nudge variant upgrade.

Extracted from aggregation_worker.py (Phase Ω⁶ split). Upgrades Pro
nudges flagged `ai_compose_pending=True` with AI-composed variants,
bounded per cycle to protect the LLM budget. Self-protection gates
on `protection_state()` so the batch halves or skips entirely when
the system is degraded.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

_log = logging.getLogger("worker.aggregation.nudge_compose")


def run(db: Session) -> int:
    """
    Upgrade Pro nudges with AI-composed variants.

    Returns the count of nudges upgraded this cycle.
    """
    import asyncio
    import json as _json
    from app.models.active_nudge import ActiveNudge
    from app.models.product import Product
    from app.services.nudge_composer import compose_nudge_variants
    from app.core.protection_state import protection_state

    state = protection_state()
    if state["level"] == "CRITICAL" or "skip_all_optional_llm_calls" in state["protective_actions"]:
        _log.info("protection_state: %s — skipping nudge_compose", state["level"])
        return 0
    if "skip_optional_llm_calls" in state["protective_actions"]:
        _log.info("protection_state: DEGRADED (llm) — skipping nudge_compose")
        return 0

    reduce_batch = (
        "reduce_batch_sizes" in state.get("protective_actions", [])
        or state.get("subsystems", {}).get("llm", {}).get("level") == "degraded"
    )
    max_per_cycle = 2 if reduce_batch else 5
    if reduce_batch:
        _log.info("protection_state: %s — reducing nudge_compose batch from 5 to %d",
                  state["level"], max_per_cycle)

    pending = (
        db.query(ActiveNudge)
        .filter(
            ActiveNudge.ai_compose_pending == True,  # noqa: E712
            ActiveNudge.status == "active",
        )
        .order_by(ActiveNudge.created_at.asc())
        .limit(max_per_cycle)
        .all()
    )
    if not pending:
        return 0

    upgraded = 0
    for nudge in pending:
        try:
            product = (
                db.query(Product)
                .filter_by(shop_domain=nudge.shop_domain, product_url=nudge.product_url)
                .first()
            )
            product_title = (
                product.title.strip() if product and product.title
                else nudge.product_url.replace("/products/", "").replace("-", " ").title()
            )

            signals = {
                "unique_visitors_24h": nudge.visitor_count or 0,
                "action_type": nudge.action_type,
            }

            variants, meta = asyncio.run(
                compose_nudge_variants(
                    product_title=product_title,
                    product_url=nudge.product_url,
                    signals=signals,
                    data_window_hours=72,
                )
            )

            if variants and len(variants) >= 2:
                primary = variants[0]
                nudge.copy_variant = primary.get("variant_name", nudge.copy_variant)
                nudge.copy_config = _json.dumps(primary.get("copy_config", {}))
                nudge.copy_variants = _json.dumps(variants)
                nudge.ai_compose_pending = False
                upgraded += 1
                _log.info(
                    "nudge_compose: upgraded nudge_id=%s shop=%s variants=%d fallback=%s",
                    nudge.id, nudge.shop_domain, len(variants), meta.get("fallback_used"),
                )
            else:
                nudge.ai_compose_pending = False
                _log.info("nudge_compose: no variants for nudge_id=%s, flag cleared", nudge.id)
        except Exception as exc:
            _log.warning("nudge_compose: failed for nudge_id=%s err=%s: %s",
                         nudge.id, type(exc).__name__, exc)
            continue

    db.flush()
    return upgraded
