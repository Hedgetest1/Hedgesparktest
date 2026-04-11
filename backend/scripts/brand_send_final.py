"""
brand_send_final.py — Final 4 emails using the TWO-SYSTEM architecture.

SYSTEM 1: FOUNDATION EMAILS
  - Explain the system, build trust, provide clarity
  - Structured sections, paragraphs + bullets, can be long
  - From: andrea@hedgesparkhq.com (personal, trust-building)
  - Reference: beta_welcome is the GOLD STANDARD

SYSTEM 2: SIGNAL EMAILS
  - React to a specific detected state
  - Short: state → meaning → action
  - From: dev@hedgesparkhq.com (system alerts)
  - Or: digest@hedgesparkhq.com (weekly digest)

Emails:
  1. Welcome       → FOUNDATION (from andrea@)
  2. Revenue trigger → SIGNAL (from dev@)
  3. Weekly digest  → HYBRID (from digest@)
  4. Re-engagement  → SIGNAL (from dev@)
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _heading, _p, _button, _bullet,
    _section_title, _separator, _step,
)
from app.services.brand_voice import validate_email_text, validate_subject_line
from app.core.email import send_email


_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
_SUPPORT_EMAIL = "dev@hedgesparkhq.com"
TO = "tedialarana@gmail.com"
SHOP = "Stella & Ivy"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 1: WELCOME — FOUNDATION
#
# Type: Foundation
# From: andrea@hedgesparkhq.com
# Trigger: Merchant just completed Shopify OAuth install.
#
# This is not a signal email. The merchant just made a decision to install.
# They need to understand what they just activated, what happens next,
# how the system works, and why it matters. This is the first impression.
#
# Structural DNA (replicated from beta_welcome gold standard):
#   - Personal greeting
#   - Opening: what just happened (ground truth)
#   - Section: what HedgeSpark does (explain the system)
#   - Section: what happens next (numbered steps)
#   - Section: your command center (chatbot)
#   - Section: what to expect (timeline)
#   - CTA centered
#   - Personal signature
#
# Alternating section accents: warm (amber) → cool (violet) → warm → cool
# ═══════════════════════════════════════════════════════════════════════════

def build_welcome():
    subject = f"HedgeSpark is live on {SHOP}"

    body = (
        _p("Hi,", color="#f1f5f9")
        + _p(
            f"HedgeSpark is now connected to "
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong>. "
            f"The tracking script is deployed on your storefront and visitor data "
            f"has started flowing into the system."
        )
        + _p(
            "This email walks you through what's happening, "
            "what to expect, and the one step that needs your attention."
        )

        # What's already running
        + _section_title("What's already running")
        + _p(
            "The moment you connected, HedgeSpark activated a lightweight tracking script "
            "on your storefront. It records page views, product interest, and browsing patterns "
            "— no personal data, no impact on page speed."
        )
        + _p(
            "You'll see your first visitor data in the dashboard within minutes. "
            "Over the first 24 hours, the system builds a behavioral baseline for your store: "
            "which products attract attention, where visitors hesitate, and where they leave.",
            color="#94a3b8",
        )

        # The one step that matters
        + _section_title("The one step that needs you", accent="cool")
        + _p(
            "To connect visitor behavior to actual sales, you'll need to install the "
            "<strong style='color:#e2e8f0;'>purchase tracking pixel</strong> on your "
            "order confirmation page."
        )
        + _bullet(
            "Without it: HedgeSpark can analyze behavior but can't attribute revenue"
        )
        + _bullet(
            "With it: full revenue attribution, conversion rates, and ROI measurement"
        )
        + _p(
            "The dashboard walks you through the setup step by step. "
            "It takes under 3 minutes. If you get stuck, the in-app chatbot can "
            "guide you through it live.",
            color="#94a3b8",
        )

        # What happens over the next weeks
        + _section_title("What to expect")
        + _step(
            1, "First 24 hours",
            "Visitor tracking is active. Initial analytics surface: "
            "top products by interest, traffic sources, browsing patterns. "
            "This is your intelligence baseline."
        )
        + _step(
            2, "Days 2–7",
            "HedgeSpark begins identifying behavioral signals — "
            "products with high intent but low conversion, checkout drop-offs, "
            "revenue leaks. Your first actionable insights will appear."
        )
        + _step(
            3, "Weeks 2–3",
            "Deeper capabilities activate: behavioral scoring, smart nudges, "
            "conversion signals, and measured impact reporting. "
            "These calibrate to your specific store data — not generic defaults."
        )
        + _step(
            4, "Ongoing",
            "The system compounds. More data means tighter models, "
            "more accurate signals, and higher-impact nudges. "
            "We ship improvements continuously."
        )

        # Your command center
        + _section_title("Your command center", accent="cool")
        + _p(
            "The <strong style='color:#f1f5f9;'>in-app chatbot</strong> is your "
            "primary interface to HedgeSpark. Use it to:"
        )
        + _bullet("Ask questions about your data, signals, or any feature")
        + _bullet("Report issues or request changes — directly, in real time")
        + _bullet("Get guided help with setup steps like the purchase pixel")
        + _p(
            "Think of it as your direct line to the system and to us. "
            "It's faster than email, and we monitor it actively.",
            color="#94a3b8",
        )

        + _separator()

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Open your dashboard", _DASHBOARD_URL)
        + '</div>'

        + _separator()

        + _p("Looking forward to seeing what the system finds,", color="#94a3b8")
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong><br>"
            "<span style='color:#c4b5fd;'>HedgeSpark</span>",
            color="#94a3b8",
        )
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"Hi,\n\n"
        f"HedgeSpark is now connected to {SHOP}. The tracking script is deployed "
        f"on your storefront and visitor data has started flowing into the system.\n\n"
        f"This email walks you through what's happening, what to expect, "
        f"and the one step that needs your attention.\n\n"
        f"WHAT'S ALREADY RUNNING\n"
        f"The moment you connected, HedgeSpark activated a lightweight tracking script. "
        f"It records page views, product interest, and browsing patterns — "
        f"no personal data, no impact on page speed.\n\n"
        f"You'll see first visitor data in minutes. Over 24 hours, the system builds "
        f"a behavioral baseline for your store.\n\n"
        f"THE ONE STEP THAT NEEDS YOU\n"
        f"Install the purchase tracking pixel on your order confirmation page.\n"
        f"- Without it: HedgeSpark can analyze behavior but can't attribute revenue\n"
        f"- With it: full revenue attribution, conversion rates, and ROI measurement\n"
        f"Dashboard walks you through it. Under 3 minutes. Chatbot can help live.\n\n"
        f"WHAT TO EXPECT\n"
        f"1. First 24 hours — visitor tracking active, initial analytics surface\n"
        f"2. Days 2-7 — behavioral signals, first actionable insights\n"
        f"3. Weeks 2-3 — scoring, smart nudges, measured impact reporting\n"
        f"4. Ongoing — system compounds, continuous improvements\n\n"
        f"YOUR COMMAND CENTER\n"
        f"The in-app chatbot is your primary interface:\n"
        f"- Ask questions about your data, signals, or any feature\n"
        f"- Report issues or request changes — directly, in real time\n"
        f"- Get guided help with setup steps\n\n"
        f"Open your dashboard: {_DASHBOARD_URL}\n\n"
        f"Looking forward to seeing what the system finds,\n"
        f"Andrea\n"
        f"HedgeSpark"
    )

    return subject, html, plain, "andrea@hedgesparkhq.com", "FOUNDATION"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 2: REVENUE TRIGGER — SIGNAL
#
# Type: Signal
# From: dev@hedgesparkhq.com
# Trigger: System detected 7 cart adds / 0 purchases on a specific product.
#
# Short. State → meaning → action. No product explanation.
# ═══════════════════════════════════════════════════════════════════════════

def build_revenue_trigger():
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
            "That pattern usually means something between cart and checkout "
            "is creating friction — a shipping cost surprise, a slow payment page, "
            "or a missing trust signal.",
            color="#94a3b8",
        )
        + _p(
            f"At your store's average order value, closing even a fraction of these "
            f"could recover ~${weekly_est:,} per week.",
            color="#94a3b8",
        )
        + _button("See the recommendation", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If this doesn't look right, reply to this email."
        + "</p>"
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"In the last 24 hours, {carts} visitors added {product} to their cart. "
        f"None of them completed checkout.\n\n"
        f"That pattern usually means something between cart and checkout "
        f"is creating friction — a shipping cost surprise, a slow payment page, "
        f"or a missing trust signal.\n\n"
        f"At your store's average order value, closing even a fraction of these "
        f"could recover ~${weekly_est:,} per week.\n\n"
        f"See the recommendation: {_DASHBOARD_URL}\n\n"
        f"If this doesn't look right, reply to this email."
    )

    return subject, html, plain, "dev@hedgesparkhq.com", "SIGNAL"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 3: WEEKLY DIGEST — HYBRID
#
# Type: Hybrid (foundation structure, signal content)
# From: digest@hedgesparkhq.com
# Trigger: 7 days of data accumulated. System has measured results.
#
# Structure: short summary card + structured insight blocks.
# Uses section titles and structured layout like foundation emails,
# but the content is all observed data — no explanation of what
# HedgeSpark is.
# ═══════════════════════════════════════════════════════════════════════════

def build_weekly_digest():
    subject = f"Your week on {SHOP}"

    body = (
        _p(
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> &middot; "
            f"Apr 01 – Apr 07, 2026",
            color="#64748b",
        )

        # Revenue summary — structured card (foundation layout)
        + _section_title("Revenue summary")
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:20px;">'
        + '<table style="width:100%;font-size:14px;border-collapse:collapse;">'
        + '<tr><td style="padding:6px 0;color:#64748b;">Revenue</td>'
        + '<td style="padding:6px 0;text-align:right;font-size:22px;font-weight:700;color:#f1f5f9;">$4,280</td></tr>'
        + '<tr><td style="padding:6px 0;color:#64748b;">Orders</td>'
        + '<td style="padding:6px 0;text-align:right;font-weight:600;color:#f1f5f9;">34</td></tr>'
        + '<tr><td style="padding:6px 0;color:#64748b;">Avg Order Value</td>'
        + '<td style="padding:6px 0;text-align:right;font-weight:600;color:#f1f5f9;">$125.88</td></tr>'
        + '<tr><td style="padding:6px 0;color:#64748b;">Visitors</td>'
        + '<td style="padding:6px 0;text-align:right;font-weight:600;color:#f1f5f9;">892</td></tr>'
        + '<tr><td style="padding:6px 0;color:#64748b;">Conversion</td>'
        + '<td style="padding:6px 0;text-align:right;font-weight:600;color:#f1f5f9;">3.81%</td></tr>'
        + '<tr><td colspan="2" style="padding:10px 0 0;font-size:13px;border-top:1px solid rgba(255,255,255,0.06);color:#64748b;">'
        + '<span style="color:#16a34a;font-weight:600;">+12.4%</span> vs prior week</td></tr>'
        + '</table></div>'

        # Measured impact — structured insight block (foundation layout, signal content)
        + _section_title("Measured impact", accent="cool")
        + '<div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + '<div style="text-align:center;margin-bottom:12px;">'
        + '<span style="font-size:28px;font-weight:700;color:#10b981;">+$340</span>'
        + '<div style="font-size:11px;color:#047857;margin-top:2px;">estimated incremental revenue</div>'
        + '</div>'
        + _p(
            "Visitors who saw your nudges converted at "
            "<strong style='color:#e2e8f0;'>4.2%</strong>. "
            "The control group — visitors who were eligible but didn't see a nudge — "
            "converted at <strong style='color:#e2e8f0;'>2.8%</strong>.",
        )
        + _p(
            "This is measured against a holdout group, not estimated. "
            "680 visitors were exposed, 212 were held back as control.",
            color="#94a3b8",
        )
        + '<p style="margin:0;font-size:11px;color:#475569;font-style:italic;">'
        + "Moderate confidence. Results are directional — sample still building."
        + '</p></div>'

        # What the system noticed — signal-style observation
        + _section_title("What the system noticed")
        + _p(
            "<strong style='color:#f1f5f9;'>Premium Leather Wallet</strong> had "
            "12 add-to-carts but only 1 purchase this week. "
            "Something in the checkout flow may be creating friction for this product.",
        )
        + _p(
            "Your dashboard has a specific recommendation.",
            color="#94a3b8",
        )

        # Top products — structured list (foundation layout)
        + _section_title("Top products", accent="cool")
        + _bullet("<strong style='color:#e2e8f0;'>Silk Scarf Collection</strong> — $1,420 revenue (12 sold)")
        + _bullet("<strong style='color:#e2e8f0;'>Premium Leather Wallet</strong> — $890 revenue (8 sold)")
        + _bullet("<strong style='color:#e2e8f0;'>Handmade Ceramic Mug</strong> — $560 revenue (14 sold)")

        + _separator()

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("View your dashboard", _DASHBOARD_URL)
        + '</div>'
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"{SHOP} — Apr 01 – Apr 07, 2026\n\n"
        f"REVENUE SUMMARY\n"
        f"  Revenue:    $4,280\n"
        f"  Orders:     34\n"
        f"  AOV:        $125.88\n"
        f"  Visitors:   892\n"
        f"  Conversion: 3.81%\n"
        f"  vs prior week: +12.4%\n\n"
        f"MEASURED IMPACT\n"
        f"  +$340 estimated incremental revenue\n"
        f"  Nudge recipients: 4.2% CVR vs 2.8% control\n"
        f"  680 exposed, 212 control. Moderate confidence.\n"
        f"  Measured against holdout group, not estimated.\n\n"
        f"WHAT THE SYSTEM NOTICED\n"
        f"  Premium Leather Wallet — 12 add-to-carts, 1 purchase.\n"
        f"  Dashboard has a specific recommendation.\n\n"
        f"TOP PRODUCTS\n"
        f"  Silk Scarf Collection — $1,420 (12 sold)\n"
        f"  Premium Leather Wallet — $890 (8 sold)\n"
        f"  Handmade Ceramic Mug — $560 (14 sold)\n\n"
        f"View your dashboard: {_DASHBOARD_URL}"
    )

    return subject, html, plain, "digest@hedgesparkhq.com", "HYBRID"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 4: RE-ENGAGEMENT — SIGNAL
#
# Type: Signal
# From: dev@hedgesparkhq.com
# Trigger: 14 consecutive days with zero visitor events recorded.
#
# Short. Observed state → probable cause → action.
# No product explanation. The system noticed something and is reporting.
# ═══════════════════════════════════════════════════════════════════════════

def build_reengagement():
    subject = f"{SHOP} — no visitor data in 14 days"

    body = (
        _p(
            f"HedgeSpark hasn't recorded any visitor events on "
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> since March 25. "
            f"That's 14 days of silence.",
        )
        + _p(
            "When this happens, it usually means the tracking script stopped loading — "
            "often after a theme update, an app reinstall, or a Shopify permission change. "
            "The store itself is fine. The connection just needs to be re-established.",
            color="#94a3b8",
        )
        + _p(
            "Your dashboard shows the exact connection status and what needs attention.",
            color="#94a3b8",
        )
        + _button("Check connection status", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If you've stopped using HedgeSpark, no action needed — we won't email again about this."
        + "</p>"
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"HedgeSpark hasn't recorded any visitor events on {SHOP} since March 25. "
        f"That's 14 days of silence.\n\n"
        f"When this happens, it usually means the tracking script stopped loading — "
        f"often after a theme update, an app reinstall, or a Shopify permission change. "
        f"The store itself is fine. The connection just needs to be re-established.\n\n"
        f"Your dashboard shows the exact connection status and what needs attention.\n\n"
        f"Check connection status: {_DASHBOARD_URL}\n\n"
        f"If you've stopped using HedgeSpark, no action needed — we won't email again about this."
    )

    return subject, html, plain, "dev@hedgesparkhq.com", "SIGNAL"


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATE + SEND
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    emails = [
        ("welcome", build_welcome),
        ("revenue_trigger", build_revenue_trigger),
        ("weekly_digest", build_weekly_digest),
        ("reengagement", build_reengagement),
    ]

    for name, builder in emails:
        subject, html, plain, from_addr, email_type = builder()

        text_check = validate_email_text(plain, is_digest=("digest" in name))
        subj_check = validate_subject_line(subject)
        word_count = len(plain.split())

        print(f"\n{'='*60}")
        print(f"EMAIL: {name}")
        print(f"TYPE: {email_type}")
        print(f"FROM: {from_addr}")
        print(f"SUBJECT: {subject}")
        print(f"WORDS: {word_count}")
        print(f"BRAND (text): passed={text_check.passed} violations={text_check.violations}")
        print(f"BRAND (subj): passed={subj_check.passed} violations={subj_check.violations}")

        from_display = f"HedgeSpark <{from_addr}>"
        if "andrea" in from_addr:
            from_display = f"Andrea from HedgeSpark <{from_addr}>"

        resend_id = send_email(
            to=TO, subject=subject, html=html, text=plain,
            from_address=from_display,
        )
        print(f"{'SENT' if resend_id else 'FAILED'}: {resend_id}")

    print(f"\n{'='*60}")
    print("Done. 4 emails sent with correct two-system architecture.")
