"""
digest_formatter.py — Format a weekly digest dict into email HTML + plain text.

Public interface:
    format_digest(digest: dict) -> tuple[str, str]
    Returns (html, plain_text).

Architecture (refactor 2026-05-12 — close A3 god-function class):
    Each digest section is a pair of pure renderer functions
    ((ctx) -> str). Renderers return the section body or "" to
    omit. The dispatcher composes the ordered list per output
    format. Adding a section = add a (plain, html) pair and
    register in _PLAIN_ORDER / _HTML_ORDER — no god function edit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

_DASHBOARD_URL = "https://app.hedgesparkhq.com/"


# ---------------------------------------------------------------------------
# Context — pre-extracted digest fields. Renderers consume this, not the raw
# dict, so all field lookups + defaults live in one place.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Ctx:
    shop: str
    currency: str
    period: str
    plan: str
    tw: dict
    lw: dict
    visitors: int
    cvr: Any  # int | float | None
    delta: Any  # int | float | None
    confidence: str
    rec: dict | None
    risk: dict
    rars_hero: dict
    rars_forecast: dict
    peer_benchmarks: dict
    product_decline: dict
    goal_progress: list
    whats_working: dict | None
    proof_report: dict
    proof: dict
    sip_insights: list
    top_products: list
    insight: dict | None


def _ctx_from(digest: dict) -> _Ctx:
    return _Ctx(
        shop=digest["shop_domain"].replace(".myshopify.com", ""),
        currency=digest["currency"],
        period=f"{digest['period_start']} – {digest['period_end']}",
        plan=digest.get("merchant_plan", "lite"),
        tw=digest["this_week"],
        lw=digest["last_week"],
        visitors=digest.get("unique_visitors", 0),
        cvr=digest.get("conversion_rate"),
        delta=digest.get("revenue_delta_pct"),
        confidence=digest.get("data_confidence", "solid"),
        rec=digest.get("recommendation"),
        risk=digest.get("revenue_at_risk", {}),
        rars_hero=digest.get("rars_hero") or {},
        rars_forecast=digest.get("rars_forecast") or {},
        peer_benchmarks=digest.get("peer_benchmarks") or {},
        product_decline=digest.get("product_decline") or {},
        goal_progress=digest.get("goal_progress") or [],
        whats_working=digest.get("whats_working"),
        proof_report=digest.get("proof_report", {}),
        proof=digest.get("proof", {}),
        sip_insights=digest.get("sip_insights", []),
        top_products=digest.get("top_products", []),
        insight=digest.get("insight"),
    )


# ===========================================================================
# PLAIN TEXT RENDERERS — each returns a multi-line string or "" to omit.
# ===========================================================================

def _plain_header(c: _Ctx) -> str:
    lines = [
        f"Weekly Revenue Digest — {c.shop}",
        c.period,
        "",
        "THIS WEEK",
        f"  Revenue:    {c.currency} {c.tw['revenue']:,.2f}",
        f"  Orders:     {c.tw['order_count']}",
        f"  AOV:        {c.currency} {c.tw['aov']:,.2f}",
    ]
    if c.visitors > 0:
        cvr_str = f" · Conversion: {c.cvr}%" if c.cvr is not None else ""
        conf_str = " (early data)" if c.confidence == "early" else ""
        lines.append(f"  Visitors:   {c.visitors:,}{cvr_str}{conf_str}")
    if c.delta is not None:
        arrow = "+" if c.delta >= 0 else ""
        lines.append(f"  vs last week: {arrow}{c.delta}% revenue")
    elif c.lw["order_count"] == 0 and c.tw["order_count"] > 0:
        lines.append("  vs last week: first week with orders!")
    return "\n".join(lines)


def _plain_recommendation(c: _Ctx) -> str:
    if not c.rec:
        return ""
    return f"\n>> {c.rec['headline']}\n   {c.rec['body']}"


def _plain_rars_hero(c: _Ctx) -> str:
    if not c.rars_hero.get("total_at_risk_eur"):
        return ""
    total_eur = c.rars_hero["total_at_risk_eur"]
    prevented = c.rars_hero.get("prevented_eur_this_month", 0)
    lines = ["", "REVENUE AT RISK RIGHT NOW", f"  {c.currency} {total_eur:,.0f}/month"]
    if prevented and prevented > 0:
        lines.append(f"  HedgeSpark already prevented {c.currency} {prevented:,.0f} this month")
    if c.rars_hero.get("headline"):
        lines.append(f"  {c.rars_hero['headline'][:200]}")
    return "\n".join(lines)


def _plain_rars_forecast(c: _Ctx) -> str:
    if c.rars_forecast.get("status") != "ok":
        return ""
    direction = c.rars_forecast.get("direction", "stable")
    forecast_eur = c.rars_forecast.get("forecast_7d_eur", 0)
    delta_pct = c.rars_forecast.get("week_delta_pct", 0)
    arrow = {"rising": "rising", "falling": "falling", "stable": "stable"}[direction]
    return (
        "\nNEXT WEEK FORECAST\n"
        f"  Risk projected {arrow} to {c.currency} {forecast_eur:,.0f}/month ({delta_pct:+.0f}%)"
    )


def _plain_peer_benchmarks(c: _Ctx) -> str:
    if not c.peer_benchmarks.get("peer_count"):
        return ""
    band = c.peer_benchmarks.get("band", "your category")
    peer_n = c.peer_benchmarks["peer_count"]
    recovery = c.peer_benchmarks.get("total_recovery_potential_eur", 0)
    lines = ["", "YOU vs SIMILAR SHOPS", f"  Compared against {peer_n} shops in {band}"]
    if recovery > 0:
        lines.append(f"  {c.currency} {recovery:,.0f}/month recoverable if you reach top 25%")
    return "\n".join(lines)


def _plain_product_decline(c: _Ctx) -> str:
    decline_products = c.product_decline.get("products") or []
    if not decline_products:
        return ""
    total_loss = c.product_decline.get("total_loss_eur_per_month", 0)
    lines = [
        "",
        "PRODUCTS LOSING MOMENTUM",
        f"  Total projected loss: {c.currency} {total_loss:,.0f}/month",
    ]
    for p in decline_products[:3]:
        title = (p.get("product_title") or "Unknown")[:60]
        loss = p.get("loss_eur", 0)
        lines.append(f"  - {title}: {c.currency} {loss:,.0f}/mo")
    return "\n".join(lines)


def _plain_goal_progress(c: _Ctx) -> str:
    if not c.goal_progress:
        return ""
    lines = ["", "YOUR MONTHLY TARGETS"]
    for g in c.goal_progress[:3]:
        metric = g["metric"].replace("_", " ").title()
        pct = g.get("progress_pct", 0)
        status_label = {
            "on_track": "on track",
            "at_risk": "at risk",
            "missed": "missed",
            "achieved": "hit it",
        }.get(g.get("status", ""), "")
        lines.append(f"  {metric}: {pct}% of target ({status_label})")
    return "\n".join(lines)


def _plain_whats_working(c: _Ctx) -> str:
    if not c.whats_working:
        return ""
    return f"\nWHAT'S WORKING\n  {c.whats_working['message']}"


def _plain_proof(c: _Ctx) -> str:
    if c.proof_report.get("has_proof"):
        pr_revenue = c.proof_report.get("incremental_revenue", 0)
        show_rev = c.proof_report.get("show_revenue", False)
        lines = ["", "YOUR PROVEN IMPACT"]
        if show_rev and pr_revenue > 0:
            lines.append(f"  +{c.currency} {pr_revenue:,.0f} estimated incremental revenue")
        lines.append(f"  {c.proof_report.get('headline', '')}")
        lines.append(f"  {c.proof_report.get('detail', '')}")
        conf = c.proof_report.get("confidence_label", "")
        if conf:
            lines.append(f"  Confidence: {conf}")
        lines.append(f"  {c.proof_report.get('trust_note', '')}")
        return "\n".join(lines)
    if c.proof.get("improvements"):
        rev_delta = c.proof.get("total_revenue_delta", 0)
        n = len(c.proof["improvements"])
        suffix = "s" if n != 1 else ""
        header = (
            f"IMPACT MEASURED: {n} action{suffix} improved results"
            + (f" · {c.currency} {rev_delta:+,.2f} revenue" if rev_delta != 0 else "")
        )
        lines = ["", header]
        for imp in c.proof["improvements"][:2]:
            lines.append(f"  {imp['summary']}")
        return "\n".join(lines)
    return ""


def _plain_risk(c: _Ctx) -> str:
    if not c.risk.get("opportunities"):
        return ""
    total = c.risk["total_at_risk"]
    count = c.risk["affected_products"]
    top_rec = c.risk.get("top_recoverable", 0)
    suffix = "s" if count != 1 else ""
    lines = [
        "",
        f"REVENUE AT RISK: {c.currency} {total:,.2f} across {count} product{suffix}",
    ]
    if top_rec > 0:
        lines.append(f"  Fixing the top issue could recover ~{c.currency} {top_rec:,.2f}")
    for opp in c.risk["opportunities"]:
        lines.append(f"  • {opp['product_name']}: {opp['problem']}")
        lines.append(f"    → {opp['action']}")
    return "\n".join(lines)


def _plain_top_products(c: _Ctx) -> str:
    if not c.top_products:
        return ""
    lines = ["", "TOP PRODUCTS"]
    for p in c.top_products:
        lines.append(f"  {p['title']} — {c.currency} {p['revenue']:,.2f} ({p['units']} sold)")
    return "\n".join(lines)


def _plain_cold_start(c: _Ctx) -> str:
    # Cold-start fallback: only when there's truly nothing actionable to show.
    if c.risk.get("opportunities") or c.top_products or c.insight:
        return ""
    return (
        "\nWe're building your store's intelligence profile.\n"
        "As more visitor and order data flows in, this digest will include\n"
        "product-level performance and actionable recommendations."
    )


def _plain_sip_insights(c: _Ctx) -> str:
    if not c.sip_insights:
        return ""
    lines = ["", "INTELLIGENCE INSIGHTS"]
    for ins in c.sip_insights[:3]:
        lines.append(f"  {ins.get('headline', '')}")
        if ins.get("detail"):
            lines.append(f"    {ins['detail']}")
    return "\n".join(lines)


def _plain_upgrade(c: _Ctx) -> str:
    if c.plan == "pro" or not c.risk.get("opportunities"):
        return ""
    top_opp = c.risk["opportunities"][0]
    others = c.risk["affected_products"] - 1
    others_suffix = "s" if others > 1 else ""
    return (
        f"\nPRO INSIGHT: We found specific fixes for \"{top_opp['product_name']}\" and "
        f"{others} other product{others_suffix}.\n"
        f"  Upgrade to Pro to unlock exact actions and track whether they work.\n"
        f"  {_DASHBOARD_URL}?upgrade=1"
    )


def _plain_footer(c: _Ctx) -> str:
    section = "signals" if c.risk.get("opportunities") else "revenue"
    return (
        f"\nView your dashboard: {_DASHBOARD_URL}?section={section}\n"
        "\n—\n"
        "HedgeSpark · Revenue Intelligence for Shopify"
    )


# ===========================================================================
# HTML RENDERERS — each returns the section block or "" to omit.
# ===========================================================================

def _html_header(c: _Ctx) -> str:
    delta_html = ""
    if c.delta is not None:
        color = "#16a34a" if c.delta >= 0 else "#dc2626"
        sign = "+" if c.delta >= 0 else ""
        delta_html = (
            f'<span style="color:{color};font-weight:600">{sign}{c.delta}%</span>'
            f' vs last week'
        )
    elif c.lw["order_count"] == 0 and c.tw["order_count"] > 0:
        delta_html = '<span style="color:#16a34a;font-weight:600">First week with orders!</span>'

    visitor_html = ""
    if c.visitors > 0:
        parts = [f"<strong>{c.visitors:,}</strong> unique visitors"]
        if c.cvr is not None:
            parts.append(f"<strong>{c.cvr}%</strong> conversion rate")
        if c.confidence == "early":
            parts.append('<span style="color:#f59e0b;font-size:11px">early data</span>')
        visitor_html = (
            '<tr><td colspan="2" style="padding:12px 0 0;font-size:13px;color:#64748b;'
            f'border-top:1px solid #f1f5f9">{" &middot; ".join(parts)}</td></tr>'
        )

    return f"""
