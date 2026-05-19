"""Contract tests for sentry_triage.reclassify_noise_incidents (born
2026-05-19).

The structural close of the "manual one-shot reclassify-per-commit"
gap that let #271 (`OperationalError server closed the connection
unexpectedly`, a class-3 noise message) sit as `triaged` forever
because it was stored in the 3.5h commit→PM2-reload race window after
the c4e429e filter landed.

These pin the eventually-consistent invariants so a future refactor
cannot silently regress them:
  - matched non-terminal noise → noise_dropped
  - REAL bugs are NEVER swept (non-vacuity — the whole safety anchor)
  - terminal dispositions (linked/resolved/resolved_by_fix/ignored)
    are NEVER reverted (operator/fix wins over the predicate)
  - idempotent (converges in one pass, no thrash)
  - bounded (max_per_cycle cap honored)

Uses the conftest SAVEPOINT-wrapped `db` fixture (real Postgres
dialect, zero production leak) — per feedback_test_hermeticity_prod_db.
"""
from __future__ import annotations

from app.models.sentry_incident import SentryIncident
from app.services.sentry_triage import reclassify_noise_incidents


def _mk(db, *, mid, status="triaged", error_type=None,
        error_title=None, raw_subject=None):
    inc = SentryIncident(
        source_message_id=mid,
        source_type="sentry_webhook",
        status=status,
        error_type=error_type,
        error_title=error_title,
        raw_subject=raw_subject,
    )
    db.add(inc)
    db.flush()
    return inc


# Ground-truthed real strings (2026-05-19 sentry_incidents inspection)
_LOADTEST_SSL = (
    "shopify_client: network error GET products.json "
    "shop=_loadtest_00019.myshopify.com: [SSL: CERTIFICATE_VERIFY_FAILED] "
    "certificate verify failed: Hostname mismatch (_ssl.c:1000)"
)
_DB_RESTART = (
    "OperationalError: (psycopg2.OperationalError) server closed the "
    "connection unexpectedly"
)
_WARMING = "HTTPException: dashboard warming — retry shortly"


class TestReclassifySweepsNoise:
    def test_loadtest_ssl_triaged_row_reclassified(self, db):
        inc = _mk(db, mid="t1", error_title=_LOADTEST_SSL)
        out = reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "noise_dropped"
        assert out["reclassified"] >= 1

    def test_db_restart_271_class_reclassified(self, db):
        """The exact #271 case — class-3 message stored as `triaged`
        during the deploy-race window. The recurring sweep self-heals
        it without a manual UPDATE."""
        inc = _mk(db, mid="t2", error_type="OperationalError",
                  error_title=_DB_RESTART, raw_subject=_DB_RESTART)
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "noise_dropped"

    def test_warming_503_reclassified(self, db):
        inc = _mk(db, mid="t3", error_type="HTTPException",
                  error_title=_WARMING)
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "noise_dropped"

    def test_received_and_parsed_also_swept(self, db):
        a = _mk(db, mid="t4a", status="received", error_title=_LOADTEST_SSL)
        b = _mk(db, mid="t4b", status="parsed", error_title=_WARMING)
        reclassify_noise_incidents(db)
        db.refresh(a)
        db.refresh(b)
        assert a.status == "noise_dropped"
        assert b.status == "noise_dropped"


class TestNonVacuityRealBugsNeverSwept:
    """The safety anchor. A real bug is by construction NOT an
    is_noise match — if this ever flips, a real merchant incident
    would be silently buried."""

    def test_real_nameerror_triaged_untouched(self, db):
        inc = _mk(db, mid="r1", error_type="NameError",
                  error_title="NameError: name 'revenue' is not defined",
                  raw_subject="NameError: name 'revenue' is not defined")
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "triaged"

    def test_real_db_schema_bug_untouched(self, db):
        inc = _mk(db, mid="r2", error_type="OperationalError",
                  error_title='OperationalError: (psycopg2.errors.'
                  'UndefinedColumn) column "foo" does not exist')
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "triaged"

    def test_real_merchant_tls_untouched(self, db):
        inc = _mk(db, mid="r3",
                  error_title="shopify_client: network error GET "
                  "products.json shop=acme.myshopify.com: [SSL: "
                  "CERTIFICATE_VERIFY_FAILED] (_ssl.c:1000)")
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "triaged"


class TestTerminalStatusNeverReverted:
    """An operator/fix disposition always wins over the noise
    predicate — a noise-shaped message that was deliberately
    linked/resolved/ignored stays that way."""

    def test_linked_noise_row_not_reverted(self, db):
        inc = _mk(db, mid="x1", status="linked", error_title=_LOADTEST_SSL)
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "linked"

    def test_resolved_by_fix_not_reverted(self, db):
        inc = _mk(db, mid="x2", status="resolved_by_fix",
                  error_title=_DB_RESTART)
        reclassify_noise_incidents(db)
        db.refresh(inc)
        assert inc.status == "resolved_by_fix"

    def test_ignored_and_parse_error_not_reverted(self, db):
        a = _mk(db, mid="x3a", status="ignored", error_title=_WARMING)
        b = _mk(db, mid="x3b", status="parse_error",
                error_title=_LOADTEST_SSL)
        reclassify_noise_incidents(db)
        db.refresh(a)
        db.refresh(b)
        assert a.status == "ignored"
        assert b.status == "parse_error"


class TestIdempotentAndBounded:
    def test_idempotent_second_pass_zero(self, db):
        _mk(db, mid="i1", error_title=_LOADTEST_SSL)
        _mk(db, mid="i2", error_title=_WARMING)
        first = reclassify_noise_incidents(db)
        assert first["reclassified"] >= 2
        second = reclassify_noise_incidents(db)
        assert second["reclassified"] == 0  # converged, no thrash

    def test_max_per_cycle_caps_scan(self, db):
        for i in range(5):
            _mk(db, mid=f"b{i}", error_title=_LOADTEST_SSL)
        out = reclassify_noise_incidents(db, max_per_cycle=2)
        assert out["scanned"] <= 2
