"""
vertical_classifier.py — Phase Ω moat #1.

Detects which commerce vertical a merchant belongs to by scanning their
products. Output is the foundation for:

  * benchmarks_vertical — peers compared within (vertical, revenue_band),
    not just revenue_band. A €15k beauty brand benchmarked against
    €15k beauty brands instead of €15k electronics shops.
  * vertical_prompt_pack — narrative copy, nudge wording, thresholds
    tuned per vertical (beauty CVR 3.5% baseline ≠ electronics 1.8%).
  * causal_explainer — vertical-specific causal hypotheses
    ("seasonal pollen surge" only fires for beauty/wellness/pets).

Design
------
* Pure deterministic — keyword scoring on product titles + tags. Zero LLM,
  zero training data. Repeatable, debuggable, free.
* Soft classification — every shop gets a score per vertical, top score
  wins. Confidence reported so downstream callers can fall back.
* Stable — cached 24h in Redis. Re-classified by daily worker if products
  change materially.
* Multi-tenant safe — shop_domain is the only key, never crosses shops.

Verticals (12)
--------------
beauty, fashion, electronics, food_beverage, home_garden, jewelry,
supplements_wellness, pets, sports_outdoor, kids_baby, books_media, other
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("vertical_classifier")

# ---------------------------------------------------------------------------
# Vocabulary — high-signal keywords per vertical. Curated, not learned.
# Each token is a substring matched case-insensitively against
# (product_title + tags + product_type). Tuned for English + Italian + ES/FR.
# ---------------------------------------------------------------------------

_VERTICAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "beauty": (
        "lipstick", "mascara", "foundation", "skincare", "serum", "moisturizer",
        "cream", "lotion", "perfume", "fragrance", "makeup", "rossetto",
        "crema", "siero", "profumo", "trucco", "labial", "maquillage",
        "blush", "eyeshadow", "concealer", "cleanser", "toner", "cosmetic",
        "nail polish", "mask", "balm", "exfoliant",
    ),
    "fashion": (
        "shirt", "dress", "jeans", "skirt", "jacket", "coat", "sweater",
        "pants", "blouse", "trousers", "tee", "t-shirt", "hoodie", "sneakers",
        "shoes", "boots", "handbag", "purse", "wallet", "scarf", "belt",
        "camicia", "vestito", "gonna", "giacca", "maglia", "pantaloni",
        "scarpe", "borsa", "robe", "chemise", "veste", "camisa", "pantalon",
        "fashion", "apparel", "clothing", "outfit",
    ),
    "electronics": (
        "phone", "laptop", "headphones", "charger", "cable", "speaker",
        "camera", "tablet", "monitor", "keyboard", "mouse", "drone",
        "smartwatch", "earbuds", "router", "ssd", "usb", "hdmi", "adapter",
        "telefono", "cuffie", "altoparlante", "schermo", "tastiera", "auricolari",
        "écouteurs", "câble", "chargeur", "auriculares", "cargador",
        "electronic", "gadget", "device", "wireless",
    ),
    "food_beverage": (
        "coffee", "tea", "wine", "beer", "snack", "chocolate", "cookie",
        "pasta", "olive oil", "honey", "sauce", "spice", "candy", "jam",
        "caffè", "tè", "vino", "birra", "biscotto", "miele", "salsa",
        "café", "thé", "vin", "bière", "miel", "dulce", "vino", "cerveza",
        "gourmet", "organic", "edible", "beverage",
    ),
    "home_garden": (
        "candle", "vase", "pillow", "blanket", "rug", "mug", "lamp",
        "curtain", "frame", "plant pot", "decor", "bedding", "towel",
        "candela", "vaso", "cuscino", "coperta", "tappeto", "lampada",
        "bougie", "coussin", "vela", "almohada", "manta",
        "home decor", "furniture", "kitchen", "bathroom",
    ),
    "jewelry": (
        "ring", "necklace", "bracelet", "earring", "pendant", "chain",
        "anello", "collana", "braccialetto", "orecchino", "ciondolo",
        "bague", "collier", "anillo", "pulsera",
        "gold", "silver", "diamond", "gemstone", "jewelry", "jewellery",
        "oro", "argento", "diamante", "or", "argent", "plata",
    ),
    "supplements_wellness": (
        "vitamin", "supplement", "protein", "collagen", "probiotic",
        "omega", "magnesium", "zinc", "ashwagandha", "creatine",
        "vitamina", "integratore", "proteina", "complemento", "complément",
        "wellness", "fitness powder", "nootropic", "adaptogen",
    ),
    "pets": (
        "dog", "cat", "puppy", "kitten", "leash", "collar", "pet food",
        "kibble", "litter", "aquarium", "hamster", "bird cage",
        "cane", "gatto", "cibo per cani", "guinzaglio", "lettiera",
        "chien", "chat", "perro", "gato",
        "pet ", "petfood", "treat", "chew toy",
    ),
    "sports_outdoor": (
        "yoga", "fitness", "gym", "running", "cycling", "bike", "tennis",
        "football", "basketball", "ski", "hiking", "camping", "kayak",
        "yoga mat", "dumbbell", "tent", "backpack", "helmet",
        "palestra", "corsa", "ciclismo", "bici", "sci", "trekking",
        "vélo", "randonnée", "bicicleta",
        "sport", "outdoor", "athletic",
    ),
    "kids_baby": (
        "baby", "toddler", "stroller", "diaper", "pacifier", "crib",
        "kids toy", "plush", "rattle", "onesie",
        "bebè", "bambino", "passeggino", "pannolino", "ciuccio",
        "bébé", "poussette", "couche", "bebé", "pañal",
        "kids", "children", "newborn", "infant",
    ),
    "books_media": (
        "book", "novel", "ebook", "audiobook", "vinyl", "cd ", "dvd",
        "blu-ray", "magazine", "comic", "manga", "poster",
        "libro", "romanzo", "rivista", "fumetto",
        "livre", "roman", "revista",
    ),
}

# Compile to lower-cased substrings (already lowercase but explicit)
_VERTICAL_KEYWORDS_LC = {
    v: tuple(k.lower() for k in keys) for v, keys in _VERTICAL_KEYWORDS.items()
}

_VERTICALS = tuple(_VERTICAL_KEYWORDS.keys()) + ("other",)

_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_KEY_PREFIX = "hs:vertical:v1"
_MIN_CONFIDENCE = 0.20  # below this → "other"
_MAX_PRODUCTS_SAMPLED = 500  # safety cap


@dataclass
class VerticalClassification:
    shop_domain: str
    vertical: str            # one of _VERTICALS
    confidence: float        # 0..1, fraction of top-vertical hits / total hits
    runner_up: str | None
    runner_up_confidence: float
    sample_size: int         # number of products inspected
    scores: dict[str, int]   # raw hit counts per vertical
    classified_at: str       # ISO timestamp


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _normalize(text_blob: str) -> str:
    """Lowercase and collapse whitespace — single allocation per product."""
    return re.sub(r"\s+", " ", (text_blob or "").lower()).strip()


def _score_text(blob: str) -> dict[str, int]:
    """Score one product's text blob across all verticals. Returns hit counts."""
    scores: dict[str, int] = {}
    if not blob:
        return scores
    for vertical, keys in _VERTICAL_KEYWORDS_LC.items():
        hits = sum(1 for k in keys if k in blob)
        if hits:
            scores[vertical] = hits
    return scores


