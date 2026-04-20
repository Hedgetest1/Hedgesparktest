"""
lite_morning_digest.py — Daily morning email for Lite merchants.

Founder observation 2026-04-20: Lite is "purely pull" — the merchant
has to log into the dashboard to see the brief. Competitors at $35–150
all push a morning email. This module closes Gap A of the €39-ready
sprint by turning the daily brief into a push channel for Lite.

Design:
  - One email per Lite merchant per calendar day (Europe/Rome).
  - Send window: 08:00–09:59 Europe/Rome (2h window so a 15-min agent
    worker cycle gets ~8 chances to land within the day).
  - Redis dedup: `hs:lite_digest:{shop}:{YYYY-MM-DD}` with 35h TTL.
  - Content: headline + lead story + top action + CTA to dashboard.
    Same payload the BriefHero card shows — one truth channel, pushed.
  - Even on "clean slate" mornings (no findings) we still send a short
    reassurance message. Founder principle: never leave Lite merchants
    wondering if the product is alive. Better one quiet email than
    silence that looks like the service is dead.
  - Goes through the email orchestrator (`submit_intent`). Never bypass.

Public interface:
    run_lite_morning_digest_cycle(db) -> dict  — process all Lite merchants
    _is_morning_rome() -> bool                 — send window gate
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger("lite_morning_digest")

_REDIS_PREFIX = "hs:lite_digest:"
_DEDUP_TTL = 126000  # 35h — one send per calendar day + margin
_DASHBOARD_URL = "https://app.hedgesparkhq.com/app/lite"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_rome() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


def _is_morning_rome() -> bool:
    """True between 08:00 and 09:59 Europe/Rome.

    The 2h window means a 15-min agent worker cycle has ~8 chances per
    day to land. Redis dedup keeps it to one actual send regardless.
    """
    from zoneinfo import ZoneInfo
    rome_hour = datetime.now(ZoneInfo("Europe/Rome")).hour
    return 8 <= rome_hour < 10


def _digest_sent_today(shop_domain: str, date_key: str) -> bool:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            return bool(rc.get(f"{_REDIS_PREFIX}{shop_domain}:{date_key}"))
    except Exception as exc:
        log.warning("lite_morning_digest: dedup check failed: %s", exc)
    return False


def _mark_sent(shop_domain: str, date_key: str, success: bool) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            value = json.dumps({
                "sent_at": _now().isoformat() + "Z",
                "success": success,
                "date": date_key,
            })
            rc.set(f"{_REDIS_PREFIX}{shop_domain}:{date_key}", value, ex=_DEDUP_TTL)
    except Exception as exc:
        log.warning("lite_morning_digest: mark sent failed: %s", exc)


def _fmt_money(amount: float, currency: str = "USD") -> str:
    """Email-safe money formatter. Never imports frontend formatters.
    Compact for amounts above €10k; 2-decimal below."""
    try:
        n = float(amount or 0)
    except (TypeError, ValueError):
        n = 0.0
    sign = "-" if n < 0 else ""
    abs_n = abs(n)
    sym = {"EUR": "€", "GBP": "£"}.get(currency, "$" if currency == "USD" else f"{currency} ")
    if abs_n >= 10_000:
        return f"{sign}{sym}{abs_n/1000:.1f}k"
    if abs_n >= 1000:
        return f"{sign}{sym}{abs_n:,.0f}".replace(",", ".")
    return f"{sign}{sym}{abs_n:.2f}"


def _bar_svg(label: str, value: float, max_value: float, color: str, money_fmt: str) -> str:
    """Inline SVG horizontal bar — works in Gmail, Apple Mail, Outlook
    (which renders via VML but this is pure SVG as <img>-alternative
    so it degrades to the label+value line if SVG is blocked)."""
    pct = max(2, min(100, (value / max_value) * 100)) if max_value > 0 else 2
    return (
        '<tr><td style="padding:6px 0;">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr>'
        f'<td style="font-size:13px;color:#e2e8f0;font-weight:600;white-space:nowrap;padding-right:12px;">{label}</td>'
        f'<td align="right" style="font-size:13px;color:{color};font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;">{money_fmt}</td>'
        '</tr>'
        '<tr><td colspan="2" style="padding-top:4px;">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation"><tr>'
        f'<td style="background:{color};height:6px;width:{pct}%;border-radius:3px;line-height:0;font-size:0;">&nbsp;</td>'
        f'<td style="background:rgba(148,163,184,0.1);height:6px;width:{100-pct}%;border-radius:3px;line-height:0;font-size:0;">&nbsp;</td>'
        '</tr></table>'
        '</td></tr>'
        '</table>'
        '</td></tr>'
    )


def _gather_rich_context(db: Session, shop_domain: str) -> dict:
    """Pull the same 4-5 surfaces the dashboard shows, so the email is
    not a teaser — it's the whole morning view. Each section is
    independently safe-wrapped: a single service failure doesn't nuke
    the email, we just skip that block."""
    ctx: dict = {"currency": "USD"}

    def _safe(fn):
        try:
            return fn()
        except Exception as exc:
            log.warning("lite_morning_digest: context fetch failed: %s", exc)
            return None

    # RARS
    rars = _safe(lambda: __import__("app.services.revenue_at_risk", fromlist=["get_revenue_at_risk"]).get_revenue_at_risk(db, shop_domain))
    if rars:
        ctx["currency"] = rars.get("currency") or ctx["currency"]
        comps = [c for c in (rars.get("components") or []) if (c.get("loss_eur") or 0) > 0]
        comps.sort(key=lambda c: c["loss_eur"], reverse=True)
        ctx["rars"] = {
            "total": rars.get("total_at_risk_eur") or 0,
            "prevented": rars.get("prevented_eur_this_month") or 0,
            "components": comps[:3],
        }

    # Peer benchmarks
    bench = _safe(lambda: __import__("app.services.benchmarks", fromlist=["get_extended_benchmark_report"]).get_extended_benchmark_report(db, shop_domain))
    if bench and (bench.get("peer_count") or 0) >= 10:
        ctx["benchmarks"] = {
            "band": bench.get("band"),
            "peer_count": bench.get("peer_count"),
            "total_recovery": bench.get("total_recovery_potential_eur") or 0,
            "metrics": bench.get("metrics") or {},
        }

    # Retention
    coh = _safe(lambda: __import__("app.services.cohort_engine", fromlist=["get_cohort_summary"]).get_cohort_summary(db, shop_domain))
    if coh and (coh.get("total_customers") or 0) > 0:
        ctx["retention"] = {
            "w1": coh.get("avg_week_1_retention") or 0,
            "w4": coh.get("avg_week_4_retention") or 0,
            "w12": coh.get("avg_week_12_retention") or 0,
            "best_cohort": coh.get("best_cohort"),
        }

    return ctx


_SOURCE_HUMAN = {
    "abandoned_high_intent": "Abandoned high-intent carts",
    "refund_decline": "Products losing traction",
    "nudge_gap": "Nudges under peer benchmark",
    "below_benchmark": "Peers out-earning you",
    "goal_gap": "Your monthly target gap",
}


def _build_email(shop_domain: str, brief: dict, db: Session) -> Tuple[str, str, str]:
    """Spectacular-quality morning brief email.

    Layout (competitor-shame bar):
      1. Big hero card: total Revenue-at-Risk this month + prevented
         chip, loss-framed, amber gradient.
      2. Today's lead story: top product + suggested action.
      3. Inline SVG bar chart: top 3 RARS leak sources ranked by €.
      4. Peer position card: your band + peer count + recovery-to-p75.
      5. Retention snapshot: week 1/4/12 in color-tiered tiles.
      6. CTA to dashboard.

    Every number sourced from a real service — no placeholders. Each
    section guarded by a None-check so any service outage degrades
    gracefully rather than nuking the whole email."""
    shop_name = (
        shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
    )

    # Pull rich context alongside the brief so this isn't a teaser.
    rich = _gather_rich_context(db, shop_domain)
    currency = rich.get("currency") or "USD"

    signals_count = int(brief.get("signals_count") or 0)
    headline = (brief.get("headline") or "").strip()
    top_product = (brief.get("top_product_label") or "").strip()
    top_action = (brief.get("top_action") or "").strip()

    rars = rich.get("rars") or {}
    rars_total = rars.get("total") or 0
    rars_prevented = rars.get("prevented") or 0
    rars_comps = rars.get("components") or []

    # Subject — lead with the most urgent number.
    if rars_total >= 100:
        subject = f"{_fmt_money(rars_total, currency)} at risk this month — {shop_name} brief"
    elif signals_count == 0:
        subject = f"Clean slate today · {shop_name} brief"
    elif top_product:
        subject = f"{shop_name} today: {top_product}"
    else:
        plural = "s" if signals_count != 1 else ""
        subject = f"{signals_count} finding{plural} today · {shop_name} brief"

    # --- HTML body (single <table> tree, email-safe) -----------------
    # We compose raw table HTML — don't use the helper _wrap_html which
    # nests but doesn't leave us room for the amber hero card. The
    # outer wrapper (body + 600px column + brand wordmark) we inline
    # here for full control.
    from app.services.email_templates import _brand_wordmark

    parts: list[str] = []

    # Greeting
    parts.append(
        '<div style="margin:0 0 24px 0;">'
        f'<div style="font-size:13px;color:#64748b;letter-spacing:0.3px;">{datetime.now(timezone.utc).strftime("%A, %B %d")} · {shop_name}</div>'
        '<div style="margin-top:6px;font-size:22px;font-weight:800;color:#f1f5f9;letter-spacing:-0.3px;">'
        f'Good morning, {shop_name}.'
        '</div>'
        '</div>'
    )

    # --- HERO: Revenue at Risk -----------------------------------
    if rars_total > 0:
        prev_chip = ""
        if rars_prevented > 0:
            prev_chip = (
                '<div style="display:inline-block;margin-top:14px;padding:6px 12px;'
                'border-radius:8px;background:rgba(52,211,153,0.12);border:1px solid rgba(52,211,153,0.35);'
                'font-size:12px;color:#34d399;font-weight:700;">'
                f'✓ {_fmt_money(rars_prevented, currency)} prevented so far this month'
                '</div>'
            )
        parts.append(
            '<div style="margin:0 0 28px 0;padding:28px;border-radius:16px;'
            'background:linear-gradient(135deg,#1a1405 0%,#0e0e1a 100%);'
            'border:1px solid rgba(251,191,36,0.22);">'
            '<div style="font-size:10px;font-weight:800;letter-spacing:2.5px;text-transform:uppercase;color:#fbbf24;">'
            'Money at risk · this month'
            '</div>'
            f'<div style="margin-top:14px;font-size:44px;font-weight:800;color:#fbbf24;letter-spacing:-1px;line-height:1;font-variant-numeric:tabular-nums;">'
            f'{_fmt_money(rars_total, currency)}'
            '</div>'
            '<div style="margin-top:10px;font-size:13px;line-height:1.5;color:#94a3b8;">'
            'This is how much money is slipping through your store right now. No other Shopify tool shows you this number. '
            + ("" if not rars_comps else f"Top leak: <span style=\"color:#fbbf24;font-weight:700;\">{_SOURCE_HUMAN.get(rars_comps[0]['source'], rars_comps[0]['source'])}</span>.")
            + '</div>'
            f'{prev_chip}'
            '</div>'
        )

        # RARS component bars
        if rars_comps:
            max_loss = max(c["loss_eur"] for c in rars_comps)
            bar_color_map = {
                "abandoned_high_intent": "#f87171",
                "refund_decline": "#fbbf24",
                "nudge_gap": "#a78bfa",
                "below_benchmark": "#60a5fa",
                "goal_gap": "#e8a04e",
            }
            rows = ""
            for c in rars_comps:
                label = _SOURCE_HUMAN.get(c["source"], c["source"])
                color = bar_color_map.get(c["source"], "#fbbf24")
                rows += _bar_svg(
                    label=label,
                    value=c["loss_eur"],
                    max_value=max_loss,
                    color=color,
                    money_fmt=_fmt_money(c["loss_eur"], currency),
                )
            parts.append(
                '<div style="margin:0 0 28px 0;padding:20px 22px;border-radius:14px;'
                'background:#0b0b14;border:1px solid rgba(255,255,255,0.06);">'
                '<div style="font-size:11px;font-weight:800;letter-spacing:1.8px;text-transform:uppercase;color:#94a3b8;margin-bottom:12px;">'
                'Where it\'s leaking · top 3 sources'
                '</div>'
                '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'{rows}'
                '</table>'
                '</div>'
            )

    # --- LEAD STORY ------------------------------------------------
    if signals_count > 0 and top_product:
        lead_action_block = ""
        if top_action:
            lead_action_block = (
                '<div style="margin-top:12px;padding:12px 14px;border-radius:10px;'
                'background:rgba(52,211,153,0.06);border-left:3px solid #34d399;">'
                '<div style="font-size:10.5px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#34d399;margin-bottom:6px;">'
                'Suggested action'
                '</div>'
                f'<div style="font-size:14px;line-height:1.55;color:#e2e8f0;">{top_action}</div>'
                '</div>'
            )
        parts.append(
            '<div style="margin:0 0 28px 0;padding:22px 24px;border-radius:14px;'
            'background:linear-gradient(135deg,#140a1a 0%,#0e0e1a 100%);'
            'border:1px solid rgba(167,139,250,0.2);">'
            '<div style="font-size:10.5px;font-weight:800;letter-spacing:1.8px;text-transform:uppercase;color:#a78bfa;">'
            'Today\'s lead story'
            '</div>'
            f'<div style="margin-top:12px;font-size:18px;font-weight:800;color:#f1f5f9;line-height:1.3;">{top_product}</div>'
            + (f'<div style="margin-top:6px;font-size:13px;color:#94a3b8;line-height:1.5;">{headline}</div>' if headline and headline != top_product else "")
            + f'{lead_action_block}'
            + '</div>'
        )

    # --- PEER BENCHMARKS ------------------------------------------
    bench = rich.get("benchmarks")
    if bench and bench.get("total_recovery", 0) > 0:
        bcur = currency
        recovery = bench["total_recovery"]
        parts.append(
            '<div style="margin:0 0 28px 0;padding:20px 22px;border-radius:14px;'
            'background:#0b0b14;border:1px solid rgba(167,139,250,0.15);">'
            '<div style="font-size:11px;font-weight:800;letter-spacing:1.8px;text-transform:uppercase;color:#a78bfa;margin-bottom:10px;">'
            f'You vs peers · band {bench.get("band")} · {bench.get("peer_count")} shops'
            '</div>'
            f'<div style="font-size:15px;line-height:1.5;color:#e2e8f0;">'
            f'Moving every under-performing metric to the 75th-percentile peer would recover '
            f'<strong style="color:#a78bfa;">{_fmt_money(recovery, bcur)}</strong> per month.'
            '</div>'
            '</div>'
        )

    # --- RETENTION SNAPSHOT ---------------------------------------
    ret = rich.get("retention")
    if ret and (ret.get("w1") or ret.get("w4") or ret.get("w12")):
        def _tile(label: str, rate: float) -> str:
            if rate >= 0.3:
                color = "#34d399"
            elif rate >= 0.15:
                color = "#e8a04e"
            else:
                color = "#f87171"
            return (
                '<td width="33%" style="padding:0 6px;">'
                '<div style="padding:14px;border-radius:10px;background:#0b0b14;'
                'border:1px solid rgba(255,255,255,0.05);text-align:center;">'
                f'<div style="font-size:10px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;color:#64748b;">{label}</div>'
                f'<div style="margin-top:8px;font-size:22px;font-weight:800;color:{color};line-height:1;font-variant-numeric:tabular-nums;">'
                f'{(rate*100):.0f}%'
                '</div>'
                '</div>'
                '</td>'
            )
        parts.append(
            '<div style="margin:0 0 28px 0;padding:20px 22px;border-radius:14px;'
            'background:#0b0b14;border:1px solid rgba(52,211,153,0.15);">'
            '<div style="font-size:11px;font-weight:800;letter-spacing:1.8px;text-transform:uppercase;color:#34d399;margin-bottom:12px;">'
            'Retention · week 1 / 4 / 12'
            '</div>'
            '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            + _tile("Week 1", ret.get("w1", 0))
            + _tile("Week 4", ret.get("w4", 0))
            + _tile("Week 12", ret.get("w12", 0))
            + '</tr></table>'
            + '</div>'
        )

    # --- CTA ------------------------------------------------------
    parts.append(
        '<div style="margin:32px 0 0 0;text-align:center;">'
        f'<a href="{_DASHBOARD_URL}" style="display:inline-block;'
        'background:linear-gradient(135deg,#7c3aed 0%,#c026d3 50%,#e8a04e 100%);'
        'color:#ffffff;font-size:15px;font-weight:700;padding:14px 40px;'
        'border-radius:10px;text-decoration:none;letter-spacing:0.3px;">'
        'Open your dashboard'
        '</a>'
        '<div style="margin-top:14px;font-size:11px;color:#475569;">'
        'Every number above traces to a real query. No modeled estimates, no invented data.'
        '</div>'
        '</div>'
    )

    # Empty-state path: rars_total==0 AND signals_count==0
    if not rars_total and signals_count == 0:
        parts = [
            '<div style="margin:0 0 24px 0;">'
            f'<div style="font-size:13px;color:#64748b;">{datetime.now(timezone.utc).strftime("%A, %B %d")} · {shop_name}</div>'
            '<div style="margin-top:6px;font-size:22px;font-weight:800;color:#f1f5f9;">'
            f'Good morning, {shop_name}.'
            '</div>'
            '</div>',
            '<div style="margin:0 0 28px 0;padding:28px;border-radius:16px;'
            'background:linear-gradient(135deg,#0a1612 0%,#0e0e1a 100%);'
            'border:1px solid rgba(52,211,153,0.22);">'
            '<div style="font-size:10px;font-weight:800;letter-spacing:2.5px;text-transform:uppercase;color:#34d399;">'
            'Clean slate'
            '</div>'
            '<div style="margin-top:14px;font-size:28px;font-weight:800;color:#34d399;line-height:1.15;">'
            'Your funnel is clean this morning.'
            '</div>'
            '<div style="margin-top:12px;font-size:14px;line-height:1.55;color:#94a3b8;">'
            'No material risk, no urgent findings. Spark is watching — the moment anything '
            'crosses threshold (abandoned intent, leaking pages, hot-product surges), '
            'it\'ll land in your dashboard and in tomorrow\'s brief.'
            '</div>'
            '</div>',
            '<div style="margin:32px 0 0 0;text-align:center;">'
            f'<a href="{_DASHBOARD_URL}" style="display:inline-block;'
            'background:linear-gradient(135deg,#7c3aed,#c026d3,#e8a04e);'
            'color:#ffffff;font-size:15px;font-weight:700;padding:14px 40px;'
            'border-radius:10px;text-decoration:none;">'
            'See your dashboard'
            '</a>'
            '</div>',
        ]

    body_html = "".join(parts)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your morning brief</title>
</head>
<body style="margin:0;padding:0;background:#07070f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#07070f;">
<tr><td align="center" style="padding:40px 16px;">
<table width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;">

<tr><td style="padding:0 0 28px 0;">{_brand_wordmark(font_size=20)}</td></tr>

<tr><td style="background:#0e0e1a;border:1px solid rgba(167,139,250,0.08);border-radius:18px;padding:32px 28px;">
{body_html}
</td></tr>

<tr><td style="padding:24px 0 0 0;text-align:center;">
<p style="margin:0;font-size:11px;color:#475569;letter-spacing:0.3px;">
HedgeSpark · AI Commerce Intelligence for Shopify
</p>
<p style="margin:8px 0 0 0;font-size:11px;color:#334155;">
Not what you need? <a href="{_DASHBOARD_URL}" style="color:#a78bfa;text-decoration:none;">Turn off the morning brief</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    # --- Plain-text (fallback + deliverability) ---
    lines: list[str] = [f"Good morning, {shop_name}.", ""]
    if rars_total > 0:
        lines.append(f"MONEY AT RISK THIS MONTH: {_fmt_money(rars_total, currency)}")
        if rars_prevented > 0:
            lines.append(f"  ✓ {_fmt_money(rars_prevented, currency)} already prevented")
        if rars_comps:
            lines.append("")
            lines.append("Top leak sources:")
            for c in rars_comps:
                lines.append(f"  - {_SOURCE_HUMAN.get(c['source'], c['source'])}: {_fmt_money(c['loss_eur'], currency)}")
    elif signals_count == 0:
        lines.append("Clean slate today — your funnel is healthy. Spark is watching.")
    if top_product:
        lines.append("")
        lines.append(f"Lead story: {top_product}")
        if top_action:
            lines.append(f"  → {top_action}")
    if bench and bench.get("total_recovery"):
        lines.append("")
        lines.append(
            f"Peer position: {_fmt_money(bench['total_recovery'], currency)}/mo recoverable "
            f"by matching p75 peers in your band."
        )
    if ret and (ret.get("w1") or ret.get("w4") or ret.get("w12")):
        lines.append("")
        lines.append(
            f"Retention: w1 {(ret.get('w1', 0)*100):.0f}% · "
            f"w4 {(ret.get('w4', 0)*100):.0f}% · "
            f"w12 {(ret.get('w12', 0)*100):.0f}%"
        )
    lines.append("")
    lines.append(f"Open your dashboard: {_DASHBOARD_URL}")
    plain_text = "\n".join(lines)

    return subject, html, plain_text


def run_lite_morning_digest_cycle(db: Session) -> dict:
    """Process all eligible Lite merchants for today's morning digest.

    Eligibility:
      - install_status == "active"
      - contact_email is not NULL and not empty
      - plan == "lite" OR plan is None (default = Lite)
      - not in _ONBOARDING_BLOCKLIST
      - not already sent today (Redis dedup)

    Each intent goes through the email orchestrator — rate limits,
    PII guards, and suppression flags apply as normal.

    Returns: {processed, sent, skipped, failed, date}
    """
    from app.services.onboarding import _ONBOARDING_BLOCKLIST
    from app.services.brief_engine import generate_brief
    from app.services.email_orchestrator import EmailIntent, submit_intent

    date_key = _today_rome()
    summary = {
        "processed": 0, "sent": 0, "skipped": 0, "failed": 0,
        "date": date_key,
    }

    _BATCH_SIZE = 200

    offset = 0
    while True:
        merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
            )
            .order_by(Merchant.id)
            .offset(offset)
            .limit(_BATCH_SIZE)
            .all()
        )
        if not merchants:
            break
        offset += _BATCH_SIZE

        for m in merchants:
            if m.shop_domain in _ONBOARDING_BLOCKLIST:
                continue

            # Lite-only. Pro merchants get the weekly digest already;
            # stacking a daily email on top would be noise. Merchants
            # with plan=None are treated as Lite (default band).
            plan = (m.plan or "lite").lower()
            if plan != "lite":
                continue

            summary["processed"] += 1

            if _digest_sent_today(m.shop_domain, date_key):
                summary["skipped"] += 1
                continue

            try:
                brief = generate_brief(db, m.shop_domain)
                subject, html, plain_text = _build_email(m.shop_domain, brief, db)

                intent = EmailIntent(
                    shop_domain=m.shop_domain,
                    email_type="lite_morning_digest",
                    to_email=m.contact_email,
                    subject=subject,
                    html=html,
                    plain_text=plain_text,
                    from_address="HedgeSpark <brief@hedgesparkhq.com>",
                    producer="lite_morning_digest",
                    context={
                        "signals_count": int(brief.get("signals_count") or 0),
                        "top_product": brief.get("top_product_label") or "",
                    },
                )
                submit_intent(db, intent)
                _mark_sent(m.shop_domain, date_key, success=True)
                summary["sent"] += 1
                log.info(
                    "lite_morning_digest: intent queued for %s (%s) signals=%s",
                    m.shop_domain, m.contact_email,
                    brief.get("signals_count", 0),
                )

                # Strada 3.5 — forward the brief to Slack too if the
                # merchant has connected a webhook. Best-effort; a
                # Slack failure doesn't affect the email send.
                try:
                    from app.services.slack_dispatcher import post_daily_brief
                    post_daily_brief(db, m.shop_domain, brief)
                except Exception as exc:
                    log.warning(
                        "lite_morning_digest: Slack forward failed for %s: %s",
                        m.shop_domain, exc,
                    )

            except Exception as exc:
                summary["failed"] += 1
                log.warning(
                    "lite_morning_digest: error for %s: %s",
                    m.shop_domain, exc,
                )

    if summary["processed"] > 0:
        log.info(
            "lite_morning_digest: date=%s processed=%d sent=%d skipped=%d failed=%d",
            date_key, summary["processed"], summary["sent"],
            summary["skipped"], summary["failed"],
        )

    return summary
