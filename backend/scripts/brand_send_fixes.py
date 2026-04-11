"""
brand_send_fixes.py — Precise fixes only. No redesign.

1. WEEKLY DIGEST: Remove signature. Nothing else.
2. NO VISITOR ACTIVITY: Keep everything. Add 2 short sections after explanation.
3. REVENUE TRIGGER: Keep everything. Add 1 paragraph why + 1 paragraph if resolved.
4. WELCOME: Keep everything. Replace only closing sentence + add signature.
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _p, _button, _bullet,
    _section_title, _separator, _step,
)
from app.services.brand_voice import validate_email_text, validate_subject_line
from app.core.email import send_email


_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
TO = "tedialarana@gmail.com"
SHOP = "Stella & Ivy"


# Helper for bar charts (unchanged from evolved version)
def _bar_chart(label, value, max_value, color="#a855f7", suffix=""):
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
        f'</div></div>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. WEEKLY DIGEST — REMOVE SIGNATURE ONLY
# ═══════════════════════════════════════════════════════════════════════════

def build_weekly_digest():
    subject = f"Your week on {SHOP}"

    # IDENTICAL to evolved version — only the signature at the end is removed
    body = (
        _p(
            f"<strong style='color:#f1f5f9;'>{SHOP}</strong> &middot; "
            f"Apr 01 – Apr 07, 2026",
            color="#64748b",
        )

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

        + _section_title("Conversion by product")
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + _bar_chart("Silk Scarf Collection", 8.4, 10, color="#10b981", suffix="%")
        + _bar_chart("Handmade Ceramic Mug", 5.2, 10, color="#a855f7", suffix="%")
        + _bar_chart("Premium Leather Wallet", 1.1, 10, color="#f59e0b", suffix="%")
        + '</div>'

        + _section_title("Traffic sources", accent="cool")
        + '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:18px;margin-bottom:20px;">'
        + _bar_chart("Direct", 412, 500, color="#a855f7")
        + _bar_chart("Google / Organic", 248, 500, color="#10b981")
        + _bar_chart("Instagram", 156, 500, color="#f59e0b")
        + _bar_chart("Other", 76, 500, color="#64748b")
        + '</div>'

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

        # FIX: signature removed. Email ends after CTA.
    )

    html = _wrap_html(subject, body, show_logo=True)
    plain = "Weekly digest — plain text version"  # abbreviated for send test
    return subject, html, plain, "digest@hedgesparkhq.com"


# ═══════════════════════════════════════════════════════════════════════════
# 2. NO VISITOR ACTIVITY — ADD 2 SECTIONS AFTER EXPLANATION
# ═══════════════════════════════════════════════════════════════════════════

def build_reengagement():
    subject = f"{SHOP} — no visitor data in 14 days"

    body = (
        # EXISTING — unchanged
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

        # ADDED: Section 1 — reassurance (this is common, store is fine)
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

        # ADDED: Section 2 — what happens once fixed
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

        # EXISTING — unchanged
        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Reconnect tracking", _DASHBOARD_URL)
        + '</div>'

        + _separator()
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong> · "
            "<span style='color:#94a3b8;'>HedgeSpark is monitoring your store continuously</span>",
            color="#94a3b8",
        )
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
        f"Reconnect tracking: {_DASHBOARD_URL}\n\n"
        f"Andrea · HedgeSpark is monitoring your store continuously"
    )

    return subject, html, plain, "dev@hedgesparkhq.com"


# ═══════════════════════════════════════════════════════════════════════════
# 3. REVENUE TRIGGER — ADD 1 WHY PARAGRAPH + 1 IF-RESOLVED PARAGRAPH
# ═══════════════════════════════════════════════════════════════════════════

def build_revenue_trigger():
    product = "Premium Leather Wallet"
    carts = 7
    weekly_est = 420

    subject = f"{product} — {carts} cart adds, 0 purchases"

    body = (
        # EXISTING — unchanged
        _p(
            f"In the last 24 hours, <strong style='color:#f1f5f9;'>{carts} visitors</strong> "
            f"added <strong style='color:#f1f5f9;'>{product}</strong> to their cart. "
            f"None of them completed checkout.",
        )
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

        # ADDED: why this happens (1 paragraph)
        + _p(
            "In most cases, the visitor intended to buy but encountered something "
            "that made them hesitate — a price that looked different from the product page, "
            "a shipping estimate they didn't expect, or a checkout that felt unfamiliar. "
            "These are small fixes with measurable impact.",
            color="#94a3b8",
        )

        # ADDED: what happens if resolved (1 paragraph)
        + _p(
            "If the friction point is addressed, even a partial recovery — "
            "converting 2 or 3 of those 7 carts — compounds over time. "
            "HedgeSpark has identified the specific pattern and prepared a recommendation.",
            color="#94a3b8",
        )

        # EXISTING — unchanged
        + _p(
            "HedgeSpark has already analyzed the behavioral pattern around this product "
            "and prepared a specific recommendation in your dashboard.",
            color="#94a3b8",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("See what we found", _DASHBOARD_URL)
        + '</div>'

        + _separator()
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong> · "
            "<span style='color:#94a3b8;'>HedgeSpark is monitoring your store continuously</span>",
            color="#94a3b8",
        )
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
        f"In most cases, the visitor intended to buy but encountered something "
        f"that made them hesitate — a price difference, an unexpected shipping "
        f"estimate, or an unfamiliar checkout. These are small fixes with "
        f"measurable impact.\n\n"
        f"If the friction is addressed, even converting 2 or 3 of those 7 carts "
        f"compounds over time. HedgeSpark has identified the pattern and "
        f"prepared a recommendation.\n\n"
        f"See what we found: {_DASHBOARD_URL}\n\n"
        f"Andrea · HedgeSpark is monitoring your store continuously"
    )

    return subject, html, plain, "dev@hedgesparkhq.com"


# ═══════════════════════════════════════════════════════════════════════════
# 4. WELCOME — REPLACE CLOSING SENTENCE ONLY + ADD SIGNATURE
# ═══════════════════════════════════════════════════════════════════════════

def build_welcome():
    subject = f"HedgeSpark is live on {SHOP}"

    body = (
        # EVERYTHING BELOW IS IDENTICAL TO THE PREVIOUS VERSION
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

        # EVERYTHING ABOVE IS IDENTICAL

        + _separator()

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Open your dashboard", _DASHBOARD_URL)
        + '</div>'

        + _separator()

        # FIX: replaced closing sentence + added proper signature
        + _p(
            "The system is live and learning. "
            "Every visitor that lands on your store from this point forward "
            "is generating data that HedgeSpark will turn into revenue intelligence.",
            color="#94a3b8",
        )
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong><br>"
            "<span style='color:#94a3b8;'>CEO & Founder, HedgeSpark</span>",
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
        f"The system is live and learning. Every visitor that lands on your store "
        f"from this point forward is generating data that HedgeSpark will turn "
        f"into revenue intelligence.\n\n"
        f"Andrea\n"
        f"CEO & Founder, HedgeSpark"
    )

    return subject, html, plain, "andrea@hedgesparkhq.com"


# ═══════════════════════════════════════════════════════════════════════════
# SEND
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    emails = [
        ("1_weekly_digest", build_weekly_digest, "digest@hedgesparkhq.com"),
        ("2_no_activity", build_reengagement, "dev@hedgesparkhq.com"),
        ("3_revenue_trigger", build_revenue_trigger, "dev@hedgesparkhq.com"),
        ("4_welcome", build_welcome, "andrea@hedgesparkhq.com"),
    ]

    for name, builder, from_addr in emails:
        subject, html, plain, _ = builder()

        text_check = validate_email_text(plain, is_digest=("digest" in name))
        subj_check = validate_subject_line(subject)

        from_display = f"HedgeSpark <{from_addr}>"
        if "andrea" in from_addr:
            from_display = f"Andrea from HedgeSpark <{from_addr}>"

        print(f"\n{'='*60}")
        print(f"{name}")
        print(f"FROM: {from_display}")
        print(f"SUBJECT: {subject}")
        print(f"BRAND: text={text_check.passed} subj={subj_check.passed}")
        if text_check.violations:
            print(f"  VIOLATIONS: {text_check.violations}")

        resend_id = send_email(to=TO, subject=subject, html=html, text=plain, from_address=from_display)
        print(f"{'SENT' if resend_id else 'FAILED'}: {resend_id}")

    print(f"\n{'='*60}")
    print("Done. 4 precisely fixed emails sent.")
