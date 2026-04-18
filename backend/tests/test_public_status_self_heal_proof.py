"""
Tests for the public /status self_heal_proof section (MA-3).

Locks the contract that /public/status always returns a
`self_heal_proof` object with 7d/30d counts of autonomous-pipeline
actions from the append-only audit_log. Competitors cannot publish
this without exposing that their "self-healing" is marketing copy —
we publish receipts, so we keep the shape stable.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _clear_public_status_cache():
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.delete("hs:public_status:v1")
    except Exception:
        pass


class TestSelfHealProof:

    def test_response_shape_always_includes_self_heal_proof(self, db):
        """Even with zero rows, the key must exist + carry the documented
        shape so the frontend never crashes on missing fields."""
        _clear_public_status_cache()
        from app.main import app
        r = TestClient(app).get("/public/status")
        assert r.status_code == 200
        j = r.json()
        assert "self_heal_proof" in j
        proof = j["self_heal_proof"]
        assert set(proof.keys()) == {
            "autonomous_fixes_7d",
            "autonomous_fixes_30d",
            "last_fix_at",
        }
        assert isinstance(proof["autonomous_fixes_7d"], int)
        assert isinstance(proof["autonomous_fixes_30d"], int)
        # last_fix_at is None or an ISO string — both valid.
        assert proof["last_fix_at"] is None or isinstance(proof["last_fix_at"], str)

    def test_30d_count_is_at_least_7d_count(self, db):
        """Window-ordering invariant: whatever rows the endpoint sees,
        the 30-day count MUST be >= the 7-day count. This is a
        mathematical guarantee regardless of the underlying data. If a
        future refactor inverts the window or miscalculates the
        threshold, this test fails.

        NOTE: we cannot seed audit_log rows from the test session and
        observe them via the endpoint because /public/status uses a
        separate engine.connect() outside the test SAVEPOINT (a
        deliberate choice for caching + read-only isolation — see
        feedback_test_hermeticity_prod_db.md). So we assert invariants
        that hold against WHATEVER data lives in the prod-shared DB,
        rather than a count against seeded fixtures.
        """
        _clear_public_status_cache()
        from app.main import app
        proof = TestClient(app).get("/public/status").json()["self_heal_proof"]
        assert proof["autonomous_fixes_30d"] >= proof["autonomous_fixes_7d"], (
            "30-day window count must include all 7-day window rows"
        )
        assert proof["autonomous_fixes_7d"] >= 0
        assert proof["autonomous_fixes_30d"] >= 0

    def test_last_fix_at_recency_invariant(self, db):
        """If ANY fixes exist in the 30d window, last_fix_at must be
        non-null. If zero fixes, last_fix_at must be null. Invariant
        again — works regardless of shared-DB state.
        """
        _clear_public_status_cache()
        from app.main import app
        proof = TestClient(app).get("/public/status").json()["self_heal_proof"]
        if proof["autonomous_fixes_30d"] > 0:
            assert proof["last_fix_at"] is not None, (
                f"fixes exist but last_fix_at is null: {proof}"
            )
        else:
            assert proof["last_fix_at"] is None, (
                f"no fixes but last_fix_at populated: {proof}"
            )

    def test_no_pii_leaks(self, db):
        """Response must NEVER include shop_domain, merchant names, or
        any field that isn't in the documented proof shape. Public-safe
        by construction."""
        _clear_public_status_cache()
        from app.main import app
        j = TestClient(app).get("/public/status").json()
        proof = j["self_heal_proof"]
        forbidden_keys = {
            "shop_domain", "shop", "merchant_id", "actor_name", "target_id",
            "before_state", "after_state", "metadata_json",
        }
        for k in proof.keys():
            assert k not in forbidden_keys, (
                f"self_heal_proof leaked forbidden field: {k}"
            )
