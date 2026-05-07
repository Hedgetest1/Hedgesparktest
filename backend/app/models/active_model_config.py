"""
ActiveModelConfig — persistent model selection per module.

One active row per module at a time. Router reads this instead of in-memory dicts.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ActiveModelConfig(Base):
    __tablename__ = "active_model_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    module = Column(String(64), nullable=False)        # orchestrator | bugfix_proposal | evolution_audit
    provider = Column(String(32), nullable=False)
    model_name = Column(String(128), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    activated_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))
    activated_by = Column(String(128), nullable=False)
    deactivated_at = Column(DateTime, nullable=True)
    replaced_by_id = Column(Integer, nullable=True)    # points to the row that replaced this one

    __table_args__ = (
        Index("ix_active_model_module_active", "module", "is_active"),
    )