<h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#f1f5f9;letter-spacing:-0.2px">Weekly Revenue Digest</h2>
<p style="font-size:13px;color:#64748b;margin:0 0 24px">{c.shop} &middot; {c.period}</p>

<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:16px">
  <table style="width:100%;font-size:14px;border-collapse:collapse">
    <tr>
      <td style="padding:8px 0;color:#64748b">Revenue</td>
      <td style="padding:8px 0;text-align:right;font-size:24px;font-weight:700;color:#f1f5f9">{c.currency} {c.tw['revenue']:,.2f}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#64748b">Orders</td>
      <td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9">{c.tw['order_count']}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#64748b">Avg Order Value</td>
      <td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9">{c.currency} {c.tw['aov']:,.2f}</td>
    </tr>
    {visitor_html}
  </table>
  {f'<p style="margin:12px 0 0;font-size:13px;color:#64748b">{delta_html}</p>' if delta_html else ''}
</div>
"""


def _html_rars_hero(c: _Ctx) -> str:
    if not c.rars_hero.get("total_at_risk_eur"):
        return ""
    total_eur = c.rars_hero["total_at_risk_eur"]
    prevented = c.rars_hero.get("prevented_eur_this_month", 0)
    prevented_block = ""
    if prevented and prevented > 0:
        prevented_block = (
            f'<p style="margin:6px 0 0;font-size:13px;color:#10b981;font-weight:600">'
            f"HedgeSpark already prevented {c.currency} {prevented:,.0f} this month"
            f"</p>"
        )
    return f"""
