"""
feature_flags.py — Progressive rollout primitive.

The missing piece for "ship to every user at once ≠ safe".

Capabilities
------------
- Killswitch (instant disable without deploy)
- Allowlist (specific shops first)
- Percentage rollout (deterministic by shop hash — same shop always
  falls on the same side of the line until the % changes)
- Ring staging (ring 0 = internal/canary, ring 1 = beta, ring 2 = all)

Storage model
-------------
Each flag is one Redis hash `hs:flag:{name}`:
    enabled      : "1" | "0"
    percentage   : "0".."100"
    allowlist    : "shop1.myshopify.com,shop2.myshopify.com"
    killswitch   : "1" | "0"  (hard off, overrides everything)
    ring         : "0" | "1" | "2"   (optional)

If Redis is unreachable we fall through to an in-process default table
so the app keeps working. Defaults are the conservative choice (off).

Determinism
-----------
`is_enabled(flag, shop)` uses SHA-256(shop || flag) % 100 < percentage.
The shop is always on the same side until percentage crosses it.

Admin surface
-------------
The admin endpoints (app/api/feature_flags_admin.py) are gated by the
ops API key so we can flip flags live without a deploy.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("feature_flags")

_REDIS_PREFIX = "hs:flag"


@dataclass(frozen=True)
class FlagDefinition:
    """Registry entry. Defaults live here — Redis overrides only tune them."""
    name: str
    description: str
    default_enabled: bool = False
    default_percentage: int = 0
    default_ring: int = 3  # 3 = off, 2 = all, 1 = beta, 0 = canary only


# Central registry. Any flag the code checks should live here — this is the
# contract between product decisions and the runtime. Adding a new flag is
# a code change (tracked in review), flipping an existing one is a Redis
# mutation (tracked in audit log).
REGISTRY: dict[str, FlagDefinition] = {
    "night_shift_agent": FlagDefinition(
        name="night_shift_agent",
        description="Phase Ω⁵ nightly AI report — default on for Pro",
        default_enabled=True,
        default_percentage=100,
        default_ring=2,
    ),
    "sse_realtime_dashboard": FlagDefinition(
        name="sse_realtime_dashboard",
        description="Server-sent events live updates on dashboard cards",
        default_enabled=True,
        default_percentage=100,
        default_ring=2,
    ),
    "community_marketplace_ui": FlagDefinition(
        name="community_marketplace_ui",
        description="Public /app/marketplace page",
        default_enabled=True,
        default_percentage=100,
        default_ring=2,
    ),
    "llm_strict_safety": FlagDefinition(
        name="llm_strict_safety",
        description="Block LLM calls that fail the prompt-injection guard",
        default_enabled=True,
        default_percentage=100,
        default_ring=2,
    ),
    "autonomous_loop": FlagDefinition(
        name="autonomous_loop",
        description="Gated auto-execution of recommended actions",
        default_enabled=False,
        default_percentage=0,
        default_ring=0,  # canary only
    ),
}


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _shop_bucket(flag: str, shop: str) -> int:
    """Return a deterministic 0..99 bucket for (flag, shop)."""
    digest = hashlib.sha256(f"{flag}:{shop}".encode("utf-8")).digest()
    # take 4 bytes → int → mod 100
    n = int.from_bytes(digest[:4], "big")
    return n % 100


def _load_flag(name: str) -> dict:
    """Read a flag's live state from Redis, falling back to the registry."""
    reg = REGISTRY.get(name)
    default = {
        "enabled": "1" if (reg and reg.default_enabled) else "0",
        "percentage": str(reg.default_percentage) if reg else "0",
        "allowlist": "",
        "killswitch": "0",
        "ring": str(reg.default_ring) if reg else "3",
    }
    rc = _redis()
    if rc is None:
        record_silent_return("feature_flags.load")
        return default
    try:
        key = f"{_REDIS_PREFIX}:{name}"
        raw = rc.hgetall(key)
        if not raw:
            return default
        # Normalize byte-strings from redis-py
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else k
            vv = v.decode() if isinstance(v, bytes) else v
            out[kk] = vv
        # Merge defaults for missing keys
        for k, v in default.items():
            out.setdefault(k, v)
        return out
    except Exception as exc:
        log.warning("feature_flags: read failed for %s: %s", name, exc)
        return default


