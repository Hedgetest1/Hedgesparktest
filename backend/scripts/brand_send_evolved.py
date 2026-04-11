"""
brand_send_evolved.py — Evolved email system with guidance layer.

AUDIT FINDINGS APPLIED:
  - Revenue trigger: added guidance layer (is this normal, what it means, what we're doing)
  - Weekly digest: added "What changed", visual chart blocks, "What we're watching next"
  - Re-engagement: added warmth, acknowledgment, forward pull instead of opt-out
  - Signature: reinforces HedgeSpark as active system + Andrea personal

PRINCIPLE: "I am watching your store with you"
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
TO = "tedialarana@gmail.com"
SHOP = "Stella & Ivy"


def _signature_system() -> str:
    """Signature that reinforces HedgeSpark as an active, watching system.
    Personal (Andrea) but anchored to the system's ongoing work."""
    return (
        _separator()
        + _p(
            "HedgeSpark is running on your store right now — analyzing traffic, "
            "measuring nudge performance, and looking for your next revenue opportunity.",
            color="#64748b",
        )
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong><br>"
            "<span style='color:#94a3b8;'>Founder, HedgeSpark</span>",
            color="#94a3b8",
        )
    )


def _signature_system_short() -> str:
    """Shorter signature for signal emails — still reinforces presence."""
    return (
        _separator()
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong> · "
            "<span style='color:#94a3b8;'>HedgeSpark is monitoring your store continuously</span>",
            color="#94a3b8",
        )
    )


