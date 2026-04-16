"""
merchant_export.py — GDPR Art. 15 (right of access) + Art. 20 (data portability).

Merchants may call `GET /merchant/export` at any time to obtain a
machine-readable dump of everything HedgeSpark holds about them and
their storefront visitors. No operator in the loop, no API key, no
ticket — session cookie is enough.

Shape — a single JSON object with the merchant's identity at the top
level and per-table arrays of the rows that belong to that shop:

    {
        "exported_at": "2026-04-11T...Z",
        "shop_domain": "foo.myshopify.com",
        "merchant": { ... },
        "events": [ ... ],
        "visitor_purchase_sessions": [ ... ],
        "shop_orders": [ ... ],
        "nudge_events": [ ... ],
        "active_nudges": [ ... ],
        "signals": [ ... ],
        "merchant_emails": [ ... ],
        "ops_alerts": [ ... ],
        "gdpr_requests": [ ... ],
        "record_counts": { ... },
    }

Hard cap: at most `_MAX_ROWS_PER_TABLE` rows per table. A merchant with
more data than that gets `truncated: true` on the relevant entry plus a
pointer to the operator-backed bulk export path. In practice this is
well above normal data volumes.

Masking: the export is delivered to the merchant over an authenticated
session, so it contains the full detail. We do NOT apply log masking
here — that's for *operator-facing logs*, where we have no lawful basis
to show PII. The merchant has the lawful basis (it's their data).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.services.audit import write_audit_log

log = logging.getLogger("merchant_export")

router = APIRouter(prefix="/merchant", tags=["merchant"])

_MAX_ROWS_PER_TABLE = 10_000


def _serialize(row) -> dict[str, Any]:
    """Turn a SQLAlchemy row into a JSON-safe dict."""
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name, None)
        if isinstance(value, datetime):
            out[col.name] = value.isoformat()
        elif isinstance(value, (bytes, bytearray)):
            out[col.name] = "<binary>"
        else:
            out[col.name] = value
    return out


def _dump(query, limit: int = _MAX_ROWS_PER_TABLE) -> tuple[list[dict], bool]:
    rows = query.limit(limit + 1).all()
    truncated = len(rows) > limit
    return [_serialize(r) for r in rows[:limit]], truncated


@router.get("/export")
def export_merchant_data(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> Response:
    """Return every row that references this shop_domain across all
    data tables. GDPR Art. 15 / Art. 20 compliant."""
    from app.models.merchant import Merchant
    from app.models.event import Event
    from app.models.visitor_purchase_session import VisitorPurchaseSession
    from app.models.shop_order import ShopOrder
    from app.models.ops_alert import OpsAlert
    from app.models.merchant_email import MerchantEmail

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    payload: dict[str, Any] = {
        "exported_at": now.isoformat() + "Z",
        "shop_domain": shop,
        "gdpr_article": "Art. 15 (access) + Art. 20 (portability)",
        "record_counts": {},
        "truncated_tables": [],
    }

    # Merchant row itself
    try:
        merchant_row = (
            db.query(Merchant).filter(Merchant.shop_domain == shop).first()
        )
        if merchant_row is not None:
            merchant_dict = _serialize(merchant_row)
            # The access token is encrypted at rest but still sensitive —
            # redact from the export artifact because it grants Shopify
            # admin access if leaked.
            merchant_dict.pop("access_token", None)
            merchant_dict.pop("access_token_plain", None)
            payload["merchant"] = merchant_dict
    except Exception as exc:
        log.warning("merchant_export: merchant row failed: %s", exc)
        payload["merchant"] = None

    table_queries: list[tuple[str, Any]] = [
        ("events", db.query(Event).filter(Event.shop_domain == shop)),
        ("visitor_purchase_sessions",
            db.query(VisitorPurchaseSession).filter(
                VisitorPurchaseSession.shop_domain == shop,
            )),
        ("shop_orders",
            db.query(ShopOrder).filter(ShopOrder.shop_domain == shop)),
        ("ops_alerts",
            db.query(OpsAlert).filter(OpsAlert.shop_domain == shop)),
        ("merchant_emails",
            db.query(MerchantEmail).filter(MerchantEmail.shop_domain == shop)),
    ]

    # Optional tables — only if the model is importable (some are feature-gated)
    try:
        from app.models.active_nudge import ActiveNudge
        table_queries.append((
            "active_nudges",
            db.query(ActiveNudge).filter(ActiveNudge.shop_domain == shop),
        ))
    except Exception as exc:
        log.warning("merchant_export: export_merchant_data failed: %s", exc)
    try:
        from app.models.nudge_event import NudgeEvent
        table_queries.append((
            "nudge_events",
            db.query(NudgeEvent).filter(NudgeEvent.shop_domain == shop),
        ))
    except Exception as exc:
        log.warning("merchant_export: export_merchant_data failed: %s", exc)
    try:
        from app.models.signal import Signal
        table_queries.append((
            "signals",
            db.query(Signal).filter(Signal.shop_domain == shop),
        ))
    except Exception as exc:
        log.warning("merchant_export: export_merchant_data failed: %s", exc)
    try:
        from app.models.gdpr_request import GdprRequest
        table_queries.append((
            "gdpr_requests",
            db.query(GdprRequest).filter(GdprRequest.shop_domain == shop),
        ))
    except Exception as exc:
        log.warning("merchant_export: export_merchant_data failed: %s", exc)

    for table_name, query in table_queries:
        try:
            rows, truncated = _dump(query)
            payload[table_name] = rows
            payload["record_counts"][table_name] = len(rows)
            if truncated:
                payload["truncated_tables"].append(table_name)
        except Exception as exc:
            log.warning("merchant_export: %s failed: %s", table_name, exc)
            payload[table_name] = []
            payload["record_counts"][table_name] = 0

    if payload["truncated_tables"]:
        payload["note"] = (
            f"Some tables exceeded the {_MAX_ROWS_PER_TABLE}-row cap and were "
            f"truncated: {payload['truncated_tables']}. Contact support for "
            f"a full operator-backed export."
        )

    # Audit trail — this is a personal-data disclosure event and must be
    # traceable. `target_id` is the shop_domain; we never log PII in the
    # audit entry itself.
    try:
        write_audit_log(
            db,
            actor_type="merchant",
            actor_name=f"merchant:{shop}",
            action_type="gdpr_self_export",
            target_type="shop",
            target_id=shop,
            shop_domain=shop,
            status="completed",
            approval_mode=None,
        )
        db.commit()
    except Exception as exc:
        log.warning("merchant_export: audit log write failed: %s", exc)
        try:
            db.rollback()
        except Exception as exc:
            log.warning("merchant_export: export_merchant_data failed: %s", exc)

    headers = {
        "Content-Disposition": (
            f'attachment; filename="hedgespark-export-{shop}-{now.strftime("%Y%m%d")}.json"'
        ),
    }
    return JSONResponse(content=payload, headers=headers)
