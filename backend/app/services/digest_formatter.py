"""
digest_formatter.py — Format a weekly digest dict into email HTML + plain text.

Public interface:
    format_digest(digest: dict) -> tuple[str, str]
    Returns (html, plain_text).
"""
from __future__ import annotations

_DASHBOARD_URL = "https://app.hedgesparkhq.com/"


def format_digest(digest: dict) -> tuple[str, str]:
    """Convert a digest dict (from weekly_digest.assemble_digest) to email content."""
    shop = digest["shop_domain"].replace(".myshopify.com", "")
    tw = digest["this_week"]
    lw = digest["last_week"]
    currency = digest["currency"]
    delta = digest.get("revenue_delta_pct")
    visitors = digest.get("unique_visitors", 0)
    cvr = digest.get("conversion_rate")
    period = f"{digest['period_start']} – {digest['period_end']}"
    risk = digest.get("revenue_at_risk", {})
    confidence = digest.get("data_confidence", "solid")

    # =====================================================================
    # PLAIN TEXT
    # =====================================================================
    lines = [
        f"Weekly Revenue Digest — {shop}",
        period,
        "",
        "THIS WEEK",
        f"  Revenue:    {currency} {tw['revenue']:,.2f}",
        f"  Orders:     {tw['order_count']}",
        f"  AOV:        {currency} {tw['aov']:,.2f}",
    ]

    if visitors > 0:
        cvr_str = f" · Conversion: {cvr}%" if cvr is not None else ""
        conf_str = " (early data)" if confidence == "early" else ""
        lines.append(f"  Visitors:   {visitors:,}{cvr_str}{conf_str}")

    if delta is not None:
        arrow = "+" if delta >= 0 else ""
        lines.append(f"  vs last week: {arrow}{delta}% revenue")
    elif lw["order_count"] == 0 and tw["order_count"] > 0:
        lines.append("  vs last week: first week with orders!")

    rec = digest.get("recommendation")
    if rec:
        lines += ["", f">> {rec['headline']}", f"   {rec['body']}"]

    ww = digest.get("whats_working")
    if ww:
        lines += ["", "WHAT'S WORKING", f"  {ww['message']}"]

    proof_report = digest.get("proof_report", {})
    proof = digest.get("proof", {})

    if proof_report.get("has_proof"):
        pr_revenue = proof_report.get("incremental_revenue", 0)
        show_rev = proof_report.get("show_revenue", False)
        lines += ["", "YOUR PROVEN IMPACT"]
        if show_rev and pr_revenue > 0:
            lines.append(f"  +{currency} {pr_revenue:,.0f} estimated incremental revenue")
        lines.append(f"  {proof_report.get('headline', '')}")
        lines.append(f"  {proof_report.get('detail', '')}")
        conf = proof_report.get("confidence_label", "")
        if conf:
            lines.append(f"  Confidence: {conf}")
        lines.append(f"  {proof_report.get('trust_note', '')}")
    elif proof.get("improvements"):
        rev_delta = proof.get("total_revenue_delta", 0)
        lines += [
            "",
            f"IMPACT MEASURED: {len(proof['improvements'])} action{'s' if len(proof['improvements']) != 1 else ''} improved results"
            + (f" · {currency} {rev_delta:+,.2f} revenue" if rev_delta != 0 else ""),
        ]
        for imp in proof["improvements"][:2]:
            lines.append(f"  {imp['summary']}")

    if risk.get("opportunities"):
        total = risk["total_at_risk"]
        count = risk["affected_products"]
        top_rec = risk.get("top_recoverable", 0)
        lines += [
            "",
            f"REVENUE AT RISK: {currency} {total:,.2f} across {count} product{'s' if count != 1 else ''}",
        ]
        if top_rec > 0:
            lines.append(
                f"  Fixing the top issue could recover ~{currency} {top_rec:,.2f}"
            )
        for opp in risk["opportunities"]:
            lines += [
                f"  • {opp['product_name']}: {opp['problem']}",
                f"    → {opp['action']}",
            ]

    if digest.get("top_products"):
        lines += ["", "TOP PRODUCTS"]
        for p in digest["top_products"]:
            lines.append(f"  {p['title']} — {currency} {p['revenue']:,.2f} ({p['units']} sold)")

    if not risk.get("opportunities") and not digest.get("top_products") and not digest.get("insight"):
        lines += [
            "",
            "We're building your store's intelligence profile.",
            "As more visitor and order data flows in, this digest will include",
            "product-level performance and actionable recommendations.",
        ]

    # Lite-only upgrade teaser — specific, not generic
    # SIP Intelligence Insights
    sip_insights = digest.get("sip_insights", [])
    if sip_insights:
        lines += ["", "INTELLIGENCE INSIGHTS"]
        for ins in sip_insights[:3]:
            lines.append(f"  {ins.get('headline', '')}")
            if ins.get("detail"):
                lines.append(f"    {ins['detail']}")

    plan = digest.get("merchant_plan", "lite")
    if plan != "pro" and risk.get("opportunities"):
        top_opp = risk["opportunities"][0]
        lines += [
            "",
            f"PRO INSIGHT: We found specific fixes for \"{top_opp['product_name']}\" and "
            f"{risk['affected_products'] - 1} other product{'s' if risk['affected_products'] > 2 else ''}.",
            f"  Upgrade to Pro to unlock exact actions and track whether they work.",
            f"  {_DASHBOARD_URL}?upgrade=1",
        ]

    plain_link = f"{_DASHBOARD_URL}?section=signals" if risk.get("opportunities") else f"{_DASHBOARD_URL}?section=revenue"
    lines += [
        "",
        f"View your dashboard: {plain_link}",
        "",
        "—",
        "HedgeSpark · Revenue Intelligence for Shopify",
    ]
    plain = "\n".join(lines)

    # =====================================================================
    # HTML
    # =====================================================================

    # --- WoW delta badge ---
    delta_html = ""
    if delta is not None:
        color = "#16a34a" if delta >= 0 else "#dc2626"
        sign = "+" if delta >= 0 else ""
        delta_html = (
            f'<span style="color:{color};font-weight:600">{sign}{delta}%</span>'
            f' vs last week'
        )
    elif lw["order_count"] == 0 and tw["order_count"] > 0:
        delta_html = '<span style="color:#16a34a;font-weight:600">First week with orders!</span>'

    # --- Visitor stats row ---
    visitor_html = ""
    if visitors > 0:
        parts = [f"<strong>{visitors:,}</strong> unique visitors"]
        if cvr is not None:
            parts.append(f"<strong>{cvr}%</strong> conversion rate")
        if confidence == "early":
            parts.append('<span style="color:#f59e0b;font-size:11px">early data</span>')
        visitor_html = (
            '<tr><td colspan="2" style="padding:12px 0 0;font-size:13px;color:#64748b;'
            f'border-top:1px solid #f1f5f9">{" &middot; ".join(parts)}</td></tr>'
        )

    # --- Recommendation ---
    rec_html = ""
    rec = digest.get("recommendation")
    if rec:
        rec_html = f"""
        <div style="margin:20px 0;padding:16px 18px;background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:8px;font-size:14px;line-height:1.6">
            <strong style="color:#10b981;font-size:14px">{rec['headline']}</strong>
            <p style="margin:6px 0 0;color:#c8d1dc">{rec['body']}</p>
        </div>
        """

    # --- What's Working ---
    working_html = ""
    ww = digest.get("whats_working")
    if ww:
        working_html = f"""
        <div style="margin:20px 0;padding:14px 18px;background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:8px;font-size:14px;line-height:1.5">
            <strong style="color:#10b981">What's Working</strong>
            <p style="margin:6px 0 0;color:#c8d1dc">{ww['message']}</p>
        </div>
        """

    # --- Proof of impact (unified proof engine) ---
    proof_html = ""
    proof_report = digest.get("proof_report", {})
    proof = digest.get("proof", {})

    if proof_report.get("has_proof"):
        pr_headline = proof_report.get("headline", "")
        pr_detail = proof_report.get("detail", "")
        pr_revenue = proof_report.get("incremental_revenue", 0)
        pr_conf = proof_report.get("confidence_label", "")
        pr_trust = proof_report.get("trust_note", "")
        show_rev = proof_report.get("show_revenue", False)

        revenue_block = ""
        if show_rev and pr_revenue > 0:
            revenue_block = f"""
            <div style="margin:10px 0;text-align:center">
                <span style="font-size:28px;font-weight:700;color:#059669">+{currency} {pr_revenue:,.0f}</span>
                <div style="font-size:11px;color:#047857;margin-top:2px">estimated incremental revenue this week</div>
            </div>
            """

        conf_badge = ""
        if pr_conf:
            conf_badge = f'<span style="display:inline-block;margin-left:8px;padding:2px 8px;background:#d1fae5;color:#065f46;border-radius:10px;font-size:10px;font-weight:600">{pr_conf}</span>'

        proof_html = f"""
        <div style="margin:20px 0;padding:16px 18px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;font-size:14px;line-height:1.5">
            <strong style="color:#065f46">Your Proven Impact</strong>{conf_badge}
            {revenue_block}
            <p style="margin:8px 0 4px;color:#065f46;font-size:14px;font-weight:600">{pr_headline}</p>
            <p style="margin:4px 0 0;color:#c8d1dc;font-size:13px">{pr_detail}</p>
            <p style="margin:10px 0 0;color:#6b7280;font-size:11px;font-style:italic">{pr_trust}</p>
        </div>
        """
    elif proof.get("improvements"):
        # Fallback to legacy action proof if proof engine has no data
        rev_delta = proof.get("total_revenue_delta", 0)
        imp_rows = ""
        for imp in proof["improvements"][:2]:
            imp_rows += f'<p style="margin:4px 0;color:#c8d1dc;font-size:13px">{imp["summary"]}</p>'
        header_extra = ""
        if rev_delta > 0:
            header_extra = f' <span style="color:#16a34a;font-weight:700">+{currency} {rev_delta:,.2f}</span>'
        proof_html = f"""
        <div style="margin:20px 0;padding:14px 18px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;font-size:14px;line-height:1.5">
            <strong style="color:#065f46">Impact Measured</strong>{header_extra}
            <p style="margin:6px 0 4px;font-size:12px;color:#047857">{len(proof['improvements'])} action{'s' if len(proof['improvements']) != 1 else ''} produced measurable improvement</p>
            {imp_rows}
        </div>
        """

    # --- SIP Intelligence Insights ---
    sip_html = ""
    sip_insights = digest.get("sip_insights", [])
    if sip_insights:
        insight_items = ""
        for ins in sip_insights[:3]:
            headline = ins.get("headline", "")
            detail = ins.get("detail", "")
            insight_items += (
                f'<div style="margin:10px 0;padding:10px 14px;background:rgba(167,139,250,0.06);'
                f'border-left:3px solid rgba(167,139,250,0.3);border-radius:4px">'
                f'<strong style="color:#c4b5fd;font-size:13px">{headline}</strong>'
            )
            if detail:
                insight_items += f'<p style="margin:4px 0 0;color:#94a3b8;font-size:12px">{detail}</p>'
            insight_items += '</div>'

        sip_html = f"""
        <h3 style="margin:24px 0 8px;font-size:15px;font-weight:700;color:#e2e8f0;text-transform:uppercase;letter-spacing:0.4px">
            Intelligence Insights
        </h3>
        {insight_items}
        """

    # --- Revenue at risk ---
    risk_html = ""
    if risk.get("opportunities"):
        total = risk["total_at_risk"]
        count = risk["affected_products"]
        top_rec = risk.get("top_recoverable", 0)
        impact_line = ""
        if top_rec > 0:
            impact_line = (
                f'<p style="margin:4px 0 10px;font-size:13px;color:#10b981;font-weight:600">'
                f'Fixing the top issue could recover ~{currency} {top_rec:,.2f}</p>'
            )
        opp_rows = ""
        for opp in risk["opportunities"]:
            opp_rows += f"""
            <div style="padding:10px 0;border-bottom:1px solid #fde68a">
                <strong style="color:#f59e0b">{opp['product_name']}</strong>
                <p style="margin:4px 0 2px;color:#c8d1dc;font-size:13px">{opp['problem']}</p>
                <p style="margin:0;color:#10b981;font-size:13px">&rarr; {opp['action']}</p>
            </div>"""
        risk_html = f"""
        <div style="margin:20px 0;padding:16px 18px;background:rgba(245,158,11,0.08);border:1px solid #fde68a;border-radius:8px;font-size:14px">
            <div style="margin-bottom:4px">
                <span style="font-size:13px;color:#f59e0b;font-weight:600">Revenue at Risk</span>
                <span style="float:right;font-size:18px;font-weight:700;color:#b45309">{currency} {total:,.2f}</span>
            </div>
            <p style="margin:0 0 4px;font-size:12px;color:#f59e0b">{count} product{'s' if count != 1 else ''} need attention</p>
            {impact_line}
            {opp_rows}
        </div>
        """

    # --- Top products ---
    products_html = ""
    if digest.get("top_products"):
        rows = ""
        for i, p in enumerate(digest["top_products"]):
            bg = "background:rgba(255,255,255,0.03);" if i % 2 == 1 else ""
            rows += (
                f'<tr style="{bg}">'
                f'<td style="padding:6px 12px 6px 0">{p["title"]}</td>'
                f'<td style="padding:6px 12px 6px 0;text-align:right;font-weight:600">'
                f'{currency} {p["revenue"]:,.2f}</td>'
                f'<td style="padding:6px 0;text-align:right;color:#64748b">'
                f'{p["units"]} sold</td></tr>'
            )
        products_html = f"""
        <h3 style="margin:24px 0 8px;font-size:14px;color:#64748b">Top Products</h3>
        <table style="width:100%;font-size:14px;border-collapse:collapse">{rows}</table>
        """

    # --- Insight ---
    insight_html = ""
    if digest.get("insight"):
        ins = digest["insight"]
        insight_html = f"""
        <div style="margin:24px 0;padding:14px 16px;background:rgba(245,158,11,0.06);border-left:4px solid #f59e0b;border-radius:4px;font-size:14px;line-height:1.5">
            <strong style="color:#f59e0b">Opportunity:</strong> {ins['message']}
        </div>
        """

    # --- Fallback when both top_products and insight are empty ---
    fallback_html = ""
    if not digest.get("top_products") and not digest.get("insight"):
        fallback_html = """
        <div style="margin:24px 0;padding:16px;background:rgba(167,139,250,0.06);border:1px solid rgba(167,139,250,0.15);border-radius:8px;font-size:14px;color:#94a3b8;line-height:1.5">
            We're building your store's intelligence profile. As more visitor and order data flows in,
            this digest will include product-level performance and actionable recommendations.
        </div>
        """

    # --- Lite-only upgrade teaser ---
    upgrade_html = ""
    if plan != "pro" and risk.get("opportunities"):
        top_opp = risk["opportunities"][0]
        others = risk["affected_products"] - 1
        others_text = f" and {others} other product{'s' if others > 1 else ''}" if others > 0 else ""
        upgrade_html = f"""
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

    # --- CTA button — deep-link to relevant section ---
    if risk.get("opportunities"):
        cta_link = f"{_DASHBOARD_URL}?section=signals"
        cta_label = "View Your Signals"
    else:
        cta_link = f"{_DASHBOARD_URL}?section=revenue"
        cta_label = "View Your Revenue"

    cta_html = f"""
    <div style="text-align:center;margin:28px 0 8px">
        <a href="{cta_link}" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#d4893a 0%,#a855f7 100%);background-color:#c47a3e;color:#ffffff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:600;letter-spacing:0.3px">
            {cta_label}
        </a>
    </div>
    """

    # Build body content, then wrap in shared dark-theme wrapper
    from app.services.email_templates import _wrap_html

    body_inner = f"""
