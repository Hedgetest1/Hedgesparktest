"""Contract: the spatial-heatmap Redis key is canonicalized
IDENTICALLY on the WRITE side (track._bump_heatmap_bucket) and the
READ side (heatmap.get_spatial_heatmap), BY CONSTRUCTION — not by the
coincidence of the dashboard passing a pre-normalized product_url.

Born 2026-05-18 (independent adversarial audit RISK #3). The WRITE
callers (track_event / track_event_batch) pass
`normalize_product_url(product_url) or page_url` to
`_bump_heatmap_bucket`, which hashes that argument verbatim. The READ
path previously hashed the `product_url` query param RAW. A product
URL carrying a variant/query (/products/x?variant=99) is stored under
md5("/products/x") on write but was looked up under
md5("…?variant=99") on read ⟹ different key ⟹ buckets invisible.

⚠️ CORRECTION (2026-05-18, commit AFTER 97d5162): an EARLIER version
of this docstring + commit 97d5162's body claimed
"`/pro/heatmap/spatial` has NO dashboard consumer (grep == 0) →
defensive only". THAT WAS FALSE — produced by a cwd-broken grep
(`grep dashboard/src` run from backend/ → non-existent path → 0). The
truth, verified with the correct path: `HeatmapCard.tsx:205` calls
`apiClient.GET("/pro/heatmap/spatial")`, rendered at
`app/app/page.tsx:3576/4534`. The spatial heatmap IS a SHIPPED,
consumed, merchant-facing feature. RISK #3 is therefore a REAL
LIVE-IMPACT fix: a product URL with a variant/query was written under
md5("/products/x") but read under md5("…?variant=…") ⟹ the rendered
HeatmapCard showed an EMPTY grid for a populated product. 36e86d8's
"shipped feature was starved" framing was correct; the 97d5162
"overclaim correction" was the actual error (verification claimed,
not done — the §22.7 failure in its purest form).

This test is the non-vacuous proof: write the canonical form the
caller actually passes, read a DIFFERENT raw form of the same
product — buckets MUST come back. Pre-fix → total_events=0.
"""
from __future__ import annotations

from unittest.mock import patch

from app.api.track import _bump_heatmap_bucket
from app.api.heatmap import get_spatial_heatmap
from app.core.url_utils import normalize_product_url


class _HashFake:
    """Minimal HASH-capable fake Redis shared by write + read."""
    def __init__(self) -> None:
        self.h: dict = {}

    def hincrby(self, key, field, n):
        self.h.setdefault(key, {})
        self.h[key][field] = self.h[key].get(field, 0) + n
        return self.h[key][field]

    def expire(self, key, ttl):
        return True

    def hgetall(self, key):
        return dict(self.h.get(key, {}))


def test_write_variant_url_read_normalized_url_coincide():
    fake = _HashFake()
    shop = "symshop.myshopify.com"
    raw = "https://symshop.myshopify.com/products/widget?variant=99"
    with patch("app.core.redis_client._client", return_value=fake):
        # WRITE exactly as track_event/track_event_batch do: the caller
        # passes normalize_product_url(...) — _bump_heatmap_bucket
        # hashes that arg verbatim (it does NOT normalize internally).
        _bump_heatmap_bucket(
            shop_domain=shop,
            url=normalize_product_url(raw),          # caller-normalized
            event_type="click",
            x_pct=55.0,
            y_pct=55.0,
        )
        # READ: a DIFFERENT raw form of the SAME product (the dashboard
        # has no contract to pre-normalize). Post-fix the read
        # normalizes too ⟹ keys coincide.
        out = get_spatial_heatmap(
            product_url=raw,
            event_type="click",
            shop=shop,
        )
    assert out["total_events"] == 1, (
        f"write/read key asymmetry — heatmap silently empty "
        f"(total_events={out['total_events']})"
    )
    assert {"x": 5, "y": 5, "count": 1} in out["buckets"]


def test_write_normalized_read_variant_also_coincide():
    """Symmetry holds in BOTH directions (write canonical, read raw)."""
    fake = _HashFake()
    shop = "symshop2.myshopify.com"
    with patch("app.core.redis_client._client", return_value=fake):
        _bump_heatmap_bucket(
            shop_domain=shop,
            url="/products/widget",                       # already canonical
            event_type="mousemove",
            x_pct=12.0,
            y_pct=88.0,
        )
        out = get_spatial_heatmap(
            product_url="https://symshop2.myshopify.com/products/widget?ref=ig",
            event_type="move",
            shop=shop,
        )
    assert out["total_events"] == 1
    assert {"x": 1, "y": 8, "count": 1} in out["buckets"]