<div style="margin:24px 0;padding:24px;background:linear-gradient(135deg,rgba(212,137,58,0.08) 0%,rgba(168,85,247,0.08) 100%);border:1px solid rgba(212,137,58,0.25);border-radius:12px;text-align:center">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.16em;color:#d4893a;margin-bottom:8px">Revenue at Risk</div>
    <div style="font-size:42px;font-weight:800;color:#f1f5f9;line-height:1.1">{c.currency} {total_eur:,.0f}<span style="font-size:14px;font-weight:600;color:#94a3b8">/month</span></div>
    {prevented_block}
</div>
"""


def _html_rars_forecast(c: _Ctx) -> str:
    if c.rars_forecast.get("status") != "ok":
        return ""
    direction = c.rars_forecast.get("direction", "stable")
    forecast_eur = c.rars_forecast.get("forecast_7d_eur", 0)
    delta_pct = c.rars_forecast.get("week_delta_pct", 0)
    arrow = {"rising": "↑", "falling": "↓", "stable": "→"}[direction]
    color = {"rising": "#dc2626", "falling": "#16a34a", "stable": "#94a3b8"}[direction]
    return f"""
<div style="margin:16px 0;padding:14px 18px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px;font-size:13px;color:#cbd5e1">
    <strong style="color:#e2e8f0">Next Week Forecast</strong>
    <span style="color:{color};font-weight:700;margin-left:8px">{arrow} {c.currency} {forecast_eur:,.0f}/mo ({delta_pct:+.0f}%)</span>
