"""Regression tests: should_send_email gate in email_orchestrator must
honor ALL `should=False` reasons, not just `complained`.

Bug 2026-05-08 brutal audit: orchestrator only suppressed when
`reason == "complained"`. Three other false-reasons (`never_opened`,
`low_open_rate:X%`, `new_merchant_weekly_cap`) were silently ignored —
the email_type would proceed to send despite the adaptive engagement
check explicitly returning blocked.

These tests pin the contract: every `(False, reason)` tuple from
should_send_email must result in the non-bypass intent being suppressed.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# Each reason that should_send_email may return as (False, reason)
# per app/services/email_performance.py:
_BLOCKING_REASONS = [
    "complained",
    "never_opened",
    "low_open_rate:0.05",
    "new_merchant_weekly_cap",
]


@pytest.mark.parametrize("reason", _BLOCKING_REASONS)
def test_orchestrator_suppresses_on_every_should_send_false_reason(reason, db):
    """Any reason that produces should=False must suppress non-bypass
    intents — not just `complained`."""
    from app.services.email_orchestrator import _resolve_merchant, EmailIntent

    intent = EmailIntent(
        shop_domain="test-adaptive.myshopify.com",
        email_type="lite_morning_digest",  # NOT in _BYPASS_RATE_LIMIT
        to_email="m@test-adaptive.myshopify.com",
        subject="test",
        html="<p>x</p>",
    )

    sent_intents = []
    suppressed_log = []

    def _mock_send(_db, i):
        sent_intents.append(i)
        return True

    def _mock_log_suppressed(_db, i, _reason):
        suppressed_log.append((i, _reason))

    with patch("app.services.email_orchestrator._is_suppressed", return_value=False), \
         patch("app.services.email_orchestrator._is_merchant_paused", return_value=False), \
         patch("app.services.email_performance.should_send_email",
               return_value=(False, reason)), \
         patch("app.services.email_orchestrator._send_intent",
               side_effect=_mock_send), \
         patch("app.services.email_orchestrator._log_suppressed",
               side_effect=_mock_log_suppressed):
        result = _resolve_merchant(db, intent.shop_domain, [intent])

    assert len(sent_intents) == 0, (
        f"reason={reason!r}: intent SENT despite adaptive gate blocking. "
        f"Bug: only `complained` reason was honored, the other 3 "
        f"({_BLOCKING_REASONS!r}) leaked through to _send_intent."
    )
    assert len(suppressed_log) == 1, (
        f"reason={reason!r}: intent must be logged as suppressed; "
        f"got {suppressed_log!r}"
    )
    assert suppressed_log[0][1] == f"adaptive:{reason}", (
        f"reason={reason!r}: log_suppressed called with wrong reason: "
        f"got {suppressed_log[0][1]!r}"
    )
    assert result.get("suppressed") == 1


def test_orchestrator_lets_bypass_intent_through_despite_adaptive_block(db):
    """Bypass intents (auto-responses, transactional) MUST proceed even
    when the adaptive gate blocks normal intents — they're rate-limit-
    immune by design."""
    from app.services.email_orchestrator import _resolve_merchant, EmailIntent
    from app.services.email_orchestrator import _BYPASS_RATE_LIMIT

    # Pick a real bypass type from the constant
    if not _BYPASS_RATE_LIMIT:
        pytest.skip("_BYPASS_RATE_LIMIT empty — no bypass intent to test")
    bypass_type = next(iter(_BYPASS_RATE_LIMIT))

    intent = EmailIntent(
        shop_domain="test-bypass.myshopify.com",
        email_type=bypass_type,
        to_email="m@test-bypass.myshopify.com",
        subject="bypass",
        html="<p>x</p>",
    )

    sent_intents = []

    def _mock_send(_db, i):
        sent_intents.append(i)
        return True

    with patch("app.services.email_orchestrator._is_suppressed", return_value=False), \
         patch("app.services.email_orchestrator._is_merchant_paused", return_value=False), \
         patch("app.services.email_performance.should_send_email",
               return_value=(False, "never_opened")), \
         patch("app.services.email_orchestrator._send_intent",
               side_effect=_mock_send):
        result = _resolve_merchant(db, intent.shop_domain, [intent])

    assert len(sent_intents) == 1, (
        f"bypass intent (type={bypass_type}) was suppressed despite being "
        f"in _BYPASS_RATE_LIMIT — adaptive gate should not block bypass"
    )
    assert result.get("sent") == 1