def _sample_products(db: Session, shop_domain: str) -> list[dict]:
    """
    Pull a sample of the merchant's products. Uses the existing `products`
    table. Falls back to shop_orders.line_items if products is empty.
    Returns a list of {"text": str} blobs ready for scoring.
    """
    blobs: list[dict] = []

    # Primary source: products table (title only — schema is minimal)
    try:
        rows = db.execute(text("""
            SELECT title
            FROM products
            WHERE shop_domain = :shop
            LIMIT :cap
        """), {"shop": shop_domain, "cap": _MAX_PRODUCTS_SAMPLED}).fetchall()
        for r in rows:
            title = r[0] or ""
            if title:
                blobs.append({"text": _normalize(title)})
    except Exception as exc:
        log.warning("vertical_classifier: products query failed: %s", exc)

    if blobs:
        return blobs

    # Fallback: extract titles from shop_orders.line_items
    try:
        rows = db.execute(text("""
            SELECT line_items
            FROM shop_orders
            WHERE shop_domain = :shop
            ORDER BY created_at DESC
            LIMIT :cap
        """), {"shop": shop_domain, "cap": _MAX_PRODUCTS_SAMPLED}).fetchall()
        for r in rows:
            items = r[0] or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("product_handle") or "")
                if title:
                    blobs.append({"text": _normalize(title)})
    except Exception as exc:
        log.warning("vertical_classifier: orders fallback failed: %s", exc)

    return blobs