</div>
"""


def _html_recommendation(c: _Ctx) -> str:
    if not c.rec:
        return ""
    return f"""
<div style="margin:20px 0;padding:16px 18px;background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:8px;font-size:14px;line-height:1.6">
    <strong style="color:#10b981;font-size:14px">{c.rec['headline']}</strong>
    <p style="margin:6px 0 0;color:#c8d1dc">{c.rec['body']}</p>
</div>
"""


def _html_goal_progress(c: _Ctx) -> str:
    if not c.goal_progress:
        return ""
    rows = ""
    for g in c.goal_progress[:3]:
        metric = g["metric"].replace("_", " ").title()
        pct = g.get("progress_pct", 0)
        status = g.get("status", "")
        badge_color = {
            "on_track": "#16a34a",
            "achieved": "#16a34a",
            "at_risk": "#f59e0b",
            "missed": "#dc2626",
        }.get(status, "#94a3b8")
        badge_text = {
            "on_track": "on track",
            "achieved": "hit it",
            "at_risk": "at risk",
            "missed": "missed",
        }.get(status, status)
        bar_pct = min(100, int(pct))
        rows += f"""
<div style="margin:10px 0">
  <div style="display:flex;justify-content:space-between;font-size:13px;color:#e2e8f0">
    <span>{metric}</span>
    <span style="color:{badge_color};font-weight:700">{pct}% &middot; {badge_text}</span>
  </div>
  <div style="margin-top:4px;height:6px;background:rgba(255,255,255,0.06);border-radius:3px">
    <div style="width:{bar_pct}%;height:100%;background:{badge_color};border-radius:3px"></div>
  </div>
