"""
TIER_2 — modification requires explicit human approval (CLAUDE.md §10).

gdpr_processor.py — processes GDPR deletion/redaction requests.

Three request types:

  customers_redact:
    Delete visitor behavioral data tied to a customer.  Shopify provides
    customer_id and optionally email.  We match via:
      1. shop_orders.customer_id → shop_orders.customer_email → nullify email
      2. visitor_purchase_sessions linked to those orders → get visitor_ids
      3. events for those visitor_ids → DELETE
      4. nudge_events for those visitor_ids → DELETE
      5. visitors for those visitor_ids → DELETE
    Financial order records (total_price, line_items) are RETAINED per GDPR
    Art. 17(3)(b) — legal obligation / legitimate interest for financial records.

  customers_data_request:
    v1: Mark as completed with a note.  Full data export pipeline is a future
    enhancement — Shopify gives 30 days to respond.

  shop_redact:
    Delete ALL data for a shop domain.  Arrives 48h after uninstall.
    Deletion order (FK-safe):
      1. events
      2. nudge_events
      3. visitor_purchase_sessions
      4. active_nudges
      5. opportunity_signals
      6. product_metrics
      7. shop_orders (financial data — deleted per Shopify's explicit request)
      8. visitors
      9. merchants (final step — removes the tenant record)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.gdpr_request import GdprRequest
from app.services.audit import write_audit_log
from app.services.alerting import write_alert

log = logging.getLogger("gdpr_processor")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def process_gdpr_request(db: Session, req: GdprRequest) -> None:
    """
    Dispatch a GDPR request to the appropriate handler.
    Updates status to 'completed' or 'failed' with details.
    """
    # Capture fields before any handler runs — shop_redact deletes the
    # gdpr_requests row itself, making req attributes inaccessible after.
    req_id = req.id
    req_type = req.request_type
    req_shop = req.shop_domain
    req_customer_id = req.customer_id

    req.status = "processing"
    db.commit()

    try:
        if req_type == "customers_redact":
            summary = _process_customers_redact(db, req)
        elif req_type == "customers_data_request":
            summary = _process_customers_data_request(db, req)
        elif req_type == "shop_redact":
            summary = _process_shop_redact(db, req)
        else:
            summary = f"Unknown request_type: {req_type}"

        # For shop_redact the req object is deleted — update only if it still exists
        if req_type != "shop_redact":
            req.status = "completed"
            req.processed_at = _now()
            req.result_summary = summary

        write_audit_log(
            db,
            actor_type="worker",
            actor_name="gdpr_worker",
            action_type=f"gdpr_{req_type}",
            target_type="merchant" if req_type == "shop_redact" else "customer",
            target_id=req_customer_id or req_shop,
            shop_domain=req_shop,
            after_state=summary,
            status="completed",
            approval_mode="autonomous",
            metadata={"gdpr_request_id": req_id},
        )

        db.commit()
        log.info(
            "gdpr_processor: completed request_id=%d type=%s shop=%s — %s",
            req_id, req_type, req_shop, summary,
        )
    except Exception as exc:
        db.rollback()
        try:
            req.status = "failed"
            req.processed_at = _now()
            req.error_detail = str(exc)[:2000]
            write_alert(
                db, severity="critical", source="gdpr_processor",
                alert_type="gdpr_failure", shop_domain=req_shop,
                summary=f"GDPR {req_type} failed for request_id={req_id}",
                detail={"error": str(exc)[:500], "request_id": req_id},
            )
            db.commit()
        except Exception:
            db.rollback()
        log.error(
            "gdpr_processor: FAILED request_id=%d type=%s shop=%s — %s",
            req_id, req_type, req_shop, exc,
        )


def _process_customers_redact(db: Session, req: GdprRequest) -> str:
    """
    Redact a specific customer's personal data.

    Strategy:
      1. Find visitor_ids linked to this customer via shop_orders → visitor_purchase_sessions
      2. Delete events, nudge_events, visitors for those visitor_ids
      3. Nullify customer_email in shop_orders
      4. Retain order financial records (GDPR Art. 17(3)(b))
    """
    shop = req.shop_domain
    customer_id = req.customer_id
    email = req.customer_email
    deleted = {"events": 0, "nudge_events": 0, "visitors": 0, "emails_redacted": 0}

    # Step 1: Find visitor_ids through the attribution chain
    # shop_orders → visitor_purchase_sessions → visitor_id
    visitor_ids: set[str] = set()

    if customer_id:
        rows = db.execute(text(
            "SELECT DISTINCT vps.visitor_id FROM visitor_purchase_sessions vps "
            "JOIN shop_orders so ON vps.shopify_order_id = so.shopify_order_id "
            "WHERE so.shop_domain = :shop AND so.customer_id = :cid AND vps.visitor_id IS NOT NULL"
        ), {"shop": shop, "cid": str(customer_id)}).fetchall()
        visitor_ids.update(r[0] for r in rows)

    if email:
        rows = db.execute(text(
            "SELECT DISTINCT vps.visitor_id FROM visitor_purchase_sessions vps "
            "JOIN shop_orders so ON vps.shopify_order_id = so.shopify_order_id "
            "WHERE so.shop_domain = :shop AND so.customer_email = :email AND vps.visitor_id IS NOT NULL"
        ), {"shop": shop, "email": email}).fetchall()
        visitor_ids.update(r[0] for r in rows)

    # Step 2: Delete behavioral data for identified visitors
    if visitor_ids:
        vid_list = list(visitor_ids)
        deleted["visitor_product_state"] = 0
        deleted["execution_audiences"] = 0
        deleted["execution_tracking"] = 0
        # Process in batches to avoid oversized IN clauses
        for i in range(0, len(vid_list), 100):
            batch = vid_list[i:i + 100]
            r = db.execute(text(
                "DELETE FROM events WHERE shop_domain = :shop AND visitor_id = ANY(:vids)"
            ), {"shop": shop, "vids": batch})
            deleted["events"] += r.rowcount

            r = db.execute(text(
                "DELETE FROM nudge_events WHERE shop_domain = :shop AND visitor_id = ANY(:vids)"
            ), {"shop": shop, "vids": batch})
            deleted["nudge_events"] += r.rowcount

            r = db.execute(text(
                "DELETE FROM visitor_product_state WHERE shop_domain = :shop AND visitor_id = ANY(:vids)"
            ), {"shop": shop, "vids": batch})
            deleted["visitor_product_state"] += r.rowcount

            r = db.execute(text(
                "DELETE FROM execution_audiences WHERE shop_domain = :shop AND visitor_id = ANY(:vids)"
            ), {"shop": shop, "vids": batch})
            deleted["execution_audiences"] += r.rowcount

            r = db.execute(text(
                "DELETE FROM execution_tracking WHERE shop_domain = :shop AND visitor_id = ANY(:vids)"
            ), {"shop": shop, "vids": batch})
            deleted["execution_tracking"] += r.rowcount

            r = db.execute(text(
                "DELETE FROM visitors WHERE shop_domain = :shop AND visitor_id = ANY(:vids)"
            ), {"shop": shop, "vids": batch})
            deleted["visitors"] += r.rowcount

    # Step 3: Nullify PII in orders (retain financial records)
    if customer_id:
        r = db.execute(text(
            "UPDATE shop_orders SET customer_email = NULL "
            "WHERE shop_domain = :shop AND customer_id = :cid AND customer_email IS NOT NULL"
        ), {"shop": shop, "cid": str(customer_id)})
        deleted["emails_redacted"] += r.rowcount

    if email:
        r = db.execute(text(
            "UPDATE shop_orders SET customer_email = NULL "
            "WHERE shop_domain = :shop AND customer_email = :email"
        ), {"shop": shop, "email": email})
        deleted["emails_redacted"] += r.rowcount

    db.commit()
    return json.dumps(deleted)


def _process_customers_data_request(db: Session, req: GdprRequest) -> str:
    """
    Customer data export request.

    Collects all data WishSpark holds for a customer, identified via:
      1. customer_id → shop_orders → visitor_purchase_sessions → visitor_ids
      2. customer_email → shop_orders (same chain)

    Produces a structured JSON export persisted in result_summary.
    Shopify allows 30 days to respond.

    Export includes:
      - Order records (financial, not PII — email already separate)
      - Behavioral events (product views, dwell, scroll)
      - Visitor product state (intent scores)
      - Nudge events (impressions, dismissals)

    Secure delivery to the customer is NOT implemented — the export
    artifact is stored in the gdpr_requests.result_summary column for
    operator retrieval. External delivery is a future enhancement.
    """
    shop = req.shop_domain
    customer_id = req.customer_id
    email = req.customer_email
    export: dict = {
        "request_id": req.id,
        "shop_domain": shop,
        "customer_id": customer_id,
        "customer_email": email,
        "data": {},
    }

    # Step 1: Find visitor_ids via attribution chain
    visitor_ids: set[str] = set()
    if customer_id:
        rows = db.execute(text(
            "SELECT DISTINCT vps.visitor_id FROM visitor_purchase_sessions vps "
            "JOIN shop_orders so ON vps.shopify_order_id = so.shopify_order_id "
            "WHERE so.shop_domain = :shop AND so.customer_id = :cid AND vps.visitor_id IS NOT NULL"
        ), {"shop": shop, "cid": str(customer_id)}).fetchall()
        visitor_ids.update(r[0] for r in rows)
    if email:
        rows = db.execute(text(
            "SELECT DISTINCT vps.visitor_id FROM visitor_purchase_sessions vps "
            "JOIN shop_orders so ON vps.shopify_order_id = so.shopify_order_id "
            "WHERE so.shop_domain = :shop AND so.customer_email = :email AND vps.visitor_id IS NOT NULL"
        ), {"shop": shop, "email": email}).fetchall()
        visitor_ids.update(r[0] for r in rows)

    export["data"]["visitor_ids_found"] = len(visitor_ids)

    # Step 2: Collect order records
    order_filter = []
    params: dict = {"shop": shop}
    if customer_id:
        order_filter.append("customer_id = :cid")
        params["cid"] = str(customer_id)
    if email:
        order_filter.append("customer_email = :email")
        params["email"] = email
    if order_filter:
        where = " OR ".join(order_filter)
        rows = db.execute(text(
            f"SELECT shopify_order_id, total_price, currency, created_at "
            f"FROM shop_orders WHERE shop_domain = :shop AND ({where}) "
            f"ORDER BY created_at DESC LIMIT 200"
        ), params).fetchall()
        export["data"]["orders"] = [
            {"order_id": r[0], "total": float(r[1] or 0), "currency": r[2], "date": str(r[3])}
            for r in rows
        ]

    # Step 3: Collect behavioral events for identified visitors
    if visitor_ids:
        vid_list = list(visitor_ids)[:100]  # cap for safety
        rows = db.execute(text(
            "SELECT visitor_id, event_type, product_url, timestamp, dwell_seconds, max_scroll_depth "
            "FROM events WHERE shop_domain = :shop AND visitor_id = ANY(:vids) "
            "ORDER BY timestamp DESC LIMIT 500"
        ), {"shop": shop, "vids": vid_list}).fetchall()
        export["data"]["events"] = [
            {"visitor_id": r[0], "type": r[1], "product": r[2],
             "timestamp": r[3], "dwell": r[4], "scroll": r[5]}
            for r in rows
        ]

        # Step 4: Visitor product state
        rows = db.execute(text(
            "SELECT visitor_id, product_url, total_views, total_dwell_seconds, "
            "max_scroll_depth, intent_score, intent_level "
            "FROM visitor_product_state WHERE shop_domain = :shop AND visitor_id = ANY(:vids) "
            "LIMIT 200"
        ), {"shop": shop, "vids": vid_list}).fetchall()
        export["data"]["visitor_state"] = [
            {"visitor_id": r[0], "product": r[1], "views": r[2],
             "dwell_total": r[3], "max_scroll": r[4], "intent_score": r[5], "intent_level": r[6]}
            for r in rows
        ]

        # Step 5: Nudge events
        rows = db.execute(text(
            "SELECT visitor_id, nudge_id, event_type, created_at "
            "FROM nudge_events WHERE shop_domain = :shop AND visitor_id = ANY(:vids) "
            "ORDER BY created_at DESC LIMIT 200"
        ), {"shop": shop, "vids": vid_list}).fetchall()
        export["data"]["nudge_events"] = [
            {"visitor_id": r[0], "nudge_id": r[1], "type": r[2], "date": str(r[3])}
            for r in rows
        ]

    summary = json.dumps(export, default=str)
    from app.core.privacy import mask_email
    log.info(
        "gdpr_processor: data export complete request_id=%d shop=%s "
        "visitors=%d orders=%d events=%d recipient=%s",
        req.id, shop,
        len(visitor_ids),
        len(export["data"].get("orders", [])),
        len(export["data"].get("events", [])),
        mask_email(email),
    )

    # Auto-delivery (GDPR Art. 15 — "electronic form", "without undue delay").
    # Previously the artifact sat in `result_summary` waiting for a human
    # operator to manually email it. We now ship it to the customer via
    # the governed email orchestrator, with the export inlined as HTML.
    # Failures are non-fatal: the artifact still persists in
    # `result_summary` so the operator has a manual-delivery fallback.
    if email:
        try:
            delivered = _deliver_customer_export(
                db=db,
                customer_email=email,
                shop_domain=shop,
                export_json=summary,
                request_id=req.id,
            )
            export["delivery"] = {
                "status": "sent" if delivered else "failed",
                "channel": "email_orchestrator",
                "recipient_hash": _hash_email(email),
            }
            summary = json.dumps(export, default=str)
        except Exception as exc:
            log.warning(
                "gdpr_processor: auto-delivery failed request_id=%d: %s",
                req.id, exc,
            )
    else:
        log.info(
            "gdpr_processor: no customer_email on request_id=%d — "
            "artifact remains in result_summary for operator retrieval",
            req.id,
        )

    return summary


def _hash_email(email: str) -> str:
    """Short stable hash for audit logs — proves a specific address was
    served without persisting the plaintext in logs/audit."""
    import hashlib
    return hashlib.sha256((email or "").encode()).hexdigest()[:16]


def _deliver_customer_export(
    *, db, customer_email: str, shop_domain: str,
    export_json: str, request_id: int,
) -> bool:
    """Send the GDPR data export to the data subject via the governed
    email orchestrator. The orchestrator handles suppression lists,
    rate limiting, audit logging, and bounce tracking — the same
    guarantees every other user-facing email gets.

    Delivery is synchronous (`send_immediate`) because the 30-day
    Art. 15 SLA does not tolerate queue delays. The export is inlined
    in the HTML body as a `<pre>` block. Returns True on a send.
    """
    try:
        from app.services.email_orchestrator import EmailIntent, send_immediate
    except Exception as exc:
        log.warning("gdpr_processor: email orchestrator unavailable: %s", exc)
        return False

    subject = f"Your HedgeSpark data export (request #{request_id})"
    body_text = (
        f"Hello,\n\n"
        f"You requested a copy of the personal data that HedgeSpark holds "
        f"for you in connection with {shop_domain}. The full export is "
        f"inlined below in JSON format.\n\n"
        f"If any of the information is incorrect you may request "
        f"rectification (GDPR Art. 16) or erasure (Art. 17) by replying "
        f"to this email or contacting privacy@hedgesparkhq.com.\n\n"
        f"— HedgeSpark Privacy Team\n\n"
        f"---\n{export_json}\n"
    )
    # Cap the HTML inline preview so we never emit multi-MB emails.
    # Shopify's data volumes make 200KB a very generous ceiling.
    inline_html = export_json[:200_000]
    body_html = (
        f"<p>Hello,</p>"
        f"<p>You requested a copy of the personal data that HedgeSpark "
        f"holds for you in connection with <strong>{shop_domain}</strong>. "
        f"The full export is inlined below in JSON format.</p>"
        f"<p>If any of the information is incorrect you may request "
        f"rectification (GDPR Art. 16) or erasure (Art. 17) by replying "
        f"to this email or contacting "
        f"<a href='mailto:privacy@hedgesparkhq.com'>privacy@hedgesparkhq.com</a>.</p>"
        f"<p>— HedgeSpark Privacy Team</p>"
        f"<hr/><pre style='white-space:pre-wrap;font-size:11px'>"
        f"{inline_html}</pre>"
    )

    try:
        result = send_immediate(
            db,
            EmailIntent(
                shop_domain=shop_domain,
                email_type="gdpr_export",
                to_email=customer_email,
                subject=subject,
                html=body_html,
                plain_text=body_text,
                from_address="HedgeSpark Privacy <privacy@hedgesparkhq.com>",
                producer="gdpr_processor",
                context={"request_id": request_id},
            ),
        )
        return bool(result) and result.get("status") == "sent"
    except Exception as exc:
        log.warning("gdpr_processor: orchestrator send failed: %s", exc)
        return False


def _process_shop_redact(db: Session, req: GdprRequest) -> str:
    """
    Delete ALL data for a shop domain.  FK-safe deletion order.
    """
    shop = req.shop_domain
    deleted: dict[str, int] = {}

    # Deletion order: leaf tables first, tenant root last.
    # Table names must match the __tablename__ in each model exactly.
    # Every table with a shop_domain column must be listed here.
    # Every table with a shop_domain column must be listed here.
    # Deletion order: leaf tables first (FK children), tenant root last.
    # worker_log and worker_state have no shop_domain — excluded.
    tables = [
        "events",
        "nudge_events",
        "nudge_impression_daily",
        "visitor_purchase_sessions",
        "active_nudges",
        "action_tasks",
        "action_snapshots",
        "execution_tracking",
        "execution_baselines",
        "execution_audiences",
        "execution_opportunities",
        "opportunity_signals",
        "product_metrics",
        "store_metrics",
        "shop_orders",
        "visitors",
        "visitor_product_state",
        "product_opportunities",
        "price_intelligence",
        "price_watch",
        "market_lookup",
        "unique_product_detection",
        "daily_brief",
        "shop_conversion_calibrations",
        "products",
        "wishlist_items",
        "gdpr_requests",
    ]

    for table in tables:
        try:
            r = db.execute(text(
                f"DELETE FROM {table} WHERE shop_domain = :shop"
            ), {"shop": shop})
            deleted[table] = r.rowcount
        except Exception as exc:
            # Table may not exist (future schema changes) — log and continue
            log.warning("gdpr_processor: skip %s for shop=%s — %s", table, shop, exc)
            db.rollback()
            deleted[table] = -1  # indicates error

    # Final: delete the merchant row itself
    try:
        r = db.execute(text(
            "DELETE FROM merchants WHERE shop_domain = :shop"
        ), {"shop": shop})
        deleted["merchants"] = r.rowcount
    except Exception as exc:
        log.warning("gdpr_processor: skip merchants for shop=%s — %s", shop, exc)
        db.rollback()
        deleted["merchants"] = -1

    db.commit()
    return json.dumps(deleted)
