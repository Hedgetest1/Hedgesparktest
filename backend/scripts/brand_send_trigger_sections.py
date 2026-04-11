"""
Revenue trigger fix — add section titles with alternating warm/cool accents.
Copy is unchanged. Only adding _section_title() where sections begin.
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _p, _button, _section_title,
)
from app.core.email import send_email

_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
TO = "tedialarana@gmail.com"

product = "Premium Leather Wallet"
carts = 7
weekly_est = 420

subject = f"{product} — {carts} cart adds, 0 purchases"

body = (
    _p(
        f"In the last 24 hours, <strong style='color:#f1f5f9;'>{carts} visitors</strong> "
        f"added <strong style='color:#f1f5f9;'>{product}</strong> to their cart. "
        f"None of them completed checkout.",
    )
    + _p(
        "This is a normal commerce pattern — it signals demand, not a problem. "
        "These visitors chose this product, added it to their cart, "
        "and were close to buying. Something small in the final steps held them back.",
        color="#94a3b8",
    )

    # Section 2: Why this happens (cool/violet)
    + _section_title("Why this happens", accent="cool")
    + _p(
        "Cart-to-checkout drop-off often comes down to small friction points: "
        "an unexpected shipping cost at the last step, a slow-loading payment page, "
        "or a missing trust signal like reviews or a return policy.",
        color="#94a3b8",
    )
    + _p(
        "In most cases, the visitor intended to buy but encountered something "
        "that made them hesitate — a price that looked different from the product page, "
        "a shipping estimate they didn't expect, or a checkout that felt unfamiliar. "
        "These are small fixes with measurable impact.",
        color="#94a3b8",
    )

    # Section 3: What this means for revenue (warm/amber)
    + _section_title("What this means for revenue")
    + _p(
        f"What makes this worth your attention is the volume. "
        f"{carts} carts in 24 hours means this product has real demand. "
        f"At your store's average order value, even a small checkout improvement "
        f"could recover ~<strong style='color:#e2e8f0;'>${weekly_est:,}/week</strong>.",
    )
    + _p(
        "If the friction point is addressed, even a partial recovery — "
        "converting 2 or 3 of those 7 carts — compounds over time.",
        color="#94a3b8",
    )

    # Section 4: What HedgeSpark is doing (cool/violet)
    + _section_title("What HedgeSpark is doing", accent="cool")
    + _p(
        "HedgeSpark has already isolated the behavioral pattern for this product "
        "and identified where visitors are dropping off. "
        "A specific recommendation is ready in your dashboard — "
        "the next step is execution, and it's straightforward.",
        color="#94a3b8",
    )

    + _p(
        "<strong style='color:#c4b5fd;'>HedgeSpark</strong> is monitoring your store continuously.",
        color="#64748b",
    )

    + '<div style="text-align:center;margin:8px 0 0 0;">'
    + _button("See what we found", _DASHBOARD_URL)
    + '</div>'
)

html = _wrap_html(subject, body, show_logo=True)

plain = (
    f"WHAT WE DETECTED\n"
    f"In the last 24 hours, {carts} visitors added {product} to their cart. "
    f"None of them completed checkout.\n"
    f"This is a normal commerce pattern — it signals demand, not a problem.\n\n"
    f"WHY THIS HAPPENS\n"
    f"Cart-to-checkout drop-off often comes down to small friction points: "
    f"unexpected shipping cost, slow payment page, or missing trust signal.\n"
    f"These are small fixes with measurable impact.\n\n"
    f"WHAT THIS MEANS FOR REVENUE\n"
    f"{carts} carts in 24 hours means real demand. Even a small improvement "
    f"could recover ~${weekly_est:,}/week. Converting 2-3 of those 7 carts "
    f"compounds over time.\n\n"
    f"WHAT HEDGESPARK IS DOING\n"
    f"HedgeSpark has already isolated the pattern and identified where visitors "
    f"are dropping off. A specific recommendation is ready in your dashboard.\n\n"
    f"HedgeSpark is monitoring your store continuously.\n\n"
    f"See what we found: {_DASHBOARD_URL}"
)

resend_id = send_email(
    to=TO, subject=subject, html=html, text=plain,
    from_address="HedgeSpark <dev@hedgesparkhq.com>",
)
print(f"{'SENT' if resend_id else 'FAILED'}: {subject} | {resend_id}")
