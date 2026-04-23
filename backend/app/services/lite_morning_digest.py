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
    """Morning brief email — MIRRORS digest_formatter.format_digest
    (the "Your Weekly Intelligence" email) 1:1. Same subject shape,
    same shell with PNG logo, same rars_hero_html amber-gradient
    card, same amber "Revenue at Risk" opportunity box with bordered
    rows, same violet peer-benchmarks box, same amber-to-violet CTA.

    Founder directive 2026-04-20: "prendi esempio dalla mail con
    oggetto your weekly intelligence... sopra non ci va spark in sè,
    ma il logo". Executed.
    """
    from app.services.email_templates import _wrap_html

    shop = shop_domain.replace(".myshopify.com", "")
    shop_name = shop.replace("-", " ").title()
    period = datetime.now(timezone.utc).strftime("%A · %B %d, %Y")

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
    bench = rich.get("benchmarks") or {}
    ret = rich.get("retention") or {}

    # -------------------------------------------------------------
    # RARS HERO — byte-for-byte copy of digest_formatter rars_hero_html
    # (amber→violet gradient card, 42px hero, prevented line below)
    # -------------------------------------------------------------
    rars_hero_html = ""
    if rars_total > 0:
        prevented_block = ""
        if rars_prevented > 0:
            prevented_block = (
                f'<p style="margin:6px 0 0;font-size:13px;color:#10b981;font-weight:600">'
                f"HedgeSpark already prevented {currency} {rars_prevented:,.0f} this month"
                f"</p>"
            )
        rars_hero_html = f"""
        <div style="margin:24px 0;padding:24px;background:linear-gradient(135deg,rgba(212,137,58,0.08) 0%,rgba(168,85,247,0.08) 100%);border:1px solid rgba(212,137,58,0.25);border-radius:12px;text-align:center">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.16em;color:#d4893a;margin-bottom:8px">Revenue at Risk</div>
            <div style="font-size:42px;font-weight:800;color:#f1f5f9;line-height:1.1">{currency} {rars_total:,.0f}<span style="font-size:14px;font-weight:600;color:#94a3b8">/month</span></div>
            {prevented_block}
        </div>
        """

    # -------------------------------------------------------------
    # LEAD STORY — recommendation-style emerald box (copied from
    # digest_formatter rec_html pattern)
    # -------------------------------------------------------------
    lead_html = ""
    if top_product:
        lead_html = f"""
        <div style="margin:20px 0;padding:16px 18px;background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:8px;font-size:14px;line-height:1.6">
            <strong style="color:#10b981;font-size:14px">Today's lead story — {top_product}</strong>
            {f'<p style="margin:6px 0 0;color:#c8d1dc">{top_action}</p>' if top_action else ''}
            {f'<p style="margin:6px 0 0;color:#94a3b8;font-size:13px">{brief_headline}</p>' if brief_headline and brief_headline != top_product else ''}
        </div>
        """

    # -------------------------------------------------------------
    # RISK COMPONENTS — byte-for-byte copy of digest_formatter risk_html
    # (amber opportunity box with bordered rows per product)
    # -------------------------------------------------------------
    risk_html = ""
    if rars_comps:
        count = len(rars_comps)
        top_loss = rars_comps[0].get("loss_eur", 0)
        impact_line = ""
        if top_loss > 0:
            impact_line = (
                f'<p style="margin:4px 0 10px;font-size:13px;color:#10b981;font-weight:600">'
                f'Fixing the top leak could recover ~{currency} {top_loss:,.2f}/month</p>'
            )
        opp_rows = ""
        for c in rars_comps:
            source = c.get("source", "unknown")
            label = _SOURCE_HUMAN.get(source, source)
            loss = c.get("loss_eur", 0)
            narrative_text = c.get("narrative") or f"Component contributing to this month's at-risk total."
            opp_rows += f"""
            <div style="padding:10px 0;border-bottom:1px solid #fde68a">
                <strong style="color:#f59e0b">{label}</strong>
                <span style="float:right;color:#b45309;font-weight:700">{currency} {loss:,.0f}/mo</span>
                <p style="margin:4px 0 2px;color:#c8d1dc;font-size:13px">{narrative_text}</p>
            </div>"""
        risk_html = f"""
        <div style="margin:20px 0;padding:16px 18px;background:rgba(245,158,11,0.08);border:1px solid #fde68a;border-radius:8px;font-size:14px">
            <div style="margin-bottom:4px">
                <span style="font-size:13px;color:#f59e0b;font-weight:600">Where it's leaking · top {count}</span>
                <span style="float:right;font-size:18px;font-weight:700;color:#b45309">{currency} {rars_total:,.0f}</span>
            </div>
            <p style="margin:0 0 4px;font-size:12px;color:#f59e0b">{count} source{'s' if count != 1 else ''} dragging this month's total</p>
            {impact_line}
            {opp_rows}
        </div>
        """

    # -------------------------------------------------------------
    # PEER BENCHMARKS — byte-for-byte from digest_formatter benchmarks_html
    # -------------------------------------------------------------
    benchmarks_html = ""
    if bench and bench.get("total_recovery", 0) > 0:
        recovery_block = (
            f'<p style="margin:6px 0 0;font-size:13px;color:#10b981;font-weight:600">'
            f"{currency} {bench['total_recovery']:,.0f}/month recoverable if you reach top 25%"
            f"</p>"
        )
        benchmarks_html = f"""
        <div style="margin:16px 0;padding:16px 18px;background:rgba(167,139,250,0.06);border:1px solid rgba(167,139,250,0.18);border-radius:8px">
            <strong style="color:#c4b5fd;font-size:14px">You vs. Similar Shops</strong>
            <p style="margin:4px 0 0;font-size:12px;color:#94a3b8">Benchmarked against {bench.get("peer_count", 0)} shops in {bench.get("band", "your band")}</p>
            {recovery_block}
        </div>
        """

    # -------------------------------------------------------------
    # RETENTION — mirror digest_formatter goals_html rows with tier bars
    # -------------------------------------------------------------
    retention_html = ""
    if ret and any(ret.get(k, 0) for k in ("w1", "w4", "w12")):
        def _row(label: str, rate: float) -> str:
            if rate >= 0.30:
                badge_color = "#16a34a"
                badge_text = "strong"
            elif rate >= 0.15:
                badge_color = "#f59e0b"
                badge_text = "typical"
            else:
                badge_color = "#dc2626"
                badge_text = "weak"
            bar_pct = min(100, int(rate * 100 * 3))  # scale so 30%+ fills the bar
            return f"""
            <div style="margin:10px 0">
              <div style="display:flex;justify-content:space-between;font-size:13px;color:#e2e8f0">
                <span>{label}</span>
                <span style="color:{badge_color};font-weight:700">{(rate*100):.0f}% · {badge_text}</span>
              </div>
              <div style="margin-top:4px;height:6px;background:rgba(255,255,255,0.06);border-radius:3px">
                <div style="width:{bar_pct}%;height:100%;background:{badge_color};border-radius:3px"></div>
              </div>
            </div>
            """
        retention_html = f"""
        <div style="margin:16px 0;padding:16px 18px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px">
            <strong style="color:#e2e8f0;font-size:14px">Retention · week 1 / 4 / 12</strong>
            {_row("Week 1 repurchase", float(ret.get("w1", 0)))}
            {_row("Week 4 repurchase", float(ret.get("w4", 0)))}
            {_row("Week 12 repurchase", float(ret.get("w12", 0)))}
        </div>
        """

    # -------------------------------------------------------------
    # CTA — byte-for-byte from digest_formatter cta_html (amber→violet)
    # -------------------------------------------------------------
    cta_html = f"""
    <div style="text-align:center;margin:28px 0 8px">
        <a href="{_DASHBOARD_URL}" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#d4893a 0%,#a855f7 100%);background-color:#c47a3e;color:#ffffff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:600;letter-spacing:0.3px">
            Open your dashboard
        </a>
    </div>
    """

    # -------------------------------------------------------------
    # BODY — mirror digest_formatter body_inner structure
    # -------------------------------------------------------------
    # Summary card is only rendered when there's data; if nothing,
    # we render the clean-slate message instead
    if rars_total == 0 and signals_count == 0 and not rars_comps:
        body_inner = f"""
<h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#f1f5f9;letter-spacing:-0.2px">Your Morning Intelligence</h2>
<p style="font-size:13px;color:#64748b;margin:0 0 24px">{shop_name} &middot; {period}</p>

<div style="margin:24px 0;padding:24px;background:linear-gradient(135deg,rgba(16,185,129,0.08) 0%,rgba(168,85,247,0.08) 100%);border:1px solid rgba(16,185,129,0.22);border-radius:12px;text-align:center">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.16em;color:#10b981;margin-bottom:8px">Clean slate</div>
  <div style="font-size:26px;font-weight:800;color:#f1f5f9;line-height:1.2">Your funnel is healthy this morning.</div>
  <p style="margin:10px 0 0;font-size:13px;color:#94a3b8;line-height:1.55">No material risk, no urgent findings. Spark is watching — anything that crosses threshold lands here tomorrow.</p>
</div>

{cta_html}
"""
    else:
        body_inner = f"""
<h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#f1f5f9;letter-spacing:-0.2px">Your Morning Intelligence</h2>
<p style="font-size:13px;color:#64748b;margin:0 0 24px">{shop_name} &middot; {period}</p>

{rars_hero_html}
{lead_html}
{risk_html}
{benchmarks_html}
{retention_html}
{cta_html}
"""

    subject = f"Your Morning Intelligence — {shop_name}"
    html_out = _wrap_html(subject, body_inner, show_logo=True)

    # -------- Plain-text (mirror digest_formatter plain structure) -----
    lines = [f"Your Morning Intelligence — {shop_name}", period, ""]
    if rars_total > 0:
        lines += ["REVENUE AT RISK", f"  {currency} {rars_total:,.0f}/month"]
        if rars_prevented > 0:
            lines.append(f"  HedgeSpark already prevented {currency} {rars_prevented:,.0f} this month")
    if top_product:
        lines += ["", f"Today's lead story — {top_product}"]
        if top_action:
            lines.append(f"  → {top_action}")
    if rars_comps:
        lines += ["", "WHERE IT'S LEAKING"]
        for c in rars_comps:
            label = _SOURCE_HUMAN.get(c.get("source", ""), c.get("source", "unknown"))
            lines.append(f"  · {label}: {currency} {c.get('loss_eur', 0):,.0f}/mo")
    if bench and bench.get("total_recovery", 0) > 0:
        lines += [
            "",
            f"YOU VS PEERS — {bench.get('peer_count', 0)} shops in {bench.get('band', 'your band')}",
            f"  {currency} {bench['total_recovery']:,.0f}/month recoverable to top 25%",
        ]
    if ret and any(ret.get(k, 0) for k in ("w1", "w4", "w12")):
        lines += [
            "",
            f"RETENTION: w1 {(ret.get('w1', 0)*100):.0f}% · "
            f"w4 {(ret.get('w4', 0)*100):.0f}% · "
            f"w12 {(ret.get('w12', 0)*100):.0f}%",
        ]
    if rars_total == 0 and signals_count == 0 and not rars_comps:
        lines = [f"Your Morning Intelligence — {shop_name}", period, "",
                 "Clean slate — your funnel is healthy. Spark is watching."]
    lines.append("")
    lines.append(f"Dashboard: {_DASHBOARD_URL}")
    plain_out = "\n".join(lines)

    return subject, html_out, plain_out


def run_lite_morning_digest_cycle(db: Session) -> dict:
    """Process all eligible Starter merchants for today's morning digest.

    Eligibility:
      - install_status == "active"
      - contact_email is not NULL and not empty
      - plan == "starter" OR plan is None (default = Starter)
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

            # Starter-only. Pro/Scale merchants get the weekly digest
            # already; stacking a daily email on top would be noise.
            # Merchants with plan=None are treated as Starter (default band).
            plan = (m.plan or "starter").lower()
            if plan != "starter":
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
                    from_address="HedgeSpark <digest@hedgesparkhq.com>",
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
