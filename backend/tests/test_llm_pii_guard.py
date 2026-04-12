"""Tests for llm_pii_guard — runtime wall on outgoing LLM prompts."""
from __future__ import annotations

import pytest

from app.core.llm_pii_guard import (
    LLMPayloadViolation,
    assert_clean,
    check_for_pii,
    get_violation_count_7d,
    sanitize,
)


# ---------- Positive detections ----------

def test_detects_email():
    findings = check_for_pii("Contact alice@example.com for details")
    assert any(f["kind"] == "email" for f in findings)


def test_detects_shopify_access_token():
    findings = check_for_pii("token = shpat_abc123def456ghi789jkl012mno345")
    assert any(f["kind"] == "shopify_token" for f in findings)


def test_detects_anthropic_key():
    findings = check_for_pii("key=sk-ant-api03-ABCDE12345FGHIJ67890XYZ")
    assert any(f["kind"] == "anthropic_key" for f in findings)


def test_detects_openai_key():
    findings = check_for_pii("openai: sk-ABCDE12345FGHIJ67890XYZ")
    assert any(f["kind"] == "openai_key" for f in findings)


def test_detects_resend_key():
    findings = check_for_pii("Resend: re_LUabc123def456ghi789jkl012")
    assert any(f["kind"] == "resend_key" for f in findings)


def test_detects_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWxpY2UifQ.signature_value_here"
    findings = check_for_pii(f"authorization: Bearer {jwt}")
    assert any(f["kind"] in ("jwt", "bearer_token") for f in findings)


def test_detects_bearer_token():
    findings = check_for_pii("Authorization: Bearer ABCdef123456789xyz0aAbBcCdDeE")
    assert any(f["kind"] == "bearer_token" for f in findings)


def test_detects_iban():
    findings = check_for_pii("IBAN: DE89370400440532013000")
    assert any(f["kind"] == "iban" for f in findings)


def test_detects_credit_card_shape():
    findings = check_for_pii("card 4242 4242 4242 4242")
    assert any(f["kind"] == "credit_card" for f in findings)


def test_detects_password_assignment():
    findings = check_for_pii('password = "supersecret123"')
    assert any(f["kind"] == "password_like" for f in findings)


# ---------- Negatives ----------

def test_clean_text_has_no_findings():
    text = (
        "The system has 120 active merchants. RARS average is 245 EUR. "
        "Heartbeat pass rate is 24/24. No PII should be detected here."
    )
    assert check_for_pii(text) == []


def test_shop_domain_not_flagged_by_default():
    """Tenant identifier; legitimate to send aggregated metrics with it."""
    findings = check_for_pii("shop_domain=acme.myshopify.com had 50 orders")
    # By default shop_domain is NOT flagged
    assert all(f["kind"] != "shop_domain" for f in findings)


def test_shop_domain_flagged_when_strict_mode(monkeypatch):
    monkeypatch.setenv("LLM_PII_GUARD_REDACT_SHOPS", "1")
    findings = check_for_pii("shop_domain=acme.myshopify.com had 50 orders")
    assert any(f["kind"] == "shop_domain" for f in findings)


def test_none_input_returns_empty():
    assert check_for_pii(None) == []
    assert check_for_pii("") == []


# ---------- Sanitize ----------

def test_sanitize_replaces_email_with_marker():
    text = "Customer is alice@example.com, order 42"
    out, findings = sanitize(text)
    assert "alice@example.com" not in out
    assert "<redacted:email>" in out
    assert len(findings) >= 1


def test_sanitize_clean_text_unchanged():
    text = "RARS = 100 EUR"
    out, findings = sanitize(text)
    assert out == text
    assert findings == []


def test_sanitize_multiple_findings_all_replaced():
    text = "alice@example.com wrote sk-ant-api03-DEADBEEFCAFE1234567890 key"
    out, _ = sanitize(text)
    assert "alice@example.com" not in out
    assert "sk-ant-api03-DEADBEEFCAFE1234567890" not in out
    assert out.count("<redacted:") >= 2


# ---------- assert_clean ----------

def test_assert_clean_passes_clean_text():
    assert_clean("RARS stable at 300 EUR") is None


def test_assert_clean_raises_on_email():
    with pytest.raises(LLMPayloadViolation) as exc_info:
        assert_clean("Notify alice@example.com")
    assert "email" in str(exc_info.value)
    # Never echo the snippet in the exception message
    assert "alice@example.com" not in str(exc_info.value)


def test_assert_clean_raises_on_api_key():
    with pytest.raises(LLMPayloadViolation):
        assert_clean("debug: sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def test_assert_clean_counter_increments():
    before = get_violation_count_7d()
    try:
        assert_clean("bob@example.com")
    except LLMPayloadViolation:
        pass
    after = get_violation_count_7d()
    if after == before:
        pytest.skip("redis unavailable")
    assert after == before + 1


# ---------- Integration with _call_llm ----------

def test_call_llm_blocks_when_payload_has_pii(monkeypatch):
    """bugfix_pipeline._call_llm must return empty when PII is detected."""
    from app.services import bugfix_pipeline as bp

    # Prevent the budget + router path from running — the guard must
    # fire before either of them get a chance.
    def _fail_budget(*a, **kw):
        raise AssertionError("budget should not be checked after pii block")

    monkeypatch.setattr("app.core.llm_budget.check_budget", lambda *a, **kw: (True, "ok"))
    # If the guard fails to fire, the test will try to reach this
    # provider call — force it to raise so we notice.
    monkeypatch.setattr(bp, "_call_provider",
                       lambda *a, **kw: (_ for _ in ()).throw(
                           AssertionError("provider should not be called")))

    result = bp._call_llm(
        "Debug context: customer alice@example.com requested refund",
        patch_risk_tier=0,
        file_count=1,
    )
    assert result == ""
