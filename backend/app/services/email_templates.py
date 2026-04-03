"""
email_templates.py — Minimal email templates for merchant lifecycle emails.

Public interface:
    render_email(email_type, context) -> (subject, html, plain_text)

Templates:
    welcome           — install confirmed, what happens next
    setup_incomplete   — onboarding stuck, action needed
    first_insight      — first signal found, come look
    connection_issue   — store connection lost, needs attention

Design: dark theme matching dashboard, responsive, minimal HTML.
No images, no tracking pixels, no marketing fluff.
"""
from __future__ import annotations

_DASHBOARD_URL = "https://app.hedgesparkhq.com/"
_SUPPORT_EMAIL = "support@hedgesparkhq.com"


# ---------------------------------------------------------------------------
# Shared HTML wrapper
# ---------------------------------------------------------------------------

def _wrap_html(title: str, body_html: str) -> str:
    """Responsive email wrapper — dark theme, 560px max-width."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#080811;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#080811;">
<tr><td align="center" style="padding:32px 16px;">
<table width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">

<!-- Header -->
<tr><td style="padding:0 0 24px 0;">
<span style="font-size:18px;font-weight:700;color:#c4b5fd;letter-spacing:0.5px;">Hedge Spark</span>
</td></tr>

<!-- Body -->
<tr><td style="background:#0f0f1a;border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:32px 28px;">
{body_html}
</td></tr>

<!-- Footer -->
<tr><td style="padding:24px 0 0 0;text-align:center;">
<p style="margin:0;font-size:11px;color:#475569;">
Hedge Spark &middot; AI Commerce Intelligence for Shopify
</p>
<p style="margin:6px 0 0 0;font-size:11px;color:#334155;">
Questions? Reply to this email or contact <a href="mailto:{_SUPPORT_EMAIL}" style="color:#7c3aed;text-decoration:none;">{_SUPPORT_EMAIL}</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _button(text: str, url: str) -> str:
    """CTA button — violet, rounded."""
    return (
        f'<a href="{url}" style="display:inline-block;background:#7c3aed;color:#fff;'
        f'font-size:14px;font-weight:600;padding:12px 24px;border-radius:10px;'
        f'text-decoration:none;margin-top:20px;">{text}</a>'
    )


def _p(text: str, color: str = "#cbd5e1") -> str:
    return f'<p style="margin:0 0 14px 0;font-size:14px;line-height:1.6;color:{color};">{text}</p>'


def _heading(text: str) -> str:
    return f'<h2 style="margin:0 0 16px 0;font-size:20px;font-weight:700;color:#f1f5f9;">{text}</h2>'


def _bullet(text: str) -> str:
    return (
        f'<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;">'
        f'<span style="color:#7c3aed;font-size:14px;line-height:1.6;">&#x2022;</span>'
        f'<span style="font-size:13px;line-height:1.6;color:#94a3b8;">{text}</span>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _render_welcome(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")

    body = (
        _heading(f"Welcome to Hedge Spark")
        + _p(f"Hedge Spark is now connected to <strong style='color:#f1f5f9;'>{shop_name}</strong> and tracking visitor behavior.")
        + _p("Here's what happens next:")
        + _bullet("Visitor tracking starts <strong style='color:#e2e8f0;'>immediately</strong> — no action needed")
        + _bullet("First insights appear in about <strong style='color:#e2e8f0;'>10 minutes</strong>")
        + _bullet("Full analysis builds over the <strong style='color:#e2e8f0;'>first 24 hours</strong>")
        + _p(
            "One optional step: connect the <strong style='color:#e2e8f0;'>purchase tracking pixel</strong> "
            "so Hedge Spark can see which visitors actually buy. "
            "You'll find the setup guide in your dashboard.",
            color="#94a3b8",
        )
        + _button("Open your dashboard", _DASHBOARD_URL)
    )

    subject = f"Hedge Spark is live on {shop_name}"

    plain = (
        f"Welcome to Hedge Spark\n\n"
        f"Hedge Spark is now connected to {shop_name} and tracking visitor behavior.\n\n"
        f"What happens next:\n"
        f"- Visitor tracking starts immediately\n"
        f"- First insights appear in about 10 minutes\n"
        f"- Full analysis builds over the first 24 hours\n\n"
        f"Optional: connect the purchase tracking pixel in your dashboard "
        f"so Hedge Spark can see which visitors buy.\n\n"
        f"Open your dashboard: {_DASHBOARD_URL}\n\n"
        f"Questions? Contact {_SUPPORT_EMAIL}"
    )

    return subject, _wrap_html(subject, body), plain


def _render_setup_incomplete(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")
    issue = ctx.get("issue", "setup is incomplete")
    hours_since = ctx.get("hours_since_install", 24)

    body = (
        _heading("Your setup needs attention")
        + _p(
            f"Hedge Spark was installed on <strong style='color:#f1f5f9;'>{shop_name}</strong> "
            f"{hours_since} hours ago, but {issue}."
        )
        + _p(
            "Until this is resolved, Hedge Spark can't track your visitors or generate insights.",
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
        f"Hedge Spark was installed on {shop_name} {hours_since} hours ago, "
        f"but {issue}.\n\n"
        f"Until this is resolved, Hedge Spark can't track visitors or generate insights.\n\n"
        f"Open your dashboard to fix: {_DASHBOARD_URL}\n\n"
        f"Need help? Reply to this email.\n\n"
        f"— Hedge Spark"
    )

    return subject, _wrap_html(subject, body), plain


def _render_first_insight(ctx: dict) -> tuple[str, str, str]:
    shop_name = ctx.get("shop_name", "your store")
    signal_count = ctx.get("signal_count", 1)
    top_signal = ctx.get("top_signal", "a product showing unusual visitor behavior")

    body = (
        _heading("Your first insight is ready")
        + _p(
            f"Hedge Spark found <strong style='color:#f1f5f9;'>"
            f"{signal_count} insight{'s' if signal_count != 1 else ''}</strong> "
            f"on {shop_name}."
        )
        + _p(
            f"Top finding: <strong style='color:#c4b5fd;'>{top_signal}</strong>",
            color="#e2e8f0",
        )
        + _p(
            "This means Hedge Spark has enough visitor data to start identifying "
            "revenue opportunities. New insights will keep appearing as traffic flows.",
            color="#94a3b8",
        )
        + _button("See your insights", _DASHBOARD_URL)
    )

    subject = f"First insight ready — {shop_name}"

    plain = (
        f"Your first insight is ready\n\n"
        f"Hedge Spark found {signal_count} insight{'s' if signal_count != 1 else ''} on {shop_name}.\n\n"
        f"Top finding: {top_signal}\n\n"
        f"This means Hedge Spark has enough visitor data to start identifying "
        f"revenue opportunities.\n\n"
        f"See your insights: {_DASHBOARD_URL}\n\n"
        f"— Hedge Spark"
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
            f"Hedge Spark has been unable to connect to "
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
        f"Hedge Spark has been unable to connect to {shop_name}{time_str}.\n\n"
        f"Issue: {issue}\n\n"
        f"While disconnected, visitor tracking and insights are paused.\n\n"
        f"Reconnect: {_DASHBOARD_URL}\n\n"
        f"If the issue persists, reply to this email.\n\n"
        f"— Hedge Spark"
    )

    return subject, _wrap_html(subject, body), plain


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_RENDERERS = {
    "welcome": _render_welcome,
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
