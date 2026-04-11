"""
email_templates.py — Minimal email templates for merchant lifecycle emails.

Public interface:
    render_email(email_type, context) -> (subject, html, plain_text)

Templates:
    welcome           — install confirmed, what happens next
    beta_welcome      — beta program onboarding, branded
    setup_incomplete   — onboarding stuck, action needed
    first_insight      — first signal found, come look
    connection_issue   — store connection lost, needs attention

Design: dark theme matching dashboard, responsive, minimal HTML.
"""
from __future__ import annotations

_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
_SUPPORT_EMAIL = "dev@hedgesparkhq.com"
_LOGO_URL = "https://app.hedgesparkhq.com/logo-beta-v2.png"


def _brand_wordmark(font_size: int = 18, letter_spacing: str = "0.3px") -> str:
    """Inline 'HedgeSpark' wordmark with brand gradient.

    Modern email clients (Apple Mail, iOS, Gmail Web) render the linear-gradient
    via -webkit-background-clip:text. Legacy clients (Outlook, Yahoo) ignore
    the gradient and fall back to the magenta middle stop (#c026d3) — still
    on-brand, never the off-brand lilac it used to be.
    """
    return (
        f'<span style="'
        f'font-size:{font_size}px;'
        f'font-weight:800;'
        f'letter-spacing:{letter_spacing};'
        f'color:#c026d3;'
        f'background:linear-gradient(135deg,#7c3aed 0%,#a855f7 25%,#c026d3 50%,#e8567a 75%,#f97316 100%);'
        f'-webkit-background-clip:text;'
        f'background-clip:text;'
        f'-webkit-text-fill-color:transparent;'
        f'">HedgeSpark</span>'
    )


# ---------------------------------------------------------------------------
# Shared HTML wrapper
# ---------------------------------------------------------------------------

def _wrap_html(title: str, body_html: str, *, show_logo: bool = False) -> str:
    """Responsive email wrapper — dark theme, 600px max-width, brand palette."""

    logo_block = ""
    if show_logo:
        logo_block = (
            '<tr><td align="center" style="padding:0 0 32px 0;">'
            f'<img src="{_LOGO_URL}" alt="HedgeSpark" '
            'width="240" style="display:block;max-width:240px;height:auto;" />'
            '</td></tr>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#07070f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#07070f;">
<tr><td align="center" style="padding:40px 16px 32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">

<!-- Logo / Header -->
{logo_block or f'<tr><td style="padding:0 0 24px 0;">{_brand_wordmark(font_size=18)}</td></tr>'}

<!-- Body -->
<tr><td style="background:#0e0e1a;border:1px solid rgba(167,139,250,0.08);border-radius:16px;padding:36px 32px;">
{body_html}
</td></tr>

<!-- Footer -->
<tr><td style="padding:28px 0 0 0;text-align:center;">
<p style="margin:0;font-size:11px;color:#475569;letter-spacing:0.3px;">
HedgeSpark &middot; AI Commerce Intelligence for Shopify
</p>
<p style="margin:8px 0 0 0;font-size:11px;color:#334155;">
Questions? Reply to this email or contact <a href="mailto:{_SUPPORT_EMAIL}" style="color:#a78bfa;text-decoration:none;">{_SUPPORT_EMAIL}</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _button(text: str, url: str) -> str:
    """CTA button — amber-to-violet gradient, rounded, email-safe fallback."""
    return (
        f'<a href="{url}" style="display:inline-block;'
        f'background:linear-gradient(135deg,#d4893a 0%,#a855f7 100%);'
        f'background-color:#c47a3e;'
        f'color:#ffffff;font-size:15px;font-weight:600;padding:14px 36px;'
        f'border-radius:10px;text-decoration:none;margin-top:20px;'
        f'letter-spacing:0.3px;">{text}</a>'
    )


def _p(text: str, color: str = "#c8d1dc") -> str:
    return f'<p style="margin:0 0 16px 0;font-size:14px;line-height:1.7;color:{color};">{text}</p>'


def _heading(text: str, *, color: str = "#f1f5f9") -> str:
    return f'<h2 style="margin:0 0 16px 0;font-size:20px;font-weight:700;color:{color};letter-spacing:-0.2px;">{text}</h2>'


def _section_title(text: str, *, accent: str = "warm") -> str:
    """Section heading — alternates warm (amber) and cool (violet) accent."""
    if accent == "cool":
        border_color = "rgba(167,139,250,0.4)"
        text_color = "#c4b5fd"
    else:
        border_color = "rgba(212,137,58,0.5)"
        text_color = "#e8a04e"
    return (
        f'<div style="margin:30px 0 16px 0;padding-bottom:8px;'
        f'border-bottom:2px solid {border_color};">'
        f'<h3 style="margin:0;font-size:15px;font-weight:700;color:{text_color};'
        f'letter-spacing:0.4px;text-transform:uppercase;">{text}</h3>'
        f'</div>'
    )


def _separator() -> str:
    return '<hr style="border:none;border-top:1px solid rgba(167,139,250,0.1);margin:28px 0;" />'


def _step(number: int, title: str, text: str) -> str:
    """Numbered onboarding step — table-based for email compat."""
    # Alternate circle colors: odd=amber-warm, even=violet
    circle_bg = "#a855f7" if number % 2 == 0 else "#d4893a"
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:18px;width:100%;">'
        f'<tr>'
        f'<td valign="top" style="width:32px;padding-right:12px;">'
        f'<div style="width:26px;height:26px;border-radius:13px;background:{circle_bg};'
        f'color:#fff;font-size:13px;font-weight:700;text-align:center;line-height:26px;">{number}</div>'
        f'</td>'
        f'<td>'
        f'<div style="font-size:14px;font-weight:700;color:#f1f5f9;margin-bottom:3px;">{title}</div>'
        f'<div style="font-size:13px;line-height:1.7;color:#94a3b8;">{text}</div>'
        f'</td>'
        f'</tr>'
        f'</table>'
    )


