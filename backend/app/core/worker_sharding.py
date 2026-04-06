"""
worker_sharding.py — opt-in sharding contract for merchant-scale workers.

Single-process workers become chokepoints past ~100 merchants. Rather than
refactoring every worker now, we expose ONE helper that lets operators
scale horizontally later by running N replicas of a worker with different
WORKER_SHARD assignments.

Contract:
  Set WORKER_SHARD="<index>/<total>" in the process env (e.g. "0/4" means
  "this replica handles shop_domains where hash(shop) % 4 == 0"). When
  unset or "0/1", the worker handles ALL shops — zero behavior change.

Usage in a worker cycle:

    from app.core.worker_sharding import shard_owns_shop

    for shop in all_shops:
        if not shard_owns_shop(shop):
            continue
        process_shop(shop)

The hash is MD5-based + modulo: deterministic, uniform, collision-safe.
No runtime dependency, no network call, no DB hit.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

log = logging.getLogger("worker_sharding")

_SHARD_RE = re.compile(r"^(\d+)/(\d+)$")


def _parse_shard() -> tuple[int, int]:
    """
    Parse WORKER_SHARD env var into (index, total). Defaults to (0, 1) when
    unset or malformed. Logs a WARNING on malformed values rather than
    silently accepting a degraded configuration.
    """
    raw = (os.getenv("WORKER_SHARD") or "").strip()
    if not raw:
        return 0, 1
    m = _SHARD_RE.match(raw)
    if not m:
        log.warning(
            "worker_sharding: invalid WORKER_SHARD=%r — expected '<i>/<n>' — falling back to 0/1",
            raw,
        )
        return 0, 1
    idx = int(m.group(1))
    total = int(m.group(2))
    if total <= 0 or idx < 0 or idx >= total:
        log.warning(
            "worker_sharding: out-of-range WORKER_SHARD=%d/%d — falling back to 0/1", idx, total,
        )
        return 0, 1
    return idx, total


def get_shard_info() -> dict:
    """Return the current (idx, total) assignment. Useful for logging/health."""
    idx, total = _parse_shard()
    return {"shard_index": idx, "shard_total": total, "is_sharded": total > 1}


def shard_owns_shop(shop_domain: str) -> bool:
    """
    Return True if THIS worker replica is responsible for this shop.

    When WORKER_SHARD is unset or "0/1", returns True for every shop
    (single-worker mode — zero behavior change).
    """
    idx, total = _parse_shard()
    if total <= 1:
        return True
    if not shop_domain:
        return idx == 0  # empty-shop tasks always on shard 0
    h = hashlib.md5(shop_domain.encode("utf-8")).digest()
    bucket = int.from_bytes(h[:4], "big") % total
    return bucket == idx
