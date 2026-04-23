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

# In-process fallback — used only when Redis is unreachable. Under multi-
# worker uvicorn (post 2026-04-23 scaling flip) Redis is the authoritative
# cache so that an `activate_model()` on worker #1 propagates to workers
# #2..#4 within _CACHE_TTL_S, not 4×_CACHE_TTL_S.
_cache: dict[str, dict] = {}  # multi-worker: redis-backed
_cache_ts: dict[str, float] = {}  # multi-worker: redis-backed

_REDIS_KEY_PREFIX = "hs:model_cfg:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def invalidate_cache(module: str | None = None):
    """Clear cache for one or all modules (Redis + in-process)."""
    rc = _redis()
    try:
        if rc is not None:
            if module:
                rc.delete(f"{_REDIS_KEY_PREFIX}:{module}")
            else:
                # Clear all cached modules — SCAN is cheap (tens of keys max)
                for key in rc.scan_iter(match=f"{_REDIS_KEY_PREFIX}:*", count=50):
                    rc.delete(key)
    except Exception as exc:
        log.warning("model_config: redis invalidate failed: %s", exc)

    # Always also clear in-process fallback so Redis-down workers converge
    # at restart.
    if module:
        _cache.pop(module, None)
        _cache_ts.pop(module, None)
    else:
        _cache.clear()
        _cache_ts.clear()


def get_active_model(module: str, db: Session | None = None) -> dict:
    """
    Return the active model config for a module.

    Primary: Redis cache (multi-worker consistent, 5 min TTL).
    Fallback: in-process cache (when Redis unreachable).
    Miss: read from DB, write back to both layers.

    Returns: {"provider": str, "model": str, "config_id": int | None}.
    """
    import json

    rc = _redis()
    # 1) Redis hit path
    if rc is not None:
        try:
            raw = rc.get(f"{_REDIS_KEY_PREFIX}:{module}")
            if raw is not None:
                try:
                    return json.loads(raw)
                except (TypeError, ValueError):
                    pass  # corrupt cache entry — fall through to DB read
        except Exception as exc:
            log.warning("model_config: redis get failed: %s", exc)

    # 2) In-process fallback (Redis down)
    now = time.monotonic()
    if module in _cache and (now - _cache_ts.get(module, 0)) < _CACHE_TTL_S:
        return _cache[module]

    # 3) DB read + populate both cache layers
    result = _read_from_db(module, db)
    _cache[module] = result
    _cache_ts[module] = now
    if rc is not None:
        try:
            rc.setex(f"{_REDIS_KEY_PREFIX}:{module}", _CACHE_TTL_S, json.dumps(result))
        except Exception as exc:
            log.warning("model_config: redis setex failed: %s", exc)
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