<h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#f1f5f9;letter-spacing:-0.2px">Weekly Revenue Digest</h2>
<p style="font-size:13px;color:#64748b;margin:0 0 24px">{shop} &middot; {period}</p>

<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:16px">
  <table style="width:100%;font-size:14px;border-collapse:collapse">
    <tr>
      <td style="padding:8px 0;color:#64748b">Revenue</td>
      <td style="padding:8px 0;text-align:right;font-size:24px;font-weight:700;color:#f1f5f9">{currency} {tw['revenue']:,.2f}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#64748b">Orders</td>
      <td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9">{tw['order_count']}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#64748b">Avg Order Value</td>
      <td style="padding:8px 0;text-align:right;font-weight:600;color:#f1f5f9">{currency} {tw['aov']:,.2f}</td>
    </tr>
    {visitor_html}
  </table>
  {f'<p style="margin:12px 0 0;font-size:13px;color:#64748b">{delta_html}</p>' if delta_html else ''}
</div>

{rec_html}
{working_html}
{proof_html}
{sip_html}
{risk_html}
{products_html}
{insight_html}
{fallback_html}
{upgrade_html}
{cta_html}
"""

    subject_title = f"Weekly Revenue Digest — {shop}"
    html = _wrap_html(subject_title, body_inner, show_logo=True)

    return html, plain