def is_enabled(
    flag: str,
    shop: str | None = None,
    *,
    default: bool | None = None,
) -> bool:
    """
    Decide whether `flag` is enabled for `shop`.

    Order of checks:
      1. Killswitch off → always False
      2. Flag enabled=0 → False
      3. Shop in allowlist → True
      4. Shop hash bucket < percentage → True
      5. Otherwise False

    If Redis/registry are both unreachable, fall back to `default`
    (or the registry default if provided).
    """
    state = _load_flag(flag)

    if state.get("killswitch") == "1":
        return False

    if state.get("enabled") != "1":
        return False

    allowlist = {s.strip() for s in (state.get("allowlist") or "").split(",") if s.strip()}
    if shop and shop in allowlist:
        return True

    try:
        pct = int(state.get("percentage", "0"))
    except ValueError:
        pct = 0

    if pct >= 100:
        return True
    if pct <= 0:
        return False
    if shop is None:
        # Global flag with % rollout but no shop context — False for safety
        return False
    return _shop_bucket(flag, shop) < pct


def ring_for_shop(flag: str, shop: str) -> int:
    """
    Ring assignment for staged rollouts. Returns 0..3 where lower = earlier.
      Ring 0: internal shops (HS_INTERNAL_SHOPS env or allowlist[:3])
      Ring 1: canary bucket (first 5% by hash)
      Ring 2: beta bucket (5..25% by hash)
      Ring 3: general population
    """
    internal_env = os.environ.get("HS_INTERNAL_SHOPS", "")
    internal = {s.strip() for s in internal_env.split(",") if s.strip()}
    if shop in internal:
        return 0

    state = _load_flag(flag)
    allowlist = [s.strip() for s in (state.get("allowlist") or "").split(",") if s.strip()]
    if shop in allowlist[:3]:
        return 0

    bucket = _shop_bucket(flag, shop)
    if bucket < 5:
        return 1
    if bucket < 25:
        return 2
    return 3


def rollout_allows(flag: str, shop: str, max_ring: int) -> bool:
    """True if shop's ring is <= max_ring. Used by the staged rollout layer."""
    return ring_for_shop(flag, shop) <= max_ring


def set_flag(
    name: str,
    *,
    enabled: bool | None = None,
    percentage: int | None = None,
    allowlist: Iterable[str] | None = None,
    killswitch: bool | None = None,
    ring: int | None = None,
) -> bool:
    """Mutate a flag's Redis state. Returns True on success."""
    rc = _redis()
    if rc is None:
        record_silent_return("feature_flags.set")
        return False
    try:
        key = f"{_REDIS_PREFIX}:{name}"
        updates = {}
        if enabled is not None:
            updates["enabled"] = "1" if enabled else "0"
        if percentage is not None:
            updates["percentage"] = str(max(0, min(100, int(percentage))))
        if allowlist is not None:
            updates["allowlist"] = ",".join(sorted({s.strip() for s in allowlist if s.strip()}))
        if killswitch is not None:
            updates["killswitch"] = "1" if killswitch else "0"
        if ring is not None:
            updates["ring"] = str(max(0, min(3, int(ring))))
        if not updates:
            return True
        rc.hset(key, mapping=updates)
        return True
    except Exception as exc:
        log.warning("feature_flags: write failed for %s: %s", name, exc)
        return False


def get_flag_state(name: str) -> dict:
    """Return current live state + registry metadata for inspection."""
    reg = REGISTRY.get(name)
    state = _load_flag(name)
    return {
        "name": name,
        "description": reg.description if reg else None,
        "enabled": state.get("enabled") == "1",
        "percentage": int(state.get("percentage", "0") or 0),
        "allowlist": [s for s in (state.get("allowlist") or "").split(",") if s.strip()],
        "killswitch": state.get("killswitch") == "1",
        "ring": int(state.get("ring", "3") or 3),
        "registered": reg is not None,
        "defaults": {
            "enabled": reg.default_enabled if reg else None,
            "percentage": reg.default_percentage if reg else None,
            "ring": reg.default_ring if reg else None,
        } if reg else None,
    }


def list_flags() -> list[dict]:
    return [get_flag_state(name) for name in REGISTRY.keys()]
