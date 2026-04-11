"""
brand_send_precise_fixes.py — Two precise fixes only.

1. NO VISITOR ACTIVITY: Remove "Andrea · monitoring" signature.
   Move "HedgeSpark is monitoring your store continuously" ABOVE the CTA button.

2. REVENUE TRIGGER: Remove "Andrea · monitoring" signature.
   Move "HedgeSpark is monitoring your store continuously" ABOVE the CTA button.
   Insert 1 paragraph after opening (normalize).
   Insert 1 paragraph before final (partnership).
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _p, _button, _bullet,
    _section_title, _separator,
)
from app.core.email import send_email


_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
TO = "tedialarana@gmail.com"
SHOP = "Stella & Ivy"


# ═══════════════════════════════════════════════════════════════════════════
# 1. NO VISITOR ACTIVITY — remove Andrea signature, move HedgeSpark line
# ═══════════════════════════════════════════════════════════════════════════

def build_reengagement():
    subject = f"{SHOP} — no visitor data in 14 days"

    body = (
        _p(
            f"HedgeSpark hasn't recorded visitor activity on "
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> since March 25.",
        )
        + _p(
            "This happens — sometimes a theme update disrupts the tracking script, "
            "sometimes things just get busy. Either way, the system noticed and "
            "wanted to flag it for you.",
            color="#94a3b8",
        )
        + _p(
            "When tracking goes silent, the most common cause is the storefront script "
            "not loading — usually after a theme change, an app reinstall, or a "
            "Shopify permission update. Your store is fine. "
            "The tracking connection just needs to be re-established.",
            color="#94a3b8",
        )

        + _section_title("This is a tracking issue, not a store issue", accent="cool")
        + _p(
            "Your Shopify store is running normally. Orders, payments, and customer-facing "
            "pages are not affected. The only thing paused is HedgeSpark's ability to "
            "observe visitor behavior — the analytics layer, not the commerce layer.",
            color="#94a3b8",
        )
        + _p(
            "This happens to a significant number of Shopify stores after theme updates. "
            "It's a known pattern and the fix is straightforward.",
            color="#94a3b8",
        )

        + _section_title("What happens once reconnected")
        + _p(
            "The moment tracking is restored, HedgeSpark resumes collecting visitor data "
            "immediately. No configuration needed — the system picks up automatically.",
        )
        + _p(
            "Any data collected before the gap is still intact. "
            "The only data missing is from the disconnected period itself. "
            "Within 24 hours of reconnection, new insights will begin appearing.",
            color="#94a3b8",
        )

        # FIX: HedgeSpark line moved here, above CTA
        + _p(
            "<strong style='color:#c4b5fd;'>HedgeSpark</strong> is monitoring your store continuously.",
            color="#64748b",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Reconnect tracking", _DASHBOARD_URL)
        + '</div>'

        # FIX: Andrea signature removed. No sign-off after CTA.
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"HedgeSpark hasn't recorded visitor activity on {SHOP} since March 25.\n\n"
        f"This happens — sometimes a theme update disrupts the tracking script, "
        f"sometimes things just get busy. Either way, the system noticed "
        f"and wanted to flag it for you.\n\n"
        f"The most common cause is the storefront script not loading — "
        f"usually after a theme change, app reinstall, or Shopify permission update. "
        f"Your store is fine. The tracking connection just needs to be re-established.\n\n"
        f"THIS IS A TRACKING ISSUE, NOT A STORE ISSUE\n"
        f"Your Shopify store is running normally. Orders, payments, and customer-facing "
        f"pages are not affected. The only thing paused is HedgeSpark's ability to "
        f"observe visitor behavior.\n"
        f"This happens to a significant number of stores after theme updates. "
        f"The fix is straightforward.\n\n"
        f"WHAT HAPPENS ONCE RECONNECTED\n"
        f"Tracking resumes immediately. No configuration needed — the system picks up "
        f"automatically. Data from before the gap is still intact. Within 24 hours "
        f"of reconnection, new insights will begin appearing.\n\n"
        f"HedgeSpark is monitoring your store continuously.\n\n"
        f"Reconnect tracking: {_DASHBOARD_URL}"
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# 2. REVENUE TRIGGER — remove Andrea signature, move HedgeSpark line,
#    insert 2 paragraphs (normalize + partnership)
# ═══════════════════════════════════════════════════════════════════════════

def build_revenue_trigger():
    product = "Premium Leather Wallet"
    carts = 7
    weekly_est = 420

    subject = f"{product} — {carts} cart adds, 0 purchases"

    body = (
        # EXISTING: opening paragraph
        _p(
            f"In the last 24 hours, <strong style='color:#f1f5f9;'>{carts} visitors</strong> "
            f"added <strong style='color:#f1f5f9;'>{product}</strong> to their cart. "
            f"None of them completed checkout.",
        )

        # *** INSERTED: normalize — calm, confident, grounded ***
        + _p(
            "This is a normal commerce pattern — it signals demand, not a problem. "
            "These visitors chose this product, added it to their cart, "
            "and were close to buying. Something small in the final steps held them back.",
            color="#94a3b8",
        )

        # EXISTING: explanation
        + _p(
            "This is a common pattern — it doesn't mean something is broken. "
            "Cart-to-checkout drop-off often comes down to small friction points: "
            "an unexpected shipping cost at the last step, a slow-loading payment page, "
            "or a missing trust signal like reviews or a return policy.",
            color="#94a3b8",
        )

        # EXISTING: volume + revenue
        + _p(
            f"What makes this worth your attention is the volume. "
            f"{carts} carts in 24 hours means this product has real demand. "
            f"At your store's average order value, even a small checkout improvement "
            f"could recover ~<strong style='color:#e2e8f0;'>${weekly_est:,}/week</strong>.",
        )

        # EXISTING: why it happens
        + _p(
            "In most cases, the visitor intended to buy but encountered something "
            "that made them hesitate — a price that looked different from the product page, "
            "a shipping estimate they didn't expect, or a checkout that felt unfamiliar. "
            "These are small fixes with measurable impact.",
            color="#94a3b8",
        )

        # *** INSERTED: partnership — supportive, controlled, intelligent ***
        + _p(
            "HedgeSpark has already isolated the behavioral pattern for this product "
            "and identified where visitors are dropping off. "
            "A specific recommendation is ready in your dashboard — "
            "the next step is execution, and it's straightforward.",
            color="#94a3b8",
        )

        # EXISTING: if resolved
        + _p(
            "If the friction point is addressed, even a partial recovery — "
            "converting 2 or 3 of those 7 carts — compounds over time. "
            "HedgeSpark has identified the specific pattern and prepared a recommendation.",
            color="#94a3b8",
        )

        # FIX: HedgeSpark line moved here, above CTA
        + _p(
            "<strong style='color:#c4b5fd;'>HedgeSpark</strong> is monitoring your store continuously.",
            color="#64748b",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("See what we found", _DASHBOARD_URL)
        + '</div>'

        # FIX: Andrea signature removed. No sign-off after CTA.
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"In the last 24 hours, {carts} visitors added {product} to their cart. "
        f"None of them completed checkout.\n\n"
        f"This is a normal commerce pattern — it signals demand, not a problem. "
        f"These visitors chose this product, added it to their cart, "
        f"and were close to buying. Something small in the final steps held them back.\n\n"
        f"Cart-to-checkout drop-off often comes down to small friction points: "
        f"an unexpected shipping cost, a slow-loading payment page, "
        f"or a missing trust signal like reviews or a return policy.\n\n"
        f"What makes this worth your attention is the volume. "
        f"{carts} carts in 24 hours means real demand. "
        f"At your store's AOV, even a small improvement "
        f"could recover ~${weekly_est:,}/week.\n\n"
        f"In most cases, the visitor intended to buy but encountered something "
        f"that made them hesitate — a price difference, an unexpected shipping "
        f"estimate, or an unfamiliar checkout. These are small fixes with "
        f"measurable impact.\n\n"
        f"HedgeSpark has already isolated the behavioral pattern for this product "
        f"and identified where visitors are dropping off. "
        f"A specific recommendation is ready in your dashboard — "
        f"the next step is execution, and it's straightforward.\n\n"
        f"If the friction is addressed, even converting 2 or 3 of those 7 carts "
        f"compounds over time.\n\n"
        f"HedgeSpark is monitoring your store continuously.\n\n"
        f"See what we found: {_DASHBOARD_URL}"
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# SEND
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sends = [
        ("no_activity", build_reengagement, "HedgeSpark <dev@hedgesparkhq.com>"),
        ("revenue_trigger", build_revenue_trigger, "HedgeSpark <dev@hedgesparkhq.com>"),
    ]

    for name, builder, from_addr in sends:
        subject, html, plain = builder()
        resend_id = send_email(to=TO, subject=subject, html=html, text=plain, from_address=from_addr)
        print(f"{name}: {'SENT' if resend_id else 'FAILED'} | {subject} | {resend_id}")
