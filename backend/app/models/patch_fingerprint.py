"""
PatchFingerprint — persistent record of attempted patches for dedup/blacklist.

Every patch that reaches apply (success or failure) gets a fingerprint stored.
Before proposing a new patch, the pipeline checks if a similar fingerprint
recently failed — preventing the system from retrying the same bad approach.

Fingerprint is a SHA-256 hash of normalized (sorted file list + title keywords).
This catches "same fix from different source" scenarios that source_ref dedup misses.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PatchFingerprint(Base):
    __tablename__ = "patch_fingerprints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    # SHA-256 hash of normalized patch identity (title + files + diff prefix)
    fingerprint = Column(String(64), nullable=False)

    # SHA-256 hash of normalized diff content (strips whitespace, comments, headers)
    # Used for semantic dedup — catches cosmetically different but structurally identical patches
    diff_fingerprint = Column(String(64), nullable=True)

    # Link back to the candidate that produced this fingerprint
    bugfix_candidate_id = Column(Integer, nullable=False)

    # Outcome of the patch attempt
    # applied | rolled_back | apply_failed | tests_failed | test_timeout
    outcome = Column(String(32), nullable=False)

    # Optional: final measured effectiveness (updated async after 48h)
    # effective | ineffective | inconclusive | NULL (not yet measured)
    measured_outcome = Column(String(32), nullable=True)

    # Failure reason (from bugfix_pipeline)
    failure_reason = Column(Text, nullable=True)

    # Source traceability
    source_type = Column(String(32), nullable=True)
    source_ref = Column(String(256), nullable=True)

    # Domain classification (from project_brain)
    affected_domain = Column(String(64), nullable=True)

    # Files touched (JSON list of paths)
    patch_files = Column(Text, nullable=True)

    # Confidence penalty — decremented on each failure for this fingerprint
    # Starts at 1.0, drops by 0.3 on each failure. At 0.0, fingerprint is blacklisted.
    confidence = Column(Float, nullable=False, default=1.0, server_default="1.0")

    # Learning isolation: classifies the evidence environment.
    # pre_merchant | internal_test | sandbox | real_merchant
    evidence_source = Column(String(32), nullable=True, default="pre_merchant")

    __table_args__ = (
        Index("ix_patch_fp_fingerprint", "fingerprint", "created_at"),
        Index("ix_patch_fp_candidate", "bugfix_candidate_id"),
        Index("ix_patch_fp_outcome", "outcome", "created_at"),
        Index("ix_patch_fp_domain", "affected_domain", "outcome"),
        Index("ix_patch_fp_diff_fingerprint", "diff_fingerprint", "created_at"),
    )
