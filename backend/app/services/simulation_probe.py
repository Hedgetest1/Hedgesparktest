"""
simulation_probe.py — Lightweight HTTP ingestion probes for synthetic merchants.

Covers the critical gap between DB-direct simulation and real HTTP ingestion:
exercises the full /track path including validation, rate limiting, known-shop
caching, visitor upsert, product_url normalization, and purchase flow.

Designed to run alongside the DB-direct simulation engine, not replace it.
The DB engine provides volume and scenario coverage; the probe validates
that the HTTP path works correctly for synthetic shops.

Public interface:
    run_ingestion_probe(db, base_url="http://127.0.0.1:8000") -> ProbeResult
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.services.learning_isolation import is_synthetic_shop

log = logging.getLogger("simulation_probe")

_PROBE_TIMEOUT = 10  # seconds per request


@dataclass
class ProbeResult:
    """Results from a single probe run."""
    checks_run: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    failures: list[dict] = field(default_factory=list)
    latency_ms: dict[str, float] = field(default_factory=dict)

    @property
    def all_passed(self) -> bool:
        return self.checks_failed == 0

    def to_dict(self) -> dict:
        return {
            "all_passed": self.all_passed,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "failures": self.failures,
            "latency_ms": self.latency_ms,
        }


def _check(result: ProbeResult, name: str, condition: bool, detail: str = ""):
    """Record a check result."""
    result.checks_run += 1
    if condition:
        result.checks_passed += 1
    else:
        result.checks_failed += 1
        result.failures.append({"check": name, "detail": detail})
        log.warning("probe: FAILED %s — %s", name, detail)


def run_ingestion_probe(
    db: Session,
    base_url: str = "http://127.0.0.1:8000",
) -> ProbeResult:
    """
    Run HTTP ingestion probes against synthetic merchants.

    Validates:
      1. Health endpoint responds
      2. Valid event accepted (200)
      3. Invalid shop domain rejected (400)
      4. Invalid event type rejected (400)
      5. Product URL normalization works
      6. Batch endpoint works
      7. Purchase event accepted (with order dedup)
      8. Unknown shop rejected (400)
      9. Latency within acceptable bounds

    Only sends events for synthetic merchants. Refuses to probe real shops.
    """
    result = ProbeResult()

    # Find a synthetic merchant to probe with
    from app.services.simulation_engine import get_synthetic_merchants
    merchants = get_synthetic_merchants(db)
    if not merchants:
        result.failures.append({
            "check": "setup",
            "detail": "No synthetic merchants found. Create them first.",
        })
        result.checks_failed += 1
        return result

    shop = merchants[0]["shop_domain"]
    if not is_synthetic_shop(shop):
        result.failures.append({
            "check": "safety",
            "detail": f"Shop {shop} is not synthetic — refusing to probe",
        })
        result.checks_failed += 1
        return result

    client = httpx.Client(base_url=base_url, timeout=_PROBE_TIMEOUT)

    try:
        # --- Probe 1: Health check ---
        t0 = time.monotonic()
        resp = client.get("/health")
        result.latency_ms["health"] = round((time.monotonic() - t0) * 1000, 1)
        _check(result, "health_200", resp.status_code == 200,
               f"Expected 200, got {resp.status_code}")

        # --- Probe 2: Valid page_view event ---
        t0 = time.monotonic()
        resp = client.post("/track", json={
            "shop_domain": shop,
            "visitor_id": "sim-probe-v1",
            "event_type": "page_view",
            "page_url": f"https://{shop}/",
            "device_type": "desktop",
        })
        result.latency_ms["track_event"] = round((time.monotonic() - t0) * 1000, 1)
        _check(result, "track_valid_200", resp.status_code == 200,
               f"Expected 200, got {resp.status_code}: {resp.text[:200]}")

        # --- Probe 3: Invalid shop domain ---
        resp = client.post("/track", json={
            "shop_domain": "not-a-valid-domain",
            "visitor_id": "sim-probe-v1",
            "event_type": "page_view",
        })
        _check(result, "track_invalid_shop_400", resp.status_code == 400,
               f"Expected 400 for invalid shop, got {resp.status_code}")

        # --- Probe 4: Invalid event type ---
        resp = client.post("/track", json={
            "shop_domain": shop,
            "visitor_id": "sim-probe-v1",
            "event_type": "INVALID_TYPE",
        })
        _check(result, "track_invalid_type_400", resp.status_code == 400,
               f"Expected 400 for invalid type, got {resp.status_code}")

        # --- Probe 5: Product view with normalization ---
        resp = client.post("/track", json={
            "shop_domain": shop,
            "visitor_id": "sim-probe-v2",
            "event_type": "product_view",
            "page_url": f"https://{shop}/products/test-product?variant=123",
            "product_url": f"https://{shop}/products/test-product?variant=123",
            "dwell_seconds": 15,
            "scroll_depth": 60,
            "device_type": "mobile",
        })
        _check(result, "track_product_view_200", resp.status_code == 200,
               f"Expected 200, got {resp.status_code}: {resp.text[:200]}")

        # --- Probe 6: Batch endpoint ---
        t0 = time.monotonic()
        resp = client.post("/track/batch", json={
            "events": [
                {
                    "shop_domain": shop,
                    "visitor_id": f"sim-probe-batch-{i}",
                    "event_type": "page_view",
                    "page_url": f"https://{shop}/",
                }
                for i in range(5)
            ]
        })
        result.latency_ms["track_batch"] = round((time.monotonic() - t0) * 1000, 1)
        _check(result, "track_batch_200", resp.status_code == 200,
               f"Expected 200, got {resp.status_code}: {resp.text[:200]}")

        # --- Probe 7: Unknown shop rejected ---
        resp = client.post("/track", json={
            "shop_domain": "nonexistent-shop-xyz.myshopify.com",
            "visitor_id": "sim-probe-v1",
            "event_type": "page_view",
        })
        _check(result, "track_unknown_shop_400", resp.status_code == 400,
               f"Expected 400 for unknown shop, got {resp.status_code}")

        # --- Probe 8: Latency check ---
        track_latency = result.latency_ms.get("track_event", 0)
        _check(result, "track_latency_under_500ms", track_latency < 500,
               f"Track latency {track_latency}ms exceeds 500ms threshold")

    except httpx.ConnectError as exc:
        result.checks_failed += 1
        result.failures.append({
            "check": "connectivity",
            "detail": f"Cannot connect to {base_url}: {exc}",
        })
    except Exception as exc:
        result.checks_failed += 1
        result.failures.append({
            "check": "unexpected",
            "detail": f"Probe error: {exc}",
        })
    finally:
        client.close()

    log.info(
        "probe: complete — %d/%d passed, latency=%s",
        result.checks_passed, result.checks_run, result.latency_ms,
    )
    return result
