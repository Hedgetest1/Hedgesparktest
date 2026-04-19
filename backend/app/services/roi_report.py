"""
roi_report.py — Monthly Self-Justification ROI report.

THE killer retention feature. At the end of each month, every Pro merchant
receives:

  "HedgeSpark cost you €99 this month.
   Detected €2,431 of revenue at risk.
   Prevented €840 via auto-fixes and holdout-measured nudges.
   Net ROI: +€741 this month."

The math that makes churn impossible. No competitor ships this because
nobody else has the RARS + holdout infrastructure to quantify prevention.

Delivery
--------
Generated on-demand via /pro/roi-report (JSON) or scheduled monthly via
aggregation_worker → email delivery through app.core.email.send_email.

Self-healing integration: ops_alert on generation failure, idempotency
key so a worker restart does not send duplicate emails in the same month.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("roi_report")

# Pro tier cost for SMB band — imported from the shared doctrine
# module `app.core.tier_pricing` so every net_roi / subscription
# calculation in the codebase tracks the same number. A pricing
# change happens in one place and all consumers update. Audited by
# `audit_tier_cost_literals.py` preflight — any inline literal
# under a cost/roi/subscription variable is blocked at commit.
from app.core.tier_pricing import TIER_SUBSCRIPTION_EUR as _TIER_SUBSCRIPTION_EUR
_PRO_TIER_COST_EUR = _TIER_SUBSCRIPTION_EUR["pro"]

_REPORT_IDEMPOTENCY_TTL_SECONDS = 35 * 24 * 3600  # 35 days
_REPORT_IDEMPOTENCY_PREFIX = "hs:roi_report_sent:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _month_key(now: datetime | None = None) -> str:
    now = now or _now()
    return f"{now.year:04d}-{now.month:02d}"


@dataclass
class ROIReport:
    shop_domain: str
    month: str  # YYYY-MM
    cost_eur: float
    at_risk_detected_eur: float
    prevented_eur: float
    net_roi_eur: float
    components: list[dict]
    headline: str
    email_body_html: str
    email_body_text: str
    generated_at: str
    # Shop's native currency for money rendering (USD/EUR/GBP/…).
    # `_eur`-suffixed fields above are actually in this currency —
    # the suffix is a historical misnomer.
    currency: str = "USD"

    def to_dict(self) -> dict:
        return {
            "shop_domain": self.shop_domain,
            "month": self.month,
            "cost_eur": round(self.cost_eur, 2),
            "at_risk_detected_eur": round(self.at_risk_detected_eur, 2),
            "prevented_eur": round(self.prevented_eur, 2),
            "net_roi_eur": round(self.net_roi_eur, 2),
            "components": self.components,
            "headline": self.headline,
            "currency": self.currency,
            "generated_at": self.generated_at,
        }


def _render_email_html(report: "ROIReport") -> str:
    """Compact HTML body for the monthly email."""
    pct_roi = (report.net_roi_eur / report.cost_eur * 100) if report.cost_eur > 0 else 0
    color = "#16a34a" if report.net_roi_eur > 0 else "#b91c1c"
    top_components = sorted(
        report.components, key=lambda c: c.get("loss_eur", 0), reverse=True
    )[:3]
    component_rows = "".join(
        f"<tr><td style='padding:8px 0;color:#64748b;'>{c.get('source','').replace('_',' ').title()}</td>"
        f"<td style='padding:8px 0;text-align:right;font-family:monospace;'>€{c.get('loss_eur',0):.0f}</td></tr>"
        for c in top_components
    )
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;padding:24px;">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,0.04);">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.14em;color:#64748b;margin-bottom:6px;">
    HedgeSpark · Monthly ROI · {report.month}
  </div>
  <h1 style="font-size:22px;margin:0 0 16px;color:#0f172a;line-height:1.3;">
    {report.headline}
  </h1>
  <div style="background:#f1f5f9;border-radius:10px;padding:20px;margin:20px 0;">
    <table style="width:100%;font-size:14px;">
      <tr><td style="padding:6px 0;color:#64748b;">Subscription cost</td>
          <td style="padding:6px 0;text-align:right;font-family:monospace;">€{report.cost_eur:.0f}</td></tr>
      <tr><td style="padding:6px 0;color:#64748b;">At-risk detected</td>
          <td style="padding:6px 0;text-align:right;font-family:monospace;">€{report.at_risk_detected_eur:.0f}</td></tr>
      <tr><td style="padding:6px 0;color:#64748b;">Prevented by HedgeSpark</td>
          <td style="padding:6px 0;text-align:right;font-family:monospace;color:#16a34a;">€{report.prevented_eur:.0f}</td></tr>
      <tr><td style="padding:10px 0;border-top:1px solid #cbd5e1;font-weight:700;color:#0f172a;">Net ROI</td>
          <td style="padding:10px 0;border-top:1px solid #cbd5e1;text-align:right;font-family:monospace;font-weight:700;color:{color};">
            {'+' if report.net_roi_eur >= 0 else ''}€{report.net_roi_eur:.0f} ({pct_roi:+.0f}%)
          </td></tr>
    </table>
  </div>
  <div style="font-size:13px;color:#64748b;margin-bottom:8px;">Top loss sources this month:</div>
  <table style="width:100%;font-size:13px;border-collapse:collapse;">
    {component_rows}
  </table>
  <div style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px;">
    Generated {report.generated_at[:10]} · HedgeSpark — the revenue leak detector for Shopify
  </div>
</div>
</body>
</html>"""


