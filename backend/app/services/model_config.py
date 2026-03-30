"""
model_config.py — Persistent model configuration with in-process cache.

The router calls get_active_model(module) which reads from DB with a 5-minute
cache. Activation and rollback write to DB and invalidate the cache.

This is the ONLY source of truth for which model is active per module.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.active_model_config import ActiveModelConfig

log = logging.getLogger("model_config")

_CACHE_TTL_S = 300  # 5 minutes
_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def invalidate_cache(module: str | None = None):
    """Clear cache for one or all modules."""
    if module:
        _cache.pop(module, None)
        _cache_ts.pop(module, None)
    else:
        _cache.clear()
        _cache_ts.clear()


def get_active_model(module: str, db: Session | None = None) -> dict:
    """
    Return the active model config for a module.
    Uses in-process cache (5 min TTL), reads from DB on miss.
    Returns: {"provider": str, "model": str} or defaults.
    """
    # Check cache
    now = time.monotonic()
    if module in _cache and (now - _cache_ts.get(module, 0)) < _CACHE_TTL_S:
        return _cache[module]

    # Read from DB
    result = _read_from_db(module, db)
    _cache[module] = result
    _cache_ts[module] = now
    return result


def _read_from_db(module: str, db: Session | None = None) -> dict:
    """Read active model from DB. Returns default if no row found."""
    from app.core.llm_router import SONNET

    defaults = {"provider": "anthropic", "model": SONNET}

    if db is None:
        try:
            from app.core.database import SessionLocal
            db = SessionLocal()
            try:
                return _query_active(db, module) or defaults
            finally:
                db.close()
        except Exception:
            return defaults

    return _query_active(db, module) or defaults


def _query_active(db: Session, module: str) -> dict | None:
    row = (
        db.query(ActiveModelConfig)
        .filter(ActiveModelConfig.module == module, ActiveModelConfig.is_active == True)
        .first()
    )
    if row:
        return {"provider": row.provider, "model": row.model_name, "config_id": row.id}
    return None


def activate_model(
    db: Session,
    module: str,
    provider: str,
    model_name: str,
    activated_by: str,
) -> ActiveModelConfig:
    """
    Activate a model for a module. Deactivates the previous active config.
    Returns the new active config row.
    """
    # Deactivate current
    current = (
        db.query(ActiveModelConfig)
        .filter(ActiveModelConfig.module == module, ActiveModelConfig.is_active == True)
        .first()
    )

    new_config = ActiveModelConfig(
        module=module,
        provider=provider,
        model_name=model_name,
        is_active=True,
        activated_by=activated_by,
    )
    db.add(new_config)
    db.flush()

    if current:
        current.is_active = False
        current.deactivated_at = _now()
        current.replaced_by_id = new_config.id

    db.flush()
    invalidate_cache(module)

    log.info("model_config: activated module=%s provider=%s model=%s by=%s", module, provider, model_name, activated_by)
    return new_config


def rollback_model(db: Session, module: str, rolled_back_by: str) -> dict:
    """
    Rollback to the previous model config for a module.
    Returns: {"status": "rolled_back", "model": ...} or {"status": "no_previous"}
    """
    # Find current active
    current = (
        db.query(ActiveModelConfig)
        .filter(ActiveModelConfig.module == module, ActiveModelConfig.is_active == True)
        .first()
    )
    if not current:
        return {"status": "no_active_config"}

    # Find the row that this one replaced (the previous active)
    previous = (
        db.query(ActiveModelConfig)
        .filter(
            ActiveModelConfig.module == module,
            ActiveModelConfig.is_active == False,
            ActiveModelConfig.replaced_by_id == current.id,
        )
        .first()
    )
    if not previous:
        return {"status": "no_previous_config"}

    # Deactivate current
    current.is_active = False
    current.deactivated_at = _now()

    # Reactivate previous
    previous.is_active = True
    previous.deactivated_at = None
    previous.replaced_by_id = None

    db.flush()
    invalidate_cache(module)

    log.info("model_config: rolled back module=%s to=%s by=%s", module, previous.model_name, rolled_back_by)
    return {
        "status": "rolled_back",
        "module": module,
        "restored_model": previous.model_name,
        "restored_provider": previous.provider,
    }


def get_all_active_configs(db: Session) -> list[dict]:
    """Return all active model configs for visibility."""
    rows = db.query(ActiveModelConfig).filter(ActiveModelConfig.is_active == True).all()
    return [
        {
            "id": r.id,
            "module": r.module,
            "provider": r.provider,
            "model": r.model_name,
            "activated_at": r.activated_at.isoformat() + "Z" if r.activated_at else None,
            "activated_by": r.activated_by,
        }
        for r in rows
    ]
