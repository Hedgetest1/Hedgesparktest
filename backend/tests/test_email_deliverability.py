"""Tests for the email deliverability preventer.

Covers:
  - `uses_org_domain()` classifies the three known cases correctly
  - `get_domain_status()` fail-opens when the Resend API is unreachable
  - `get_domain_status()` classifies status=failed / verified correctly
  - `send_email()` short-circuits when domain failed AND org sender
  - `send_email()` passes through when sender is explicit resend.dev
  - Hourly task detects verified ↔ failed flips and calls telegram
  - Hourly task is silent on unchanged state + first observation
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# uses_org_domain
# ---------------------------------------------------------------------------

def test_uses_org_domain_matches_hedgesparkhq():
    from app.services.email_deliverability import uses_org_domain
    assert uses_org_domain("HedgeSpark <dev@hedgesparkhq.com>") is True
    assert uses_org_domain("dev@hedgesparkhq.com") is True
    assert uses_org_domain("digest@hedgesparkhq.com") is True


def test_uses_org_domain_rejects_resend_dev():
    from app.services.email_deliverability import uses_org_domain
    assert uses_org_domain("HedgeSpark <onboarding@resend.dev>") is False
    assert uses_org_domain("onboarding@resend.dev") is False


def test_uses_org_domain_handles_none_and_empty():
    from app.services.email_deliverability import uses_org_domain
    assert uses_org_domain(None) is False
    assert uses_org_domain("") is False


# ---------------------------------------------------------------------------
# get_domain_status — API reachable
# ---------------------------------------------------------------------------

def test_get_domain_status_verified_path():
    from app.services import email_deliverability as ed

    fake_api = {"status": "verified", "name": "hedgesparkhq.com"}
    with patch.object(ed, "_fetch_domain_status", return_value=fake_api), \
         patch.object(ed, "_client", create=True, return_value=None):
        # Force-refresh to skip any cached Redis value.
        out = ed.get_domain_status(force_refresh=True)

    assert out["verified"] is True
    assert out["status"] == "verified"
    assert out["reason"] == ""


def test_get_domain_status_failed_path():
    from app.services import email_deliverability as ed

    fake_api = {"status": "failed", "name": "hedgesparkhq.com"}
    with patch.object(ed, "_fetch_domain_status", return_value=fake_api):
        out = ed.get_domain_status(force_refresh=True)

    assert out["verified"] is False
    assert out["status"] == "failed"
    assert "failed" in out["reason"]


def test_get_domain_status_fail_open_on_api_unreachable():
    """When the Resend API returns None (no key / timeout / 5xx), the
    preventer must fail OPEN so transient API outages never suppress mail."""
    from app.services import email_deliverability as ed

    with patch.object(ed, "_fetch_domain_status", return_value=None):
        out = ed.get_domain_status(force_refresh=True)

    assert out["verified"] is True
    assert out["status"] == "unknown"
    assert out["reason"] == "api_unreachable"


# ---------------------------------------------------------------------------
# send_email — suppression gate
# ---------------------------------------------------------------------------

def test_send_email_suppressed_when_domain_failed_and_org_sender():
    """With DNS failed and default sender (org domain), send_email must
    return None without touching the Resend SDK."""
    import os
    os.environ["RESEND_API_KEY"] = "test_key_ignored"
    from app.core import email as core_email

    with patch("app.services.email_deliverability.is_domain_verified", return_value=False), \
         patch("app.services.email_deliverability.uses_org_domain", return_value=True), \
         patch.object(core_email, "_get_from_address", return_value="HedgeSpark <dev@hedgesparkhq.com>"):
        import resend  # type: ignore
        with patch.object(resend.Emails, "send") as sdk_send:
            result = core_email.send_email(
                to="merchant@example.com",
                subject="test",
                html="<p>hi</p>",
            )

    assert result is None
    assert sdk_send.called is False


def test_send_email_passthrough_when_explicit_resend_dev_sender():
    """Operator scripts that explicitly pass a non-org sender (typically
    onboarding@resend.dev for founder self-test) must flow through even
    when DNS is failed."""
    import os
    os.environ["RESEND_API_KEY"] = "test_key_ignored"
    from app.core import email as core_email

    fake_resp = {"id": "resend_abc_123"}
    # is_domain_verified returns False but the sender is NOT org-domain,
    # so uses_org_domain short-circuits the gate.
    with patch("app.services.email_deliverability.is_domain_verified", return_value=False), \
         patch("app.services.email_deliverability.uses_org_domain", return_value=False):
        import resend  # type: ignore
        with patch.object(resend.Emails, "send", return_value=fake_resp):
            result = core_email.send_email(
                to="tedialarana@gmail.com",
                subject="test",
                html="<p>hi</p>",
                from_address="HedgeSpark <onboarding@resend.dev>",
            )

    assert result == "resend_abc_123"


# ---------------------------------------------------------------------------
# Hourly task — flip detection
# ---------------------------------------------------------------------------

def test_dns_status_task_alerts_on_flip_to_verified():
    from app.workers.tasks import email_dns_status_task as t

    status = {"verified": True, "status": "verified", "reason": "", "fetched_at": 0}
    with patch("app.services.email_deliverability.get_domain_status", return_value=status), \
         patch("app.services.email_deliverability.invalidate_cache"), \
         patch("app.services.email_deliverability.read_last_verified_state", return_value=False), \
         patch("app.services.email_deliverability.write_last_verified_state"), \
         patch("app.services.telegram_agent.send_message") as mock_tg:
        t.run()

    assert mock_tg.called
    msg = mock_tg.call_args[0][0]
    assert "🟢" in msg
    assert "verified" in msg.lower()


def test_dns_status_task_alerts_on_flip_to_failed():
    from app.workers.tasks import email_dns_status_task as t

    status = {"verified": False, "status": "failed", "reason": "resend_status=failed", "fetched_at": 0}
    with patch("app.services.email_deliverability.get_domain_status", return_value=status), \
         patch("app.services.email_deliverability.invalidate_cache"), \
         patch("app.services.email_deliverability.read_last_verified_state", return_value=True), \
         patch("app.services.email_deliverability.write_last_verified_state"), \
         patch("app.services.telegram_agent.send_message") as mock_tg:
        t.run()

    assert mock_tg.called
    msg = mock_tg.call_args[0][0]
    assert "🔴" in msg
    assert "failed" in msg.lower()


def test_dns_status_task_silent_on_unchanged():
    from app.workers.tasks import email_dns_status_task as t

    status = {"verified": True, "status": "verified", "reason": "", "fetched_at": 0}
    with patch("app.services.email_deliverability.get_domain_status", return_value=status), \
         patch("app.services.email_deliverability.invalidate_cache"), \
         patch("app.services.email_deliverability.read_last_verified_state", return_value=True), \
         patch("app.services.email_deliverability.write_last_verified_state"), \
         patch("app.services.telegram_agent.send_message") as mock_tg:
        t.run()

    assert mock_tg.called is False


def test_dns_status_task_silent_on_first_observation():
    """When prev state is None (first ever run), don't spam an alert."""
    from app.workers.tasks import email_dns_status_task as t

    status = {"verified": False, "status": "failed", "reason": "", "fetched_at": 0}
    with patch("app.services.email_deliverability.get_domain_status", return_value=status), \
         patch("app.services.email_deliverability.invalidate_cache"), \
         patch("app.services.email_deliverability.read_last_verified_state", return_value=None), \
         patch("app.services.email_deliverability.write_last_verified_state"), \
         patch("app.services.telegram_agent.send_message") as mock_tg:
        t.run()

    assert mock_tg.called is False


