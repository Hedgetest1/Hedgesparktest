"""
seed_dev_data.py — Deterministic dev-merchant data seeder for HedgeSpark.

Populates the test merchant (hedgespark-dev.myshopify.com) with realistic
synthetic data so every dashboard section can be reviewed with populated UI.

Safety guarantees
-----------------
- Hard-coded target shop (DEV_SHOP constant). Refuses any other value.
- All seeded rows are tagged with marker fields so --reset can reliably remove
  them without touching real data:
    active_nudges.trigger_source = 'seed_dev'
    shop_orders.shopify_order_id  LIKE 'seed-order-%'   (new seed orders)
    shop_orders.customer_email    LIKE '%@seed.hedgespark.dev'  (enriched orders)
    visitor_purchase_sessions.visitor_id LIKE 'seed-%'
    events.visitor_id             LIKE 'seed-%'
    nudge_events (deleted by nudge_id cascade via seed_dev nudges)
- Read-only on sensitive tables (merchants, billing, tokens, secrets).
- Idempotent: can be re-run safely after --reset.

What it seeds
-------------
1. active_nudges          3 nudges (one per existing tracked product)
2. nudge_events           60 holdout_assigned + 240 shown per nudge
                          (with clicks, dismissals, realistic distribution)
3. nudge conversions      Chain: for each converting visitor, creates a real
                          shop_order (with line_items) + visitor_purchase_session
                          linking visitor_id → order. Control group CVR ~3%,
                          exposed CVR ~6% → clean +100% lift signal.
4. shop_orders line_items Backfills the 9 pre-existing orders with line_items
                          (rotating through the 3 products) and customer
                          identity (email + id). Enables Gateway Products +
                          Predicted LTV for original customers.
5. visitor_purchase_sessions (audience)  25 additional sessions with source
                          variety (direct/paid_meta/paid_google/organic/email)
                          for AudienceSegments behavioral analysis.
6. events                 150+ recent behavioral events (last 48h) so
                          /pro/segments has fresh visitors to segment.

Usage
-----
    cd /opt/wishspark/backend
    ./venv/bin/python -m scripts.seed_dev_data           # append-safe
    ./venv/bin/python -m scripts.seed_dev_data --reset   # wipe seed + reseed fresh
    ./venv/bin/python -m scripts.seed_dev_data --dry-run # preview, no writes
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal

# ---------------------------------------------------------------------------
# Safety constants
# ---------------------------------------------------------------------------
DEV_SHOP = "hedgespark-dev.myshopify.com"
SEED_MARKER_TRIGGER = "seed_dev"
SEED_MARKER_EMAIL_SUFFIX = "@seed.hedgespark.dev"
SEED_VISITOR_PREFIX = "seed-"
SEED_ORDER_PREFIX = "seed-order-"

# Deterministic RNG so output is stable across runs
rng = random.Random(42)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hours_ago(h: float) -> datetime:
    return now() - timedelta(hours=h)


def random_time_in_last(hours: float) -> datetime:
    return now() - timedelta(hours=rng.uniform(0, hours))


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------
def assert_safe(db: Session) -> None:
    row = db.execute(
        text("SELECT shop_domain, install_status FROM merchants WHERE shop_domain = :s"),
        {"s": DEV_SHOP},
    ).first()
    if row is None:
        print(f"❌ ABORT: merchant {DEV_SHOP!r} not found")
        sys.exit(1)
    if row[0] != DEV_SHOP:
        print(f"❌ ABORT: shop mismatch — expected {DEV_SHOP!r} got {row[0]!r}")
        sys.exit(1)
    print(f"✓ Target confirmed: {row[0]} (install_status={row[1]})")


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------
def reset(db: Session, dry_run: bool = False) -> None:
    print("\n─── RESET ───")

    # Collect seeded nudge ids (for cascade delete of their events)
    seeded_nudge_ids = [
        r[0] for r in db.execute(
            text("SELECT id FROM active_nudges WHERE shop_domain = :s AND trigger_source = :m"),
            {"s": DEV_SHOP, "m": SEED_MARKER_TRIGGER},
        ).fetchall()
    ]
    n_nudge_events = 0
    if seeded_nudge_ids:
        n_nudge_events = db.execute(
            text("SELECT COUNT(*) FROM nudge_events WHERE shop_domain = :s AND nudge_id = ANY(:ids)"),
            {"s": DEV_SHOP, "ids": seeded_nudge_ids},
        ).scalar()

    # Count everything we'll remove
    counts = {
        "active_nudges (marked)": len(seeded_nudge_ids),
        "nudge_events (of marked nudges)": n_nudge_events,
        "shop_orders (seed orders)": db.execute(
            text("SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :s AND shopify_order_id LIKE :p"),
            {"s": DEV_SHOP, "p": SEED_ORDER_PREFIX + "%"},
        ).scalar(),
        "visitor_purchase_sessions (seed)": db.execute(
            text("SELECT COUNT(*) FROM visitor_purchase_sessions WHERE shop_domain = :s AND visitor_id LIKE :p"),
            {"s": DEV_SHOP, "p": SEED_VISITOR_PREFIX + "%"},
        ).scalar(),
        "events (seed)": db.execute(
            text("SELECT COUNT(*) FROM events WHERE shop_domain = :s AND visitor_id LIKE :p"),
            {"s": DEV_SHOP, "p": SEED_VISITOR_PREFIX + "%"},
        ).scalar(),
        "shop_orders with seed email (identity revert)": db.execute(
            text("SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :s AND customer_email LIKE :p"),
            {"s": DEV_SHOP, "p": "%" + SEED_MARKER_EMAIL_SUFFIX},
        ).scalar(),
    }
    for k, v in counts.items():
        print(f"  will remove/revert {k}: {v}")

    if dry_run:
        print("  (dry-run: no deletes)")
        return

    if seeded_nudge_ids:
        db.execute(
            text("DELETE FROM nudge_events WHERE shop_domain = :s AND nudge_id = ANY(:ids)"),
            {"s": DEV_SHOP, "ids": seeded_nudge_ids},
        )
    db.execute(
        text("DELETE FROM active_nudges WHERE shop_domain = :s AND trigger_source = :m"),
        {"s": DEV_SHOP, "m": SEED_MARKER_TRIGGER},
    )
    db.execute(
        text("DELETE FROM visitor_purchase_sessions WHERE shop_domain = :s AND visitor_id LIKE :p"),
        {"s": DEV_SHOP, "p": SEED_VISITOR_PREFIX + "%"},
    )
    db.execute(
        text("DELETE FROM shop_orders WHERE shop_domain = :s AND shopify_order_id LIKE :p"),
        {"s": DEV_SHOP, "p": SEED_ORDER_PREFIX + "%"},
    )
    db.execute(
        text("DELETE FROM events WHERE shop_domain = :s AND visitor_id LIKE :p"),
        {"s": DEV_SHOP, "p": SEED_VISITOR_PREFIX + "%"},
    )
    # Revert pre-existing orders enriched with seed email: clear identity and line_items
    db.execute(
        text("""
            UPDATE shop_orders
            SET customer_email = NULL, customer_id = NULL, line_items = '[]'::jsonb
            WHERE shop_domain = :s AND customer_email LIKE :p
        """),
        {"s": DEV_SHOP, "p": "%" + SEED_MARKER_EMAIL_SUFFIX},
    )
    db.commit()
    print("  ✓ reset complete")


# ---------------------------------------------------------------------------
# Nudge templates
# ---------------------------------------------------------------------------
NUDGE_TEMPLATES = [
    {
        "action_type": "social_proof",
        "copy_variant": "A",
        "copy_config": json.dumps({"title": "14 people viewed this today", "subtitle": "Popular choice", "style": "social_proof"}),
    },
    {
        "action_type": "urgency",
        "copy_variant": "B",
        "copy_config": json.dumps({"title": "Only 3 left in stock", "subtitle": "Selling fast", "style": "urgency"}),
    },
    {
        "action_type": "interest_based",
        "copy_variant": "C",
        "copy_config": json.dumps({"title": "Still thinking it over?", "subtitle": "Save for later", "style": "interest_based"}),
    },
]

# Pretty product names for line_items (for clean Gateway Products display)
PRODUCT_NAMES = {
    "/products/midnight-candle": "Midnight Candle",
    "/products/silk-pillowcase": "Silk Pillowcase",
    "/products/ceramic-mug":     "Ceramic Mug",
}


# ---------------------------------------------------------------------------
# 1. active_nudges
# ---------------------------------------------------------------------------
def seed_active_nudges(db: Session, dry_run: bool = False) -> list[dict]:
    print("\n─── SEED active_nudges ───")
    product_urls = [
        r[0] for r in db.execute(
            text("SELECT product_url FROM product_metrics WHERE shop_domain = :s ORDER BY id"),
            {"s": DEV_SHOP},
        ).fetchall()
    ]
    # Fallback if no product_metrics yet
    if not product_urls:
        product_urls = list(PRODUCT_NAMES.keys())
    nudges: list[dict] = []
    t = now()
    for i, product_url in enumerate(product_urls[:3]):
        tmpl = NUDGE_TEMPLATES[i]
        if dry_run:
            print(f"  [dry] would insert nudge on {product_url}: {tmpl['action_type']}")
            nudges.append({"id": -(i + 1), "product_url": product_url, "action_type": tmpl["action_type"], "created_at": t - timedelta(days=14)})
            continue
        result = db.execute(
            text("""
                INSERT INTO active_nudges (
                    shop_domain, product_url, action_type, trigger_source,
                    copy_variant, copy_config, status,
                    created_at, updated_at, expires_at,
                    visitor_count, estimated_revenue_window, calibration_state,
                    holdout_pct, is_bootstrap
                ) VALUES (
                    :shop, :product_url, :action_type, :trigger_source,
                    :copy_variant, :copy_config, 'active',
                    :created, :updated, :expires,
                    300, 0.0, 'empirical',
                    20, false
                )
                RETURNING id, created_at
            """),
            {
                "shop": DEV_SHOP,
                "product_url": product_url,
                "action_type": tmpl["action_type"],
                "trigger_source": SEED_MARKER_TRIGGER,
                "copy_variant": tmpl["copy_variant"],
                "copy_config": tmpl["copy_config"],
                "created": t - timedelta(days=14),
                "updated": t,
                "expires": t + timedelta(days=30),
            },
        )
        row = result.fetchone()
        nid = row[0]
        created_at = row[1]
        nudges.append({
            "id": nid,
            "product_url": product_url,
            "action_type": tmpl["action_type"],
            "created_at": created_at,
        })
        print(f"  ✓ nudge #{nid} → {product_url} ({tmpl['action_type']})")
    return nudges


# ---------------------------------------------------------------------------
# 2. Nudge activity — nudge_events + conversions via shop_orders + sessions
# ---------------------------------------------------------------------------
def seed_nudge_activity(db: Session, nudges: list[dict], dry_run: bool = False) -> None:
    print("\n─── SEED nudge activity (events + conversions) ───")
    total_nev = 0
    total_orders = 0
    total_sessions = 0

    # Conversion rates — tuned so holdout < exposed by a large margin (visible lift)
    HOLDOUT_VISITORS = 60
    EXPOSED_VISITORS = 240
    HOLDOUT_CVR = 0.035   # ~2 purchasers of 60
    EXPOSED_CVR = 0.07    # ~17 purchasers of 240 → lift ≈ 100%

    for nudge in nudges:
        nid = nudge["id"]
        product_url = nudge["product_url"]
        product_name = PRODUCT_NAMES.get(product_url, product_url.split("/")[-1].replace("-", " ").title())
        action_type = nudge["action_type"]
        nudge_created = nudge["created_at"]

        # Generate deterministic visitor pools per nudge
        holdout_visitors = [f"{SEED_VISITOR_PREFIX}h{nid}-{i:04d}-{uuid.uuid4().hex[:6]}" for i in range(HOLDOUT_VISITORS)]
        exposed_visitors = [f"{SEED_VISITOR_PREFIX}e{nid}-{i:04d}-{uuid.uuid4().hex[:6]}" for i in range(EXPOSED_VISITORS)]

        # Precompute first-event times per visitor (distributed over last 14 days,
        # but only after nudge_created to be within attribution windows)
        def first_event_time() -> datetime:
            max_ago_hours = min(14 * 24, (now() - nudge_created).total_seconds() / 3600)
            return now() - timedelta(hours=rng.uniform(0.5, max(1.0, max_ago_hours - 0.5)))

        events_batch: list[dict] = []

        # Holdout events
        holdout_first_ts: dict[str, datetime] = {}
        for vid in holdout_visitors:
            t_first = first_event_time()
            holdout_first_ts[vid] = t_first
            events_batch.append({
                "shop": DEV_SHOP, "nid": nid, "vid": vid, "product_url": product_url,
                "event_type": "holdout_assigned", "ts": t_first,
                "meta": json.dumps({"seed": True}),
            })

        # Exposed events
        exposed_first_ts: dict[str, datetime] = {}
        for vid in exposed_visitors:
            t_first = first_event_time()
            exposed_first_ts[vid] = t_first
            events_batch.append({
                "shop": DEV_SHOP, "nid": nid, "vid": vid, "product_url": product_url,
                "event_type": "shown", "ts": t_first,
                "meta": json.dumps({"seed": True}),
            })
            roll = rng.random()
            if roll < 0.60:
                events_batch.append({
                    "shop": DEV_SHOP, "nid": nid, "vid": vid, "product_url": product_url,
                    "event_type": "clicked",
                    "ts": t_first + timedelta(seconds=rng.randint(3, 90)),
                    "meta": json.dumps({"seed": True}),
                })
            elif roll < 0.70:
                events_batch.append({
                    "shop": DEV_SHOP, "nid": nid, "vid": vid, "product_url": product_url,
                    "event_type": "dismissed",
                    "ts": t_first + timedelta(seconds=rng.randint(2, 30)),
                    "meta": json.dumps({"seed": True}),
                })

        # Insert all nudge events
        if not dry_run:
            for ev in events_batch:
                db.execute(
                    text("""
                        INSERT INTO nudge_events (
                            shop_domain, nudge_id, visitor_id, product_url,
                            event_type, created_at, event_meta
                        ) VALUES (
                            :shop, :nid, :vid, :product_url, :event_type, :ts, :meta
                        )
                    """),
                    ev,
                )
        total_nev += len(events_batch)

        # Pick converters: random subset from each group
        n_holdout_conv = max(1, int(HOLDOUT_VISITORS * HOLDOUT_CVR))
        n_exposed_conv = max(1, int(EXPOSED_VISITORS * EXPOSED_CVR))
        holdout_converters = rng.sample(holdout_visitors, n_holdout_conv)
        exposed_converters = rng.sample(exposed_visitors, n_exposed_conv)

        # Create shop_orders + visitor_purchase_sessions for each converter
        def create_conversion(visitor_id: str, first_ts: datetime, group: str) -> None:
            nonlocal total_orders, total_sessions
            # Purchase happens 10 min to 48h after nudge event (well within 168h window)
            confirmed_at = first_ts + timedelta(minutes=rng.randint(10, 48 * 60))
            # Unique shopify_order_id seeded
            seed_order_id = f"{SEED_ORDER_PREFIX}n{nid}-{group}-{uuid.uuid4().hex[:10]}"
            customer_email = f"buyer-n{nid}-{group}-{uuid.uuid4().hex[:6]}{SEED_MARKER_EMAIL_SUFFIX}"
            customer_id = f"seed-cust-n{nid}-{group}-{uuid.uuid4().hex[:6]}"
            total_price = round(rng.uniform(30, 250), 2)
            line_items = [
                {
                    "title": product_name,
                    "product_url": product_url,
                    "handle": product_url.split("/")[-1],
                    "quantity": 1,
                    "price": str(total_price),
                }
            ]

            if dry_run:
                return

            # Insert shop_order
            db.execute(
                text("""
                    INSERT INTO shop_orders (
                        shop_domain, shopify_order_id, total_price, currency,
                        customer_id, customer_email, line_items, created_at,
                        ingested_at, source
                    ) VALUES (
                        :shop, :oid, :price, 'USD',
                        :cid, :email, :line_items, :created, :ingested, 'seed_dev'
                    )
                """),
                {
                    "shop": DEV_SHOP, "oid": seed_order_id, "price": total_price,
                    "cid": customer_id, "email": customer_email,
                    "line_items": json.dumps(line_items),
                    "created": confirmed_at, "ingested": confirmed_at,
                },
            )
            total_orders += 1

            # Insert visitor_purchase_session linking visitor_id → this order
            attribution_evidence = json.dumps({
                "first_event_ts": int(first_ts.timestamp() * 1000),
                "last_event_ts": int(confirmed_at.timestamp() * 1000),
                "total_events": rng.randint(3, 15),
                "distinct_sources": ["direct"],
                "seed": True,
                "nudge_id": nid,
                "group": group,
            })
            db.execute(
                text("""
                    INSERT INTO visitor_purchase_sessions (
                        shop_domain, visitor_id, shopify_order_id, product_url,
                        confirmed_at, ingested_at,
                        first_source, last_source, attribution_evidence
                    ) VALUES (
                        :shop, :vid, :oid, :product_url,
                        :confirmed, :confirmed,
                        'direct', 'direct', :evidence
                    )
                """),
                {
                    "shop": DEV_SHOP, "vid": visitor_id, "oid": seed_order_id,
                    "product_url": product_url, "confirmed": confirmed_at,
                    "evidence": attribution_evidence,
                },
            )
            total_sessions += 1

        for vid in holdout_converters:
            create_conversion(vid, holdout_first_ts[vid], "hld")
        for vid in exposed_converters:
            create_conversion(vid, exposed_first_ts[vid], "exp")

        print(f"  ✓ nudge #{nid}: {len(events_batch)} events, {n_holdout_conv + n_exposed_conv} conversions ({n_exposed_conv} exp, {n_holdout_conv} hld)")

    print(f"  totals: nudge_events={total_nev}  conversion_orders={total_orders}  sessions={total_sessions}")


# ---------------------------------------------------------------------------
# 3. Enrich the pre-existing 9 shop_orders with identity + line_items
# ---------------------------------------------------------------------------
SEED_CUSTOMERS = [
    ("sarah.chen"     + SEED_MARKER_EMAIL_SUFFIX, "seed-cust-001"),
    ("marco.rossi"    + SEED_MARKER_EMAIL_SUFFIX, "seed-cust-002"),
    ("elena.garcia"   + SEED_MARKER_EMAIL_SUFFIX, "seed-cust-003"),
    ("james.oniel"    + SEED_MARKER_EMAIL_SUFFIX, "seed-cust-004"),
    ("priya.patel"    + SEED_MARKER_EMAIL_SUFFIX, "seed-cust-005"),
    ("tomas.koch"     + SEED_MARKER_EMAIL_SUFFIX, "seed-cust-006"),
]


def seed_veteran_repeat_buyers(db: Session, dry_run: bool = False) -> None:
    """
    Create a pool of ~35 'veteran' repeat customers who each have 2 orders:
      1st order: ceramic-mug   (strengthens ceramic-mug as GATEWAY)
      2nd order: silk-pillowcase  (flips silk-pillowcase to LOYALTY)

    Without this seed, every customer is a one-time buyer so EVERY product
    shows as 'Gateway' in the UI. Adding these 35 veterans pushes
    silk-pillowcase's gateway_rate below 50% so it renders as 'Loyalty'
    (emerald), creating the Gateway/Loyalty visual mix on the dashboard.
    """
    print("\n─── SEED veteran repeat buyers ───")
    count = 35
    first_product  = "/products/ceramic-mug"
    second_product = "/products/silk-pillowcase"
    first_name  = PRODUCT_NAMES[first_product]
    second_name = PRODUCT_NAMES[second_product]

    for i in range(count):
        customer_email = f"veteran-{i:03d}{SEED_MARKER_EMAIL_SUFFIX}"
        customer_id = f"seed-cust-vet-{i:03d}"
        # First order: 60-180 days ago
        first_at = now() - timedelta(days=rng.randint(60, 180))
        # Second order: 7-45 days ago (must be AFTER first)
        second_at = now() - timedelta(days=rng.randint(7, 45))
        if second_at <= first_at:
            second_at = first_at + timedelta(days=30)

        first_price  = round(rng.uniform(18, 45), 2)
        second_price = round(rng.uniform(60, 180), 2)

        first_line_items = [{
            "title": first_name, "product_url": first_product,
            "handle": first_product.split("/")[-1],
            "quantity": 1, "price": str(first_price),
        }]
        second_line_items = [{
            "title": second_name, "product_url": second_product,
            "handle": second_product.split("/")[-1],
            "quantity": 1, "price": str(second_price),
        }]

        if dry_run:
            continue

        # First order
        db.execute(
            text("""
                INSERT INTO shop_orders (
                    shop_domain, shopify_order_id, total_price, currency,
                    customer_id, customer_email, line_items, created_at,
                    ingested_at, source
                ) VALUES (
                    :shop, :oid, :price, 'USD',
                    :cid, :email, :line_items, :created, :ingested, 'seed_dev'
                )
            """),
            {
                "shop": DEV_SHOP,
                "oid": f"{SEED_ORDER_PREFIX}vet-{i:03d}-1",
                "price": first_price,
                "cid": customer_id, "email": customer_email,
                "line_items": json.dumps(first_line_items),
                "created": first_at, "ingested": first_at,
            },
        )
        # Second order (loyalty signal)
        db.execute(
            text("""
                INSERT INTO shop_orders (
                    shop_domain, shopify_order_id, total_price, currency,
                    customer_id, customer_email, line_items, created_at,
                    ingested_at, source
                ) VALUES (
                    :shop, :oid, :price, 'USD',
                    :cid, :email, :line_items, :created, :ingested, 'seed_dev'
                )
            """),
            {
                "shop": DEV_SHOP,
                "oid": f"{SEED_ORDER_PREFIX}vet-{i:03d}-2",
                "price": second_price,
                "cid": customer_id, "email": customer_email,
                "line_items": json.dumps(second_line_items),
                "created": second_at, "ingested": second_at,
            },
        )

    print(f"  ✓ inserted {count} veteran customers × 2 orders each = {count * 2} shop_orders")
    print(f"     first-order product: {first_name} (→ GATEWAY)")
    print(f"     second-order product: {second_name} (→ LOYALTY)")


def seed_existing_orders_enrichment(db: Session, dry_run: bool = False) -> None:
    print("\n─── ENRICH pre-existing shop_orders ───")
    product_urls = list(PRODUCT_NAMES.keys())
    orders = db.execute(
        text("""
            SELECT id, total_price FROM shop_orders
            WHERE shop_domain = :s
              AND shopify_order_id NOT LIKE :seedprefix
              AND (customer_email IS NULL OR customer_email NOT LIKE :seedemail)
            ORDER BY created_at ASC
        """),
        {"s": DEV_SHOP, "seedprefix": SEED_ORDER_PREFIX + "%", "seedemail": "%" + SEED_MARKER_EMAIL_SUFFIX},
    ).fetchall()
    if not orders:
        print("  ⚠  no pre-existing anonymous orders to enrich")
        return

    # Distribute: first 3 customers get 2 orders each (repeat), last 3 get 1 each
    assignments = [
        SEED_CUSTOMERS[0], SEED_CUSTOMERS[0],
        SEED_CUSTOMERS[1], SEED_CUSTOMERS[1],
        SEED_CUSTOMERS[2], SEED_CUSTOMERS[2],
        SEED_CUSTOMERS[3],
        SEED_CUSTOMERS[4],
        SEED_CUSTOMERS[5],
    ]
    for idx, (order_row, (email, cust_id)) in enumerate(zip(orders, assignments)):
        order_id = order_row[0]
        total_price = float(order_row[1] or 0)
        # Rotate product for variety
        product_url = product_urls[idx % len(product_urls)]
        product_name = PRODUCT_NAMES[product_url]
        line_items = [
            {
                "title": product_name,
                "product_url": product_url,
                "handle": product_url.split("/")[-1],
                "quantity": 1,
                "price": str(total_price),
            }
        ]
        if dry_run:
            print(f"  [dry] would enrich order {order_id} → {email} with product {product_name}")
            continue
        db.execute(
            text("""
                UPDATE shop_orders
                SET customer_email = :email,
                    customer_id    = :cid,
                    line_items     = :line_items
                WHERE id = :oid
            """),
            {
                "email": email, "cid": cust_id,
                "line_items": json.dumps(line_items),
                "oid": order_id,
            },
        )
    print(f"  ✓ enriched {min(len(orders), len(assignments))} orders (identity + line_items)")


# ---------------------------------------------------------------------------
# 4. Audience sessions (generic source variety)
# ---------------------------------------------------------------------------
SOURCES = [
    ("direct", None),
    ("paid_meta", "meta_spring_retarget"),
    ("paid_google", "google_brand_kw"),
    ("organic_social", "instagram_post"),
    ("email", "weekly_newsletter"),
    ("referral", None),
]


def seed_audience_sessions(db: Session, dry_run: bool = False) -> None:
    print("\n─── SEED visitor_purchase_sessions (audience variety) ───")
    product_urls = list(PRODUCT_NAMES.keys())
    count = 25
    for i in range(count):
        vid = SEED_VISITOR_PREFIX + f"aud-{i:03d}-" + uuid.uuid4().hex[:6]
        order_id = f"{SEED_ORDER_PREFIX}aud-{9000 + i}"
        product = rng.choice(product_urls)
        source, campaign = rng.choice(SOURCES)
        confirmed = now() - timedelta(days=rng.randint(1, 60))
        if dry_run:
            continue
        # Create a backing shop_order so lift/cohort engines see the full chain
        total_price = round(rng.uniform(25, 180), 2)
        line_items = [
            {
                "title": PRODUCT_NAMES[product],
                "product_url": product,
                "handle": product.split("/")[-1],
                "quantity": 1,
                "price": str(total_price),
            }
        ]
        db.execute(
            text("""
                INSERT INTO shop_orders (
                    shop_domain, shopify_order_id, total_price, currency,
                    customer_id, customer_email, line_items, created_at,
                    ingested_at, source
                ) VALUES (
                    :shop, :oid, :price, 'USD',
                    :cid, :email, :line_items, :created, :ingested, 'seed_dev'
                )
            """),
            {
                "shop": DEV_SHOP, "oid": order_id, "price": total_price,
                "cid": f"seed-cust-aud-{i:03d}",
                "email": f"audience-{i:03d}{SEED_MARKER_EMAIL_SUFFIX}",
                "line_items": json.dumps(line_items),
                "created": confirmed, "ingested": confirmed,
            },
        )
        evidence = json.dumps({
            "first_event_ts": int((confirmed - timedelta(hours=rng.randint(1, 72))).timestamp() * 1000),
            "last_event_ts": int(confirmed.timestamp() * 1000),
            "total_events": rng.randint(3, 40),
            "distinct_sources": [source],
            "seed": True,
            "category": "audience",
        })
        db.execute(
            text("""
                INSERT INTO visitor_purchase_sessions (
                    shop_domain, visitor_id, shopify_order_id, product_url,
                    confirmed_at, ingested_at,
                    first_source, first_campaign, last_source, last_campaign,
                    attribution_evidence
                ) VALUES (
                    :shop, :vid, :oid, :product,
                    :confirmed, :confirmed,
                    :source, :campaign, :source, :campaign,
                    :evidence
                )
            """),
            {
                "shop": DEV_SHOP, "vid": vid, "oid": order_id,
                "product": product, "confirmed": confirmed,
                "source": source, "campaign": campaign, "evidence": evidence,
            },
        )
    print(f"  ✓ inserted {count} audience sessions + backing orders")


# ---------------------------------------------------------------------------
# 5. Recent behavioral events for /pro/segments
# ---------------------------------------------------------------------------
def seed_recent_events(db: Session, dry_run: bool = False) -> None:
    print("\n─── SEED events (recent behavioral) ───")
    product_urls = list(PRODUCT_NAMES.keys())
    visitors_per_product = 50
    total = 0
    for product_url in product_urls[:3]:
        for i in range(visitors_per_product):
            vid = f"{SEED_VISITOR_PREFIX}ev-{uuid.uuid4().hex[:12]}"
            roll = rng.random()
            if roll < 0.20:
                scroll, dwell, visit_count = rng.randint(75, 100), rng.randint(60, 180), rng.randint(2, 5)
            elif roll < 0.60:
                scroll, dwell, visit_count = rng.randint(40, 80), rng.randint(20, 70), rng.randint(1, 3)
            else:
                scroll, dwell, visit_count = rng.randint(5, 40), rng.randint(2, 20), 1

            source_type = rng.choice(["direct", "paid", "organic", "social", "email"])
            device = rng.choice(["mobile", "desktop", "tablet"])

            for _ in range(visit_count):
                ts_dt = hours_ago(rng.uniform(0, 48))
                ts_ms = int(ts_dt.timestamp() * 1000)
                if dry_run:
                    total += 1
                    continue
                db.execute(
                    text("""
                        INSERT INTO events (
                            visitor_id, event_type, url, product_url, timestamp,
                            dwell_seconds, max_scroll_depth, shop_domain,
                            source_type, device_type
                        ) VALUES (
                            :vid, 'product_view', :url, :product_url, :ts,
                            :dwell, :scroll, :shop, :source, :device
                        )
                    """),
                    {
                        "vid": vid, "url": f"https://{DEV_SHOP}{product_url}",
                        "product_url": product_url, "ts": ts_ms,
                        "dwell": dwell, "scroll": scroll, "shop": DEV_SHOP,
                        "source": source_type, "device": device,
                    },
                )
                total += 1
    print(f"  ✓ inserted {total} recent behavioral events")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(db: Session) -> None:
    print("\n─── POST-SEED SUMMARY ───")
    queries = [
        ("active_nudges", "SELECT COUNT(*) FROM active_nudges WHERE shop_domain = :s"),
        ("nudge_events", "SELECT COUNT(*) FROM nudge_events WHERE shop_domain = :s"),
        ("events (total)", "SELECT COUNT(*) FROM events WHERE shop_domain = :s"),
        ("events (seed)", "SELECT COUNT(*) FROM events WHERE shop_domain = :s AND visitor_id LIKE :p"),
        ("shop_orders (total)", "SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :s"),
        ("shop_orders (seed orders)", "SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :s AND shopify_order_id LIKE :p2"),
        ("shop_orders (with email)", "SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :s AND customer_email IS NOT NULL"),
        ("shop_orders (with line_items)", "SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :s AND jsonb_array_length(line_items) > 0"),
        ("visitor_purchase_sessions (total)", "SELECT COUNT(*) FROM visitor_purchase_sessions WHERE shop_domain = :s"),
        ("visitor_purchase_sessions (seed)", "SELECT COUNT(*) FROM visitor_purchase_sessions WHERE shop_domain = :s AND visitor_id LIKE :p"),
    ]
    params = {"s": DEV_SHOP, "p": SEED_VISITOR_PREFIX + "%", "p2": SEED_ORDER_PREFIX + "%"}
    for name, q in queries:
        n = db.execute(text(q), params).scalar()
        print(f"  {name:<40} {n}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Seed dev merchant with review data.")
    parser.add_argument("--reset", action="store_true", help="wipe seed rows before seeding")
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    args = parser.parse_args()

    print(f"HedgeSpark dev data seeder — target: {DEV_SHOP}")
    if args.dry_run:
        print("(DRY RUN — no writes will occur)")

    db = SessionLocal()
    try:
        assert_safe(db)

        if args.reset:
            reset(db, dry_run=args.dry_run)

        nudges = seed_active_nudges(db, dry_run=args.dry_run)
        if nudges:
            seed_nudge_activity(db, nudges, dry_run=args.dry_run)
        seed_existing_orders_enrichment(db, dry_run=args.dry_run)
        seed_veteran_repeat_buyers(db, dry_run=args.dry_run)
        seed_audience_sessions(db, dry_run=args.dry_run)
        seed_recent_events(db, dry_run=args.dry_run)

        if not args.dry_run:
            db.commit()
        print_summary(db)
        print("\n✓ seed done")
    except Exception as exc:
        db.rollback()
        print(f"\n❌ error, rolled back: {exc}")
        raise
    finally:
        db.close()

    # Auto-run smoke test to confirm every Pro dashboard section is populated.
    # Fail the seed script if any section would render empty UI in the dashboard.
    if not args.dry_run:
        print("\n─── POST-SEED HEALTH CHECK ───")
        try:
            from scripts.verify_dev_health import run_all, print_table
            db2 = SessionLocal()
            try:
                results = run_all(db2)
            finally:
                db2.close()
            print_table(results, verbose=False)
            if any(r.status != "pass" for r in results):
                print("\n⚠  Health check failed — some sections would render empty.")
                sys.exit(2)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"⚠  Could not run health check: {exc}")


if __name__ == "__main__":
    main()
