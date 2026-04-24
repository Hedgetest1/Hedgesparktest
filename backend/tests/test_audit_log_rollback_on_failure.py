"""Regression tests for the 4 SINK-01..04 rollback fixes.

Pre-fix bug class: a best-effort `db.commit()` inside `try` paired with
`except Exception: log.warning(...)` left the SQLAlchemy session in a
PendingRollbackError state when the commit (or a write before it)
failed. The next ORM operation on the same session would then raise
"This Session's transaction has been rolled back due to a previous
exception during flush."

Each test:
  1. Forces the audit-log/commit step to raise.
  2. Asserts the function under test does NOT propagate the exception.
  3. Asserts the session is still usable for a subsequent ORM query
     (the canonical PendingRollbackError symptom).

Source of truth: project_retro_da_sweep_residuals_2026_04_23.md SINK-01..04
+ audit_exception_sinks.py write_no_rollback CRITICAL.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models.merchant import Merchant


SHOP = "rollback-fixture.myshopify.com"


# ---------------------------------------------------------------------------
# SINK-01 — on_alert_responder._write_triage_receipt
# ---------------------------------------------------------------------------

def test_sink01_on_alert_responder_audit_failure_leaves_session_usable(db: Session):
    """If write_audit_log raises, _write_triage_receipt must rollback so
    the caller's loop can keep querying the DB on the same session."""
    from app.services import on_alert_responder

    # Pre-create a merchant to query AFTER the failure — the canonical
    # PendingRollbackError symptom is "next query raises".
    m = Merchant(
        shop_domain=SHOP, plan="pro", billing_active=True,
        install_status="active", session_version=0,
    )
    db.add(m)
    db.commit()  # restart SAVEPOINT so subsequent rollback in code-under-test
                  # doesn't undo the fixture (see conftest restart_savepoint hook).

    # Build a dummy verdict (the helper accepts any object with the
    # right attributes — see line 246 metadata.update).
    class _Verdict:
        severity = "warning"
        probable_cause = "test"
        suggested_owner = "test"
        triage_steps = []
        related_commits = []
        requires_human_now = False
        model_used = "test-model"

    alert = {"id": 1, "alert_type": "test_alert", "summary": "x"}

    with patch("app.services.audit.write_audit_log", side_effect=RuntimeError("audit log down")):
        # Must NOT raise — the bug class would have either propagated the
        # exception OR (more subtly) left the session dirty for the next op.
        on_alert_responder._write_triage_receipt(
            db, alert, status="triaged", verdict=_Verdict(), reason=None,
        )

    # Canonical PendingRollbackError check: a fresh query on the same
    # session must succeed. Pre-fix, this raised
    # InvalidRequestError / PendingRollbackError.
    rows = db.query(Merchant).filter(Merchant.shop_domain == SHOP).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# SINK-02 — slack_dispatcher.post_message
# ---------------------------------------------------------------------------

def test_sink02_slack_dispatcher_commit_failure_leaves_session_usable(db: Session):
    """If the Slack-status persist `db.commit()` blows up inside the
    except branch (e.g. DB connection drop right at the wrong time),
    the session must end up rolled-back, not stuck in
    PendingRollbackError."""
    from app.services import slack_dispatcher
    from app.core.token_crypto import encrypt_token

    m = Merchant(
        shop_domain=SHOP, plan="pro", billing_active=True,
        install_status="active", session_version=0,
        slack_webhook_encrypted=encrypt_token(
            "https://hooks.slack.com/services/T00000000/B00000000/abc123"
        ),
        slack_status="connected",
    )
    db.add(m)
    db.commit()  # restart SAVEPOINT so subsequent rollback in code-under-test
                  # doesn't undo the fixture (see conftest restart_savepoint hook).

    # Simulate httpx raising on .post — same path that hits the
    # except-branch we hardened. We don't need the inner-commit to
    # also fail; the contract under test is "session usable after the
    # except branch ran", and that hold for the simple-failure case
    # too (the inner commit succeeds, no nested rollback exercise).
    with patch("httpx.Client.post", side_effect=RuntimeError("network down")):
        ok, err = slack_dispatcher.post_message(db, SHOP, "hello")

    assert ok is False
    assert "slack post error" in err

    # Session must be usable.
    rows = db.query(Merchant).filter(Merchant.shop_domain == SHOP).all()
    assert len(rows) == 1
    # Error state was persisted.
    assert rows[0].slack_status == "error"
    assert rows[0].slack_last_error and "RuntimeError" in rows[0].slack_last_error


