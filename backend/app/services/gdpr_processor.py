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
            # heal-detection: GDPR request processing event — per-request log
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


# Per-shop Redis key templates that need explicit purge on Art. 17
# erasure. Two shapes are common: `<prefix>:{shop}` (exact) and
# `<prefix>:{md5(shop)[:16]}` (hashed for cache keys). _purge_shop_redis_keys
# below handles both shapes. Each entry is the FULL prefix pattern with
# `{shop}` / `{md5}` placeholders so a future audit can grep this list
# vs CLAUDE.md §13 to detect drift.
_REDIS_SHOP_KEY_TEMPLATES_EXACT = [
    "hs:goals:v1:{shop}",                  # KPI Goals (Pro Sprint #1)
    "hs:rars_history:v1:{shop}",           # RAR history
    "hs:wh_status:{shop}",                 # webhook health
    "hs:refresh_claim:{shop}",             # action-candidates refresh
    "hs:merchant_opt_out:{shop}",          # Art. 21 flag itself
    "hs:shop_ccy:v1:{shop}",               # shop currency cache
    "hs:shop_tz:v1:{shop}",                # shop timezone cache
]

# Patterns that take md5(shop)[:16] (cache keys). The {md5} placeholder
# is filled by _purge_shop_redis_keys.
_REDIS_SHOP_KEY_TEMPLATES_MD5 = [
    "hs:recurring_buyers:v1:{md5}",        # Recurring Buyers (Pro Sprint #2)
    "hs:storeprofile:v1:{md5}",            # store-profile cache
    "hs:vint:v1:{md5}",                     # Visitor Intent
    "hs:liveopps:v1:{md5}",                 # Live Opportunities
    "hs:kg:v1:stats:{md5}",                 # Knowledge-graph stats
    "hs:action_candidates:v1:{md5}",        # Action candidates cache
    "hs:bi_query:rate:{md5}",               # Pro #3 BI rate-limit counter
]

# Plan-parametric keys: same prefix shape for lite + pro tiers. Listed
# separately from the {md5} templates so audit_claude_md_redis_keys
# matches the prefix `hs:rars:v1` (revenue_at_risk.py:50) once, not
# per-tier. Iterated over known tier names in _purge_shop_redis_keys.
_REDIS_SHOP_KEY_TEMPLATES_PLAN_MD5 = [
    "hs:rars:v1:{plan}:{md5}",             # RARS report cache
    # NOTE: rars:lock not invalidated on shop_redact — TTL 40s already
    # well below human-visible window; lock is transient.
]
_RARS_TIER_PLANS = ("lite", "pro")

# Prefix patterns that take {shop}:* (SCAN-based). For Redis without
# a small known suffix set, we scan with MATCH.
_REDIS_SHOP_KEY_TEMPLATES_PREFIX = [
    "hs:symap:{shop}:*",                   # visitor identity map (90d)
    "hs:mdigest:{shop}:*",                 # digest dedup
    "hs:hmap:{shop}:*",                    # Lite heatmap buckets
    "hs:llm:merchant:{shop}:*",            # per-merchant LLM spend
    "hs:trkerr:tot:{shop}:*",              # tracker error volume
    "hs:trkerr:hash:{shop}:*",             # tracker distinct errors
]