def _render_email_text(report: "ROIReport") -> str:
    """Plain-text fallback for email clients that strip HTML."""
    pct_roi = (report.net_roi_eur / report.cost_eur * 100) if report.cost_eur > 0 else 0
    lines = [
        f"HedgeSpark Monthly ROI — {report.month}",
        "",
        report.headline,
        "",
        f"  Subscription cost:        €{report.cost_eur:.0f}",
        f"  At-risk detected:         €{report.at_risk_detected_eur:.0f}",
        f"  Prevented by HedgeSpark:  €{report.prevented_eur:.0f}",
        f"  Net ROI:                  €{report.net_roi_eur:+.0f} ({pct_roi:+.0f}%)",
        "",
        "Top loss sources this month:",
    ]
    for c in sorted(report.components, key=lambda c: c.get("loss_eur", 0), reverse=True)[:3]:
        lines.append(f"  - {c.get('source','').replace('_',' ').title()}: €{c.get('loss_eur',0):.0f}")
    lines.append("")
    lines.append("— HedgeSpark")
    return "\n".join(lines)


def generate_roi_report(db: Session, shop_domain: str) -> ROIReport:
    """
    Build (but do not send) the ROI report for the current month.
    Returns an ROIReport dataclass with both JSON and email-body views.
    """
    from app.services.revenue_at_risk import get_revenue_at_risk
    rars = get_revenue_at_risk(db, shop_domain)

    at_risk = float(rars.get("total_at_risk_eur") or 0)
    prevented = float(rars.get("prevented_eur_this_month") or 0)
    components = rars.get("components") or []
    net_roi = prevented - _PRO_TIER_COST_EUR

    if net_roi > 0:
        headline = (
            f"🟢 HedgeSpark paid for itself +€{net_roi:.0f} this month."
        )
    elif prevented > 0:
        headline = (
            f"HedgeSpark detected €{at_risk:.0f} at risk and prevented €{prevented:.0f}."
        )
    else:
        headline = (
            f"HedgeSpark surfaced €{at_risk:.0f} at risk this month — "
            "review the detected sources below."
        )

    try:
        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"

    now = _now()
    report = ROIReport(
        shop_domain=shop_domain,
        month=_month_key(now),
        cost_eur=_PRO_TIER_COST_EUR,
        at_risk_detected_eur=at_risk,
        prevented_eur=prevented,
        net_roi_eur=net_roi,
        components=components,
        headline=headline,
        email_body_html="",
        email_body_text="",
        generated_at=now.isoformat(),
        currency=currency,
    )
    report.email_body_html = _render_email_html(report)
    report.email_body_text = _render_email_text(report)
    return report


def _already_sent_this_month(shop_domain: str, month: str) -> bool:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("roi_report.dedup_read")
            return False
        key = f"{_REPORT_IDEMPOTENCY_PREFIX}:{hashlib.md5((shop_domain + month).encode()).hexdigest()[:16]}"
        return rc.exists(key) > 0
    except Exception as exc:
        log.warning("roi_report: _already_sent_this_month failed: %s", exc)
        return False


def _mark_sent_this_month(shop_domain: str, month: str) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("roi_report.dedup_write")
            return
        key = f"{_REPORT_IDEMPOTENCY_PREFIX}:{hashlib.md5((shop_domain + month).encode()).hexdigest()[:16]}"
        rc.setex(key, _REPORT_IDEMPOTENCY_TTL_SECONDS, "1")
    except Exception as exc:
        log.warning("roi_report: _mark_sent_this_month failed: %s", exc)


def send_roi_report(
    db: Session, shop_domain: str, recipient_email: str,
    force: bool = False,
) -> dict:
    """
    Generate and email the ROI report. Idempotent: if already sent this
    month for this shop, returns without re-sending unless `force=True`.
    """
    month = _month_key()
    if not force and _already_sent_this_month(shop_domain, month):
        return {
            "shop_domain": shop_domain,
            "month": month,
            "skipped": True,
            "reason": "already_sent_this_month",
        }

    try:
        report = generate_roi_report(db, shop_domain)
    except Exception as exc:
        log.warning("roi_report: generation failed shop=%s: %s", shop_domain, exc)
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="roi_report",
                alert_type="roi_report_generate_failed",
                summary=f"ROI report generation failed for {shop_domain}: {type(exc).__name__}",
                shop_domain=shop_domain,
                detail={"error": str(exc)[:500]},
            )
        except Exception as exc:
            log.warning("roi_report: send_roi_report failed: %s", exc)
        return {"shop_domain": shop_domain, "error": "generation_failed"}

    # All email sends MUST go through the email orchestrator (governance,
    # rate limit, spam guard). Architecture enforced by test_email_architecture.
    try:
        from app.services.email_orchestrator import EmailIntent, send_immediate
        intent = EmailIntent(
            shop_domain=shop_domain,
            email_type="monthly_roi_report",
            to_email=recipient_email,
            subject=f"{report.headline}  ·  HedgeSpark ROI {report.month}",
            html=report.email_body_html,
            plain_text=report.email_body_text,
            producer="roi_report",
            context={"month": report.month, "net_roi_eur": report.net_roi_eur},
        )
        result = send_immediate(db, intent)
        if result.get("status") == "sent":
            _mark_sent_this_month(shop_domain, month)
    except Exception as exc:
        log.warning("roi_report: send failed shop=%s: %s", shop_domain, exc)
        return {
            "shop_domain": shop_domain,
            "month": month,
            "sent": False,
            "error": f"email_send_failed: {type(exc).__name__}",
        }

    return {
        "shop_domain": shop_domain,
        "month": month,
        "sent": True,
        "report": report.to_dict(),
    }
