"""Regression pins for SEC-MED fail-closed conversions (2026-05-08).

Pre-fix behavior across 4 sites:
  - email_orchestrator.budget_check returned True (fail-OPEN) on any
    Resend-budget-check exception → uncapped email sends during a
    transient budget-infrastructure outage.
  - agent_worker._run_audit_log_integrity_check `pass`-on-exception →
    proceeded with the expensive chain walk during Redis hiccup.
  - dashboard_rate_limit_middleware logged "fail-open" + called next →
    unbounded /pro/* + /merchant/* requests during Redis outage.
  - survey._rate_limit_check returned True on Redis exception → flood
    window combined with PII regex bypass.

Post-fix: all 4 fail-CLOSED with in-process fallback (where applicable)
or hard-skip + alert (email budget, audit-log walk).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


def test_dashboard_rate_limit_local_fallback_caps_at_120():
    """dashboard_rate_limit_middleware in-process fallback must cap
    at 120 requests/60s/bucket when Redis is unavailable."""
    from app.main import _dashboard_rl_local_allow, _DASH_RL_BUCKETS
    bucket = "test-bucket-md5fp:127.0.0.1"
    _DASH_RL_BUCKETS.pop(bucket, None)
    for _ in range(120):
        assert _dashboard_rl_local_allow(bucket) is True
    # 121st must be denied.
    assert _dashboard_rl_local_allow(bucket) is False
    _DASH_RL_BUCKETS.pop(bucket, None)


def test_survey_rate_limit_local_fallback_caps_at_3():
    """survey._rate_limit_check fallback must cap at 3/60s per IP hash."""
    from app.api.survey import _survey_rl_local_check, _SURVEY_RL_LOCAL_BUCKETS
    iphash = "test_ip_hash_2026_05_08"
    _SURVEY_RL_LOCAL_BUCKETS.pop(iphash, None)
    assert _survey_rl_local_check(iphash) is True
    assert _survey_rl_local_check(iphash) is True
    assert _survey_rl_local_check(iphash) is True
    assert _survey_rl_local_check(iphash) is False
    _SURVEY_RL_LOCAL_BUCKETS.pop(iphash, None)


def test_survey_rate_limit_uses_local_fallback_when_redis_none():
    from app.api import survey
    iphash = "test_ip_hash_2026_05_08_local"
    survey._SURVEY_RL_LOCAL_BUCKETS.pop(iphash, None)
    with patch.object(survey, "_redis_client", return_value=None):
        # 3 allowed, 4th blocked.
        for _ in range(3):
            assert survey._rate_limit_check(iphash) is True
        assert survey._rate_limit_check(iphash) is False
    survey._SURVEY_RL_LOCAL_BUCKETS.pop(iphash, None)


def test_survey_rate_limit_uses_local_fallback_on_redis_exception():
    from app.api import survey
    iphash = "test_ip_hash_2026_05_08_exc"
    survey._SURVEY_RL_LOCAL_BUCKETS.pop(iphash, None)
    fake_rc = MagicMock()
    fake_rc.incr.side_effect = RuntimeError("redis kaboom")
    with patch.object(survey, "_redis_client", return_value=fake_rc):
        for _ in range(3):
            assert survey._rate_limit_check(iphash) is True
        assert survey._rate_limit_check(iphash) is False
    survey._SURVEY_RL_LOCAL_BUCKETS.pop(iphash, None)


def test_email_orchestrator_source_contract_fail_closed():
    """Static-source pin: the budget-check try/except in _send_intent
    must indicate fail-closed semantics + `_log_suppressed` +
    `return False` in the exception handler. Pre-fix had only
    `log.warning("fail-open")` and fell through to send.
    """
    import inspect
    from app.services import email_orchestrator
    src = inspect.getsource(email_orchestrator._send_intent)
    assert "RESEND_MONTHLY_LIMIT" in src, "budget check must still be present"
    # Pin: explicit fail-closed sentinel string (the budget-check block
    # writes a `_log_suppressed(db, intent, "email_budget_check_unavailable")`
    # in the except branch, plus the `fail-CLOSED` log marker).
    assert "email_budget_check_unavailable" in src, (
        "email_orchestrator._send_intent budget-check exception path "
        "must call _log_suppressed with `email_budget_check_unavailable` "
        "(fail-CLOSED contract). Reverting to fail-open is a §2.9 "
        "invariant violation (every LLM/email cap is a north-star "
        "invariant)."
    )
    assert "fail-CLOSED for cost protection" in src, (
        "email_orchestrator must carry the explicit 'fail-CLOSED for "
        "cost protection' log marker in the budget-check except path."
    )
