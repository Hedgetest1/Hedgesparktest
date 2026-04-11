"""
brand_send_4_behavioral.py — 4 behavioral email templates.

Each email starts from an observed merchant state.
No product explanations. No feature lists. The system is already watching.

1. WELCOME — trigger: OAuth install just completed, tracking live
2. REVENUE TRIGGER — trigger: real-time product anomaly detected
3. WEEKLY DIGEST — trigger: 7 days of data accumulated, results measured
4. RE-ENGAGEMENT — trigger: 14 days zero visitor events
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _heading, _p, _button, _bullet,
    _section_title, _separator,
)
from app.services.brand_voice import validate_email_text, validate_subject_line
from app.core.email import send_email


_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
TO = "tedialarana@gmail.com"
FROM = "Hedge Spark <dev@hedgesparkhq.com>"
SHOP = "Stella & Ivy"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 1: WELCOME
# State: OAuth complete. Tracking script deployed. First events arriving.
# The system is live. This is a status update, not an introduction.
# ═══════════════════════════════════════════════════════════════════════════

def build_welcome():
    subject = f"HedgeSpark is connected to {SHOP}"

    body = (
        _p(
            f"HedgeSpark is now live on "
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong>. "
            f"The tracking script is deployed and visitor data has started flowing.",
        )
        + _p(
            "You don't need to do anything right now. "
            "First insights will appear in your dashboard within the next few hours "
            "as traffic comes in.",
            color="#94a3b8",
        )
        + _p(
            "One thing you'll want to set up when you're ready: the "
            "<strong style='color:#e2e8f0;'>purchase pixel</strong>. "
            "Without it, HedgeSpark can see visitor behavior but can't connect it to revenue. "
            "The dashboard walks you through it — takes under 3 minutes.",
            color="#94a3b8",
        )
        + _button("Open your dashboard", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If anything looks off, reply to this email."
        + "</p>"
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"HedgeSpark is now live on {SHOP}. "
        f"The tracking script is deployed and visitor data has started flowing.\n\n"
        f"You don't need to do anything right now. "
        f"First insights will appear in your dashboard within the next few hours "
        f"as traffic comes in.\n\n"
        f"One thing you'll want to set up when you're ready: the purchase pixel. "
        f"Without it, HedgeSpark can see visitor behavior but can't connect it to revenue. "
        f"The dashboard walks you through it — takes under 3 minutes.\n\n"
        f"Open your dashboard: {_DASHBOARD_URL}\n\n"
        f"If anything looks off, reply to this email."
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 2: REVENUE TRIGGER
# State: HedgeSpark detected a specific product anomaly in the last 24h.
# 7 visitors added Premium Leather Wallet to cart. Zero purchased.
# The system observed this. It's reporting what it found.
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

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 3: WEEKLY DIGEST
# State: 7 days of data accumulated. System has measured results.
# Revenue, orders, conversion — all real numbers from real events.
# If nudges ran with holdout, lift is measured against control group.
# This is a report from a system that has been working all week.
# ═══════════════════════════════════════════════════════════════════════════

def build_weekly_digest():
    subject = f"Your week on {SHOP}"

    body = (
        # The system is reporting what it observed this week
        _p(
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> — "
            f"Apr 01 – Apr 07, 2026",
            color="#64748b",
        )

        # Revenue card — observed data, not projections
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin:8px 0 20px 0;">'
        + '<table style="width:100%;font-size:14px;border-collapse:collapse;">'
        + '<tr>'
        + '<td style="padding:6px 0;color:#64748b;">Revenue</td>'
        + '<td style="padding:6px 0;text-align:right;font-size:22px;font-weight:700;color:#f1f5f9;">$4,280</td>'
        + '</tr>'
        + '<tr>'
        + '<td style="padding:6px 0;color:#64748b;">Orders</td>'
        + '<td style="padding:6px 0;text-align:right;font-weight:600;color:#f1f5f9;">34</td>'
        + '</tr>'
        + '<tr>'
        + '<td style="padding:6px 0;color:#64748b;">Conversion</td>'
        + '<td style="padding:6px 0;text-align:right;font-weight:600;color:#f1f5f9;">3.81%</td>'
        + '</tr>'
        + '<tr>'
        + '<td colspan="2" style="padding:10px 0 0;font-size:13px;border-top:1px solid rgba(255,255,255,0.06);color:#64748b;">'
        + '<span style="color:#16a34a;font-weight:600;">+12.4%</span> vs prior week'
        + '</td>'
        + '</tr>'
        + '</table>'
        + '</div>'

        # Proof — measured against control group, not estimated
        + '<div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + '<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.1em;color:#047857;margin-bottom:10px;">Measured impact</div>'
        + _p(
            "Visitors who saw your nudges converted at "
            "<strong style='color:#e2e8f0;'>4.2%</strong>. "
            "The control group converted at "
            "<strong style='color:#e2e8f0;'>2.8%</strong>. "
            "That's <strong style='color:#10b981;'>+$340</strong> in estimated incremental revenue.",
        )
        + '<p style="margin:0;font-size:11px;color:#475569;font-style:italic;">'
        + "680 exposed, 212 control. Moderate confidence."
        + '</p>'
        + '</div>'

        # What the system noticed — one specific, actionable observation
        + _p(
            "<strong style='color:#f1f5f9;'>Premium Leather Wallet</strong> "
            "had 12 add-to-carts but only 1 purchase this week. "
            "Your dashboard has a specific recommendation for this product.",
            color="#94a3b8",
        )

        + _button("View your dashboard", _DASHBOARD_URL)
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"{SHOP} — Apr 01 – Apr 07, 2026\n\n"
        f"Revenue: $4,280 | Orders: 34 | Conversion: 3.81% | +12.4% vs prior week\n\n"
        f"MEASURED IMPACT\n"
        f"Nudge recipients: 4.2% CVR. Control group: 2.8% CVR.\n"
        f"+$340 estimated incremental revenue.\n"
        f"680 exposed, 212 control. Moderate confidence.\n\n"
        f"Premium Leather Wallet had 12 add-to-carts but only 1 purchase this week. "
        f"Your dashboard has a specific recommendation.\n\n"
        f"View your dashboard: {_DASHBOARD_URL}"
    )

    return subject, html, plain


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 4: RE-ENGAGEMENT
# State: 14 consecutive days with zero visitor events recorded.
# The system has been watching and noticed the silence.
# Something is likely broken. This is diagnostic, not guilt-inducing.
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

    return subject, html, plain


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
        subject, html, plain = builder()

        text_check = validate_email_text(plain, is_digest=("digest" in name))
        subj_check = validate_subject_line(subject)

        print(f"\n{'='*60}")
        print(f"EMAIL: {name}")
        print(f"SUBJECT: {subject}")
        print(f"WORDS: {len(plain.split())}")
        print(f"BRAND (text): passed={text_check.passed} violations={text_check.violations} warnings={text_check.warnings}")
        print(f"BRAND (subj): passed={subj_check.passed} violations={subj_check.violations}")

        resend_id = send_email(to=TO, subject=subject, html=html, text=plain, from_address=FROM)
        print(f"{'SENT' if resend_id else 'FAILED'}: {resend_id}")

    print(f"\n{'='*60}")
    print("Done. 4 behavioral emails sent.")