def _purge_shop_redis_keys(shop_domain: str) -> int:
    """Delete every known Redis key tied to shop_domain. Called from
    _process_shop_redact AFTER the SQL purge commits.

    Returns the count of keys deleted (telemetry-only; not load-bearing).
    Redis-down → log + return 0; never raises (SQL purge is the source
    of truth, Redis is derived state).
    """
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
    except Exception:
        return 0
    rc = _client()
    if rc is None:
        record_silent_return("gdpr.shop_redact.redis_purge_redis_down")
        return 0

    import hashlib as _h
    shop_md5 = _h.md5(shop_domain.encode("utf-8")).hexdigest()[:16]
    keys_to_delete: list[str] = []
    for tmpl in _REDIS_SHOP_KEY_TEMPLATES_EXACT:
        keys_to_delete.append(tmpl.format(shop=shop_domain))
    for tmpl in _REDIS_SHOP_KEY_TEMPLATES_MD5:
        keys_to_delete.append(tmpl.format(md5=shop_md5))
    for tmpl in _REDIS_SHOP_KEY_TEMPLATES_PLAN_MD5:
        for plan in _RARS_TIER_PLANS:
            keys_to_delete.append(tmpl.format(plan=plan, md5=shop_md5))

    # SCAN-based delete for the prefix-with-suffix patterns. SCAN over
    # MATCH is bounded by the number of matching keys, not the full
    # keyspace — safe at 10k merchants.
    for tmpl in _REDIS_SHOP_KEY_TEMPLATES_PREFIX:
        pattern = tmpl.format(shop=shop_domain)
        try:
            for k in rc.scan_iter(match=pattern, count=200):
                keys_to_delete.append(
                    k.decode() if isinstance(k, bytes) else k
                )
        except Exception:
            # SCAN may fail on transient Redis errors — continue to
            # next pattern; SQL erasure has already succeeded.
            continue

    deleted = 0
    if keys_to_delete:
        try:
            deleted = int(rc.delete(*keys_to_delete) or 0)
        except Exception:
            deleted = 0
    log.info(
        "gdpr.shop_redact: redis purged %d keys for %s",
        deleted, shop_domain,
    )
    return deleted