</div>
"""
    return f"""
<div style="margin:16px 0;padding:16px 18px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:8px">
    <strong style="color:#e2e8f0;font-size:14px">Your Monthly Targets</strong>
    {rows}
</div>
"""


def _html_peer_benchmarks(c: _Ctx) -> str:
    if not c.peer_benchmarks.get("peer_count"):
        return ""
    band = c.peer_benchmarks.get("band", "your category")
    peer_n = c.peer_benchmarks["peer_count"]
    recovery = c.peer_benchmarks.get("total_recovery_potential_eur", 0)
    recovery_block = ""
    if recovery > 0:
        recovery_block = (
            f'<p style="margin:6px 0 0;font-size:13px;color:#10b981;font-weight:600">'
            f"{c.currency} {recovery:,.0f}/month recoverable if you reach top 25%"
            f"</p>"
        )
    return f"""
<div style="margin:16px 0;padding:16px 18px;background:rgba(167,139,250,0.06);border:1px solid rgba(167,139,250,0.18);border-radius:8px">
    <strong style="color:#c4b5fd;font-size:14px">You vs. Similar Shops</strong>
    <p style="margin:4px 0 0;font-size:12px;color:#94a3b8">Benchmarked against {peer_n} shops in {band}</p>
    {recovery_block}
</div>
"""


def _html_product_decline(c: _Ctx) -> str:
    decline_products = c.product_decline.get("products") or []
    if not decline_products:
        return ""
    total_loss = c.product_decline.get("total_loss_eur_per_month", 0)
    rows = ""
    for p in decline_products[:3]:
        title = (p.get("product_title") or "Unknown")[:60]
        loss = p.get("loss_eur", 0)
        rows += (
            f'<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">'
            f'<strong style="color:#f1f5f9;font-size:13px">{title}</strong>'
            f'<span style="float:right;color:#dc2626;font-weight:700">{c.currency} {loss:,.0f}/mo</span>'
            f'</div>'
        )
    return f"""