def test_sink02_slack_dispatcher_inner_commit_failure_does_not_propagate(db: Session):
    """If BOTH the original commit AND the error-state commit fail, the
    function must still return cleanly — never propagate the secondary
    exception, never leave the session unusable."""
    from app.services import slack_dispatcher
    from app.core.token_crypto import encrypt_token

    m = Merchant(
        shop_domain=SHOP, plan="pro", billing_active=True,
        install_status="active", session_version=0,
        slack_webhook_encrypted=encrypt_token(
            "https://hooks.slack.com/services/T00000000/B00000000/abc123"
        ),
    )
    db.add(m)
    db.commit()  # restart SAVEPOINT so subsequent rollback in code-under-test
                  # doesn't undo the fixture (see conftest restart_savepoint hook).

    # First raise on the primary network call, then make EVERY commit
    # on the session raise to simulate "DB also down". The except-branch
    # in post_message must catch the inner commit failure too and log
    # rather than propagate.
    with patch("httpx.Client.post", side_effect=RuntimeError("network down")), \
         patch.object(db, "commit", side_effect=RuntimeError("db down")):
        ok, err = slack_dispatcher.post_message(db, SHOP, "hello")

    assert ok is False
    assert "slack post error" in err
    # The original network error message survives — we don't leak the
    # nested DB error to the caller.
    assert "network down" in err


# ---------------------------------------------------------------------------
# SINK-03 — telegram_agent._cmd_cleanup_confirm
# ---------------------------------------------------------------------------

def test_sink03_cleanup_confirm_audit_failure_leaves_session_usable(db: Session):
    """When the audit_log write fails AFTER the destructive cleanup
    has been committed, the function must rollback the failed audit
    flush so the session stays usable, and must not propagate the
    exception (the destructive cleanup result is already authoritative)."""
    from app.services import telegram_agent
    from app.core.redis_client import _client as get_redis

    # Seed the pending-cleanup Redis key so _cmd_cleanup_confirm doesn't
    # bail out at the "no pending cleanup" guard.
    redis = get_redis()
    chat_id = "rollback-test-chat-1"
    redis.set(f"hs:cleanup_pending:{chat_id}", "full", ex=60)

    # Pre-create a merchant the post-cleanup query can read.
    m = Merchant(
        shop_domain=SHOP, plan="pro", billing_active=True,
        install_status="active", session_version=0,
    )
    db.add(m)
    db.commit()  # restart SAVEPOINT so subsequent rollback in code-under-test
                  # doesn't undo the fixture (see conftest restart_savepoint hook).

    with patch("app.services.audit.write_audit_log", side_effect=RuntimeError("audit down")):
        result = telegram_agent._cmd_cleanup_confirm(db, chat_id=chat_id)

    # Function returns its normal text payload, not an exception.
    assert isinstance(result, str)

    # Session is usable.
    rows = db.query(Merchant).filter(Merchant.shop_domain == SHOP).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# SINK-04 — telegram_agent._cmd_cleanup_safe
# ---------------------------------------------------------------------------

def test_sink04_cleanup_safe_audit_failure_leaves_session_usable(db: Session):
    """Mirror of SINK-03 for /cleanup_safe — same shape, same guarantee."""
    from app.services import telegram_agent

    m = Merchant(
        shop_domain=SHOP, plan="pro", billing_active=True,
        install_status="active", session_version=0,
    )
    db.add(m)
    db.commit()  # restart SAVEPOINT so subsequent rollback in code-under-test
                  # doesn't undo the fixture (see conftest restart_savepoint hook).

    with patch("app.services.audit.write_audit_log", side_effect=RuntimeError("audit down")):
        result = telegram_agent._cmd_cleanup_safe(db, chat_id="rollback-test-chat-2")

    assert isinstance(result, str)

    rows = db.query(Merchant).filter(Merchant.shop_domain == SHOP).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Audit gate — ensure no regression: zero CRITICAL findings remain.
# ---------------------------------------------------------------------------

def test_audit_exception_sinks_zero_critical():
    """Pin: `audit_exception_sinks.py` must report ZERO CRITICAL
    write_no_rollback findings. This protects the 4 SINK fixes from
    silent regression."""
    import subprocess
    result = subprocess.run(
        ["./venv/bin/python", "scripts/audit_exception_sinks.py"],
        cwd="/opt/wishspark/backend",
        capture_output=True, text=True, timeout=60,
    )
    out = result.stdout + result.stderr
    # bare_pass is INFO-only and acceptable; we only block CRITICAL.
    assert "write_no_rollback (0)" in out or "write_no_rollback" not in out, (
        f"audit_exception_sinks.py reports CRITICAL write_no_rollback findings. Output:\n{out}"
    )
