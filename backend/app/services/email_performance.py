"""
email_performance.py — Self-improving email system memory.

Tracks per-merchant, per-email-type performance metrics and uses them
to adaptively control email frequency and template selection.

The system learns from its own behavior:
    - Emails not opened → reduce frequency
    - Emails opened but not clicked → flag CTA quality
    - Complaints → permanent suppression
    - Never opened after 3 sends → stop sending that type

This is deterministic. No LLM. Zero cost.

Public interface:
    record_email_event(db, shop_domain, email_type, event_type) -> None
    should_send_email(db, shop_domain, email_type) -> tuple[bool, str]
    get_email_stats(db, shop_domain, email_type) -> dict | None
    get_store_email_health(db, shop_domain) -> dict
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("email_performance")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record_email_event(
    db: Session,
    shop_domain: str,
    email_type: str,
    event_type: str,
) -> None:
    """
    Record an email performance event (sent, opened, clicked, replied, complained).

    Uses upsert pattern — creates row if not exists, increments counter if exists.
    """
    now = _now()

    # Map event_type to column to increment
    col_map = {
        "sent": "sent_count",
        "opened": "opened_count",
        "clicked": "clicked_count",
        "replied": "replied_count",
        "complained": "complained_count",
    }

    col = col_map.get(event_type)
    if not col:
        return

    # Upsert: INSERT ON CONFLICT UPDATE
    # elite-hardening-allowed: column from whitelist dict (the `col` value is looked up from a hardcoded mapping; the dict IS the guard)
    db.execute(text(f"""
        INSERT INTO merchant_email_stats (shop_domain, email_type, {col}, updated_at)
        VALUES (:shop, :etype, 1, :now)
        ON CONFLICT (shop_domain, email_type)
        DO UPDATE SET
            {col} = merchant_email_stats.{col} + 1,
            updated_at = :now
    """), {"shop": shop_domain, "etype": email_type, "now": now})

    # Update last_sent/last_opened timestamps
    if event_type == "sent":
        db.execute(text("""
            UPDATE merchant_email_stats SET last_sent_at = :now
            WHERE shop_domain = :shop AND email_type = :etype
        """), {"shop": shop_domain, "etype": email_type, "now": now})
    elif event_type == "opened":
        db.execute(text("""
            UPDATE merchant_email_stats SET last_opened_at = :now
            WHERE shop_domain = :shop AND email_type = :etype
        """), {"shop": shop_domain, "etype": email_type, "now": now})


def should_send_email(
    db: Session,
    shop_domain: str,
    email_type: str,
) -> tuple[bool, str]:
    """
    Adaptive send decision based on per-merchant email performance history.

    Growth-aware: aggressive during first 7 days, conservative after.
    Recovery-aware: blocked merchants get one retry after a cooldown.

    Returns (should_send: bool, reason: str).
    """
    row = db.execute(text("""
        SELECT sent_count, opened_count, clicked_count, complained_count,
               last_sent_at
        FROM merchant_email_stats
        WHERE shop_domain = :shop AND email_type = :etype
    """), {"shop": shop_domain, "etype": email_type}).first()

    if not row:
        return True, "no_history"

    sent, opened, clicked, complained, last_sent_at = row

    # Hard block: merchant complained — never send this type again
    if (complained or 0) > 0:
        return False, "complained"

    # Check if merchant is in first-7-day activation window
    is_new = _is_new_merchant(db, shop_domain)
    if is_new:
        # New merchants get aggressive treatment: only block on complaint
        # Allow up to 7 sends in first week (roughly 1/day)
        if (sent or 0) >= 7:
            return False, "new_merchant_weekly_cap"
        return True, "new_merchant_aggressive"

    # Established merchants: adaptive throttling
    # Soft block: sent 3+ times, never opened — but allow ONE retry after 14 days
    if (sent or 0) >= 3 and (opened or 0) == 0:
        if last_sent_at:
            days_since = (_now() - last_sent_at).days
            if days_since >= 14 and (sent or 0) < 5:
                # Recovery attempt: try one more after 14-day cooldown
                return True, "recovery_attempt"
        return False, "never_opened"

    # Soft block: open rate below 15% after 7+ sends (more forgiving than before)
    if (sent or 0) >= 7:
        open_rate = (opened or 0) / sent
        if open_rate < 0.15:
            return False, f"low_open_rate:{open_rate:.0%}"

    return True, "ok"


def _is_new_merchant(db: Session, shop_domain: str) -> bool:
    """Check if merchant installed within the last 7 days."""
    row = db.execute(text("""
        SELECT installed_at FROM merchants
        WHERE shop_domain = :shop AND install_status = 'active'
    """), {"shop": shop_domain}).first()
    if not row or not row[0]:
        return False
    age_days = (_now() - row[0]).days
    return age_days <= 7


def get_email_stats(db: Session, shop_domain: str, email_type: str | None = None) -> list[dict]:
    """Get email performance stats for a merchant, optionally filtered by type."""
    if email_type:
        rows = db.execute(text("""
            SELECT shop_domain, email_type, sent_count, opened_count, clicked_count,
                   replied_count, complained_count, last_sent_at, last_opened_at, updated_at
            FROM merchant_email_stats
            WHERE shop_domain = :shop AND email_type = :etype
        """), {"shop": shop_domain, "etype": email_type}).fetchall()
    else:
        rows = db.execute(text("""
            SELECT shop_domain, email_type, sent_count, opened_count, clicked_count,
                   replied_count, complained_count, last_sent_at, last_opened_at, updated_at
            FROM merchant_email_stats
            WHERE shop_domain = :shop
            ORDER BY updated_at DESC
        """), {"shop": shop_domain}).fetchall()

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    return [
        {
            "email_type": r[1],
            "sent": r[2] or 0,
            "opened": r[3] or 0,
            "clicked": r[4] or 0,
            "replied": r[5] or 0,
            "complained": r[6] or 0,
            "open_rate": round((r[3] or 0) / r[2], 2) if r[2] else None,
            "click_rate": round((r[4] or 0) / r[2], 2) if r[2] else None,
            "last_sent_at": _ts(r[7]),
            "last_opened_at": _ts(r[8]),
        }
        for r in rows
    ]


def get_store_email_health(db: Session, shop_domain: str) -> dict:
    """Aggregate email health for a merchant across all email types."""
    row = db.execute(text("""
        SELECT
            COALESCE(SUM(sent_count), 0) as total_sent,
            COALESCE(SUM(opened_count), 0) as total_opened,
            COALESCE(SUM(clicked_count), 0) as total_clicked,
            COALESCE(SUM(complained_count), 0) as total_complained,
            COUNT(*) as email_types
        FROM merchant_email_stats
        WHERE shop_domain = :shop
    """), {"shop": shop_domain}).first()

    if not row or row[0] == 0:
        return {"health": "no_data", "total_sent": 0}

    total_sent, total_opened, total_clicked, total_complained, types = row
    open_rate = total_opened / total_sent if total_sent else 0

    if total_complained > 0:
        health = "complained"
    elif open_rate < 0.1 and total_sent >= 5:
        health = "unengaged"
    elif open_rate < 0.3:
        health = "low_engagement"
    else:
        health = "healthy"

    return {
        "health": health,
        "total_sent": total_sent,
        "total_opened": total_opened,
        "total_clicked": total_clicked,
        "open_rate": round(open_rate, 2),
        "total_complained": total_complained,
    }