<div style="margin:16px 0;padding:16px 18px;background:rgba(220,38,38,0.06);border:1px solid rgba(220,38,38,0.18);border-radius:8px">
    <strong style="color:#fca5a5;font-size:14px">Products Losing You Money</strong>
    <p style="margin:4px 0 10px;font-size:12px;color:#94a3b8">Projected loss this month: <strong style="color:#fca5a5">{c.currency} {total_loss:,.0f}</strong></p>
    {rows}
</div>
"""


def _html_whats_working(c: _Ctx) -> str:
    if not c.whats_working:
        return ""
    return f"""
<div style="margin:20px 0;padding:14px 18px;background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:8px;font-size:14px;line-height:1.5">
    <strong style="color:#10b981">What's Working</strong>
    <p style="margin:6px 0 0;color:#c8d1dc">{c.whats_working['message']}</p>
</div>
"""


def _html_proof(c: _Ctx) -> str:
    if c.proof_report.get("has_proof"):
        pr_headline = c.proof_report.get("headline", "")
        pr_detail = c.proof_report.get("detail", "")
        pr_revenue = c.proof_report.get("incremental_revenue", 0)
        pr_conf = c.proof_report.get("confidence_label", "")
        pr_trust = c.proof_report.get("trust_note", "")
        show_rev = c.proof_report.get("show_revenue", False)
        revenue_block = ""
        if show_rev and pr_revenue > 0:
            revenue_block = f"""
<div style="margin:10px 0;text-align:center">
    <span style="font-size:28px;font-weight:700;color:#059669">+{c.currency} {pr_revenue:,.0f}</span>
    <div style="font-size:11px;color:#047857;margin-top:2px">estimated incremental revenue this week</div>
</div>
"""
        conf_badge = ""
        if pr_conf:
            conf_badge = f'<span style="display:inline-block;margin-left:8px;padding:2px 8px;background:#d1fae5;color:#065f46;border-radius:10px;font-size:10px;font-weight:600">{pr_conf}</span>'
        return f"""
<div style="margin:20px 0;padding:16px 18px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;font-size:14px;line-height:1.5">
    <strong style="color:#065f46">Your Proven Impact</strong>{conf_badge}
    {revenue_block}
    <p style="margin:8px 0 4px;color:#065f46;font-size:14px;font-weight:600">{pr_headline}</p>
    <p style="margin:4px 0 0;color:#c8d1dc;font-size:13px">{pr_detail}</p>
    <p style="margin:10px 0 0;color:#6b7280;font-size:11px;font-style:italic">{pr_trust}</p>
</div>
"""
    if c.proof.get("improvements"):
        rev_delta = c.proof.get("total_revenue_delta", 0)
        imp_rows = ""
        for imp in c.proof["improvements"][:2]:
            imp_rows += f'<p style="margin:4px 0;color:#c8d1dc;font-size:13px">{imp["summary"]}</p>'
        header_extra = ""
        if rev_delta > 0:
            header_extra = f' <span style="color:#16a34a;font-weight:700">+{c.currency} {rev_delta:,.2f}</span>'
        n = len(c.proof["improvements"])
        suffix = "s" if n != 1 else ""
        return f"""
<div style="margin:20px 0;padding:14px 18px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;font-size:14px;line-height:1.5">
    <strong style="color:#065f46">Impact Measured</strong>{header_extra}
    <p style="margin:6px 0 4px;font-size:12px;color:#047857">{n} action{suffix} produced measurable improvement</p>
    {imp_rows}
</div>
"""
    return ""


def _html_sip_insights(c: _Ctx) -> str:
    if not c.sip_insights:
        return ""
    items = ""
    for ins in c.sip_insights[:3]:
        headline = ins.get("headline", "")
        detail = ins.get("detail", "")
        items += (
            f'<div style="margin:10px 0;padding:10px 14px;background:rgba(167,139,250,0.06);'
            f'border-left:3px solid rgba(167,139,250,0.3);border-radius:4px">'
            f'<strong style="color:#c4b5fd;font-size:13px">{headline}</strong>'
        )
        if detail:
            items += f'<p style="margin:4px 0 0;color:#94a3b8;font-size:12px">{detail}</p>'
        items += '</div>'
    return f"""
