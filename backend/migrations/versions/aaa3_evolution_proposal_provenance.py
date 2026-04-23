"""evolution_proposals: proposal_provider + proposal_model columns

Revision ID: aaa3_evolution_proposal_provenance
Revises: aaa0_slack_integration
Create Date: 2026-04-23

TIER_1 per CLAUDE.md §10 (migrations/) + blanket TIER01_TIER02 approval
active for this sprint.

Why
---
The 2026-04-23 sibling audit (triggered by the bugfix_pipeline E2E
probe) found that monthly_evolution_audit._store_proposals persists
Opus-generated proposals without recording which provider/model
produced them. At monthly cadence with Opus as the only model today
this is tolerable, but it breaks:
  - per-model accuracy analytics (Opus-vs-Sonnet proposal acceptance)
  - cost attribution at the per-proposal level
  - future migration of the audit to a different model (e.g. Claude 4.8)
    without losing the pre-migration historical dataset

Sibling to BugFixCandidate.proposal_provider (shipped 2026-04-23 earlier
in this session). Same semantics: provider is the actual provider
returned by the LLM call; model is the model string.

Schema
------
  proposal_provider VARCHAR(32) NULL  — 'anthropic' | 'openai' | NULL
                                          for legacy rows pre-migration
  proposal_model    VARCHAR(64) NULL  — e.g. 'claude-opus-4-7'

Both nullable to keep the migration cheap (no backfill required for
historical rows — they pre-date the observability requirement).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "aaa3_evolution_proposal_provenance"
down_revision: Union[str, Sequence[str], None] = "aaa0_slack_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "evolution_proposals",
        sa.Column("proposal_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("proposal_model", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evolution_proposals", "proposal_model")
    op.drop_column("evolution_proposals", "proposal_provider")