def classify_shop(db: Session, shop_domain: str, *, force: bool = False) -> VerticalClassification:
    """
    Classify a shop into one of the 12 verticals. Cached 24h in Redis.

    `force=True` skips the cache and recomputes.
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"

    if not force:
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                cached = rc.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return VerticalClassification(**data)
        except Exception:
            pass

    blobs = _sample_products(db, shop_domain)
    aggregate: dict[str, int] = {}
    for b in blobs:
        for v, hits in _score_text(b["text"]).items():
            aggregate[v] = aggregate.get(v, 0) + hits

    if not aggregate:
        result = VerticalClassification(
            shop_domain=shop_domain,
            vertical="other",
            confidence=0.0,
            runner_up=None,
            runner_up_confidence=0.0,
            sample_size=len(blobs),
            scores={},
            classified_at=_now_iso(),
        )
    else:
        ranked = sorted(aggregate.items(), key=lambda kv: kv[1], reverse=True)
        total = sum(aggregate.values())
        top_v, top_hits = ranked[0]
        confidence = round(top_hits / total, 3) if total else 0.0
        runner_up = ranked[1][0] if len(ranked) > 1 else None
        runner_up_conf = round(ranked[1][1] / total, 3) if (len(ranked) > 1 and total) else 0.0
        chosen = top_v if confidence >= _MIN_CONFIDENCE else "other"
        result = VerticalClassification(
            shop_domain=shop_domain,
            vertical=chosen,
            confidence=confidence,
            runner_up=runner_up,
            runner_up_confidence=runner_up_conf,
            sample_size=len(blobs),
            scores=aggregate,
            classified_at=_now_iso(),
        )

    # Cache
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(result.__dict__, default=str))
    except Exception:
        pass

    return result


def get_vertical(db: Session, shop_domain: str) -> str:
    """Convenience wrapper — returns just the vertical string."""
    return classify_shop(db, shop_domain).vertical


def all_verticals() -> tuple[str, ...]:
    """Public list of supported verticals."""
    return _VERTICALS


def classify_active_shops_batch(
    db: Session,
    *,
    chunk_size: int = 100,
    force: bool = False,
) -> dict:
    """
    Bulk classify every active merchant. Designed for the nightly worker
    at 10k-merchant scale — chunked, Redis-cached, single SQL pass for
    shop list, deterministic, idempotent.

    Returns {processed, by_vertical, errors}.
    """
    rows = db.execute(text("""
        SELECT shop_domain
        FROM merchants
        WHERE install_status = 'active'
        ORDER BY shop_domain
    """)).fetchall()
    shops = [r[0] for r in rows]
    by_vertical: dict[str, int] = {}
    errors = 0
    processed = 0
    for i in range(0, len(shops), chunk_size):
        chunk = shops[i:i + chunk_size]
        for s in chunk:
            try:
                c = classify_shop(db, s, force=force)
                by_vertical[c.vertical] = by_vertical.get(c.vertical, 0) + 1
                processed += 1
            except Exception as exc:
                errors += 1
                log.debug("vertical_classifier: bulk failed for %s: %s", s, exc)
    if errors:
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="vertical_classifier",
                alert_type="bulk_classify_errors",
                summary=f"Bulk vertical classify completed with {errors}/{len(shops)} errors",
                detail={"errors": errors, "total": len(shops)},
            )
        except Exception:
            pass
    return {
        "processed": processed,
        "errors": errors,
        "by_vertical": by_vertical,
        "total_shops": len(shops),
    }
