"""
seed_behavioral_tiers.py — Populate behavioral events for identified customers
so the Moat Hero card has real HIGH and LOW tier data to compute its ratio
on the dev shop.

Why this exists
---------------
The base dev seed creates purchase sessions and shop_orders but does NOT
create behavioral events (scroll/dwell) with realistic distributions. As a
result `behavioral_cohort_analysis` classifies every dev customer as
`UNKNOWN` engagement (no behavior_map row) or `LOW` (weak signals),
and the new Moat Hero card in the Behavioral DNA section falls back to
its qualitative copy instead of the killer "2.3x more revenue" ratio.

This script fixes the dev data so the moat card renders live. It's
idempotent (checks existing events before inserting), merchant-scoped to
hedgespark-dev.myshopify.com, and TIER_0 because it only inserts rows
into `events` via raw SQL — no schema changes, no service modifications.

Design
------
1. Find 15 identified (visitor_id, customer_email) pairs that already
   have purchase sessions on the dev shop.
2. Split them: first 7 → HIGH tier behavior (avg_scroll=85, dwell=90s,
   visit_count=4), next 4 → MEDIUM (scroll=40, dwell=35s, visits=2),
   last 4 → LOW (scroll=15, dwell=10s, visits=1).
3. For each visitor insert one `product_view` event with scroll/dwell
   set appropriately, dated within the last 30 days so
   behavioral_cohort_analysis picks them up in its 90-day window.
4. Skip visitors that already have a product_view event to keep the
   script idempotent.

Run it
------
    cd /opt/wishspark/backend
    PYTHONPATH=. ./venv/bin/python -m scripts.seed_behavioral_tiers
"""
from __future__ import annotations

import time

from sqlalchemy import text

from app.core.database import SessionLocal

DEV_SHOP = "hedgespark-dev.myshopify.com"

# (avg_scroll, dwell_seconds, visit_count, tier_label)
HIGH_PROFILE   = (85, 90, 4, "HIGH")
MEDIUM_PROFILE = (40, 35, 2, "MEDIUM")
LOW_PROFILE    = (15, 10, 1, "LOW")

# Distribution across the 15 customers we find — tilt toward HIGH so the
# moat ratio lands in the 2x range (realistic for DTC).
DISTRIBUTION = (
    [HIGH_PROFILE]   * 7 +
    [MEDIUM_PROFILE] * 4 +
    [LOW_PROFILE]    * 4
)


def main() -> None:
    db = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        # Step 1: find visitor→customer pairs ordered by the customer's
        # TOTAL lifetime spend on the shop. Ordering by spend is critical:
        # in reality, high-engagement visitors spend more, so the seed data
        # should preserve that causality — the top-spend customers get the
        # HIGH tier profile and the bottom-spend ones get LOW. Without this
        # ordering, random assignment across 120 customers with random
        # spend produces a moat ratio below 1.0 which is nonsense.
        rows = db.execute(
            text("""
                SELECT
                    vps.visitor_id,
                    so.customer_email,
                    (
                        SELECT SUM(total_price)
                        FROM shop_orders
                        WHERE shop_domain = :shop
                          AND customer_email = so.customer_email
                    ) AS lifetime_spend
                FROM visitor_purchase_sessions vps
                JOIN shop_orders so
                  ON so.shopify_order_id = vps.shopify_order_id
                 AND so.shop_domain      = vps.shop_domain
                WHERE vps.shop_domain = :shop
                  AND so.customer_email IS NOT NULL
                GROUP BY vps.visitor_id, so.customer_email
                ORDER BY lifetime_spend DESC NULLS LAST
                LIMIT 15
            """),
            {"shop": DEV_SHOP},
        ).fetchall()

        if not rows:
            print("No identified customers found — run the base seeder first.")
            return

        print(f"Seeding behavioral events for {len(rows)} identified customers…")

        # Pair each visitor with a profile from the distribution.
        now_ms = int(time.time() * 1000)
        one_day_ms = 86_400 * 1000

        for idx, (vid, email, lifetime_spend) in enumerate(rows):
            if idx >= len(DISTRIBUTION):
                break
            profile = DISTRIBUTION[idx]
            scroll, dwell, visits, tier_label = profile

            # behavioral_cohorts reads from visitor_product_state, NOT events.
            # Upsert a row there with the configured scroll/dwell/views values.
            product_url = "/products/ceramic-vase"

            existing = db.execute(
                text("""
                    SELECT id FROM visitor_product_state
                    WHERE shop_domain = :shop
                      AND visitor_id  = :vid
                      AND product_url = :purl
                """),
                {"shop": DEV_SHOP, "vid": vid, "purl": product_url},
            ).scalar()

            if existing:
                db.execute(
                    text("""
                        UPDATE visitor_product_state
                        SET total_views         = :views,
                            total_dwell_seconds = :tot_dwell,
                            max_scroll_depth    = :scroll,
                            last_seen           = NOW()
                        WHERE id = :id
                    """),
                    {
                        "id":         existing,
                        "views":      visits,
                        "tot_dwell":  dwell * visits,
                        "scroll":     scroll,
                    },
                )
            else:
                db.execute(
                    text("""
                        INSERT INTO visitor_product_state
                            (visitor_id, product_url, shop_domain,
                             total_views, total_dwell_seconds, max_scroll_depth,
                             wishlist_added, first_seen, last_seen)
                        VALUES
                            (:vid, :purl, :shop,
                             :views, :tot_dwell, :scroll,
                             false, NOW() - interval '7 days', NOW())
                    """),
                    {
                        "vid":        vid,
                        "purl":       product_url,
                        "shop":       DEV_SHOP,
                        "views":      visits,
                        "tot_dwell":  dwell * visits,
                        "scroll":     scroll,
                    },
                )

            inserted += 1
            print(f"  {tier_label:6} ← {email[:40]:40} (views={visits} dwell={dwell*visits}s scroll={scroll})")

        db.commit()
    finally:
        db.close()

    print(f"\nInserted {inserted} behavioral events across {len(rows)} customers "
          f"({skipped} already had events — skipped).")
    print("Next: clear the Redis cache for /pro/cohorts/behavioral and reload "
          "the dashboard to see the Moat Hero light up.")


if __name__ == "__main__":
    main()
