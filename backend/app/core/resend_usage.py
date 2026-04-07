"""
resend_usage.py — Resend free-tier usage tracking.

Counts emails sent this month from the merchant_emails table (status='sent').
Compares against the configurable monthly limit (default: 100 for free tier).

Public interface:
    get_resend_usage(db) -> dict
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.merchant_email import MerchantEmail

# Resend free tier: 100 emails/month (as of 2025).
# Override via env if plan changes.
RESEND_MONTHLY_LIMIT = int(os.getenv("RESEND_MONTHLY_LIMIT", "100"))


def get_resend_usage(db: Session) -> dict:
    """
    Return Resend usage for the current calendar month.

    Returns:
        {
            "sent": int,
            "limit": int,
            "pct": float,          # 0.0–100.0
            "status": "ok" | "warning" | "critical",
        }
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    sent = (
        db.query(func.count(MerchantEmail.id))
        .filter(
            MerchantEmail.status == "sent",
            MerchantEmail.created_at >= month_start,
        )
        .scalar()
    ) or 0

    pct = (sent / RESEND_MONTHLY_LIMIT * 100) if RESEND_MONTHLY_LIMIT > 0 else 0.0

    if pct >= 90:
        status = "critical"
    elif pct >= 70:
        status = "warning"
    else:
        status = "ok"

    return {
        "sent": sent,
        "limit": RESEND_MONTHLY_LIMIT,
        "pct": round(pct, 1),
        "status": status,
    }
