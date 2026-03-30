"""Tests for persistent model config — activation, rollback, router integration."""
import pytest
from unittest.mock import patch, MagicMock

from app.models.active_model_config import ActiveModelConfig
from app.services.model_config import (
    activate_model,
    rollback_model,
    get_active_model,
    get_all_active_configs,
    invalidate_cache,
    _CACHE_TTL_S,
)
from app.core.llm_router import SONNET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    """Create an in-memory SQLite session with only the active_model_configs table."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    engine = create_engine("sqlite:///:memory:")
    ActiveModelConfig.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _seed(db, module="orchestrator", provider="anthropic", model="claude-sonnet-4-20250514"):
    row = ActiveModelConfig(
        module=module,
        provider=provider,
        model_name=model,
        is_active=True,
        activated_by="seed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def test_activate_creates_new_row():
    db = _make_db()
    seed = _seed(db)

    new = activate_model(db, "orchestrator", "anthropic", "claude-opus-4-20250514", "operator")
    db.commit()

    assert new.is_active is True
    assert new.model_name == "claude-opus-4-20250514"

    # Old row deactivated
    db.refresh(seed)
    assert seed.is_active is False
    assert seed.replaced_by_id == new.id
    assert seed.deactivated_at is not None


def test_activate_without_previous():
    db = _make_db()
    new = activate_model(db, "new_module", "openai", "gpt-4o", "operator")
    db.commit()

    assert new.is_active is True
    assert new.module == "new_module"


def test_activate_multiple_times():
    db = _make_db()
    _seed(db)
    v2 = activate_model(db, "orchestrator", "anthropic", "v2", "op")
    db.commit()
    v3 = activate_model(db, "orchestrator", "anthropic", "v3", "op")
    db.commit()

    actives = db.query(ActiveModelConfig).filter(
        ActiveModelConfig.module == "orchestrator",
        ActiveModelConfig.is_active == True,
    ).all()
    assert len(actives) == 1
    assert actives[0].id == v3.id


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def test_rollback_restores_previous():
    db = _make_db()
    seed = _seed(db, model="sonnet-original")
    activate_model(db, "orchestrator", "anthropic", "opus-new", "operator")
    db.commit()

    result = rollback_model(db, "orchestrator", "operator")
    db.commit()

    assert result["status"] == "rolled_back"
    assert result["restored_model"] == "sonnet-original"

    # Verify DB state
    active = db.query(ActiveModelConfig).filter(
        ActiveModelConfig.module == "orchestrator",
        ActiveModelConfig.is_active == True,
    ).first()
    assert active.model_name == "sonnet-original"


def test_rollback_no_previous():
    db = _make_db()
    _seed(db)  # Only one row, no previous

    result = rollback_model(db, "orchestrator", "operator")
    assert result["status"] == "no_previous_config"


def test_rollback_no_active():
    db = _make_db()
    result = rollback_model(db, "nonexistent", "operator")
    assert result["status"] == "no_active_config"


# ---------------------------------------------------------------------------
# get_active_model
# ---------------------------------------------------------------------------

def test_get_active_model_from_db():
    db = _make_db()
    _seed(db, model="test-model")
    invalidate_cache()

    result = get_active_model("orchestrator", db)
    assert result["model"] == "test-model"
    assert result["provider"] == "anthropic"


def test_get_active_model_returns_default_when_empty():
    db = _make_db()
    invalidate_cache()

    result = get_active_model("nonexistent", db)
    assert result["provider"] == "anthropic"
    assert result["model"] == SONNET


# ---------------------------------------------------------------------------
# get_all_active_configs
# ---------------------------------------------------------------------------

def test_get_all_active_configs():
    db = _make_db()
    _seed(db, module="orchestrator")
    _seed(db, module="bugfix_proposal")

    configs = get_all_active_configs(db)
    modules = {c["module"] for c in configs}
    assert "orchestrator" in modules
    assert "bugfix_proposal" in modules


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_invalidation():
    db = _make_db()
    _seed(db, model="cached-model")
    invalidate_cache()

    # First read populates cache
    r1 = get_active_model("orchestrator", db)
    assert r1["model"] == "cached-model"

    # Activate new model (invalidates cache internally)
    activate_model(db, "orchestrator", "anthropic", "new-model", "op")
    db.commit()

    r2 = get_active_model("orchestrator", db)
    assert r2["model"] == "new-model"


# ---------------------------------------------------------------------------
# Router integration — _get_persistent_model reads from model_config
# ---------------------------------------------------------------------------

def test_router_reads_persistent_config():
    """select_model() should use the persistent config for base model."""
    from app.core.llm_router import select_model, _get_persistent_model

    with patch("app.core.llm_router._get_persistent_model", return_value="custom-model"):
        sel = select_model(module="bugfix_proposal")
        assert sel.model == "custom-model"


def test_router_escalation_overrides_persistent():
    """Escalation (previous_failed) always uses Opus regardless of persistent config."""
    from app.core.llm_router import select_model, OPUS

    sel = select_model(module="bugfix_proposal", previous_failed=True)
    assert sel.model == OPUS
    assert sel.escalation is True


def test_router_persistent_fallback_on_error():
    """If DB is unavailable, router falls back to constants."""
    from app.core.llm_router import _get_persistent_model

    with patch("app.services.model_config.get_active_model", side_effect=Exception("db down")):
        invalidate_cache()
        result = _get_persistent_model("orchestrator", "anthropic")
        assert result == SONNET