def _bullet(text: str, *, accent: str = "#a78bfa") -> str:
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">'
        f'<tr>'
        f'<td valign="top" style="padding-right:10px;color:{accent};font-size:14px;line-height:1.7;">&#x2022;</td>'
        f'<td style="font-size:13px;line-height:1.7;color:#94a3b8;">{text}</td>'
        f'</tr>'
        f'</table>'
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _render_welcome(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")

    body = (
        _heading(f"HedgeSpark is watching {shop_name}")
        + _p(f"HedgeSpark is now connected to <strong style='color:#f1f5f9;'>{shop_name}</strong>.")
        + _section_title("What's happening right now", accent="warm")
        + _bullet("Visitor tracking is <strong style='color:#e2e8f0;'>active</strong> — every pageview, scroll, and cart event")
        + _bullet("Product intelligence builds over the <strong style='color:#e2e8f0;'>first 24 hours</strong>")
        + _bullet("Signals appear when HedgeSpark finds <strong style='color:#e2e8f0;'>revenue you're losing</strong>")
        + _separator()
        + _section_title("What makes this different", accent="cool")
        + _p(
            "HedgeSpark doesn't just show you data. It tells you what to fix, "
            "deploys the fix, and <strong style='color:#e2e8f0;'>proves whether it worked</strong> "
            "— with a real control group.",
        )
        + _p(
            "The longer it runs, the smarter it gets for your store.",
            color="#94a3b8",
        )
        + _button("Open your dashboard", _DASHBOARD_URL)
        + _p(
            "No action needed from you. HedgeSpark is already learning.",
            color="#64748b",
        )
    )

    subject = f"HedgeSpark is live on {shop_name}"

    plain = (
        f"HedgeSpark is watching {shop_name}\n\n"
        f"What's happening right now:\n"
        f"- Visitor tracking is active — every pageview, scroll, and cart event\n"
        f"- Product intelligence builds over the first 24 hours\n"
        f"- Signals appear when HedgeSpark finds revenue you're losing\n\n"
        f"What makes this different:\n"
        f"HedgeSpark doesn't just show you data. It tells you what to fix, "
        f"deploys the fix, and proves whether it worked — with a real control group.\n"
        f"The longer it runs, the smarter it gets for your store.\n\n"
        f"Open your dashboard: {_DASHBOARD_URL}\n\n"
        f"No action needed. HedgeSpark is already learning.\n\n"
        f"Questions? {_SUPPORT_EMAIL}"
    )

    return subject, _wrap_html(subject, body, show_logo=True), plain


def _render_beta_welcome(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")
    merchant_name = ctx.get("merchant_name", "")

    greeting = f"Hi {merchant_name}," if merchant_name else "Hi,"

    body = (
        # Intro — selection + ambition + architecture signal
        _p(greeting, color="#f1f5f9")
        + _p(
            f"You've been <strong style='color:#e8a04e;'>carefully selected</strong> "
            f"to join the HedgeSpark private beta. "
            f"This is a confidential early access program — only a small number "
            f"of merchants are participating at this stage."
        )
        + _p(
            "We're building HedgeSpark to be the most technically advanced AI commerce "
            "intelligence system for Shopify — deliberately architected to scale, adapt, "
            "and compound in value as your store grows. "
            "That level of ambition only works if we build it alongside "
            "real merchants, with real stores, generating real revenue."
        )

        # What HedgeSpark does — revenue-focused
        + _section_title("What HedgeSpark does")
        + _p(
            "HedgeSpark is an AI intelligence layer that sits on top of your Shopify store. "
            "It continuously analyzes visitor behavior and turns it into revenue signals "
            "you can act on — the kind of patterns that are invisible in standard analytics."
        )
        + _bullet("Identifies products with high purchase intent that aren't converting — and tells you why")
        + _bullet("Detects where revenue is leaking: drop-offs, hesitation patterns, missed opportunities")
        + _bullet("Generates targeted nudges designed to turn undecided visitors into paying customers")
        + _p(
            "The goal is concrete: <strong style='color:#f1f5f9;'>more revenue from the traffic you already have</strong>.",
            color="#94a3b8",
        )

        # Onboarding — concrete step-by-step sequence
        + _section_title("What happens when you start", accent="cool")
        + _step(
            1, "We connect to your store",
            f"Once you open your dashboard, HedgeSpark connects to "
            f"<strong style='color:#e2e8f0;'>{shop_name}</strong> via Shopify. "
            f"This is automatic — it takes a few seconds. "
            f"From this point, we begin collecting visitor behavior data."
        )
        + _step(
            2, "Visitor tracking activates",
            "A lightweight tracking script loads on your storefront. "
            "It records page views, product interest, and browsing patterns — "
            "no personal data, no impact on page speed. "
            "You'll see your first visitor data in the dashboard within minutes."
        )
        + _step(
            3, "You install the purchase pixel",
            "To connect visitor behavior to actual sales, you'll need to add a small "
            "tracking pixel to your order confirmation page. "
            "The dashboard will walk you through it step by step. "
            "Without this pixel, HedgeSpark can analyze behavior but can't attribute revenue."
        )
        + _step(
            4, "Lite insights start appearing",
            "Within the first few days, HedgeSpark surfaces your initial analytics: "
            "which products attract the most attention, where visitors hesitate, "
            "and where they leave. This is your Lite intelligence baseline."
        )
        + _step(
            5, "Pro features unlock progressively",
            "Over weeks 2–3, we activate deeper capabilities: "
            "behavioral scoring, smart nudges, conversion signals, and revenue attribution. "
            "We calibrate these with your specific store data — not generic defaults."
        )
        + _step(
            6, "The system compounds",
            "HedgeSpark gets sharper every week. More data means tighter models, "
            "more accurate signals, and higher-impact nudges. "
            "We ship improvements continuously — your feedback on Monday can be live by Friday."
        )

        # Architecture + team intensity
        + _section_title("How we build")
        + _p(
            "HedgeSpark is built on a layered architecture — real-time event processing, "
            "behavioral modeling, and an AI engine that evolves with every data point. "
            "This is not a dashboard bolted onto an API. It's a system designed from the "
            "ground up to get smarter over time."
        )
        + _p(
            "We operate on a fast development cycle. "
            "New features, fixes, and improvements ship every week — not every quarter. "
            "Beta merchants see changes in days, not months.",
            color="#94a3b8",
        )

        # Chatbot as primary interface + feedback
        + _section_title("Your command center", accent="cool")
        + _p(
            "The <strong style='color:#f1f5f9;'>in-app chatbot</strong> is your primary "
            "interface to HedgeSpark. Use it to:"
        )
        + _bullet("Ask questions about your data, signals, or any feature")
        + _bullet("Report issues or request changes — directly, in real time")
        + _bullet("Get guided help with setup steps like the purchase pixel")
        + _bullet("Request specific analyses or ask why a metric changed")
        + _p(
            "Think of it as your direct line to the system and to us. "
            "It's faster than email, and we monitor it actively.",
            color="#94a3b8",
        )
        + _p(
            "For longer-form feedback, strategic proposals, or anything that needs a detailed "
            "conversation, email "
            f"<a href='mailto:{_SUPPORT_EMAIL}' style='color:#a78bfa;text-decoration:none;'>"
            f"{_SUPPORT_EMAIL}</a>.",
            color="#94a3b8",
        )

        # What you get — stronger beta advantage
        + _section_title("Your beta advantage")
        + _p(
            "Being in this early is not symbolic. "
            "Beta merchants who actively participate will receive concrete, lasting benefits:",
        )
        + _bullet("<strong style='color:#e2e8f0;'>Full access</strong> to every feature we ship — free for the entire beta period")
        + _bullet(
            "<strong style='color:#e2e8f0;'>Significant discounts or free months</strong> at launch — "
            "the level of benefit scales with your level of involvement"
        )
        + _bullet(
            "<strong style='color:#e2e8f0;'>Priority access</strong> to Pro features and new capabilities "
            "before they reach the general release"
        )
        + _bullet(
            "<strong style='color:#e2e8f0;'>Direct influence</strong> on the roadmap — "
            "you're not submitting feature requests into a queue, you're shaping the product with us"
        )

        # Confidentiality + security
        + _section_title("Confidentiality & security", accent="cool")
        + _p(
            "Your store data is encrypted at rest and in transit. "
            "We follow GDPR requirements and take cybersecurity seriously — "
            "this is non-negotiable for us, even at this early stage.",
            color="#94a3b8",
        )
        + _p(
            "We never share merchant data with third parties. "
            "HedgeSpark processes behavioral signals only — no personal customer data leaves your store.",
            color="#94a3b8",
        )
        + _p(
            "This beta is private and invite-only. "
            "We ask that you keep product details confidential for now.",
            color="#94a3b8",
        )
        + _p(
            "We're an early-stage company. Trust is something we earn — "
            "and we intend to earn it through transparency, reliability, and results.",
            color="#94a3b8",
        )

        + _separator()

        # CTA
        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Start your onboarding", _DASHBOARD_URL)
        + '</div>'

        + _separator()

        # Signature
        + _p("Looking forward to building this together,", color="#94a3b8")
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong><br>"
            + _brand_wordmark(font_size=14),
            color="#94a3b8",
        )
    )

    subject = "You're in — HedgeSpark Private Beta"

    plain = (
        f"{greeting}\n\n"
        f"You've been carefully selected to join the HedgeSpark private beta. "
        f"This is a confidential early access program — only a small number "
        f"of merchants are participating at this stage.\n\n"
        f"We're building HedgeSpark to be the most technically advanced AI commerce "
        f"intelligence system for Shopify — deliberately architected to scale, adapt, "
        f"and compound in value as your store grows. That level of ambition only works "
        f"if we build it alongside real merchants, with real stores, generating real revenue.\n\n"
        f"WHAT HEDGESPARK DOES\n"
        f"HedgeSpark is an AI intelligence layer that sits on top of your Shopify store. "
        f"It continuously analyzes visitor behavior and turns it into revenue signals "
        f"you can act on.\n"
        f"- Identifies products with high purchase intent that aren't converting\n"
        f"- Detects where revenue is leaking: drop-offs, hesitation, missed opportunities\n"
        f"- Generates targeted nudges to turn undecided visitors into paying customers\n"
        f"The goal is concrete: more revenue from the traffic you already have.\n\n"
        f"WHAT HAPPENS WHEN YOU START\n"
        f"1. We connect to your store — automatic via Shopify, takes seconds. "
        f"We begin collecting visitor behavior data.\n"
        f"2. Visitor tracking activates — a lightweight script records page views, "
        f"product interest, and browsing patterns. First data in minutes.\n"
        f"3. You install the purchase pixel — connects visitor behavior to actual "
        f"sales. Dashboard walks you through it. Without it, we can analyze "
        f"behavior but can't attribute revenue.\n"
        f"4. Lite insights start appearing — within the first few days: which "
        f"products attract attention, where visitors hesitate, where they leave.\n"
        f"5. Pro features unlock progressively — weeks 2-3: behavioral scoring, "
        f"smart nudges, conversion signals, revenue attribution. Calibrated to "
        f"your store data.\n"
        f"6. The system compounds — more data means tighter models, more accurate "
        f"signals, higher-impact nudges. We ship improvements every week.\n\n"
        f"HOW WE BUILD\n"
        f"HedgeSpark is built on a layered architecture — real-time event processing, "
        f"behavioral modeling, and an AI engine that evolves with every data point. "
        f"We operate on a fast development cycle. New features ship every week — "
        f"not every quarter.\n\n"
        f"YOUR COMMAND CENTER\n"
        f"The in-app chatbot is your primary interface to HedgeSpark:\n"
        f"- Ask questions about your data, signals, or any feature\n"
        f"- Report issues or request changes — directly, in real time\n"
        f"- Get guided help with setup steps like the purchase pixel\n"
        f"- Request specific analyses or ask why a metric changed\n"
        f"It's faster than email, and we monitor it actively.\n"
        f"For longer-form feedback or strategic proposals: {_SUPPORT_EMAIL}\n\n"
        f"YOUR BETA ADVANTAGE\n"
        f"Being in this early is not symbolic. Active participants receive:\n"
        f"- Full access to every feature we ship — free for the entire beta\n"
        f"- Significant discounts or free months at launch — scales with involvement\n"
        f"- Priority access to Pro features before general release\n"
        f"- Direct influence on the roadmap — not a feature request queue\n\n"
        f"CONFIDENTIALITY & SECURITY\n"
        f"Your data is encrypted at rest and in transit. We follow GDPR requirements "
        f"and take cybersecurity seriously. We never share merchant data with third "
        f"parties. This beta is private and invite-only.\n\n"
        f"We're an early-stage company. Trust is something we earn — and we intend "
        f"to earn it through transparency, reliability, and results.\n\n"
        f"Start your onboarding: {_DASHBOARD_URL}\n\n"
        f"Looking forward to building this together,\n"
        f"Andrea\n"
        f"HedgeSpark"
    )

    return subject, _wrap_html(subject, body, show_logo=True), plain


def _render_setup_incomplete(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")
    issue = ctx.get("issue", "setup is incomplete")
    hours_since = ctx.get("hours_since_install", 24)

    body = (
        _heading("Your setup needs attention")
        + _p(
            f"HedgeSpark was installed on <strong style='color:#f1f5f9;'>{shop_name}</strong> "
            f"{hours_since} hours ago, but {issue}."
        )
        + _p(
            "Until this is resolved, HedgeSpark can't track your visitors or generate insights.",
            color="#94a3b8",
        )
        + _p(
            "Open your dashboard — the setup panel will guide you through what's needed. "
            "Most issues resolve in under a minute.",
            color="#94a3b8",
        )
        + _button("Fix setup now", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "If you need help, reply to this email and we'll assist you directly."
        + "</p>"
    )

    subject = f"Action needed: {shop_name} setup incomplete"

    plain = (
        f"Your setup needs attention\n\n"
        f"HedgeSpark was installed on {shop_name} {hours_since} hours ago, "
        f"but {issue}.\n\n"
        f"Until this is resolved, HedgeSpark can't track visitors or generate insights.\n\n"
        f"Open your dashboard to fix: {_DASHBOARD_URL}\n\n"
        f"Need help? Reply to this email.\n\n"
        f"— HedgeSpark"
    )

    return subject, _wrap_html(subject, body), plain


def _render_first_insight(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")
    signal_count = ctx.get("signal_count", 1)
    top_signal = ctx.get("top_signal", "a product showing unusual visitor behavior")

    s = "s" if signal_count != 1 else ""

    body = (
        _heading(f"{signal_count} product{s} need attention")
        + _p(
            f"HedgeSpark found <strong style='color:#f1f5f9;'>"
            f"{signal_count} signal{s}</strong> on {shop_name}."
        )
        + _p(
            f"Top finding: <strong style='color:#e8a04e;'>{top_signal}</strong>",
            color="#e2e8f0",
        )
        + _p(
            "This isn't a guess. HedgeSpark measured scroll depth, dwell time, "
            "and cart behavior across real visitors to detect this pattern.",
            color="#94a3b8",
        )
        + _p(
            "Signals will get sharper every week as the system learns from your store.",
            color="#64748b",
        )
        + _button("See your signals", _DASHBOARD_URL)
    )

    subject = f"{shop_name}: {signal_count} product{s} need attention"

    plain = (
        f"{signal_count} product{s} need attention\n\n"
        f"HedgeSpark found {signal_count} signal{s} on {shop_name}.\n\n"
        f"Top finding: {top_signal}\n\n"
        f"This isn't a guess. HedgeSpark measured scroll depth, dwell time, "
        f"and cart behavior across real visitors to detect this pattern.\n\n"
        f"Signals will get sharper every week.\n\n"
        f"See your signals: {_DASHBOARD_URL}\n\n"
        f"— HedgeSpark"
    )

    return subject, _wrap_html(subject, body), plain


def _render_connection_issue(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")
    issue = ctx.get("issue", "the connection to your store was lost")
    stuck_minutes = ctx.get("stuck_minutes", 0)

    hours = max(1, stuck_minutes // 60) if stuck_minutes else None
    time_str = f" for {hours} hour{'s' if hours and hours != 1 else ''}" if hours else ""

    body = (
        _heading("Connection issue detected")
        + _p(
            f"HedgeSpark has been unable to connect to "
            f"<strong style='color:#f1f5f9;'>{shop_name}</strong>{time_str}."
        )
        + _p(
            f"The issue: <strong style='color:#fbbf24;'>{issue}</strong>",
            color="#e2e8f0",
        )
        + _p(
            "While disconnected, visitor tracking and insights are paused. "
            "Open your dashboard to reconnect — it usually takes a few seconds.",
            color="#94a3b8",
        )
        + _button("Reconnect now", _DASHBOARD_URL)
        + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        + "This can happen after Shopify updates or permission changes. "
        + "If the issue persists, reply to this email."
        + "</p>"
    )

    subject = f"Connection issue — {shop_name}"

    plain = (
        f"Connection issue detected\n\n"
        f"HedgeSpark has been unable to connect to {shop_name}{time_str}.\n\n"
        f"Issue: {issue}\n\n"
        f"While disconnected, visitor tracking and insights are paused.\n\n"
        f"Reconnect: {_DASHBOARD_URL}\n\n"
        f"If the issue persists, reply to this email.\n\n"
        f"— HedgeSpark"
    )

    return subject, _wrap_html(subject, body), plain


# ---------------------------------------------------------------------------
# 48h Follow-up variants (behavioral)
# ---------------------------------------------------------------------------

def _followup_signature() -> str:
    """Shared signature block for all follow-ups — identical to beta_welcome."""
    return (
        _separator()
        + _p("Talk soon,", color="#94a3b8")
        + _p(
            "<strong style='color:#f1f5f9;'>Andrea</strong><br>"
            + _brand_wordmark(font_size=14),
            color="#94a3b8",
        )
    )


def _render_followup_opened(ctx: dict) -> tuple[str, str, str]:
    """Variant 1 — Opened but did not click. Reduce friction, reassure."""
    merchant_name = ctx.get("merchant_name", "")
    greeting = f"Hi {merchant_name}," if merchant_name else "Hi,"

    body = (
        _p(greeting, color="#f1f5f9")
        + _p(
            "You opened my last email — so I know HedgeSpark caught your attention. "
            "I want to make sure nothing is holding you back from getting started."
        )
        + _p(
            "If it felt like a lot to take in, here's the simple version: "
            "<strong style='color:#f1f5f9;'>you don't need to prepare anything</strong>. "
            "The entire onboarding is guided.",
        )

        + _section_title("What actually happens", accent="cool")
        + _step(
            1, "Open your dashboard",
            "Go to <strong style='color:#e2e8f0;'>app.hedgesparkhq.com</strong>. "
            "HedgeSpark connects to your store automatically via Shopify. Takes seconds."
        )
        + _step(
            2, "Data starts flowing",
            "A lightweight script begins recording visitor behavior — "
            "page views, product interest, browsing patterns. No code to write. "
            "First data shows up in minutes."
        )
        + _step(
            3, "We guide the rest",
            "The purchase pixel, Lite insights, Pro feature unlocks — "
            "the dashboard walks you through each step when the time comes. "
            "Nothing is expected upfront."
        )

        + _section_title("The chatbot handles everything")
        + _p(
            "Once you're inside the dashboard, the "
            "<strong style='color:#f1f5f9;'>in-app chatbot</strong> "
            "is your primary interface. It's not a support widget — "
            "it's how you interact with the system:"
        )
        + _bullet("Ask it anything about your data or setup")
        + _bullet("Tell it if something looks wrong — it routes directly to the team")
        + _bullet("Use it to request analyses or get pixel setup help")
        + _p(
            "You're not navigating this alone. The system responds, and so do we.",
            color="#94a3b8",
        )

        + _separator()
        + _p(
            "Other beta merchants are already onboarding and seeing their first signals. "
            "Your spot is reserved — but early data means better results.",
            color="#94a3b8",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Start onboarding", _DASHBOARD_URL)
        + '</div>'

        + _followup_signature()
    )

    subject = "The setup is simpler than it looks"

    plain = (
        f"{greeting}\n\n"
        f"You opened my last email — so I know HedgeSpark caught your attention. "
        f"I want to make sure nothing is holding you back.\n\n"
        f"You don't need to prepare anything. The entire onboarding is guided.\n\n"
        f"WHAT ACTUALLY HAPPENS\n"
        f"1. Open your dashboard at app.hedgesparkhq.com — connects automatically.\n"
        f"2. Data starts flowing — visitor behavior recorded in minutes.\n"
        f"3. We guide the rest — pixel, insights, Pro unlock, all step by step.\n\n"
        f"THE CHATBOT HANDLES EVERYTHING\n"
        f"The in-app chatbot is your primary interface:\n"
        f"- Ask anything about your data or setup\n"
        f"- Report issues — routes directly to the team\n"
        f"- Request analyses or get pixel setup help\n\n"
        f"Other beta merchants are already onboarding. Your spot is reserved — "
        f"but early data means better results.\n\n"
        f"Start onboarding: {_DASHBOARD_URL}\n\n"
        f"Talk soon,\n"
        f"Andrea\n"
        f"HedgeSpark"
    )

    return subject, _wrap_html(subject, body, show_logo=True), plain


def _render_followup_clicked(ctx: dict) -> tuple[str, str, str]:
    """Variant 2 — Clicked but didn't complete onboarding. Unblock."""
    merchant_name = ctx.get("merchant_name", "")
    greeting = f"Hi {merchant_name}," if merchant_name else "Hi,"

    body = (
        _p(greeting, color="#f1f5f9")
        + _p(
            "I noticed you opened the dashboard but didn't finish setting up. "
            "That's completely normal — something probably interrupted you, "
            "or a step wasn't clear enough."
        )
        + _p(
            "I want to make sure you get through it, because "
            "<strong style='color:#f1f5f9;'>the sooner data starts flowing, "
            "the sooner HedgeSpark can find revenue you're missing</strong>."
        )

        + _section_title("Where you probably are", accent="cool")
        + _p(
            "Most merchants who pause during setup stop at one of these points. "
            "Here's exactly what each step requires:",
        )
        + _step(
            1, "Store connection",
            "This happens automatically when you open the dashboard. "
            "If it didn't complete, try refreshing. If it failed, "
            "<strong style='color:#e2e8f0;'>tell the chatbot</strong> — we'll fix it in real time."
        )
        + _step(
            2, "Visitor tracking",
            "Activates on its own after connection. No action needed from you. "
            "If your dashboard shows zero visitors after 10 minutes, "
            "open the chatbot and tell us — it's a one-minute fix on our side."
        )
        + _step(
            3, "Purchase pixel",
            "This is the step that requires a manual action: adding a small snippet "
            "to your order confirmation page. The dashboard shows you exactly where. "
            "If you're unsure, <strong style='color:#e2e8f0;'>ask the chatbot to guide you through it "
            "step by step</strong>. It takes under 3 minutes."
        )

        + _section_title("If anything stopped you — write here")
        + _p(
            "The <strong style='color:#f1f5f9;'>in-app chatbot</strong> is the fastest way "
            "to unblock yourself. It's not generic support — it connects directly to the "
            "system and to the team. Use it for:"
        )
        + _bullet("Setup issues — we diagnose and fix while you wait")
        + _bullet("Confusing steps — we walk you through them live")
        + _bullet("Errors or unexpected behavior — we see what you see")
        + _p(
            "If you'd rather write a longer message: "
            f"<a href='mailto:{_SUPPORT_EMAIL}' style='color:#a78bfa;text-decoration:none;'>"
            f"{_SUPPORT_EMAIL}</a>.",
            color="#94a3b8",
        )

        + _separator()
        + _p(
            "You've already done the hardest part — you showed up. "
            "The rest is configuration, and we handle most of it with you.",
            color="#94a3b8",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Continue onboarding", _DASHBOARD_URL)
        + '</div>'

        + _followup_signature()
    )

    subject = "You were almost in — let's finish"

    plain = (
        f"{greeting}\n\n"
        f"I noticed you opened the dashboard but didn't finish setting up. "
        f"That's normal — something probably interrupted you.\n\n"
        f"The sooner data starts flowing, the sooner HedgeSpark can find "
        f"revenue you're missing.\n\n"
        f"WHERE YOU PROBABLY ARE\n"
        f"1. Store connection — automatic on dashboard open. If it failed, "
        f"tell the chatbot.\n"
        f"2. Visitor tracking — activates on its own. If zero visitors after "
        f"10 min, tell the chatbot.\n"
        f"3. Purchase pixel — one manual step: small snippet on order "
        f"confirmation page. Dashboard guides you. Ask the chatbot for help.\n\n"
        f"IF ANYTHING STOPPED YOU\n"
        f"The in-app chatbot is the fastest way to unblock yourself:\n"
        f"- Setup issues — we diagnose and fix while you wait\n"
        f"- Confusing steps — we walk you through them live\n"
        f"- Errors — we see what you see\n"
        f"Or email: {_SUPPORT_EMAIL}\n\n"
        f"You've already done the hardest part — you showed up.\n\n"
        f"Continue onboarding: {_DASHBOARD_URL}\n\n"
        f"Talk soon,\n"
        f"Andrea\n"
        f"HedgeSpark"
    )

    return subject, _wrap_html(subject, body, show_logo=True), plain


def _render_followup_noopen(ctx: dict) -> tuple[str, str, str]:
    """Variant 3 — Did not open. Short, sharp, curiosity-driven."""
    merchant_name = ctx.get("merchant_name", "")
    greeting = f"Hi {merchant_name}," if merchant_name else "Hi,"

    body = (
        _p(greeting, color="#f1f5f9")
        + _p(
            "I sent you a beta invite a couple of days ago. "
            "In case it got buried — here's the short version."
        )

        + _section_title("What this is", accent="cool")
        + _p(
            "HedgeSpark is an AI intelligence layer for Shopify. "
            "It analyzes your visitors and finds revenue you're currently losing — "
            "products with high intent that aren't converting, "
            "drop-off patterns, missed opportunities."
        )
        + _p(
            "You were selected for the private beta. "
            "A small number of merchants are testing it right now, "
            "and the first stores are already seeing initial signals.",
            color="#94a3b8",
        )

        + _section_title("What it takes to start")
        + _bullet("Open the dashboard — store connects automatically")
        + _bullet("Visitor tracking starts on its own — first data in minutes")
        + _bullet("The chatbot guides you through every step after that")
        + _p(
            "No preparation needed. No risk. Free during beta.",
            color="#94a3b8",
        )

        + _section_title("Why it matters now", accent="cool")
        + _p(
            "HedgeSpark gets smarter with data. "
            "The earlier you start, the more it learns about your store — "
            "and the better your results when Pro features unlock."
        )
        + _p(
            "Beta participants who engage actively also get priority access, "
            "significant discounts, and direct influence on the roadmap.",
            color="#94a3b8",
        )

        + '<div style="text-align:center;margin:8px 0 0 0;">'
        + _button("Start onboarding", _DASHBOARD_URL)
        + '</div>'

        + _followup_signature()
    )

    subject = "Quick note — your HedgeSpark beta access"

    plain = (
        f"{greeting}\n\n"
        f"I sent you a beta invite a couple of days ago. "
        f"In case it got buried — here's the short version.\n\n"
        f"WHAT THIS IS\n"
        f"HedgeSpark is an AI intelligence layer for Shopify. It finds revenue "
        f"you're losing — high-intent products not converting, drop-off patterns, "
        f"missed opportunities.\n\n"
        f"You were selected for the private beta. A small number of merchants "
        f"are testing it now, and first stores are already seeing signals.\n\n"
        f"WHAT IT TAKES TO START\n"
        f"- Open the dashboard — store connects automatically\n"
        f"- Visitor tracking starts on its own — first data in minutes\n"
        f"- The chatbot guides every step after that\n"
        f"No preparation. No risk. Free during beta.\n\n"
        f"WHY IT MATTERS NOW\n"
        f"The earlier you start, the more HedgeSpark learns about your store. "
        f"Active participants get priority access, discounts, and roadmap influence.\n\n"
        f"Start onboarding: {_DASHBOARD_URL}\n\n"
        f"Talk soon,\n"
        f"Andrea\n"
        f"HedgeSpark"
    )

    return subject, _wrap_html(subject, body, show_logo=True), plain


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_RENDERERS = {
    "welcome": _render_welcome,
    "beta_welcome": _render_beta_welcome,
    "followup_opened": _render_followup_opened,
    "followup_clicked": _render_followup_clicked,
    "followup_noopen": _render_followup_noopen,
    "setup_incomplete": _render_setup_incomplete,
    "first_insight": _render_first_insight,
    "connection_issue": _render_connection_issue,
}


def render_email(
    email_type: str,
    context: dict,
) -> tuple[str, str, str]:
    """
    Render a lifecycle email.

    Returns (subject, html, plain_text).
    Raises ValueError for unknown email_type.
    """
    renderer = _RENDERERS.get(email_type)
    if not renderer:
        raise ValueError(f"Unknown email_type: {email_type}")
    return renderer(context)
