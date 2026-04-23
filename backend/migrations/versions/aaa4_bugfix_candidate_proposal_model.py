"""bugfix_candidates: proposal_model column

Revision ID: aaa4_bugfix_candidate_proposal_model
Revises: aaa3_evolution_proposal_provenance
Create Date: 2026-04-23

TIER_2 per CLAUDE.md §10 (migrations/) + blanket TIER01_TIER02 approval.

Why
---
Earlier in the 2026-04-23 session we fixed the observability gap on
BugFixCandidate.proposal_provider (commit 86ebaa3). `_call_llm` now
returns (text, actual_provider, actual_model) but the `actual_model`
half was discarded because no column existed to hold it.

Adding `proposal_model` completes the provenance pair, matching:
  - EvolutionProposal.proposal_provider/proposal_model (shipped d80d9b0)
  - MetaReview.model_used (already present)
  - ModelUpgradeProposal.candidate_provider/candidate_model (already present)

Enables:
  - Per-model success-rate analytics (Sonnet-4.6 vs Opus-4.7 patch quality)
  - Cost attribution at the per-candidate level
  - Retroactive comparison when we migrate to future Claude versions

Schema
------
  proposal_model VARCHAR(64) NULL  — e.g. 'claude-sonnet-4-6'

Nullable to keep historical rows clean. Backfill would require
inferring from commit timestamp + routing rules; not worth the cost
for a non-critical observability field.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "aaa4_bugfix_candidate_proposal_model"
down_revision: Union[str, Sequence[str], None] = "aaa3_evolution_proposal_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bugfix_candidates",
        sa.Column("proposal_model", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "proposal_model")
