"""operator_blocklist.py — Block outbound merchant-facing channels for
operator/dev shops (founder-owned tenants used for end-to-end testing).

Born 2026-05-06 after the founder reported receiving a real merchant
digest email at `tedialarana@gmail.com` ("REVENUE 3.090 THIS WEEK -
5 ORDERS, 20.674$ at risk"). Root cause: the founder's dev tenant
`hedgespark-dev.myshopify.com` is a real merchant row in the DB
(plan=pro, billing_active=true) used for interactive /app testing.
The merchant_digest cycle correctly filters by billing_active=true
→ matches the dev tenant → emails the founder.

The fundamental product rule: **operator/dev shops never receive
merchant-facing email**. The founder uses the dashboard interactively;
they never want a daily/weekly digest, a re-engagement email, an
onboarding nudge, or a lifecycle email. They DO want GDPR Art. 15
data exports if they exercise that flow themselves (legally
required) — that path is documented as exempt.

Architectural shape: ONE predicate `is_operator_dev_shop()` consumed
at the email_orchestrator boundary AND at every direct `send_email()`
caller in scripts/. A single source of truth so adding a new operator
tenant is one-line + ripple downstream automatically.

Companion: `audit_operator_dev_shop_no_outbound.py` verifies post-
fact (against email_event rows / resend logs) that no message was
sent in the trailing 7-day window. Re-fires periodic via
invariant_monitor.

To add a new dev tenant: edit _OPERATOR_DEV_SHOPS below + ship a
commit. The audit will detect any send within 7 days of the addition
(which would mean a path is bypassing the guard).
"""
from __future__ import annotations

# Hardcoded — no env var. Operator shops are a small finite set
# managed via code review; an env-var override would let an attacker
# bypass merchant emails by toggling the env. The doctrine "0 errori"
# requires this to be code-reviewed.
_OPERATOR_DEV_SHOPS: frozenset[str] = frozenset({
    # Founder's primary dev tenant. Used for /app interactive testing,
    # OAuth flow validation, and end-to-end integration. Must NEVER
    # receive merchant emails (digest / lifecycle / re-engagement /
    # silence / onboarding / breach-self / etc.).
    "hedgespark-dev.myshopify.com",
})


# Email addresses that should NEVER receive a merchant-shaped
# message regardless of which shop it's attached to. Defense in
# depth — even if the merchant row is misconfigured (a dev tenant
# loses its blocklist tag temporarily), the email-address gate
# still catches the leak. Lowercased for case-insensitive match.
_OPERATOR_EMAIL_ADDRESSES: frozenset[str] = frozenset({
    "tedialarana@gmail.com",
})


def is_operator_dev_shop(shop_domain: str | None) -> bool:
    """Return True iff the shop is an operator/dev tenant that must
    not receive merchant-facing email. Conservative — returns False
    on None/empty input so a missing shop_domain doesn't accidentally
    block a real send."""
    if not shop_domain or not isinstance(shop_domain, str):
        return False
    return shop_domain.strip().lower() in _OPERATOR_DEV_SHOPS


def is_operator_email(to_email: str | None) -> bool:
    """Return True iff the destination address is a known operator/
    founder address. Used as belt-and-suspenders gate at send-time
    in case the shop-level filter is bypassed (e.g. direct send_email
    call from a script that constructs the recipient list manually)."""
    if not to_email or not isinstance(to_email, str):
        return False
    return to_email.strip().lower() in _OPERATOR_EMAIL_ADDRESSES


def operator_dev_shops() -> frozenset[str]:
    """Read-only accessor for diagnostics / audits."""
    return _OPERATOR_DEV_SHOPS


def operator_emails() -> frozenset[str]:
    """Read-only accessor for diagnostics / audits."""
    return _OPERATOR_EMAIL_ADDRESSES


# Alert types that describe a merchant-funnel-state problem — by
# definition, the operator dev tenant always looks "stuck" on these
# because the founder uses /app to test, not to convert. Firing them
# for operator shops pollutes ops_alerts with noise.
#
# Pre-2026-05-13 these alerts persisted unresolved (e.g. id=137153
# "Events flowing but 0 signals after 1195h" on hedgespark-dev). The
# detection is correct — funnel IS stuck — but the conclusion does not
# apply to an operator tenant.
#
# Real-bug alerts (LLM failures, webhook errors, etc.) STILL fire for
# operator shops because we want to see code-level breakage during
# testing. The set below is intentionally narrow.
_OPERATOR_IRRELEVANT_ALERT_TYPES: frozenset[str] = frozenset({
    "slow_activation",
    "pixel_abandonment",
    "onboarding_drift",
    "onboarding_slow_progress",
    "onboarding_confusion",
    "onboarding_stuck",
    "onboarding_failed",
    "merchant_silent",
    "low_conversion_rate",
})


def is_operator_silenced_alert(
    shop_domain: str | None, alert_type: str | None
) -> bool:
    """Return True iff the alert is a merchant-funnel-state class AND
    the target shop is an operator dev tenant. Used by
    `app.services.alerting.write_alert` to drop these specific noisy
    combinations before persistence.

    Conservative: returns False if either input is missing OR the
    alert type is outside the curated set."""
    if not shop_domain or not alert_type:
        return False
    if alert_type.strip().lower() not in _OPERATOR_IRRELEVANT_ALERT_TYPES:
        return False
    return is_operator_dev_shop(shop_domain)


def operator_silenced_alert_types() -> frozenset[str]:
    """Read-only accessor for the operator-irrelevant alert type set."""
    return _OPERATOR_IRRELEVANT_ALERT_TYPES
