"""
simulation_engine.py — Synthetic merchant simulation for operational hardening.

Generates realistic merchant activity that exercises the full product pipeline
(ingestion → aggregation → signals → bugfix → learning) WITHOUT contaminating
product learning, business learning, or long-term autonomous reasoning.

All synthetic merchants are:
  - Permanently labeled (Merchant.is_synthetic = True)
  - Named with recognizable prefix ("sim-")
  - Classified as 'sandbox' evidence source
  - Excluded from reinforcement weights, confidence scoring, Opus context

Design principles:
  - Deterministic: same seed → same event sequence
  - Realistic: event patterns model actual Shopify store behavior
  - Safe: impossible to confuse with real merchants
  - Simple: no framework, no magic — just structured DB writes

Public interface:
    create_synthetic_merchants(db, count=5) -> list[str]
    run_simulation_cycle(db, scenario="mixed") -> SimulationSummary
    cleanup_synthetic_merchants(db) -> dict
    get_simulation_status(db) -> dict
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger("simulation_engine")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Synthetic shop domain convention — unmistakable, never a real Shopify domain
_SHOP_SUFFIX = ".synthetic.hedgespark.test"

# Maximum synthetic merchants in one environment
_MAX_SYNTHETIC_MERCHANTS = 100

# Merchant archetypes — each defines a realistic store behavior profile
ARCHETYPES: dict[str, dict] = {
    "healthy": {
        "description": "Normal store — steady traffic, healthy conversion, no issues",
        "hourly_visitors": (8, 25),     # (min, max) unique visitors per hour
        "page_views_per_visit": (2, 6),
        "product_view_pct": 0.65,       # % of visits that view a product
        "cart_pct": 0.12,               # % of product viewers who add to cart
        "purchase_pct": 0.04,           # % of product viewers who purchase
        "avg_order_value": (25.0, 85.0),
        "dwell_seconds": (15, 90),
        "scroll_depth": (30, 85),
        "mobile_pct": 0.62,
        "failure_rate": 0.0,            # no tracking failures
        "products": 8,
    },
    "high_traffic_low_conversion": {
        "description": "Lots of visitors, almost nobody buys — needs nudges",
        "hourly_visitors": (30, 80),
        "page_views_per_visit": (3, 8),
        "product_view_pct": 0.70,
        "cart_pct": 0.03,
        "purchase_pct": 0.005,
        "avg_order_value": (15.0, 45.0),
        "dwell_seconds": (5, 30),
        "scroll_depth": (15, 50),
        "mobile_pct": 0.75,
        "failure_rate": 0.0,
        "products": 15,
    },
    "broken_tracking": {
        "description": "Tracker partially broken — events arrive but with gaps",
        "hourly_visitors": (10, 30),
        "page_views_per_visit": (1, 3),
        "product_view_pct": 0.40,
        "cart_pct": 0.08,
        "purchase_pct": 0.02,
        "avg_order_value": (30.0, 70.0),
        "dwell_seconds": (0, 5),        # broken dwell tracking
        "scroll_depth": (0, 0),          # broken scroll tracking
        "mobile_pct": 0.55,
        "failure_rate": 0.35,            # 35% of events fail/drop
        "products": 6,
    },
    "low_volume": {
        "description": "Small store — barely any traffic, occasional sales",
        "hourly_visitors": (0, 3),
        "page_views_per_visit": (1, 4),
        "product_view_pct": 0.50,
        "cart_pct": 0.15,
        "purchase_pct": 0.08,
        "avg_order_value": (40.0, 120.0),
        "dwell_seconds": (20, 120),
        "scroll_depth": (40, 95),
        "mobile_pct": 0.50,
        "failure_rate": 0.0,
        "products": 4,
    },
    "noisy": {
        "description": "Bot/spam traffic mixed with real — tests filtering",
        "hourly_visitors": (50, 200),
        "page_views_per_visit": (1, 2),
        "product_view_pct": 0.20,
        "cart_pct": 0.01,
        "purchase_pct": 0.001,
        "avg_order_value": (10.0, 30.0),
        "dwell_seconds": (0, 3),
        "scroll_depth": (0, 10),
        "mobile_pct": 0.85,
        "failure_rate": 0.10,
        "products": 20,
    },
    "delayed_orders": {
        "description": "Orders arrive late — tests attribution window handling",
        "hourly_visitors": (12, 35),
        "page_views_per_visit": (3, 7),
        "product_view_pct": 0.60,
        "cart_pct": 0.10,
        "purchase_pct": 0.035,
        "avg_order_value": (50.0, 150.0),
        "dwell_seconds": (25, 100),
        "scroll_depth": (35, 80),
        "mobile_pct": 0.58,
        "failure_rate": 0.0,
        "products": 10,
    },
}

# Product catalog templates — realistic product handles
_PRODUCT_HANDLES = [
    "classic-leather-wallet", "organic-cotton-tee", "wireless-earbuds-pro",
    "handmade-ceramic-mug", "bamboo-cutting-board", "scented-soy-candle",
    "minimalist-watch", "yoga-mat-premium", "stainless-water-bottle",
    "natural-lip-balm", "eco-tote-bag", "artisan-coffee-blend",
    "silk-pillowcase", "plant-based-protein", "travel-backpack",
    "essential-oil-set", "wooden-sunglasses", "recycled-notebook",
    "compression-socks", "himalayan-salt-lamp",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SimulationSummary:
    merchants_active: int = 0
    events_generated: int = 0
    events_failed: int = 0
    purchases_generated: int = 0
    alerts_generated: int = 0
    scenarios_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "merchants_active": self.merchants_active,
            "events_generated": self.events_generated,
            "events_failed": self.events_failed,
            "purchases_generated": self.purchases_generated,
            "alerts_generated": self.alerts_generated,
            "scenarios_run": self.scenarios_run,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _epoch_ms(dt: datetime | None = None) -> int:
    dt = dt or _now()
    return int(dt.timestamp() * 1000)


def _synthetic_shop_domain(name: str) -> str:
    """Generate a synthetic shop domain from a name."""
    clean = name.lower().replace(" ", "-").replace("_", "-")
    return f"sim-{clean}{_SHOP_SUFFIX}"


def _deterministic_seed(shop_domain: str, cycle_id: str) -> int:
    """Generate a deterministic seed from shop + cycle."""
    h = hashlib.sha256(f"{shop_domain}:{cycle_id}".encode()).hexdigest()
    return int(h[:8], 16)


# ---------------------------------------------------------------------------
# Merchant lifecycle
# ---------------------------------------------------------------------------

def create_synthetic_merchants(
    db: Session,
    count: int = 5,
    archetypes: list[str] | None = None,
) -> list[str]:
    """
    Create synthetic merchants in the database.

    Each merchant gets:
      - is_synthetic = True (permanent, irreversible)
      - Recognizable shop_domain (sim-*.synthetic.hedgespark.test)
      - Assigned archetype stored in onboarding_error field (reused for metadata)
      - install_status = "active" so workers pick them up
      - No access_token (prevents OAuth confusion)
      - No contact_email (prevents email delivery)

    Returns list of created shop_domains.
    """
    if archetypes is None:
        archetypes = list(ARCHETYPES.keys())

    existing = (
        db.query(Merchant.shop_domain)
        .filter(Merchant.is_synthetic == True)  # noqa: E712
        .count()
    )
    if existing + count > _MAX_SYNTHETIC_MERCHANTS:
        raise ValueError(
            f"Would exceed max synthetic merchants ({existing} + {count} > {_MAX_SYNTHETIC_MERCHANTS})"
        )

    created = []
    for i in range(count):
        archetype = archetypes[i % len(archetypes)]
        shop_domain = _synthetic_shop_domain(f"{archetype}-{existing + i + 1:03d}")

        # Skip if already exists
        if db.query(Merchant.id).filter(Merchant.shop_domain == shop_domain).first():
            created.append(shop_domain)
            continue

        merchant = Merchant(
            shop_domain=shop_domain,
            access_token=None,          # No token — prevents OAuth confusion
            plan="starter",
            install_status="active",
            billing_active=False,
            is_synthetic=True,
            contact_email=None,         # No email — prevents delivery
            onboarding_status="ready",
            onboarding_error=json.dumps({"archetype": archetype}),  # Store archetype
        )
        db.add(merchant)
        created.append(shop_domain)
        log.info("simulation: created synthetic merchant %s (archetype=%s)", shop_domain, archetype)

    db.flush()
    return created


def get_synthetic_merchants(db: Session) -> list[dict]:
    """Return all synthetic merchants with their archetypes."""
    merchants = (
        db.query(Merchant)
        .filter(Merchant.is_synthetic == True)  # noqa: E712
        .all()
    )
    result = []
    for m in merchants:
        archetype = "unknown"
        if m.onboarding_error:
            try:
                meta = json.loads(m.onboarding_error)
                archetype = meta.get("archetype", "unknown")
            except (json.JSONDecodeError, ValueError):
                pass
        result.append({
            "shop_domain": m.shop_domain,
            "archetype": archetype,
            "installed_at": str(m.installed_at),
            "is_synthetic": True,
        })
    return result


def cleanup_synthetic_merchants(db: Session) -> dict:
    """
    Remove all synthetic merchants and their data.

    Deletes: merchants, events, product_metrics, opportunity_signals,
    ops_alerts scoped to synthetic shops.

    Returns summary of deleted rows.
    """
    synthetic_shops = [
        r.shop_domain for r in
        db.query(Merchant.shop_domain)
        .filter(Merchant.is_synthetic == True)  # noqa: E712
        .all()
    ]
    if not synthetic_shops:
        return {"deleted_merchants": 0}

    summary = {"shops": synthetic_shops}

    # Delete in dependency order
    for table in ["opportunity_signals", "product_metrics", "daily_brief", "ops_alerts"]:
        try:
            result = db.execute(
                text(f"DELETE FROM {table} WHERE shop_domain = ANY(:shops)"),
                {"shops": synthetic_shops},
            )
            summary[f"deleted_{table}"] = result.rowcount
        except Exception:
            summary[f"deleted_{table}"] = 0

    # Events
    try:
        result = db.execute(
            text("DELETE FROM events WHERE shop_domain = ANY(:shops)"),
            {"shops": synthetic_shops},
        )
        summary["deleted_events"] = result.rowcount
    except Exception:
        summary["deleted_events"] = 0

    # Merchants last
    try:
        result = db.execute(
            text("DELETE FROM merchants WHERE is_synthetic = true"),
        )
        summary["deleted_merchants"] = result.rowcount
    except Exception:
        summary["deleted_merchants"] = 0

    db.flush()
    log.info("simulation: cleanup complete — %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Event generation
# ---------------------------------------------------------------------------

def _generate_events_for_merchant(
    db: Session,
    shop_domain: str,
    archetype: dict,
    hours: int = 1,
    rng: random.Random | None = None,
) -> tuple[int, int, int]:
    """
    Generate realistic events for a single synthetic merchant.

    Returns (events_written, events_dropped, purchases_written).
    """
    rng = rng or random.Random()
    products = _PRODUCT_HANDLES[:archetype["products"]]
    now = _now()
    events_written = 0
    events_dropped = 0
    purchases_written = 0

    for hour_offset in range(hours):
        hour_start = now - timedelta(hours=hours - hour_offset)
        visitor_count = rng.randint(*archetype["hourly_visitors"])

        for _ in range(visitor_count):
            visitor_id = f"sim-v-{rng.randint(100000, 999999)}"
            device = "mobile" if rng.random() < archetype["mobile_pct"] else "desktop"
            pages = rng.randint(*archetype["page_views_per_visit"])
            viewed_product = None

            for page_idx in range(pages):
                # Simulate failure rate
                if rng.random() < archetype["failure_rate"]:
                    events_dropped += 1
                    continue

                ts = hour_start + timedelta(
                    minutes=rng.randint(0, 59),
                    seconds=rng.randint(0, 59),
                )

                is_product_page = (
                    page_idx > 0
                    and rng.random() < archetype["product_view_pct"]
                )

                if is_product_page:
                    product = rng.choice(products)
                    viewed_product = product
                    event_type = "product_view"
                    product_url = f"/products/{product}"
                    page_url = f"https://{shop_domain}{product_url}"
                else:
                    event_type = "page_view"
                    product_url = None
                    page_url = f"https://{shop_domain}/"

                dwell = rng.randint(*archetype["dwell_seconds"])
                scroll = rng.randint(*archetype["scroll_depth"])

                _insert_event(
                    db, shop_domain=shop_domain, visitor_id=visitor_id,
                    event_type=event_type, page_url=page_url,
                    product_url=product_url, timestamp=_epoch_ms(ts),
                    dwell_seconds=dwell, scroll_depth=scroll,
                    device_type=device,
                )
                events_written += 1

            # Add to cart?
            if viewed_product and rng.random() < archetype["cart_pct"]:
                ts = hour_start + timedelta(minutes=rng.randint(0, 59))
                _insert_event(
                    db, shop_domain=shop_domain, visitor_id=visitor_id,
                    event_type="add_to_cart",
                    page_url=f"https://{shop_domain}/products/{viewed_product}",
                    product_url=f"/products/{viewed_product}",
                    timestamp=_epoch_ms(ts), device_type=device,
                )
                events_written += 1

                # Purchase?
                if rng.random() < archetype["purchase_pct"] / max(archetype["cart_pct"], 0.01):
                    order_total = round(rng.uniform(*archetype["avg_order_value"]), 2)
                    ts = hour_start + timedelta(minutes=rng.randint(0, 59))
                    _insert_event(
                        db, shop_domain=shop_domain, visitor_id=visitor_id,
                        event_type="purchase",
                        page_url=f"https://{shop_domain}/thank-you",
                        product_url=f"/products/{viewed_product}",
                        timestamp=_epoch_ms(ts), device_type=device,
                    )
                    events_written += 1
                    purchases_written += 1

    db.flush()
    return events_written, events_dropped, purchases_written


def _insert_event(
    db: Session, *, shop_domain: str, visitor_id: str,
    event_type: str, page_url: str, product_url: str | None,
    timestamp: int, dwell_seconds: int = 0, scroll_depth: int = 0,
    device_type: str = "desktop",
) -> None:
    """Insert a single event row. Uses raw SQL for speed."""
    db.execute(text("""
        INSERT INTO events
            (shop_domain, visitor_id, event_type, url, product_url,
             timestamp, dwell_seconds, max_scroll_depth, device_type)
        VALUES
            (:shop, :vid, :etype, :url, :purl, :ts, :dwell, :scroll, :device)
    """), {
        "shop": shop_domain,
        "vid": visitor_id,
        "etype": event_type,
        "url": page_url,
        "purl": product_url,
        "ts": timestamp,
        "dwell": dwell_seconds,
        "scroll": scroll_depth,
        "device": device_type,
    })


# ---------------------------------------------------------------------------
# Alert injection (for bugfix pipeline exercise)
# ---------------------------------------------------------------------------

def _inject_synthetic_alerts(
    db: Session,
    shop_domain: str,
    archetype: str,
    rng: random.Random,
) -> int:
    """
    Inject realistic ops_alerts for a synthetic merchant to exercise
    the bugfix pipeline. Returns count of alerts created.
    """
    alerts_created = 0

    if archetype == "broken_tracking":
        # Tracking failure alert
        from app.services.alerting import write_alert
        write_alert(
            db, severity="warning", source="simulation_engine",
            alert_type="tracker_data_gap",
            shop_domain=shop_domain,
            summary=f"[SIM] Tracking data gap detected for {shop_domain} — "
                    f"expected events not arriving for some product pages",
            detail=json.dumps({
                "synthetic": True,
                "archetype": archetype,
                "gap_hours": rng.randint(1, 6),
            }),
        )
        alerts_created += 1

    elif archetype == "noisy":
        # Suspicious traffic alert
        from app.services.alerting import write_alert
        write_alert(
            db, severity="info", source="simulation_engine",
            alert_type="suspicious_traffic_pattern",
            shop_domain=shop_domain,
            summary=f"[SIM] Unusual traffic pattern for {shop_domain} — "
                    f"high volume, low engagement, possible bot traffic",
            detail=json.dumps({
                "synthetic": True,
                "archetype": archetype,
                "hourly_rate": rng.randint(50, 200),
            }),
        )
        alerts_created += 1

    elif archetype == "high_traffic_low_conversion":
        # Conversion concern
        from app.services.alerting import write_alert
        write_alert(
            db, severity="info", source="simulation_engine",
            alert_type="low_conversion_rate",
            shop_domain=shop_domain,
            summary=f"[SIM] Very low conversion rate for {shop_domain} — "
                    f"high traffic but minimal cart adds",
            detail=json.dumps({
                "synthetic": True,
                "archetype": archetype,
            }),
        )
        alerts_created += 1

    return alerts_created


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_simulation_cycle(
    db: Session,
    scenario: str = "mixed",
    hours: int = 1,
    seed: int | None = None,
) -> SimulationSummary:
    """
    Run one simulation cycle — generates events for all synthetic merchants.

    Scenarios:
      - "mixed": All archetypes generate their natural activity
      - "healthy_only": Only healthy merchants generate events
      - "stress": All merchants at 3x normal volume
      - "failure": Only failure-prone archetypes active

    Returns SimulationSummary with counts.
    """
    summary = SimulationSummary()
    cycle_id = _now().strftime("%Y%m%d-%H%M")

    merchants = get_synthetic_merchants(db)
    if not merchants:
        summary.errors.append("No synthetic merchants found. Run create_synthetic_merchants() first.")
        return summary

    summary.merchants_active = len(merchants)
    summary.scenarios_run.append(scenario)

    for m in merchants:
        shop = m["shop_domain"]
        archetype_name = m["archetype"]
        archetype = ARCHETYPES.get(archetype_name)
        if not archetype:
            summary.errors.append(f"Unknown archetype '{archetype_name}' for {shop}")
            continue

        # Scenario filtering
        if scenario == "healthy_only" and archetype_name != "healthy":
            continue
        if scenario == "failure" and archetype_name not in ("broken_tracking", "noisy"):
            continue

        # Deterministic RNG per merchant per cycle
        rng_seed = seed or _deterministic_seed(shop, cycle_id)
        rng = random.Random(rng_seed)

        # Volume multiplier
        effective_hours = hours
        if scenario == "stress":
            effective_hours = hours * 3

        try:
            written, dropped, purchases = _generate_events_for_merchant(
                db, shop, archetype, hours=effective_hours, rng=rng,
            )
            summary.events_generated += written
            summary.events_failed += dropped
            summary.purchases_generated += purchases

            # Inject scenario-appropriate alerts
            alerts = _inject_synthetic_alerts(db, shop, archetype_name, rng)
            summary.alerts_generated += alerts

        except Exception as exc:
            summary.errors.append(f"{shop}: {exc}")
            log.warning("simulation: error for %s: %s", shop, exc)

    db.flush()
    log.info(
        "simulation: cycle complete — merchants=%d events=%d purchases=%d alerts=%d scenario=%s",
        summary.merchants_active, summary.events_generated,
        summary.purchases_generated, summary.alerts_generated, scenario,
    )
    return summary


# ---------------------------------------------------------------------------
# Status / observability
# ---------------------------------------------------------------------------

def get_simulation_status(db: Session) -> dict:
    """
    Comprehensive simulation status for operator visibility.

    Returns counts of synthetic merchants, events, alerts, candidates,
    and lessons — all clearly separated from real merchant data.
    """
    merchants = get_synthetic_merchants(db)
    synthetic_shops = [m["shop_domain"] for m in merchants]

    status = {
        "synthetic_merchants": len(merchants),
        "merchants": merchants,
        "isolation_mode": "sandbox",
    }

    if not synthetic_shops:
        status.update({
            "synthetic_events": 0,
            "synthetic_alerts": 0,
            "synthetic_metrics": 0,
            "synthetic_signals": 0,
            "synthetic_candidates": 0,
            "synthetic_lessons": 0,
        })
        return status

    # Count synthetic data across pipeline stages
    try:
        for table, key in [
            ("events", "synthetic_events"),
            ("ops_alerts", "synthetic_alerts"),
            ("product_metrics", "synthetic_metrics"),
            ("opportunity_signals", "synthetic_signals"),
        ]:
            row = db.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE shop_domain = ANY(:shops)"),
                {"shops": synthetic_shops},
            ).fetchone()
            status[key] = row[0] if row else 0
    except Exception:
        pass

    # Count sandbox-labeled learning artifacts
    try:
        row = db.execute(
            text("SELECT COUNT(*) FROM bugfix_candidates WHERE evidence_source = 'sandbox'"),
        ).fetchone()
        status["synthetic_candidates"] = row[0] if row else 0
    except Exception:
        status["synthetic_candidates"] = 0

    try:
        row = db.execute(
            text("SELECT COUNT(*) FROM system_lessons WHERE evidence_source = 'sandbox'"),
        ).fetchone()
        status["synthetic_lessons"] = row[0] if row else 0
    except Exception:
        status["synthetic_lessons"] = 0

    return status