<h3 style="margin:24px 0 8px;font-size:15px;font-weight:700;color:#e2e8f0;text-transform:uppercase;letter-spacing:0.4px">
    Intelligence Insights
</h3>
{items}
"""


def _html_risk(c: _Ctx) -> str:
    if not c.risk.get("opportunities"):
        return ""
    total = c.risk["total_at_risk"]
    count = c.risk["affected_products"]
    top_rec = c.risk.get("top_recoverable", 0)
    impact_line = ""
    if top_rec > 0:
        impact_line = (
            f'<p style="margin:4px 0 10px;font-size:13px;color:#10b981;font-weight:600">'
            f'Fixing the top issue could recover ~{c.currency} {top_rec:,.2f}</p>'
        )
    opp_rows = ""
    for opp in c.risk["opportunities"]:
        opp_rows += f"""
<div style="padding:10px 0;border-bottom:1px solid #fde68a">
    <strong style="color:#f59e0b">{opp['product_name']}</strong>
    <p style="margin:4px 0 2px;color:#c8d1dc;font-size:13px">{opp['problem']}</p>
    <p style="margin:0;color:#10b981;font-size:13px">&rarr; {opp['action']}</p>
</div>"""
    suffix = "s" if count != 1 else ""
    return f"""
<div style="margin:20px 0;padding:16px 18px;background:rgba(245,158,11,0.08);border:1px solid #fde68a;border-radius:8px;font-size:14px">
    <div style="margin-bottom:4px">
        <span style="font-size:13px;color:#f59e0b;font-weight:600">Revenue at Risk</span>
        <span style="float:right;font-size:18px;font-weight:700;color:#b45309">{c.currency} {total:,.2f}</span>
    </div>
    <p style="margin:0 0 4px;font-size:12px;color:#f59e0b">{count} product{suffix} need attention</p>
    {impact_line}
    {opp_rows}
</div>
"""


def _html_top_products(c: _Ctx) -> str:
    if not c.top_products:
        return ""
    rows = ""
    for i, p in enumerate(c.top_products):
        bg = "background:rgba(255,255,255,0.03);" if i % 2 == 1 else ""
        rows += (
            f'<tr style="{bg}">'
            f'<td style="padding:6px 12px 6px 0">{p["title"]}</td>'
            f'<td style="padding:6px 12px 6px 0;text-align:right;font-weight:600">'
            f'{c.currency} {p["revenue"]:,.2f}</td>'
            f'<td style="padding:6px 0;text-align:right;color:#64748b">'
            f'{p["units"]} sold</td></tr>'
        )
    return f"""
<h3 style="margin:24px 0 8px;font-size:14px;color:#64748b">Top Products</h3>
<table style="width:100%;font-size:14px;border-collapse:collapse">{rows}</table>
"""


def _html_insight(c: _Ctx) -> str:
    if not c.insight:
        return ""
    return f"""
<div style="margin:24px 0;padding:14px 16px;background:rgba(245,158,11,0.06);border-left:4px solid #f59e0b;border-radius:4px;font-size:14px;line-height:1.5">
    <strong style="color:#f59e0b">Opportunity:</strong> {c.insight['message']}
</div>
"""


def _html_cold_start(c: _Ctx) -> str:
    # Cold-start: omit when there's any actionable content (top_products or insight).
    if c.top_products or c.insight:
        return ""
    return """
<div style="margin:24px 0;padding:16px;background:rgba(167,139,250,0.06);border:1px solid rgba(167,139,250,0.15);border-radius:8px;font-size:14px;color:#94a3b8;line-height:1.5">
    We're building your store's intelligence profile. As more visitor and order data flows in,
    this digest will include product-level performance and actionable recommendations.
