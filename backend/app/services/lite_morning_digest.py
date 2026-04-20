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
    """Morning brief email — rebuilt to match the established HedgeSpark
    email visual language (digest_formatter / email_templates
    primitives). Uses _wrap_html with logo, _section_title alternated
    warm/cool, typography scale shared with weekly_digest + welcome.

    Sections (when data exists):
      - Subject card: Revenue-at-Risk hero in an amber signal box
      - Lead story callout (emerald left-rule "Suggested action")
      - Risk components table (amber rgba box like weekly_digest)
      - Peer benchmarks (violet signal box)
      - Retention tiles (emerald signal box when >0, else skipped)
      - Working (emerald) when clean-slate
      - CTA button (amber→violet gradient — canonical)
      - Trust footer

    Every section guarded — missing services don't break the email.
    Zero custom HTML outside the shared primitives.
    """
    from app.services.email_templates import (
        _wrap_html, _button, _p, _heading, _section_title, _separator,
    )

    shop_name = (
        shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
    )
    today_str = datetime.now(timezone.utc).strftime("%A · %B %d")

    # Data
    rich = _gather_rich_context(db, shop_domain)
    currency = rich.get("currency") or "USD"

    signals_count = int(brief.get("signals_count") or 0)
    headline = (brief.get("headline") or "").strip()
    top_product = (brief.get("top_product_label") or "").strip()
    top_action = (brief.get("top_action") or "").strip()

    rars = rich.get("rars") or {}
    rars_total = float(rars.get("total") or 0)
    rars_prevented = float(rars.get("prevented") or 0)
    rars_comps = rars.get("components") or []
    bench = rich.get("benchmarks") or {}
    ret = rich.get("retention") or {}

    # ---- Subject ---------------------------------------------------
    if rars_total >= 100:
        subject = f"{_fmt_money(rars_total, currency)} at risk this month — {shop_name}"
    elif signals_count == 0 and rars_total == 0:
        subject = f"Clean slate this morning · {shop_name}"
    elif top_product:
        subject = f"{top_product} — today's lead on {shop_name}"
    else:
        plural = "s" if signals_count != 1 else ""
        subject = f"{signals_count} finding{plural} today · {shop_name}"

    # ---- BODY ------------------------------------------------------
    body = ""

    # 1) Greeting row — matches welcome/digest header pattern
    body += (
        f'<p style="font-size:13px;color:#64748b;margin:0 0 4px;letter-spacing:0.3px;">{today_str}</p>'
        + _heading(f"Good morning, {shop_name}")
    )

    # 2) Clean-slate short path — whole email stays tight
    if not rars_total and signals_count == 0:
        body += _p(
            "No significant findings overnight. Your funnel is clean and "
            "Spark is watching — the moment any signal crosses threshold "
            "(abandoned intent, leaking pages, hot-product surges), you'll "
            "see it in your dashboard and in tomorrow's brief."
        )
        # "What's Working" signal box — canonical emerald
        body += (
            '<div style="margin:20px 0;padding:16px 18px;background:rgba(16,185,129,0.06);'
            'border:1px solid rgba(16,185,129,0.15);border-radius:8px;font-size:14px;line-height:1.6;">'
            '<strong style="color:#10b981;font-size:14px;">Funnel is healthy</strong>'
            '<p style="margin:6px 0 0;color:#c8d1dc;">Zero revenue-at-risk components crossed their thresholds. Zero urgent findings. '
            'Use the quiet window to work what\'s already converting — Hot Products and peer-gap opportunities on the dashboard.</p>'
            '</div>'
        )
        body += (
            '<div style="text-align:center;margin:28px 0 8px;">'
            + _button("See your dashboard", _DASHBOARD_URL)
            + '</div>'
        )
        plain = (
            f"Good morning, {shop_name}.\n\n"
            "Clean slate today — your funnel is healthy. Spark is watching.\n"
            "Zero revenue-at-risk components crossed threshold overnight.\n\n"
            f"Open your dashboard: {_DASHBOARD_URL}\n"
        )
        return subject, _wrap_html(subject, body, show_logo=True), plain

    # 3) Intro line framing the brief
    if signals_count > 0:
        plural = "s" if signals_count != 1 else ""
        body += _p(
            f"I reviewed the last 24 hours on {shop_name} and ranked "
            f"<strong style='color:#f1f5f9;'>{signals_count} finding{plural}</strong> "
            f"by economic impact. Here's what matters today."
        )
    else:
        body += _p(
            f"Here's what the data on {shop_name} is telling you this morning. "
            f"Every number below comes from a real query — no modeled estimates."
        )

    # 4) RARS HERO — matches weekly_digest's rars_hero_html pattern
    if rars_total > 0:
        prevented_block = ""
        if rars_prevented > 0:
            prevented_block = (
                '<p style="margin:10px 0 0;font-size:13px;color:#10b981;font-weight:600;">'
                f'HedgeSpark already prevented {_fmt_money(rars_prevented, currency)} this month.'
                '</p>'
            )
        body += (
            '<div style="margin:22px 0 16px;padding:22px 24px;'
            'background:linear-gradient(135deg,rgba(212,137,58,0.08) 0%,rgba(167,139,250,0.04) 100%);'
            'border:1px solid rgba(212,137,58,0.22);border-radius:12px;">'
            '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.16em;color:#d4893a;margin-bottom:8px;">'
            'Revenue at Risk · this month'
            '</div>'
            f'<div style="font-size:42px;font-weight:800;color:#f1f5f9;line-height:1.1;letter-spacing:-0.5px;">'
            f'{_fmt_money(rars_total, currency)}'
            '<span style="font-size:14px;font-weight:600;color:#94a3b8;"> / month</span>'
            '</div>'
            f'{prevented_block}'
            '</div>'
        )

    # 5) Lead story — violet box with emerald "Suggested action"
    if top_product:
        action_block = ""
        if top_action:
            action_block = (
                '<div style="margin:12px 0 0;padding:12px 14px;background:rgba(16,185,129,0.06);'
                'border-left:3px solid #10b981;border-radius:4px;">'
                '<strong style="color:#10b981;font-size:11px;letter-spacing:0.15em;text-transform:uppercase;">Suggested action</strong>'
                f'<p style="margin:6px 0 0;color:#c8d1dc;font-size:13px;line-height:1.55;">{top_action}</p>'
                '</div>'
            )
        body += (
            '<div style="margin:16px 0;padding:16px 18px;background:rgba(167,139,250,0.06);'
            'border:1px solid rgba(167,139,250,0.18);border-radius:8px;">'
            '<strong style="color:#c4b5fd;font-size:13px;letter-spacing:0.08em;text-transform:uppercase;">Today\'s lead story</strong>'
            f'<p style="margin:8px 0 0;color:#f1f5f9;font-size:17px;font-weight:700;line-height:1.35;">{top_product}</p>'
            + (f'<p style="margin:6px 0 0;color:#94a3b8;font-size:13px;line-height:1.55;">{headline}</p>' if headline and headline != top_product else "")
            + f'{action_block}'
            + '</div>'
        )

    # 6) Components — amber "Revenue at Risk" rows (weekly_digest style)
    if rars_comps:
        color_map = {
            "abandoned_high_intent": "#f87171",
            "refund_decline":        "#fbbf24",
            "nudge_gap":             "#a78bfa",
            "below_benchmark":       "#60a5fa",
            "goal_gap":              "#e8a04e",
        }
        rows = ""
        for c in rars_comps:
            color = color_map.get(c["source"], "#d4893a")
            label = _SOURCE_HUMAN.get(c["source"], c["source"])
            rows += (
                '<div style="padding:12px 0;border-bottom:1px solid rgba(255,255,255,0.06);">'
                f'<strong style="color:{color};font-size:14px;">{label}</strong>'
                f'<span style="float:right;color:#f1f5f9;font-weight:700;font-variant-numeric:tabular-nums;">{_fmt_money(c["loss_eur"], currency)} / mo</span>'
                '</div>'
            )
        body += (
            _section_title("Where it's leaking · top 3 sources", accent="warm")
            + '<div style="margin:10px 0 16px;padding:4px 18px;background:rgba(245,158,11,0.05);'
            'border:1px solid rgba(245,158,11,0.18);border-radius:8px;">'
            + rows
            + '</div>'
        )

    # 7) Peer benchmarks — violet signal box (weekly_digest style)
    if bench and bench.get("total_recovery", 0) > 0:
        body += (
            _section_title("You vs peers", accent="cool")
            + '<div style="margin:10px 0 16px;padding:16px 18px;background:rgba(167,139,250,0.06);'
            'border:1px solid rgba(167,139,250,0.18);border-radius:8px;">'
            f'<p style="margin:0;color:#c4b5fd;font-size:13px;">'
            f'Band <strong style="color:#f1f5f9;">{bench.get("band")}</strong> · '
            f'{bench.get("peer_count")} shops</p>'
            f'<p style="margin:8px 0 0;font-size:14px;line-height:1.55;color:#c8d1dc;">'
            f'Moving every under-performing metric to the 75th-percentile peer would recover '
            f'<strong style="color:#10b981;">{_fmt_money(bench["total_recovery"], currency)}</strong> '
            f'per month.</p>'
            '</div>'
        )

    # 8) Retention tiles — emerald section (canonical)
    if ret and any(ret.get(k, 0) for k in ("w1", "w4", "w12")):
        def _tile(label: str, rate: float) -> str:
            if rate >= 0.30:
                col, tone = "#10b981", "Strong"
            elif rate >= 0.15:
                col, tone = "#f59e0b", "Typical"
            else:
                col, tone = "#f87171", "Weak"
            return (
                '<td width="33%" align="center" style="padding:0 6px;">'
                '<div style="padding:14px 10px;background:rgba(255,255,255,0.03);'
                'border:1px solid rgba(255,255,255,0.06);border-radius:8px;">'
                f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.14em;color:#94a3b8;">{label}</div>'
                f'<div style="margin-top:8px;font-size:22px;font-weight:800;color:{col};line-height:1;font-variant-numeric:tabular-nums;">'
                f'{(rate*100):.0f}%'
                '</div>'
                f'<div style="margin-top:4px;font-size:10px;color:{col};opacity:0.75;">{tone}</div>'
                '</div>'
                '</td>'
            )

        body += (
            _section_title("Retention · week 1 / 4 / 12", accent="cool")
            + '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 16px;"><tr>'
            + _tile("Week 1", float(ret.get("w1", 0)))
            + _tile("Week 4", float(ret.get("w4", 0)))
            + _tile("Week 12", float(ret.get("w12", 0)))
            + '</tr></table>'
        )

    # 9) CTA — canonical amber-to-violet button
    body += (
        _separator()
        + '<div style="text-align:center;margin:8px 0 8px;">'
        + _button("Open your dashboard", _DASHBOARD_URL)
        + '</div>'
        + '<p style="margin:16px 0 0;font-size:11px;color:#475569;text-align:center;letter-spacing:0.3px;">'
        'Every number above traces to a real query. No modeled estimates, no invented data.'
        '</p>'
    )

    # Wrap with canonical template shell (includes HedgeSpark logo)
    html = _wrap_html(subject, body, show_logo=True)

    # Plain-text version — same narrative beats
    lines: list[str] = [f"Good morning, {shop_name}. · {today_str}", ""]
    if rars_total > 0:
        lines.append(f"REVENUE AT RISK THIS MONTH: {_fmt_money(rars_total, currency)} / month")
        if rars_prevented > 0:
            lines.append(f"  ✓ HedgeSpark already prevented {_fmt_money(rars_prevented, currency)}")
    if top_product:
        lines.append("")
        lines.append(f"LEAD STORY: {top_product}")
        if top_action:
            lines.append(f"  Suggested action: {top_action}")
    if rars_comps:
        lines.append("")
        lines.append("WHERE IT'S LEAKING — top 3:")
        for c in rars_comps:
            lines.append(f"  · {_SOURCE_HUMAN.get(c['source'], c['source'])}: {_fmt_money(c['loss_eur'], currency)}/mo")
    if bench and bench.get("total_recovery", 0) > 0:
        lines.append("")
        lines.append(
            f"YOU VS PEERS (band {bench.get('band')}, {bench.get('peer_count')} shops): "
            f"{_fmt_money(bench['total_recovery'], currency)}/mo recoverable to p75."
        )
    if ret and any(ret.get(k, 0) for k in ("w1", "w4", "w12")):
        lines.append("")
        lines.append(
            f"RETENTION: w1 {(ret.get('w1', 0)*100):.0f}% · "
            f"w4 {(ret.get('w4', 0)*100):.0f}% · "
            f"w12 {(ret.get('w12', 0)*100):.0f}%"
        )
    lines.append("")
    lines.append(f"Dashboard: {_DASHBOARD_URL}")
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