# ---------------------------------------------------------------------------
# Published-DKIM strict-base64 check (audit_email_deliverability.py)
# ---------------------------------------------------------------------------
#
# Context: on 2026-04-22 a DKIM record was pasted into Hostinger with 3
# embedded spaces inside the p= base64. Resend accepted it (lax decoder),
# Gmail rejected it (strict decoder) → every email silent-dropped with
# Resend's last_event still reporting "delivered". These tests lock in
# the preventer against that exact class.

def _run_dkim_check_with_dig_output(dig_output: str):
    """Invoke the audit's DKIM checker with a mocked dig subprocess."""
    from unittest.mock import MagicMock
    import importlib.util
    import os

    audit_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "audit_email_deliverability.py",
    )
    spec = importlib.util.spec_from_file_location("audit_ed", audit_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fake_result = MagicMock(stdout=dig_output, stderr="")
    with patch.object(mod.subprocess, "run", return_value=fake_result):
        return mod._check_published_dkim_strict()


def test_dkim_check_rejects_embedded_whitespace():
    """The exact 2026-04-22 bug: 3 spaces inside base64."""
    ok, reason = _run_dkim_check_with_dig_output(
        '"p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDAUvzm0LoxudxEXjDQPcciK4P4jnyqqAQ+CKzwVw5nh2HyVI/32MjBzgyJWv3hseu02mWfl0T5CfYv   dBRDCI/Sj48ZIaZ5TsHmPiUTvBvdfjDsjsBOsAJ5GMA/veJK/mlxGC5fEWWzo5g8ZnegdPyrKOIXQmThsGA8EgMBhD7mRQIDAQAB"'
    )
    assert ok is False
    assert "whitespace" in reason.lower()


def test_dkim_check_accepts_clean_tagged_record():
    """v=DKIM1; k=rsa; p=<clean base64> — the canonical good form."""
    ok, reason = _run_dkim_check_with_dig_output(
        '"v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDAUvzm0LoxudxEXjDQPcciK4P4jnyqqAQ+CKzwVw5nh2HyVI/32MjBzgyJWv3hseu02mWfl0T5CfYvdBRDCI/Sj48ZIaZ5TsHmPiUTvBvdfjDsjsBOsAJ5GMA/veJK/mlxGC5fEWWzo5g8ZnegdPyrKOIXQmThsGA8EgMBhD7mRQIDAQAB"'
    )
    assert ok is True
    assert reason == ""


def test_dkim_check_accepts_bare_p_value():
    """Resend's short form (no v=DKIM1; tag) is also accepted when clean."""
    ok, reason = _run_dkim_check_with_dig_output(
        '"p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDAUvzm0LoxudxEXjDQPcciK4P4jnyqqAQ+CKzwVw5nh2HyVI/32MjBzgyJWv3hseu02mWfl0T5CfYvdBRDCI/Sj48ZIaZ5TsHmPiUTvBvdfjDsjsBOsAJ5GMA/veJK/mlxGC5fEWWzo5g8ZnegdPyrKOIXQmThsGA8EgMBhD7mRQIDAQAB"'
    )
    assert ok is True


def test_dkim_check_fails_open_on_missing_record():
    """Empty dig output (no TXT) returns False with clear reason."""
    ok, reason = _run_dkim_check_with_dig_output("")
    assert ok is False
    assert "no TXT record" in reason


def test_dkim_check_rejects_malformed_base64():
    """Characters outside the base64 alphabet are caught."""
    ok, reason = _run_dkim_check_with_dig_output(
        '"p=not!valid@base64"'
    )
    assert ok is False
    # Reason could mention either the decode failure or special chars.
    assert "p=" in reason or "base64" in reason.lower()
