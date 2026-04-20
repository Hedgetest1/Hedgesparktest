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
    """Morning brief email — MIRRORS the _render_night_shift_digest
    structure in email_templates.py 1:1. Night shift is HedgeSpark's
    established daily email pattern (Pro-only "morning after"
    report); we reuse its visual grammar for the Lite morning brief
    so a merchant who sees both recognises one consistent system.

    Layout (copied from _render_night_shift_digest):
      1. _heading(headline) — the narrative title
      2. _p(narrative, color="#cbd5e1") — context
      3. Side-by-side KPI cards: Revenue-at-Risk / Prevented
      4. _section_title("Top action flagged", accent="warm") + _p
      5. _section_title("Where it's leaking", accent="cool") +
         journal-style colored-border rows for each component
      6. _separator()
      7. closing _p + _button("Open dashboard", ...)
      8. _wrap_html(subject, body)  # NO show_logo=True
    """
    from app.services.email_templates import (
        _wrap_html, _button, _p, _heading, _section_title, _separator,
    )

    shop_name = (
        shop_domain.replace(".myshopify.com", "").replace("-", " ").title()
    )

    # Data
    rich = _gather_rich_context(db, shop_domain)
    currency = rich.get("currency") or "USD"

    signals_count = int(brief.get("signals_count") or 0)
    brief_headline = (brief.get("headline") or "").strip()
    top_product = (brief.get("top_product_label") or "").strip()
    top_action = (brief.get("top_action") or "").strip()

    rars = rich.get("rars") or {}
    rars_total = float(rars.get("total") or 0)
    rars_prevented = float(rars.get("prevented") or 0)
    rars_comps = rars.get("components") or []

    # ------- Headline + narrative (mirror night_shift style) ---------
    if rars_total > 0 and top_product:
        headline = f"{_fmt_money(rars_total, currency)} at risk · lead story on {top_product}"
    elif rars_total > 0:
        headline = f"{_fmt_money(rars_total, currency)} at risk this month"
    elif top_product:
        headline = f"{shop_name}: today's lead is {top_product}"
    elif signals_count > 0:
        plural = "s" if signals_count != 1 else ""
        headline = f"{signals_count} finding{plural} in your overnight brief"
    else:
        headline = "Clean slate this morning"

    narrative = brief_headline if brief_headline else (
        f"Your morning brief for {shop_name}. Every number below comes from a real query, "
        "ranked by economic impact."
    )

    # ------- Empty / clean-slate short path --------------------------
    if rars_total == 0 and signals_count == 0:
        body_parts = [
            _heading("Clean slate this morning"),
            _p(
                f"No material risk this morning on {shop_name}. Your funnel is clean, "
                "no abandoned-intent products crossed threshold, no leaking pages, no "
                "urgent findings in the overnight review.",
                color="#cbd5e1",
            ),
            _p(
                "Spark is still watching. The moment any signal crosses threshold "
                "(abandoned intent, leaking pages, hot-product surge, peer-gap widening), "
                "it'll land in your dashboard and in tomorrow's brief.",
                color="#94a3b8",
            ),
            _separator(),
            _p(
                "Use a quiet morning to work what's already converting — Hot Products and "
                "peer-gap opportunities on the dashboard.",
                color="#64748b",
            ),
            _button("Open dashboard", _DASHBOARD_URL),
        ]
        subject = f"{shop_name}: clean slate this morning"
        html_out = _wrap_html(subject, "".join(body_parts))
        plain_out = (
            f"Clean slate this morning on {shop_name}.\n\n"
            "No material risk, no abandoned-intent products crossed threshold,\n"
            "no leaking pages, no urgent findings.\n\n"
            f"Open dashboard: {_DASHBOARD_URL}\n\n"
            "— HedgeSpark"
        )
        return subject, html_out, plain_out

    # ------- Body (mirror _render_night_shift_digest structure) ------
    body_parts = [
        _heading(headline),
        _p(narrative, color="#cbd5e1") if narrative else "",
    ]

    # KPI cards — side-by-side table, EXACT copy of night_shift pattern.
    # Left: Revenue at risk (amber). Right: Prevented (emerald).
    kpi_cards = []
    if rars_total > 0:
        kpi_cards.append(
            f'<td style="padding:0 6px 0 0;vertical-align:top;width:50%;">'
            f'<div style="padding:14px 16px;border-radius:12px;border:1px solid rgba(232,160,78,0.22);'
            f'background:rgba(232,160,78,0.06);">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.16em;'
            f'text-transform:uppercase;color:#94a3b8;">Revenue at risk</div>'
            f'<div style="margin-top:4px;font-size:22px;font-weight:800;color:#e8a04e;">'
            f'{_fmt_money(rars_total, currency)}/mo</div></div></td>'
        )
    if rars_prevented > 0:
        kpi_cards.append(
            f'<td style="padding:0 0 0 6px;vertical-align:top;width:50%;">'
            f'<div style="padding:14px 16px;border-radius:12px;border:1px solid rgba(52,211,153,0.22);'
            f'background:rgba(52,211,153,0.06);">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.16em;'
            f'text-transform:uppercase;color:#94a3b8;">Prevented this month</div>'
            f'<div style="margin-top:4px;font-size:22px;font-weight:800;color:#34d399;">'
            f'{_fmt_money(rars_prevented, currency)}</div></div></td>'
        )
    if kpi_cards:
        body_parts.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'width="100%" style="margin:10px 0 4px 0;"><tr>'
            + "".join(kpi_cards)
            + '</tr></table>'
        )

    # Top action (mirrors night_shift "Top action flagged" section)
    if top_product and top_action:
        body_parts.append(_section_title("Top action flagged", accent="warm"))
        body_parts.append(
            _p(
                f"<strong style='color:#f1f5f9;'>{top_product}</strong> — {top_action}",
                color="#e2e8f0",
            )
        )

    # "Where it's leaking" — EXACT journal-row pattern from night_shift
    if rars_comps:
        body_parts.append(_section_title("Where it's leaking", accent="cool"))
        verdict_color = {
            "abandoned_high_intent": "#f87171",
            "refund_decline":        "#e8a04e",
            "nudge_gap":             "#a78bfa",
            "below_benchmark":       "#60a5fa",
            "goal_gap":              "#e8a04e",
        }
        for c in rars_comps:
            source = c.get("source", "unknown")
            label = _SOURCE_HUMAN.get(source, source)
            amount = _fmt_money(c.get("loss_eur", 0), currency)
            v_color = verdict_color.get(source, "#94a3b8")
            body_parts.append(
                f'<div style="margin:6px 0;padding:8px 12px;border-left:2px solid {v_color};'
                f'background:rgba(255,255,255,0.02);">'
                f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
                f'text-transform:uppercase;color:{v_color};">'
                f'{label} · {amount}/mo</div>'
                f'<div style="margin-top:3px;font-size:13px;color:#cbd5e1;">'
                f'{c.get("narrative") or "Component contributing to total at-risk this month."}'
                f'</div>'
                f'</div>'
            )

    # Closing (mirror night_shift's closing)
    body_parts.append(_separator())
    body_parts.append(
        _p(
            "No competitor tells you in real-time how much money is slipping through your "
            "store right now. This is the receipt.",
            color="#64748b",
        )
    )
    body_parts.append(_button("Open dashboard", _DASHBOARD_URL))
    body_parts.append(
        _p(
            "You can pause this email anytime from Settings → Notifications.",
            color="#64748b",
        )
    )

    subject = f"{shop_name}: morning brief — {headline[:72]}"
    body_html = "".join(body_parts)
    html_out = _wrap_html(subject, body_html)

    # Plain-text — mirrors night_shift plain structure.
    plain_lines = [headline, ""]
    if narrative:
        plain_lines.append(narrative)
        plain_lines.append("")
    if rars_total > 0:
        plain_lines.append(f"Revenue at risk: {_fmt_money(rars_total, currency)}/mo")
    if rars_prevented > 0:
        plain_lines.append(f"Prevented this month: {_fmt_money(rars_prevented, currency)}")
    if top_product and top_action:
        plain_lines.append("")
        plain_lines.append("Top action flagged:")
        plain_lines.append(f"  {top_product} — {top_action}")
    if rars_comps:
        plain_lines.append("")
        plain_lines.append("Where it's leaking:")
        for c in rars_comps:
            label = _SOURCE_HUMAN.get(c.get("source", ""), c.get("source", "unknown"))
            amount = _fmt_money(c.get("loss_eur", 0), currency)
            plain_lines.append(f"  • {label.upper()} · {amount}/mo")
    plain_lines.append("")
    plain_lines.append(f"Open dashboard: {_DASHBOARD_URL}")
    plain_lines.append("")
    plain_lines.append("Pause this email: Settings → Notifications")
    plain_lines.append("— HedgeSpark")
    plain_out = "\n".join(plain_lines)

    return subject, html_out, plain_out


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