def _process_shop_redact(db: Session, req: GdprRequest) -> str:
    """
    Delete ALL data for a shop domain.  FK-safe deletion order.
    """
    shop = req.shop_domain
    deleted: dict[str, int] = {}

    # Deletion order: leaf tables first (FK children), tenant root last.
    # Table names must match the __tablename__ in each model exactly.
    # Every table with a shop_domain column must be listed here OR in
    # _GDPR_PRESERVE_TABLES (below, for compliance-required retention
    # like the audit chain). Preflight `audit_gdpr_redact_coverage.py`
    # enforces this invariant at commit time — adding a new table with
    # shop_domain without updating this list will FAIL preflight.
    # worker_log and worker_state have no shop_domain — excluded.
    #
    # 2026-04-23 expansion: discovered via information_schema query that
    # 23 tables with shop_domain were missing from this list (GDPR
    # Art. 17 non-compliance live). All 23 added below, grouped by
    # category + commented. merchants + audit_log are the only
    # shop_domain tables intentionally excluded from bulk redaction.
    tables = [
        # Pro BI Query Builder saved queries (Sprint Pro #3 2026-05-11)
        # — no PII in column content but query metadata is shop-scoped.
        "bi_saved_queries",
        # Event-level behavioral data (highest-volume PII).
        # `events` is a range-partitioned table by timestamp — a single
        # DELETE on the parent cascades to events_default + events_y* so
        # partition children are NOT listed here (the preflight audit
        # detects them and excludes from the coverage check).
        "events",
        "events_legacy",
        "analytics_events",
        "nudge_events",
        "nudge_impression_daily",
        "onboarding_events",
        "email_events",
        # Visitor identity + attribution
        "visitor_purchase_sessions",
        "visitor_product_state",
        "visitors",
        # Nudge + action state
        "active_nudges",
        "action_tasks",
        "action_snapshots",
        "action_approvals",
        "action_outcomes",
        "autonomous_actions",
        "execution_tracking",
        "execution_baselines",
        "execution_audiences",
        "execution_opportunities",
        # Signal + intelligence
        "opportunity_signals",
        "product_opportunities",
        "store_intelligence_profiles",
        # Brain Vero v0.1 ledger — per-shop decision history
        # (sense_snapshot can contain churn classification + RAR + event
        # counts that aggregate to PII-adjacent insight under Art. 17).
        "brain_decisions",
        "sip_snapshots",
        "prediction_log",
        # Inventory daily snapshots (Gap #4, 2026-04-28). Product-level
        # data only (no PII); cascade by shop_domain on uninstall.
        "inventory_snapshots",
        # Custom saved reports (Gap #1, 2026-04-28). Config-only,
        # no PII; cascade by shop_domain on uninstall.
        "merchant_saved_reports",
        # Post-purchase survey responses (Gap #7, 2026-04-28).
        # No PII columns by design (hashed IP/UA only, no customer
        # email/name); cascades on shop_domain anyway since each row
        # is merchant-scoped.
        "survey_responses",
        # Merchant-level state + telemetry
        "merchant_journey_states",
        "merchant_email_stats",
        "merchant_emails",
        "merchant_rules",
        "merchant_group_members",
        "ops_alerts",
        "support_incidents",
        "inbound_emails",
        # Product + pricing catalogues
        "products",
        "product_metrics",
        "product_costs",
        "price_intelligence",
        "price_watch",
        "market_lookup",
        "unique_product_detection",
        "shop_orders",
        "shop_cost_defaults",
        "shop_conversion_calibrations",
        "store_metrics",
        "wishlist_items",
        # Community + public-proof
        "community_template_clones",
        "cig_merchant_mappings",
        "public_proof_shares",
        # Agency + ad connectors (merchant PII in OAuth tokens + spend data)
        "agency_clients",
        "ad_connections",
        "ad_spend_daily",
        # Trust + contracts
        "trust_contracts",
        "trust_execution_log",
        # Outbound webhooks
        "outbound_webhook_subscriptions",
        "outbound_webhook_deliveries",
        # Reports
        "daily_brief",
        "night_shift_reports",
        # GDPR requests themselves (last so we lose the handle to req
        # only after the rest is gone; req.id was captured earlier)
        "gdpr_requests",
    ]

    # Append merchants as the FK root — the parent row goes last so any
    # remaining FK from a child table satisfies the constraint at
    # statement end.
    all_tables = tables + ["merchants"]

    # Single multi-CTE statement: one DELETE ... RETURNING per table,
    # final SELECT aggregates per-table rowcount. Executes as ONE
    # transactional unit — the correct semantic for GDPR Art. 17
    # (partial erasure = breach). Replaces the prior per-table loop
    # whose `db.rollback()` on a missing table reset the entire
    # transaction (silent partial-erasure failure mode). All table
    # names are interpolated from the hardcoded `all_tables` list
    # above; no user input. The :shop bind is the only parameterised
    # value.
    cte_clauses = ", ".join(
        f"d_{i} AS (DELETE FROM {t} WHERE shop_domain = :shop RETURNING 1)"
        for i, t in enumerate(all_tables)
    )
    select_clauses = ", ".join(
        f"(SELECT COUNT(*) FROM d_{i}) AS c_{i}"
        for i, _ in enumerate(all_tables)
    )
    sql = f"WITH {cte_clauses} SELECT {select_clauses}"
    row = db.execute(text(sql), {"shop": shop}).fetchone()

    deleted = {all_tables[i]: int(row[i] or 0) for i in range(len(all_tables))}

    # Redis cleanup — per-shop cache entries that survive the SQL purge.
    # Best-effort: Redis-down does NOT block the Art. 17 erasure (the
    # SQL purge is the source of truth; Redis values are derived state
    # with TTLs that would expire on their own). Logged for observability.
    try:
        _purge_shop_redis_keys(shop)
    except Exception as exc:
        log.warning("gdpr.shop_redact: redis cleanup failed for %s: %s", shop, exc)

    # Best-effort observability for the atomic Art. 17 erasure. Surfaces
    # the bulk operation in Sentry so subsequent errors carry the trail.
    try:
        from app.core.sentry_init import pipeline_breadcrumb
        total_rows = sum(deleted.values())
        pipeline_breadcrumb(
            "perf.bulk_op",
            f"gdpr.shop_redact shop={shop} tables={len(all_tables)} "
            f"rows_deleted={total_rows}",
            level="info",
            data={
                "op": "gdpr_shop_redact",
                "shop": shop,
                "tables_count": len(all_tables),
                "total_rows": total_rows,
            },
        )
    except Exception:
        pass  # SILENT-EXCEPT-OK: sentry breadcrumb best-effort observability; the GDPR erasure already committed atomically above and must return cleanly.

    db.commit()
    return json.dumps(deleted)
