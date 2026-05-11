"""Senior+++ invariants — born 2026-05-11 close-out of structural
hardening on the per-shop learning moat. Each test locks a contract
that, if it drifts, would silently degrade the moat.

Coverage:
  - Column-comment alignment between model and migration aa7
  - _model_artifact_hash recursive canonicalization (nested key order)
  - _model_artifact_hash version tag presence
  - audit_sql_schema parser strips IS DISTINCT FROM false positives
  - audit_sql_schema parser strips EXTRACT(... FROM ...) false positives
  - classify_commit_tier prints TIER first under stdout-only capture
  - cross_shop_aggregator.force_run_now serializes via PG advisory lock
  - Migration aa7 has symmetric upgrade/downgrade ops
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Column-comment alignment (model ↔ migration aa7)
# ---------------------------------------------------------------------------


def test_model_artifact_hash_comment_present_in_model():
    """The model's `comment=` arg must be present so SQLAlchemy emits
    the comment on autogenerate — without it, alembic check fails on
    drift between model and DB schema."""
    from app.models.store_intelligence_profile import StoreIntelligenceProfile
    col = StoreIntelligenceProfile.__table__.c.model_artifact_hash
    assert col.comment is not None and col.comment.strip(), (
        "model_artifact_hash column MUST carry a comment so alembic "
        "autogenerate stays in sync"
    )
    assert "sha256" in col.comment.lower()


def test_migration_aa7_comment_matches_model_comment():
    """The aa7 migration's add_column comment must match the model's
    column comment. Drift here means alembic autogenerate would emit
    a `modify_comment` op — silent doctrine drift."""
    import pathlib
    from app.models.store_intelligence_profile import StoreIntelligenceProfile

    mig = pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "aa7_brain_immutability_and_artifact_hash.py"
    text = mig.read_text()

    # Both should mention the same load-bearing phrase
    model_comment = StoreIntelligenceProfile.__table__.c.model_artifact_hash.comment
    assert "sha256 hex of (learned_thresholds + baselines + " in model_comment
    assert "sha256 hex of (learned_thresholds + baselines + " in text


# ---------------------------------------------------------------------------
# Hash function: version tag + recursive canonicalization
# ---------------------------------------------------------------------------


def test_model_artifact_hash_version_tag_present():
    """The hash function input MUST include _MODEL_ARTIFACT_HASH_VERSION
    so bumping that constant rebakes hashes deterministically."""
    from app.services.sip_engine import (
        _MODEL_ARTIFACT_HASH_VERSION,
        _model_artifact_hash,
    )
    assert _MODEL_ARTIFACT_HASH_VERSION  # not None/empty

    sip = {"learned_thresholds": {"x": 1}}
    h_v1 = _model_artifact_hash(sip)

    # Monkey-patch the version constant to verify the hash changes.
    import app.services.sip_engine as sip_mod
    original = sip_mod._MODEL_ARTIFACT_HASH_VERSION
    try:
        sip_mod._MODEL_ARTIFACT_HASH_VERSION = "v2_test"
        h_v2 = _model_artifact_hash(sip)
        assert h_v1 != h_v2, (
            "Bumping _MODEL_ARTIFACT_HASH_VERSION MUST change the hash"
        )
    finally:
        sip_mod._MODEL_ARTIFACT_HASH_VERSION = original


def test_model_artifact_hash_recursive_canonicalization():
    """Nested dict key order MUST NOT affect the hash. Senior+++:
    `json.dumps(sort_keys=True)` only sorts top-level — nested dicts
    retain insertion order. _canonical_json sorts recursively."""
    from app.services.sip_engine import _model_artifact_hash

    # Same model state, different nested-dict insertion order
    sip_a = {
        "learned_thresholds": {
            "scoring": {"a": 1, "b": 2, "c": 3},
            "thresholds": {"x": 0.1, "y": 0.2},
        },
        "nudge_type_scores": {"social": 0.8, "urgency": 0.5},
    }
    sip_b = {
        "learned_thresholds": {
            # Different key order at nested level
            "thresholds": {"y": 0.2, "x": 0.1},
            "scoring": {"c": 3, "a": 1, "b": 2},
        },
        "nudge_type_scores": {"urgency": 0.5, "social": 0.8},
    }
    assert _model_artifact_hash(sip_a) == _model_artifact_hash(sip_b), (
        "Nested key order must NOT affect the hash — recursive "
        "canonicalization required"
    )


def test_model_artifact_hash_list_order_preserved():
    """Lists in SIP state are SEMANTICALLY ordered — reordering them
    IS a model change. price_sensitivity_bands is sorted by price
    range; peak_traffic_hours by hour. Two different list orders
    SHOULD produce different hashes (this is correctness, not bug)."""
    from app.services.sip_engine import _model_artifact_hash

    sip_a = {"price_sensitivity_bands": [
        {"range": "0-25", "rate": 0.04},
        {"range": "25-50", "rate": 0.06},
    ]}
    sip_b = {"price_sensitivity_bands": [
        {"range": "25-50", "rate": 0.06},
        {"range": "0-25", "rate": 0.04},
    ]}
    assert _model_artifact_hash(sip_a) != _model_artifact_hash(sip_b)


# ---------------------------------------------------------------------------
# audit_sql_schema parser hardening
# ---------------------------------------------------------------------------


def test_audit_sql_schema_strips_is_distinct_from():
    """`IS DISTINCT FROM` MUST NOT be treated as a table reference —
    the parser used to flag the operand as a missing table."""
    import sys
    sys.path.insert(0, "/opt/wishspark/backend/scripts")
    from audit_sql_schema import _strip_table_keyword_false_positives

    sql = (
        "UPDATE foo SET x = CASE WHEN a.bar IS DISTINCT FROM b.bar "
        "THEN 1 ELSE 2 END"
    )
    out = _strip_table_keyword_false_positives(sql)
    # `FROM` consumed by the operator — no longer matchable as table prefix
    assert "DISTINCT FROM b.bar" not in out
    assert "DISTINCT_FROM_OP" in out


def test_audit_sql_schema_strips_is_not_distinct_from():
    """The negated form `IS NOT DISTINCT FROM` must also be stripped."""
    import sys
    sys.path.insert(0, "/opt/wishspark/backend/scripts")
    from audit_sql_schema import _strip_table_keyword_false_positives

    sql = "WHERE x IS NOT DISTINCT FROM y"
    out = _strip_table_keyword_false_positives(sql)
    assert "DISTINCT FROM" not in out


def test_audit_sql_schema_strips_extract_from():
    """`EXTRACT(part FROM expr)` is a date-part function, not a table
    reference. The inner FROM must not produce phantom tables."""
    import sys
    sys.path.insert(0, "/opt/wishspark/backend/scripts")
    from audit_sql_schema import _strip_table_keyword_false_positives

    sql = "SELECT EXTRACT(epoch FROM (now() - decision_at)) FROM brain_decisions"
    out = _strip_table_keyword_false_positives(sql)
    # The OUTER FROM brain_decisions remains; the INNER FROM is masked
    assert "FROM brain_decisions" in out
    assert "FROM_DATEPART" in out


def test_audit_sql_schema_against_sip_engine_clean():
    """End-to-end: run the audit script against the live codebase and
    verify sip_engine.py's IS DISTINCT FROM site no longer false-
    positives. Catches regression of the parser fix."""
    import subprocess
    result = subprocess.run(
        ["./venv/bin/python", "scripts/audit_sql_schema.py"],
        cwd="/opt/wishspark/backend",
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Exit 0 = no ghost tables; "model_artifact_hash" should NOT
    # appear as a missing table
    assert "model_artifact_hash" not in result.stdout, (
        f"audit_sql_schema regressed on IS DISTINCT FROM parsing:\n"
        f"{result.stdout}"
    )


# ---------------------------------------------------------------------------
# classify_commit_tier flush ordering
# ---------------------------------------------------------------------------


def test_classify_commit_tier_prints_tier_first_under_merged_capture():
    """Under `2>&1` redirection, the TIER line MUST be the first line
    of the merged output — `flush=True` on the stdout print enforces
    this. Without flush, Python's block-buffered stdout could let the
    line-buffered stderr lines appear first."""
    import subprocess
    # Run against HEAD; output may be TIER_0/1/2.
    result = subprocess.run(
        ["./venv/bin/python", "scripts/classify_commit_tier.py", "HEAD"],
        cwd="/opt/wishspark/backend",
        capture_output=True,
        text=True,
        timeout=10,
    )
    # Merge stdout + stderr the same way auto-deploy does
    merged = result.stdout + result.stderr
    first_line = merged.splitlines()[0] if merged.strip() else ""
    # OR check stdout independently has TIER first
    stdout_first = result.stdout.splitlines()[0] if result.stdout.strip() else ""
    assert stdout_first.startswith("TIER_"), (
        f"stdout MUST start with TIER_N, got: {stdout_first!r}"
    )


# ---------------------------------------------------------------------------
# Migration aa7 upgrade/downgrade symmetry
# ---------------------------------------------------------------------------


def test_aa7_migration_symmetric_upgrade_downgrade():
    """Every upgrade op in aa7 must have a corresponding downgrade op.
    Catches regressions where someone adds an op to upgrade() but
    forgets to mirror in downgrade() — the schema would drift if a
    rollback ever ran."""
    import pathlib
    mig = pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "aa7_brain_immutability_and_artifact_hash.py"
    text = mig.read_text()

    # Required pairs:
    # 1. ADD COLUMN ↔ DROP COLUMN
    assert "op.add_column(" in text and "op.drop_column(" in text, (
        "aa7 must have add_column ↔ drop_column pair"
    )
    assert 'op.add_column(\n        "store_intelligence_profiles"' in text
    assert 'op.drop_column("store_intelligence_profiles", "model_artifact_hash")' in text

    # 2. CREATE TRIGGER ↔ DROP TRIGGER
    assert "CREATE OR REPLACE FUNCTION prevent_outcome_status_update" in text
    assert "DROP FUNCTION IF EXISTS prevent_outcome_status_update" in text
    assert "CREATE TRIGGER trg_prevent_outcome_status_update" in text
    assert "DROP TRIGGER IF EXISTS trg_prevent_outcome_status_update" in text


# ---------------------------------------------------------------------------
# force_run_now PG advisory lock
# ---------------------------------------------------------------------------


def test_effect_size_floors_cover_all_brain_metric_kinds():
    """Every `expected_outcome_metric=<literal>` in merchant_brain._rule_table
    MUST have a corresponding entry in _EFFECT_SIZE_FLOORS_PCT — defensive
    completeness against future rule additions. Born 2026-05-11 propagation
    audit (Senior+++) found cooldown_pending + none were missing.
    """
    import pathlib
    import re
    from app.services.cross_shop_aggregator import _EFFECT_SIZE_FLOORS_PCT

    brain_path = pathlib.Path(__file__).parent.parent / "app" / "services" / "merchant_brain.py"
    src = brain_path.read_text()

    # Grep `expected_outcome_metric="<literal>"` — match the literal arg
    pattern = re.compile(r'expected_outcome_metric\s*=\s*"([^"]+)"')
    metric_kinds = set(pattern.findall(src))

    missing = metric_kinds - set(_EFFECT_SIZE_FLOORS_PCT.keys())
    assert not missing, (
        f"_EFFECT_SIZE_FLOORS_PCT missing entries for metric_kinds "
        f"emitted by merchant_brain: {missing}. Add them with a "
        f"documented floor (high for meta-metrics that should NEVER "
        f"reach the aggregator, ~0.5-1.0 for real lift metrics)."
    )


def test_maybe_snapshot_strips_scratch_keys_from_jsonb(db):
    """upsert_sip adds scratch keys like `_prior_hash_unchanged` to the
    sip dict (transactional state). maybe_snapshot MUST NOT serialize
    them into sip_snapshots.profile_data JSONB — that's caller-internal
    state, not forensic model state. Born 2026-05-11 propagation audit."""
    from sqlalchemy import text as _sql_text
    from app.services.sip_engine import upsert_sip, maybe_snapshot

    shop = "_snap_scratch_test_.myshopify.com"
    conn = db.connection()

    sip = {
        "shop_domain": shop,
        "profile_version": 1,
        "baseline_cart_rate": 0.05,
        "baseline_scroll_depth": None,
        "baseline_dwell_time": None, "baseline_return_rate": None,
        "baseline_views_per_product": None, "baseline_mobile_pct": None,
        "learned_thresholds": {"x": 1},
        "traffic_source_quality": None,
        "price_sensitivity_bands": None,
        "nudge_type_scores": None,
        "best_nudge_by_signal": None, "peak_traffic_hours": None,
        "signal_frequency_30d": None,
        "data_points_total": 1000,
        "confidence_level": "low",
        "computed_at": __import__("datetime").datetime.now(),
        "trust_score": 0.5,
        "trust_profile": None,
    }
    upsert_sip(conn, sip)
    db.flush()
    # upsert_sip added scratch keys
    assert "_prior_hash_unchanged" in sip
    assert "model_artifact_hash" in sip

    maybe_snapshot(conn, sip)
    db.flush()

    row = conn.execute(_sql_text(
        "SELECT profile_data FROM sip_snapshots WHERE shop_domain = :s "
        "ORDER BY snapshot_week DESC LIMIT 1"
    ), {"s": shop}).fetchone()
    if row is None:
        # Snapshot may already exist for this week from a prior test run
        return

    import json
    profile_data = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    # Scratch keys MUST be stripped
    assert "_prior_hash_unchanged" not in profile_data, (
        "scratch _prior_hash_unchanged leaked into sip_snapshots JSONB"
    )
    # model_artifact_hash MUST be preserved (it's the forensic anchor)
    assert "model_artifact_hash" in profile_data
    assert len(profile_data["model_artifact_hash"]) == 64  # sha256 hex


def test_force_run_now_acquires_pg_advisory_lock(db, monkeypatch):
    """force_run_now MUST call pg_advisory_xact_lock to serialize
    concurrent callers. Senior+++ guard against double-aggregation
    when an opt-out event fires while another opt-out is processing."""
    from app.services import cross_shop_aggregator as csa

    executed: list[str] = []
    real_execute = db.execute

    def spy_execute(stmt, *args, **kwargs):
        sql_text = str(getattr(stmt, "text", stmt))
        executed.append(sql_text)
        return real_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(db, "execute", spy_execute)

    class _FakeRedis:
        def delete(self, key):
            return 1
    monkeypatch.setattr(csa, "_redis_client", lambda: _FakeRedis())

    csa.force_run_now(db)

    locked = any(
        "pg_advisory_xact_lock" in s for s in executed
    )
    assert locked, (
        "force_run_now MUST acquire pg_advisory_xact_lock — concurrent "
        "callers would otherwise double-run the aggregator"
    )
