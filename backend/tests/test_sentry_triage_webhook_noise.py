"""
Integration tests for `sentry_triage.ingest_webhook` noise-denylist.

Born 2026-05-13 after Agent-review surfaced that the inbound webhook
path had no end-to-end test for the new Step 2b noise filter — only
the `is_noise` / `is_shutdown_signal_type` helpers were tested in
isolation. A future revert of the Step 2b block would have passed
silently. These tests drive realistic Sentry webhook payloads through
the actual `ingest_webhook` function and assert the wire-in works.
"""
from __future__ import annotations

# The conftest `db` fixture provides a SAVEPOINT-wrapped session against
# _test_engine — every flush rolls back at test teardown so no row
# leaks to production. The ad-hoc `SessionLocal()` fixture this file
# previously declared bypassed that SAVEPOINT and left rows in prod
# (per `feedback_test_hermeticity_prod_db.md`). Removed 2026-05-13
# Agent audit close — use the conftest fixture by argument name.


def _payload(
    *,
    title: str,
    meta_type: str | None = None,
    culprit: str = "agent_worker.py at 0x7f",
    event_id: str | None = None,
) -> dict:
    """Build a minimal Sentry webhook payload shape that ingest_webhook expects."""
    issue: dict = {
        "id": "test-issue-001",
        "title": title,
        "culprit": culprit,
    }
    if meta_type is not None:
        issue["metadata"] = {"type": meta_type}
    return {
        "data": {
            "issue": issue,
            "event": {
                "event_id": event_id or "evt-test-001",
            },
        },
    }


class TestIngestWebhookNoiseDenylist:
    """Lock the Step 2b wire-in: real Sentry payloads with
    shutdown-signal titles MUST be dropped before storage."""

    def test_bare_keyboard_interrupt_title_dropped(self, db):
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(title="KeyboardInterrupt", event_id="evt-bare-1"),
            sentry_event_id="evt-bare-1",
        )
        assert out["status"] == "noise_dropped"
        assert out["incident_id"] is None
        # NO row was stored in sentry_incidents
        from sqlalchemy import text
        cnt = db.execute(
            text("SELECT COUNT(*) FROM sentry_incidents WHERE source_message_id = :k"),
            {"k": "sentry_wh:evt-bare-1"},
        ).scalar()
        assert cnt == 0

    def test_colon_suffix_title_dropped(self, db):
        # AGENT-REVIEW FINDING: pre-fix exact-match-only let this slip
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(
                title="KeyboardInterrupt: signal received during sleep",
                event_id="evt-colon-1",
            ),
            sentry_event_id="evt-colon-1",
        )
        assert out["status"] == "noise_dropped"

    def test_metadata_type_match_dropped_even_if_title_is_label(self, db):
        # Sentry's canonical bare-class type lives at issue.metadata.type.
        # Even if title is e.g. localized or labeled, metadata.type
        # must be checked.
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(
                title="Worker shutdown signal",
                meta_type="SystemExit",
                event_id="evt-meta-1",
            ),
            sentry_event_id="evt-meta-1",
        )
        assert out["status"] == "noise_dropped"

    def test_asyncio_cancelled_error_dropped(self, db):
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(
                title="asyncio.CancelledError",
                event_id="evt-async-1",
            ),
            sentry_event_id="evt-async-1",
        )
        assert out["status"] == "noise_dropped"

    def test_secret_class_500_dropped(self, db):
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(
                title="RuntimeError: OPS_API_KEY not configured",
                event_id="evt-secret-1",
            ),
            sentry_event_id="evt-secret-1",
        )
        assert out["status"] == "noise_dropped"


class TestIngestWebhookRealBugsNotDropped:
    """Conservative guard — the noise filter MUST NOT silently drop
    real bugs. Substring `KeyboardInterrupt` inside a different
    exception's message (e.g. caught-and-rethrown) is NOT noise."""

    def test_real_runtime_error_not_dropped(self, db):
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(
                title="RuntimeError: caught KeyboardInterrupt during cleanup",
                meta_type="RuntimeError",
                event_id="evt-real-1",
            ),
            sentry_event_id="evt-real-1",
        )
        assert out["status"] != "noise_dropped"
        # An incident IS stored for this real bug
        assert out["incident_id"] is not None
        db.rollback()  # cleanup — don't pollute the DB

    def test_key_error_not_dropped(self, db):
        from app.services.sentry_triage import ingest_webhook
        out = ingest_webhook(
            db=db,
            payload=_payload(
                title="KeyError: 'shop_domain'",
                meta_type="KeyError",
                event_id="evt-real-2",
            ),
            sentry_event_id="evt-real-2",
        )
        assert out["status"] != "noise_dropped"
        db.rollback()
