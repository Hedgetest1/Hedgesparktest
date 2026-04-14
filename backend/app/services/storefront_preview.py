"""
storefront_preview.py — Phase Ω'' demo pre-signup.

Scrape a Shopify store's public /products.json endpoint, run vertical
classification, and return a preview narrative + estimated ROI without
requiring OAuth or any merchant data.

Killer because: every other SaaS forces a 5-step OAuth dance before
showing value. We let prospects type their domain and see a real,
data-driven preview in 30 seconds.

Safety
------
* No PII is fetched — `/products.json` is a Shopify-supported public
  endpoint, exposing only product titles, descriptions, prices.
* Hard cap of 250 products fetched (1 page × 250 max).
* Per-domain rate limit: 1 preview / 60s in Redis (`hs:demo_lock:{domain}`).
* Timeout: 6 seconds. Fails graceful with structured error.
* Domain validation: must end in `.myshopify.com` OR be an http(s) URL.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

log = logging.getLogger("storefront_preview")

_TIMEOUT_S = 6.0
_MAX_PRODUCTS = 250
_USER_AGENT = "HedgeSpark-Preview/1.0 (+https://hedgesparkhq.com)"
_RATE_LIMIT_TTL = 60
_RATE_LIMIT_KEY = "hs:demo_lock:{}"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


_DOMAIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,60}[a-z0-9]\.myshopify\.com$", re.I)
_GENERIC_DOMAIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,60}(\.[a-z]{2,})+$", re.I)


def normalize_domain(input_url: str) -> str | None:
    """
    Accept raw user input ('mystore', 'mystore.myshopify.com',
    'https://mystore.com/', 'mystore.com/products') and return a
    canonical hostname or None if invalid.
    """
    if not input_url:
        return None
    s = input_url.strip().lower()
    s = s.replace("https://", "").replace("http://", "")
    if "/" in s:
        s = s.split("/", 1)[0]
    if not s:
        return None
    if "." not in s:
        s = f"{s}.myshopify.com"
    if _DOMAIN_PATTERN.match(s):
        return s
    if _GENERIC_DOMAIN_PATTERN.match(s):
        return s
    return None


def _check_rate_limit(domain: str) -> bool:
    """True = allowed, False = rate-limited."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return True  # fail-open: redis down, allow the preview
        key = _RATE_LIMIT_KEY.format(domain)
        if rc.get(key):
            return False
        rc.setex(key, _RATE_LIMIT_TTL, "1")
        return True
    except Exception:
        # fail-open: prefer letting the preview through over blocking
        # a real merchant evaluation on a transient Redis error.
        return True


def fetch_products(domain: str) -> list[dict]:
    """Pull /products.json from a Shopify store. Returns a list of product dicts."""
    try:
        import httpx
    except ImportError:
        return []
    url = f"https://{domain}/products.json?limit={_MAX_PRODUCTS}"
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200:
            return []
        body = r.json()
        products = body.get("products") if isinstance(body, dict) else None
        if not isinstance(products, list):
            return []
        return products[:_MAX_PRODUCTS]
    except Exception as exc:
        log.debug("storefront_preview: fetch failed %s: %s", domain, exc)
        return []


def _classify_from_products(products: list[dict]) -> tuple[str, float, dict[str, int]]:
    """Run the existing vertical classifier on scraped product titles."""
    from app.services.vertical_classifier import _normalize, _score_text, _MIN_CONFIDENCE

    aggregate: dict[str, int] = {}
    for p in products:
        title = p.get("title") or ""
        ptype = p.get("product_type") or ""
        tags = p.get("tags") or []
        if isinstance(tags, list):
            tags_text = " ".join(str(t) for t in tags)
        else:
            tags_text = str(tags)
        blob = _normalize(f"{title} {ptype} {tags_text}")
        for v, hits in _score_text(blob).items():
            aggregate[v] = aggregate.get(v, 0) + hits

    if not aggregate:
        return "other", 0.0, {}
    ranked = sorted(aggregate.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(aggregate.values())
    top_v, top_hits = ranked[0]
    confidence = round(top_hits / total, 3) if total else 0.0
    chosen = top_v if confidence >= _MIN_CONFIDENCE else "other"
    return chosen, confidence, aggregate


def _estimate_recovery(products: list[dict], vertical: str) -> dict:
    """
    Quick heuristic ROI estimator from public data only:
      * Average price across visible products
      * Vertical baseline CVR
      * Assumed traffic (1k visitors/mo for demo) → estimated MRR
      * Recovery potential = 15% of estimated MRR
    """
    from app.services.vertical_prompt_pack import baseline_cvr_pct, baseline_aov_eur

    prices: list[float] = []
    for p in products:
        for v in (p.get("variants") or []):
            try:
                pr = float(v.get("price") or 0)
                if pr > 0:
                    prices.append(pr)
            except Exception:
                continue
    avg_price = sum(prices) / len(prices) if prices else baseline_aov_eur(vertical)
    cvr_pct = baseline_cvr_pct(vertical)
    assumed_visitors = 1000  # demo placeholder — clearly labelled as estimate
    estimated_orders = round(assumed_visitors * cvr_pct / 100.0)
    estimated_mrr = round(estimated_orders * avg_price, 2)
    estimated_recovery = round(estimated_mrr * 0.15, 2)
    return {
        "avg_price_eur": round(avg_price, 2),
        "vertical_baseline_cvr_pct": cvr_pct,
        "assumed_monthly_visitors": assumed_visitors,
        "estimated_monthly_orders": estimated_orders,
        "estimated_monthly_revenue_eur": estimated_mrr,
        "estimated_recovery_eur": estimated_recovery,
    }


def preview(input_url: str) -> dict:
    """
    Public entry point. Validates domain, fetches products, classifies,
    and returns a preview narrative + estimated ROI.

    Always returns a dict with `ok` bool. Never raises.
    """
    domain = normalize_domain(input_url)
    if not domain:
        return {"ok": False, "error": "invalid_domain"}

    if not _check_rate_limit(domain):
        return {"ok": False, "error": "rate_limited", "retry_after_s": _RATE_LIMIT_TTL}

    products = fetch_products(domain)
    if not products:
        return {
            "ok": False,
            "error": "no_products_found",
            "domain": domain,
            "hint": "Make sure the store is public and the /products.json endpoint is reachable.",
        }

    vertical, confidence, scores = _classify_from_products(products)
    from app.services.vertical_prompt_pack import get_profile
    profile = get_profile(vertical)
    roi = _estimate_recovery(products, vertical)

    narrative = (
        f"Detected: {profile.display_name}. "
        f"We scanned {len(products)} products and your range averages "
        f"€{roi['avg_price_eur']}. At your vertical's typical "
        f"{roi['vertical_baseline_cvr_pct']}% conversion rate, "
        f"a 1k-visitor month would generate ~{roi['estimated_monthly_orders']} orders "
        f"≈ €{roi['estimated_monthly_revenue_eur']}/mo. "
        f"Hedge Spark typically recovers ~15% of that "
        f"(€{roi['estimated_recovery_eur']}/mo) within the first 30 days "
        f"— this is just an estimate, the real number is bigger or smaller "
        f"depending on traffic and execution."
    )

    return {
        "ok": True,
        "domain": domain,
        "products_scanned": len(products),
        "vertical": vertical,
        "vertical_display": profile.display_name,
        "vertical_confidence": confidence,
        "roi_estimate": roi,
        "narrative": narrative,
        "next_step_cta": "Connect your store to see your real numbers in 60 seconds.",
        "generated_at": _now().isoformat(),
    }
