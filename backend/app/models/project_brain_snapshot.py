"""
ProjectBrainSnapshot — persistent, structured system knowledge.

Each row is a point-in-time snapshot of everything the reviewer layer
needs to make informed assessments: codebase index, runtime state,
domain criticality, and a reference to the active constitution version.

snapshot_type: full | codebase | runtime
    full = codebase index + runtime state (daily refresh)
    codebase = codebase-only refresh
    runtime = runtime-only refresh (cheaper, can run more often)

codebase_json structure:
    {
        "files": [{"path", "domain", "criticality", "lines", "has_test"}...],
        "domains": {"billing": {"criticality": "critical", "file_count": N}...},
        "stats": {"total_files", "total_lines", "critical_files", "services", "models", "apis"}
    }

runtime_json structure:
    {
        "alerts": {"total", "critical", "warning", "recent": [...]},
        "bugfixes": {"open", "applied", "failed", "recent": [...]},
        "merges": {"total", "healthy", "regressed", "recent": [...]},
        "evolution": {"open", "by_risk": {}, "gc_summary": {}},
        "model_config": {"modules": {module: {provider, model}}},
        "system_vitals": {from build_system_summary},
        "llm_budget": {from get_usage_summary},
        "support_incidents": {"open", "recent": [...]}
    }
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProjectBrainSnapshot(Base):
    __tablename__ = "project_brain_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))
    snapshot_type = Column(String(16), nullable=False)        # full | codebase | runtime

    # Structured knowledge (JSON)
    codebase_json = Column(Text, nullable=True)               # codebase index
    runtime_json = Column(Text, nullable=True)                # runtime state

    # Summary stats (queryable without parsing JSON)
    total_files = Column(Integer, nullable=True)
    critical_files = Column(Integer, nullable=True)
    open_alerts = Column(Integer, nullable=True)
    open_bugfixes = Column(Integer, nullable=True)
    open_evolution = Column(Integer, nullable=True)

    # Constitution version tag — links to the code-defined constitution
    constitution_version = Column(String(16), nullable=False, default="v1", server_default="v1")

    __table_args__ = (
        Index("ix_brain_snapshots_type_created", "snapshot_type", "created_at"),
    )
