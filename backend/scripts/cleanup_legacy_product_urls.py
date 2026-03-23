"""
cleanup_legacy_product_urls.py — Backfill and clean up legacy product URLs.

What this script does
---------------------
Legacy data (pre-normalization) contains product_url values that are full
URLs (https://shop.myshopify.com/products/my-widget?variant=123) rather
than canonical paths (/products/my-widget).  This script:

  1. events.product_url
     UPDATE rows where product_url is a full URL containing /products/
     to the canonical /products/{handle} form.

  2. visitor_product_state.product_url
     DELETE rows where product_url is not a valid /products/{handle} path.
     These rows were created from non-product page URLs and cannot be
     meaningfully joined against normalized product keys.

  3. price_intelligence.product_url
     DELETE rows where product_url is not a valid /products/{handle} path.

  4. unique_product_detection.product_url
     DELETE rows where product_url is not a valid /products/{handle} path.

Safety
------
  - DRY-RUN IS THE DEFAULT.  The script prints counts and sample rows
    but performs NO mutations unless --apply is passed explicitly.
  - All mutations in apply mode run inside a single transaction per table.
    If any statement fails, only that table's transaction is rolled back —
    other tables are unaffected.

Usage
-----
  # Dry run (default) — prints counts, no DB changes
  cd /opt/wishspark/backend
  venv/bin/python3 scripts/cleanup_legacy_product_urls.py

  # Apply — performs the actual UPDATEs and DELETEs
  cd /opt/wishspark/backend
  venv/bin/python3 scripts/cleanup_legacy_product_urls.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.core.database import engine

# ---------------------------------------------------------------------------
# SQL — canonical product path pattern
# ---------------------------------------------------------------------------
#
# A valid canonical product_url matches: /products/<handle>
#   - starts with /products/
#   - followed by at least one non-slash, non-whitespace character
#   - no query string, fragment, or scheme
#
# A "normalizable" events.product_url is a full URL that contains /products/
# but does NOT already start with /products/ (i.e. it has a scheme or host).
# ---------------------------------------------------------------------------

_VALID_PRODUCT_RE  = r"^/products/[^/?#\s]+"          # already canonical
_FULL_URL_PRODUCT  = r"^https?://.*?/products/[^/?#\s]+"  # full URL, extractable


def _banner(label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")


def _count(conn, sql: str, params: dict | None = None) -> int:
    row = conn.execute(text(sql), params or {}).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Phase 1 — events.product_url: UPDATE full URLs → canonical path
# ---------------------------------------------------------------------------

def _events_update_counts(conn) -> tuple[int, int]:
    """Return (total product_url rows, normalizable full-URL rows)."""
    total = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE product_url IS NOT NULL",
    )
    normalizable = _count(
        conn,
        f"SELECT COUNT(*) FROM events WHERE product_url ~ :re",
        {"re": _FULL_URL_PRODUCT},
    )
    return total, normalizable


def _events_show_samples(conn) -> None:
    rows = conn.execute(
        text(f"""
            SELECT id, shop_domain, product_url
            FROM events
            WHERE product_url ~ :re
            LIMIT 5
        """),
        {"re": _FULL_URL_PRODUCT},
    ).fetchall()
    if rows:
        print("  Sample rows (before → after):")
        for r in rows:
            from app.core.url_utils import normalize_product_url
            canon = normalize_product_url(r.product_url)
            print(f"    id={r.id} shop={r.shop_domain}")
            print(f"      before: {r.product_url}")
            print(f"      after:  {canon}")


def _events_apply(conn) -> int:
    """
    Normalize product_url in-place using a PostgreSQL regexp_replace.
    Extracts /products/{handle} from the full URL.
    """
    result = conn.execute(
        text(r"""
            UPDATE events
            SET product_url = regexp_replace(
                product_url,
                '^https?://[^/]+(/products/[^/?#]+).*$',
                '\1'
            )
            WHERE product_url ~ :re
        """),
        {"re": _FULL_URL_PRODUCT},
    )
    conn.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Phase 2–4 — DELETE non-canonical rows from lookup tables
# ---------------------------------------------------------------------------

_LOOKUP_TABLES = [
    "visitor_product_state",
    "price_intelligence",
    "unique_product_detection",
]


def _lookup_counts(conn, table: str) -> tuple[int, int]:
    """Return (total rows, garbage rows that don't match canonical path)."""
    total = _count(conn, f"SELECT COUNT(*) FROM {table}")
    garbage = _count(
        conn,
        f"SELECT COUNT(*) FROM {table} WHERE product_url IS NULL OR product_url !~ :re",
        {"re": _VALID_PRODUCT_RE},
    )
    return total, garbage


def _lookup_show_samples(conn, table: str) -> None:
    rows = conn.execute(
        text(f"""
            SELECT product_url
            FROM {table}
            WHERE product_url IS NULL OR product_url !~ :re
            LIMIT 5
        """),
        {"re": _VALID_PRODUCT_RE},
    ).fetchall()
    if rows:
        print(f"  Sample garbage product_url values in {table}:")
        for r in rows:
            print(f"    {r.product_url!r}")


def _lookup_apply(conn, table: str) -> int:
    result = conn.execute(
        text(f"""
            DELETE FROM {table}
            WHERE product_url IS NULL OR product_url !~ :re
        """),
        {"re": _VALID_PRODUCT_RE},
    )
    conn.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"\n[cleanup_legacy_product_urls] mode={mode}")

    with engine.connect() as conn:

        # ------------------------------------------------------------------ #
        # Phase 1 — events.product_url normalization                          #
        # ------------------------------------------------------------------ #
        _banner("Phase 1 — events.product_url (UPDATE full URLs → /products/{handle})")

        total_events, normalizable = _events_update_counts(conn)
        print(f"  Total events with product_url : {total_events:,}")
        print(f"  Normalizable (full-URL) rows  : {normalizable:,}")

        if normalizable > 0:
            _events_show_samples(conn)

        if apply and normalizable > 0:
            updated = _events_apply(conn)
            print(f"  [APPLIED] Updated {updated:,} rows in events.")
        elif apply:
            print("  [APPLIED] No rows needed updating.")
        else:
            print(f"  [DRY-RUN] Would update {normalizable:,} rows in events.")

        # ------------------------------------------------------------------ #
        # Phases 2–4 — lookup table garbage DELETE                            #
        # ------------------------------------------------------------------ #
        for table in _LOOKUP_TABLES:
            _banner(f"Phase — {table} (DELETE non-canonical product_url rows)")

            try:
                total, garbage = _lookup_counts(conn, table)
            except Exception as exc:
                print(f"  [SKIP] Could not query {table}: {exc}")
                continue

            print(f"  Total rows    : {total:,}")
            print(f"  Garbage rows  : {garbage:,}")

            if garbage > 0:
                _lookup_show_samples(conn, table)

            if apply and garbage > 0:
                try:
                    deleted = _lookup_apply(conn, table)
                    print(f"  [APPLIED] Deleted {deleted:,} rows from {table}.")
                except Exception as exc:
                    conn.rollback()
                    print(f"  [ERROR] Rollback on {table}: {exc}", file=sys.stderr)
            elif apply:
                print(f"  [APPLIED] No garbage rows in {table}.")
            else:
                print(f"  [DRY-RUN] Would delete {garbage:,} rows from {table}.")

    print(f"\n[cleanup_legacy_product_urls] done (mode={mode})\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill and clean legacy product URLs (dry-run by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Perform actual DB mutations. Without this flag the script is read-only.",
    )
    args = parser.parse_args()
    main(apply=args.apply)