# Helper for Resend-compatible horizontal bar charts
def _bar_chart(label: str, value: float, max_value: float, color: str = "#a855f7", suffix: str = "") -> str:
    """Simple horizontal bar for email — pure HTML/CSS, no images."""
    pct = min(int((value / max_value) * 100), 100) if max_value > 0 else 0
    display = f"{value:,.0f}{suffix}" if value >= 1 else f"{value:.1%}"
    return (
        f'<div style="margin-bottom:10px;">'
        f'<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px;">'
        f'<span style="color:#94a3b8;">{label}</span>'
        f'<span style="color:#f1f5f9;font-weight:600;">{display}</span>'
        f'</div>'
        f'<div style="background:rgba(255,255,255,0.06);border-radius:4px;height:6px;overflow:hidden;">'
        f'<div style="background:{color};height:100%;width:{pct}%;border-radius:4px;"></div>'
        f'</div>'
        f'</div>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 1: REVENUE TRIGGER — SIGNAL (with guidance layer)
#
# CHANGES FROM PREVIOUS VERSION:
#   + Added "Is this normal?" guidance paragraph (reassurance + calibration)
#   + Added "What HedgeSpark is doing" paragraph (system as companion)
#   + Replaced defensive sign-off with active signature
#   + CTA verb changed: "See the recommendation" → "See what we found"
# ═══════════════════════════════════════════════════════════════════════════

def build_revenue_trigger():
    product = "Premium Leather Wallet"
    carts = 7
    weekly_est = 420

    subject = f"{product} — {carts} cart adds, 0 purchases"

    body = (
        # OBSERVE — what the system detected
        _p(
            f"In the last 24 hours, <strong style='color:#f1f5f9;'>{carts} visitors</strong> "
            f"added <strong style='color:#f1f5f9;'>{product}</strong> to their cart. "
            f"None of them completed checkout.",
        )

        # GUIDE — is this normal? what does it mean?
        + _p(
            "This is a common pattern — it doesn't mean something is broken. "
            "Cart-to-checkout drop-off often comes down to small friction points: "
            "an unexpected shipping cost at the last step, a slow-loading payment page, "
            "or a missing trust signal like reviews or a return policy.",
            color="#94a3b8",
        )
        + _p(
            f"What makes this worth your attention is the volume. "
            f"{carts} carts in 24 hours means this product has real demand. "
            f"At your store's average order value, even a small checkout improvement "
            f"could recover ~<strong style='color:#e2e8f0;'>${weekly_est:,}/week</strong>.",
        )

        # COMPANION — what the system is already doing
        + _p(
            "HedgeSpark has already analyzed the behavioral pattern around this product "
            "and prepared a specific recommendation in your dashboard.",
            color="#94a3b8",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("See what we found", _DASHBOARD_URL)
        + '</div>'

        + _signature_system_short()
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"In the last 24 hours, {carts} visitors added {product} to their cart. "
        f"None of them completed checkout.\n\n"
        f"This is a common pattern — it doesn't mean something is broken. "
        f"Cart-to-checkout drop-off often comes down to small friction points: "
        f"an unexpected shipping cost, a slow-loading payment page, "
        f"or a missing trust signal like reviews or a return policy.\n\n"
        f"What makes this worth your attention is the volume. "
        f"{carts} carts in 24 hours means real demand. "
        f"At your store's AOV, even a small improvement "
        f"could recover ~${weekly_est:,}/week.\n\n"
        f"HedgeSpark has already analyzed the pattern and prepared "
        f"a specific recommendation in your dashboard.\n\n"
        f"See what we found: {_DASHBOARD_URL}\n\n"
        f"Andrea · HedgeSpark is monitoring your store continuously"
    )

    return subject, html, plain, "dev@hedgesparkhq.com", "SIGNAL"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 2: WEEKLY DIGEST — HYBRID (with narrative + charts + forward look)
#
# CHANGES FROM PREVIOUS VERSION:
#   + Added "What changed this week" narrative paragraph (interpretation)
#   + Added 2 visual bar chart blocks (conversion by product, traffic sources)
#   + Added "What we're watching next week" section (forward pull)
#   + Proof section rewritten with human meaning, not just methodology
#   + Added signature
# ═══════════════════════════════════════════════════════════════════════════

def build_weekly_digest():
    subject = f"Your week on {SHOP}"

    body = (
        _p(
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> &middot; "
            f"Apr 01 – Apr 07, 2026",
            color="#64748b",
        )

        # Revenue summary card
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

        # NEW: What changed this week — narrative interpretation
        + _section_title("What changed this week", accent="cool")
        + _p(
            "Your revenue grew 12.4% week-over-week. Most of that came from "
            "<strong style='color:#e2e8f0;'>Silk Scarf Collection</strong>, "
            "which saw a spike in traffic mid-week — likely from an organic social mention. "
            "Conversion rate also improved slightly, from 3.4% to 3.81%, "
            "which aligns with the nudges HedgeSpark activated on two of your products.",
        )
        + _p(
            "The one area to watch: <strong style='color:#e2e8f0;'>Premium Leather Wallet</strong> "
            "continues to attract interest (12 add-to-carts) but isn't converting. "
            "That pattern has been consistent for two weeks now.",
            color="#94a3b8",
        )

        # NEW: Visual chart blocks — conversion by product
        + _section_title("Conversion by product")
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + _bar_chart("Silk Scarf Collection", 8.4, 10, color="#10b981", suffix="%")
        + _bar_chart("Handmade Ceramic Mug", 5.2, 10, color="#a855f7", suffix="%")
        + _bar_chart("Premium Leather Wallet", 1.1, 10, color="#f59e0b", suffix="%")
        + '</div>'

        # NEW: Visual chart blocks — traffic sources
        + _section_title("Traffic sources", accent="cool")
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + _bar_chart("Direct", 412, 500, color="#a855f7")
        + _bar_chart("Google / Organic", 248, 500, color="#10b981")
        + _bar_chart("Instagram", 156, 500, color="#f59e0b")
        + _bar_chart("Other", 76, 500, color="#64748b")
        + '</div>'

        # Measured impact — rewritten with human meaning
        + _section_title("Measured impact")
        + '<div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + '<div style="text-align:center;margin-bottom:12px;">'
        + '<span style="font-size:28px;font-weight:700;color:#10b981;">+$340</span>'
        + '<div style="font-size:11px;color:#047857;margin-top:2px;">estimated incremental revenue</div>'
        + '</div>'
        + _p(
            "Here's what this means: we showed nudges to 680 visitors and held back "
            "212 as a control group. The visitors who saw nudges purchased at "
            "<strong style='color:#e2e8f0;'>4.2%</strong> — the control group at "
            "<strong style='color:#e2e8f0;'>2.8%</strong>. "
            "That difference translates to roughly $340 in revenue that "
            "wouldn't have happened without the nudges.",
        )
        + '<p style="margin:0;font-size:11px;color:#475569;font-style:italic;">'
        + "Moderate confidence — sample is still building. We'll update this as more data comes in."
        + '</p></div>'

        # NEW: What we're watching next week
        + _section_title("What we're watching next week", accent="cool")
        + _bullet(
            "<strong style='color:#e2e8f0;'>Premium Leather Wallet checkout friction</strong> — "
            "two weeks of high intent, low conversion. If the pattern holds, "
            "we'll propose a specific intervention."
        )
        + _bullet(
            "<strong style='color:#e2e8f0;'>Silk Scarf traffic sustainability</strong> — "
            "this week's spike may be a one-time event or the start of a trend. "
            "Next week's data will clarify."
        )
        + _bullet(
            "<strong style='color:#e2e8f0;'>Nudge confidence level</strong> — "
            "as sample size grows, the measured impact will move from moderate "
            "to high confidence. We expect this within 1–2 more weeks."
        )

        + '<div style="text-align:center;margin:20px 0 0 0;">'
        + _button("View your dashboard", _DASHBOARD_URL)
        + '</div>'

        + _signature_system()
    )

    html = _wrap_html(subject, body, show_logo=True)

    plain = (
        f"{SHOP} — Apr 01 – Apr 07, 2026\n\n"
        f"REVENUE SUMMARY\n"
        f"  Revenue: $4,280 | Orders: 34 | AOV: $125.88\n"
        f"  Visitors: 892 | Conversion: 3.81% | +12.4% vs prior week\n\n"
        f"WHAT CHANGED THIS WEEK\n"
        f"Revenue grew 12.4% WoW. Most came from Silk Scarf Collection — "
        f"likely organic social traffic mid-week. Conversion improved from "
        f"3.4% to 3.81%, aligning with nudge activation on two products.\n"
        f"Watch area: Premium Leather Wallet — 12 add-to-carts, "
        f"low conversion for second consecutive week.\n\n"
        f"CONVERSION BY PRODUCT\n"
        f"  Silk Scarf Collection: 8.4%\n"
        f"  Handmade Ceramic Mug: 5.2%\n"
        f"  Premium Leather Wallet: 1.1%\n\n"
        f"TRAFFIC SOURCES\n"
        f"  Direct: 412 | Google: 248 | Instagram: 156 | Other: 76\n\n"
        f"MEASURED IMPACT\n"
        f"  +$340 estimated incremental revenue\n"
        f"  680 visitors saw nudges (4.2% CVR) vs 212 control (2.8% CVR)\n"
        f"  That difference = ~$340 that wouldn't have happened without nudges.\n"
        f"  Moderate confidence — sample still building.\n\n"
        f"WHAT WE'RE WATCHING NEXT WEEK\n"
        f"  - Leather Wallet checkout friction (may propose intervention)\n"
        f"  - Silk Scarf traffic sustainability (one-time or trend?)\n"
        f"  - Nudge confidence level (expect high confidence in 1-2 weeks)\n\n"
        f"View your dashboard: {_DASHBOARD_URL}\n\n"
        f"HedgeSpark is running on your store right now — analyzing traffic, "
        f"measuring nudge performance, and looking for your next revenue opportunity.\n\n"
        f"Andrea\n"
        f"Founder, HedgeSpark"
    )

    return subject, html, plain, "digest@hedgesparkhq.com", "HYBRID"


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL 3: RE-ENGAGEMENT — SIGNAL (with warmth + forward pull)
#
# CHANGES FROM PREVIOUS VERSION:
#   + Added acknowledgment ("this happens — stores get busy")
#   + Added "what you're missing" forward pull instead of opt-out
#   + Added "what HedgeSpark was doing" during the silence
#   + Replaced opt-out plant with gentle re-invitation
#   + Added system signature
# ═══════════════════════════════════════════════════════════════════════════

def build_reengagement():
    subject = f"{SHOP} — no visitor data in 14 days"

    body = (
        # OBSERVE — what the system detected
        _p(
            f"HedgeSpark hasn't recorded visitor activity on "
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> since March 25.",
        )

        # ACKNOWLEDGE — life happens
        + _p(
            "This happens — sometimes a theme update disrupts the tracking script, "
            "sometimes things just get busy. Either way, the system noticed and "
            "wanted to flag it for you.",
            color="#94a3b8",
        )

        # GUIDE — what it usually means
        + _p(
            "When tracking goes silent, the most common cause is the storefront script "
            "not loading — usually after a theme change, an app reinstall, or a "
            "Shopify permission update. Your store is fine. "
            "The tracking connection just needs to be re-established.",
            color="#94a3b8",
        )

        # FORWARD PULL — what they're missing, not what they should do
        + _p(
            "While tracking was paused, HedgeSpark couldn't analyze visitor behavior "
            "or measure nudge performance. "
            "Once reconnected, the system picks up where it left off — "
            "no data from before the gap is lost.",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Reconnect tracking", _DASHBOARD_URL)
        + '</div>'

        + _signature_system_short()
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
        f"While tracking was paused, HedgeSpark couldn't analyze visitor behavior "
        f"or measure nudge performance. Once reconnected, the system picks up "
        f"where it left off — no data from before the gap is lost.\n\n"
        f"Reconnect tracking: {_DASHBOARD_URL}\n\n"
        f"Andrea · HedgeSpark is monitoring your store continuously"
    )

    return subject, html, plain, "dev@hedgesparkhq.com", "SIGNAL"


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATE + SEND
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    emails = [
        ("revenue_trigger", build_revenue_trigger),
        ("weekly_digest", build_weekly_digest),
        ("reengagement", build_reengagement),
    ]

    for name, builder in emails:
        subject, html, plain, from_addr, email_type = builder()

        text_check = validate_email_text(plain, is_digest=("digest" in name))
        subj_check = validate_subject_line(subject)

        print(f"\n{'='*60}")
        print(f"EMAIL: {name} ({email_type})")
        print(f"FROM: {from_addr}")
        print(f"SUBJECT: {subject}")
        print(f"WORDS: {len(plain.split())}")
        print(f"BRAND: text={text_check.passed} subj={subj_check.passed}")
        if text_check.violations:
            print(f"  VIOLATIONS: {text_check.violations}")
        if text_check.warnings:
            print(f"  WARNINGS: {text_check.warnings}")

        from_display = f"HedgeSpark <{from_addr}>"
        resend_id = send_email(
            to=TO, subject=subject, html=html, text=plain,
            from_address=from_display,
        )
        print(f"{'SENT' if resend_id else 'FAILED'}: {resend_id}")

    print(f"\n{'='*60}")
    print("Done. 3 evolved emails sent.")