</div>
"""


def _html_upgrade(c: _Ctx) -> str:
    if c.plan == "pro" or not c.risk.get("opportunities"):
        return ""
    top_opp = c.risk["opportunities"][0]
    others = c.risk["affected_products"] - 1
    others_text = f" and {others} other product{'s' if others > 1 else ''}" if others > 0 else ""
    return f"""
<div style="margin:20px 0;padding:14px 18px;background:rgba(167,139,250,0.08);border:1px solid #c4b5fd;border-radius:8px;font-size:13px;line-height:1.6">
    <strong style="color:#c4b5fd">Pro Insight Available</strong>
    <p style="margin:6px 0 0;color:#a78bfa">
        We found specific fixes for &ldquo;{top_opp['product_name']}&rdquo;{others_text}.
        Upgrade to Pro to unlock exact actions and track whether they work.
    </p>
    <div style="margin-top:10px">
        <a href="{_DASHBOARD_URL}?upgrade=1" style="display:inline-block;padding:10px 20px;background:linear-gradient(135deg,#d4893a 0%,#a855f7 100%);background-color:#c47a3e;color:#ffffff;text-decoration:none;border-radius:8px;font-size:12px;font-weight:600">
            Unlock Pro Actions
        </a>
    </div>
</div>
"""


def _html_cta(c: _Ctx) -> str:
    if c.risk.get("opportunities"):
        cta_link = f"{_DASHBOARD_URL}?section=signals"
        cta_label = "View Your Signals"
    else:
        cta_link = f"{_DASHBOARD_URL}?section=revenue"
        cta_label = "View Your Revenue"
    return f"""
<div style="text-align:center;margin:28px 0 8px">
    <a href="{cta_link}" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#d4893a 0%,#a855f7 100%);background-color:#c47a3e;color:#ffffff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:600;letter-spacing:0.3px">
        {cta_label}
    </a>
</div>
"""


# ===========================================================================
# Section ordering — separate per format because the visual hierarchy differs.
# Adding a section: write _plain_X + _html_X, register in the lists below.
# ===========================================================================

_Renderer = Callable[[_Ctx], str]

_PLAIN_ORDER: tuple[_Renderer, ...] = (
    _plain_header,
    _plain_recommendation,
    _plain_rars_hero,
    _plain_rars_forecast,
    _plain_peer_benchmarks,
    _plain_product_decline,
    _plain_goal_progress,
    _plain_whats_working,
    _plain_proof,
    _plain_risk,
    _plain_top_products,
    _plain_cold_start,
    _plain_sip_insights,
    _plain_upgrade,
    _plain_footer,
)

_HTML_ORDER: tuple[_Renderer, ...] = (
    _html_header,
    _html_rars_hero,
    _html_rars_forecast,
    _html_recommendation,
    _html_goal_progress,
    _html_peer_benchmarks,
    _html_product_decline,
    _html_whats_working,
    _html_proof,
    _html_sip_insights,
    _html_risk,
    _html_top_products,
    _html_insight,
    _html_cold_start,
    _html_upgrade,
    _html_cta,
)


def format_digest(digest: dict) -> tuple[str, str]:
    """Convert a digest dict (from weekly_digest.assemble_digest) to email content.

    Composes each section via the ordered renderer lists above. Plain
    text joins rendered fragments with newlines; HTML wraps in the
    shared dark-theme template. Sections that return "" are omitted.
    """
    ctx = _ctx_from(digest)
    plain = "\n".join(r(ctx) for r in _PLAIN_ORDER if r(ctx))
    html_inner = "\n".join(r(ctx) for r in _HTML_ORDER if r(ctx))
    from app.services.email_templates import _wrap_html
    title = f"Weekly Revenue Digest — {ctx.shop}"
    html = _wrap_html(title, html_inner, show_logo=True)
    return html, plain
