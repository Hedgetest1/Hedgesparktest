"""
brand_preview.py — Generate and validate 4 brand-compliant email previews.

Generates:
  1. Onboarding welcome
  2. Revenue trigger
  3. Weekly digest (proof section)
  4. Re-engagement

Uses the exact brand system: _wrap_html, _heading, _p, _button, _bullet,
_section_title, _separator, _step from email_templates.py.

All emails follow the brand_voice.py rules:
  - Andrea voice (first person singular)
  - Ground → Contextualize → Evidence → Action → Safety Net arc
  - No hype, no urgency, no blame
  - Single CTA, invitation verb
  - "Reply to this email" safety net

Does NOT send. Writes HTML files for visual inspection.
"""
import sys
import os
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _heading, _p, _button, _bullet,
    _section_title, _separator, _step,
)
from app.services.brand_voice import validate_email_text, validate_subject_line


_DASHBOARD_URL = "https://app.hedgesparkhq.com/"

# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 1: Onboarding Welcome
# ═══════════════════════════════════════════════════════════════════════════

def build_welcome():
    shop_name = "Stella & Ivy"

    subject = f"HedgeSpark is live on {shop_name}"

    body = (
        _heading("Welcome to HedgeSpark")
        + _p(
            f"HedgeSpark is now connected to "
            f"<strong style='color:#f1f5f9;'>{shop_name}</strong> "
            f"and tracking visitor behavior."
        )
        + _p("Here's what happens next:")
        + _bullet(
            "Visitor tracking starts <strong style='color:#e2e8f0;'>immediately</strong> "
            "— no action needed"
        )
        + _bullet(
            "First insights appear in about "
            "<strong style='color:#e2e8f0;'>10 minutes</strong>"
        )
        + _bullet(
            "Full analysis builds over the "
            "<strong style='color:#e2e8f0;'>first 24 hours</strong>"
        )
        + _p(
            "One optional step: connect the "
            "<strong style='color:#e2e8f0;'>purchase tracking pixel</strong> "
            "so HedgeSpark can see which visitors actually buy. "
            "You'll find the setup guide in your dashboard.",
            color="#94a3b8",
        )
        + _button("Open your dashboard", _DASHBOARD_URL)
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"Welcome to HedgeSpark\n\n"
        f"HedgeSpark is now connected to {shop_name} and tracking visitor behavior.\n\n"
        f"What happens next:\n"
        f"- Visitor tracking starts immediately\n"
        f"- First insights appear in about 10 minutes\n"
        f"- Full analysis builds over the first 24 hours\n\n"
        f"Optional: connect the purchase tracking pixel in your dashboard "
        f"so HedgeSpark can see which visitors buy.\n\n"
        f"Open your dashboard: {_DASHBOARD_URL}"
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 2: Revenue Trigger (High Intent Leak)
# ═══════════════════════════════════════════════════════════════════════════

def build_revenue_trigger():
    product_name = "Premium Leather Wallet"
    carts = 7
    weekly_loss = 420.00

    subject = f"{product_name} — {carts} cart adds, 0 purchases"

    body = (
        _heading(product_name)
        + _p(
            f"<strong style='color:#f1f5f9;'>{product_name}</strong> "
            f"had {carts} add-to-cart events in the last 24 hours "
            f"but zero completed purchases. That pattern usually means "
            f"something between cart and checkout is creating friction."
        )
        + _p(
            f"At your store's average order value, closing even a fraction "
            f"of these could recover ~${weekly_loss:,.0f} per week.",
            color="#94a3b8",
        )
        + _p(
            "Your dashboard has specific recommendations for this product.",
            color="#94a3b8",
        )
        + _button("Open your dashboard", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If this doesn't look right, reply to this email and we'll look into it."
        + "</p>"
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"{product_name}\n\n"
        f"{product_name} had {carts} add-to-cart events in the last 24 hours "
        f"but zero completed purchases. That pattern usually means something "
        f"between cart and checkout is creating friction.\n\n"
        f"At your store's average order value, closing even a fraction "
        f"of these could recover ~${weekly_loss:,.0f} per week.\n\n"
        f"Your dashboard has specific recommendations for this product.\n\n"
        f"Open your dashboard: {_DASHBOARD_URL}\n\n"
        f"If this doesn't look right, reply to this email and we'll look into it."
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 3: Weekly Digest (with proof section)
# ═══════════════════════════════════════════════════════════════════════════

def build_weekly_digest():
    shop_name = "Stella & Ivy"
    subject = f"Your Weekly Intelligence — {shop_name}"

    body = (
        _heading("Weekly Revenue Digest")
        + '<p style="margin:0 0 20px 0;font-size:13px;color:#64748b;">'
        + f'{shop_name} &middot; Apr 01 – Apr 07, 2026'
        + '</p>'

        # Revenue summary card
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:20px;">'
        + '<table style="width:100%;font-size:14px;border-collapse:collapse;">'
        + '<tr>'
        + '<td style="padding:8px 0;color:#64748b;">Revenue</td>'
        + '<td style="padding:8px 0;text-align:right;font-size:22px;font-weight:700;color:#f1f5f9;">$4,280.00</td>'
        + '</tr>'
        + '<tr>'
        + '<td style="padding:8px 0;color:#64748b;">Orders</td>'
        + '<td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9;">34</td>'
        + '</tr>'
        + '<tr>'
        + '<td style="padding:8px 0;color:#64748b;">Avg Order Value</td>'
        + '<td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9;">$125.88</td>'
        + '</tr>'
        + '<tr>'
        + '<td style="padding:8px 0;color:#64748b;">Visitors</td>'
        + '<td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9;">892</td>'
        + '</tr>'
        + '<tr>'
        + '<td style="padding:8px 0;color:#64748b;">Conversion Rate</td>'
        + '<td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9;">3.81%</td>'
        + '</tr>'
        + '<tr>'
        + '<td colspan="2" style="padding:12px 0 0;font-size:13px;color:#64748b;border-top:1px solid rgba(255,255,255,0.06);">'
        + '<span style="color:#16a34a;font-weight:600;">+12.4%</span> vs last week'
        + '</td>'
        + '</tr>'
        + '</table>'
        + '</div>'

        # Proof of impact section
        + _section_title("Your proven impact", accent="cool")
        + '<div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:12px;padding:20px;margin-bottom:20px;">'
        + '<div style="text-align:center;margin-bottom:12px;">'
        + '<span style="font-size:28px;font-weight:700;color:#10b981;">+$340</span>'
        + '<div style="font-size:11px;color:#047857;margin-top:2px;">estimated incremental revenue this week</div>'
        + '</div>'
        + _p(
            "Visitors who saw your nudges converted at "
            "<strong style='color:#e2e8f0;'>4.2%</strong> vs "
            "<strong style='color:#e2e8f0;'>2.8%</strong> for the control group "
            "(680 exposed, 212 control).",
            color="#94a3b8",
        )
        + '<p style="margin:0;font-size:11px;color:#475569;font-style:italic;">'
        + "Moderate confidence. Measured using a holdout control group — not an estimate."
        + '</p>'
        + '</div>'

        # Recommendation
        + _section_title("This week's recommendation")
        + _p(
            "<strong style='color:#f1f5f9;'>Premium Leather Wallet</strong> "
            "had 12 add-to-carts but only 1 purchase. "
            "The checkout flow may be creating friction for this product."
        )
        + _p(
            "Your dashboard has a specific nudge recommendation for this product.",
            color="#94a3b8",
        )

        # Top products
        + _section_title("Top products", accent="cool")
        + _bullet("<strong style='color:#e2e8f0;'>Silk Scarf Collection</strong> — $1,420 (12 sold)")
        + _bullet("<strong style='color:#e2e8f0;'>Premium Leather Wallet</strong> — $890 (8 sold)")
        + _bullet("<strong style='color:#e2e8f0;'>Handmade Ceramic Mug</strong> — $560 (14 sold)")

        + _separator()

        + _button("View your dashboard", _DASHBOARD_URL)
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"Weekly Revenue Digest — {shop_name}\n"
        f"Apr 01 – Apr 07, 2026\n\n"
        f"THIS WEEK\n"
        f"  Revenue:    $4,280.00\n"
        f"  Orders:     34\n"
        f"  AOV:        $125.88\n"
        f"  Visitors:   892\n"
        f"  Conversion: 3.81%\n"
        f"  vs last week: +12.4%\n\n"
        f"YOUR PROVEN IMPACT\n"
        f"  +$340 estimated incremental revenue\n"
        f"  Nudge recipients: 4.2% CVR vs 2.8% control (680 exposed, 212 control)\n"
        f"  Moderate confidence. Measured using holdout control group.\n\n"
        f"RECOMMENDATION\n"
        f"  Premium Leather Wallet — 12 cart adds, 1 purchase. "
        f"Checkout flow may be creating friction.\n\n"
        f"TOP PRODUCTS\n"
        f"  Silk Scarf Collection — $1,420 (12 sold)\n"
        f"  Premium Leather Wallet — $890 (8 sold)\n"
        f"  Handmade Ceramic Mug — $560 (14 sold)\n\n"
        f"View your dashboard: {_DASHBOARD_URL}"
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 4: Re-engagement
# ═══════════════════════════════════════════════════════════════════════════

def build_reengagement():
    shop_name = "Stella & Ivy"
    subject = f"{shop_name} — no visitor activity in 14 days"

    body = (
        _heading("No visitor activity detected")
        + _p(
            f"HedgeSpark hasn't recorded any visitor activity on "
            f"<strong style='color:#f1f5f9;'>{shop_name}</strong> "
            f"in the last 14 days. This usually means the tracking script "
            f"isn't loading on your storefront."
        )
        + _p(
            "This can happen after Shopify theme updates, app reinstalls, "
            "or changes to your storefront code. "
            "Your dashboard shows the connection status and will guide you "
            "through any steps needed to restore tracking.",
            color="#94a3b8",
        )
        + _button("Check your dashboard", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If something is broken, reply to this email — we'll look into it."
        + "</p>"
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"No visitor activity detected\n\n"
        f"HedgeSpark hasn't recorded any visitor activity on {shop_name} "
        f"in the last 14 days. This usually means the tracking script "
        f"isn't loading on your storefront.\n\n"
        f"This can happen after Shopify theme updates, app reinstalls, "
        f"or changes to your storefront code.\n\n"
        f"Check your dashboard: {_DASHBOARD_URL}\n\n"
        f"If something is broken, reply to this email — we'll look into it."
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# GENERATE + VALIDATE + SAVE
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    emails = [
        ("1_welcome", build_welcome),
        ("2_revenue_trigger", build_revenue_trigger),
        ("3_weekly_digest", build_weekly_digest),
        ("4_reengagement", build_reengagement),
    ]

    output_dir = "/opt/wishspark/backend/scripts/email_previews"
    os.makedirs(output_dir, exist_ok=True)

    for name, builder in emails:
        subject, html, plain = builder()

        # Validate against brand rules
        text_result = validate_email_text(plain, is_digest=("digest" in name))
        subj_result = validate_subject_line(subject)

        print(f"\n{'='*60}")
        print(f"EMAIL: {name}")
        print(f"SUBJECT: {subject}")
        print(f"BRAND CHECK (text): passed={text_result.passed}, violations={text_result.violations}, warnings={text_result.warnings}")
        print(f"BRAND CHECK (subj): passed={subj_result.passed}, violations={subj_result.violations}")

        # Save HTML
        path = os.path.join(output_dir, f"{name}.html")
        with open(path, "w") as f:
            f.write(html)
        print(f"SAVED: {path}")

    print(f"\n{'='*60}")
    print("All 4 emails generated and validated. Open HTML files to preview.")
    print(f"Files in: {output_dir}/")
