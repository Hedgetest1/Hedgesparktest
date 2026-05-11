"""brain_immutability_and_artifact_hash

Senior+++ close 2026-05-11 of two competitor-CTO audit findings on the
per-shop learning engine moat:

  Finding #3 (outcome_status immutability): brain_decisions.outcome_status
  was app-stamp + audit_log best-effort. A direct UPDATE could rewrite a
  stamped outcome and the hash chain could break if the audit_log write
  failed. Senior+++ fix: DB-level TRIGGER prevent_outcome_status_update
  raises on any attempt to change outcome_status from a non-NULL value
  (NULL → set is allowed; set → different is not). Cannot be bypassed
  by app code or operator psql session.

  Finding cosmetic (profile_version semantic): the column was being used
  as both "upsert counter" and "model version" — confused semantics that
  let the memo claim "v117" be cosmetic-true while actually meaning
  "115th upsert" (no hash change). Senior+++ fix: ADD COLUMN
  model_artifact_hash CHAR(64) — sha256 of (learned_thresholds +
  baselines + nudge_scores). sip_engine.upsert_sip recomputes the hash;
  profile_version increments ONLY when the hash changes (real model
  state change), otherwise stays. The memo claim becomes truthful.

Revision ID: aa7_brain_immutability_hash
Revises: aa6_bi_readonly_role
"""
from alembic import op
import sqlalchemy as sa


revision = "aa7_brain_immutability_hash"
down_revision = "aa6_bi_readonly_role"
branch_labels = None
depends_on = None


# Trigger function: prevent_outcome_status_update.
#
# Allowed transitions:
#   - NULL → any value (initial outcome stamp)
# Blocked transitions:
#   - non-NULL → different value (post-stamp rewrite, including
#     non-NULL → NULL "unset" attempts)
#
# Rationale: brain_decisions is the immutable forensic ledger for
# every brain decision. The outcome stamp ("effective" / "ineffective"
# / "neutral" / "evaluation_failed") is the closing entry. Once
# stamped, audit chain integrity REQUIRES it to never change. The
# hash chain in audit_log is best-effort (writes can fail); the DB
# trigger is the hard guarantee.
_TRIGGER_FN_SQL = """
CREATE OR REPLACE FUNCTION prevent_outcome_status_update()
RETURNS trigger AS $$
BEGIN
    IF OLD.outcome_status IS NOT NULL
       AND (NEW.outcome_status IS NULL
            OR NEW.outcome_status != OLD.outcome_status) THEN
        RAISE EXCEPTION
            'brain_decisions.outcome_status is immutable once stamped '
            '(decision_id=% old=% new=%)',
            OLD.id, OLD.outcome_status, NEW.outcome_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_BIND_SQL = """
DROP TRIGGER IF EXISTS trg_prevent_outcome_status_update
    ON brain_decisions;
CREATE TRIGGER trg_prevent_outcome_status_update
    BEFORE UPDATE ON brain_decisions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_outcome_status_update();
"""

_TRIGGER_DROP_SQL = """
DROP TRIGGER IF EXISTS trg_prevent_outcome_status_update
    ON brain_decisions;
DROP FUNCTION IF EXISTS prevent_outcome_status_update();
"""


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────
    # Part 1: outcome_status immutability TRIGGER (audit finding #3)
    # ──────────────────────────────────────────────────────────────
    op.execute(_TRIGGER_FN_SQL)
    op.execute(_TRIGGER_BIND_SQL)

    # ──────────────────────────────────────────────────────────────
    # Part 2: model_artifact_hash column on SIP (cosmetic finding)
    # ──────────────────────────────────────────────────────────────
    op.add_column(
        "store_intelligence_profiles",
        sa.Column(
            "model_artifact_hash",
            sa.CHAR(64),
            nullable=True,
            comment=(
                "sha256 hex of (learned_thresholds + baselines + "
                "nudge_scores). profile_version increments only when "
                "this changes — otherwise the upsert is a no-op for "
                "version semantics."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("store_intelligence_profiles", "model_artifact_hash")
    op.execute(_TRIGGER_DROP_SQL)
