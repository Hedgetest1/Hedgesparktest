"""
Phase Ω killer #2 — causal explainer tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.product import Product
from app.models.shop_order import ShopOrder
from app.services.causal_explainer import (
    HypothesisDef,
    CausalHypothesis,
    _CATALOG,
    _format_narrative,
    explain,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


SHOP = "causal-test.myshopify.com"


def test_catalog_has_expected_hypotheses():
    assert "ad_creative_fatigue" in _CATALOG
    assert "competitor_promo" in _CATALOG
    assert "system_distress" in _CATALOG


def test_hypothesis_def_dataclass_defaults():
    h = HypothesisDef(
        label="x", supporting_signals=("a",), suppressing_signals=(),
        narrative_template="t", recommended_action="r",
    )
    assert h.base_prior == 0.10


def test_format_narrative_substitutes():
    template = "Drop was {revenue_drop_24h_delta_pct}%"
    signals = {"revenue_drop_24h": {"delta_pct": -22.5}}
    out = _format_narrative(template, signals)
    assert out == "Drop was -22.5%"


def test_format_narrative_no_substitution():
    out = _format_narrative("plain text", {})
    assert out == "plain text"


def test_format_narrative_unresolved_left_alone():
    out = _format_narrative("{nothing_here}", {})
    assert "{nothing_here}" in out


def test_causal_hypothesis_to_dict():
    c = CausalHypothesis(
        label="x", confidence=0.5, score=1.0, prior=0.3,
        evidence=["s1"], suppressors=[], narrative="n", recommended_action="r",
    )
    d = c.to_dict()
    assert d["label"] == "x"
    assert d["confidence"] == 0.5


def _plant_revenue_drop(db, shop):
    """Plant a strong revenue drop scenario."""
    db.add(ShopOrder(
        shop_domain=shop, shopify_order_id=f"gid://{shop}/today",
        total_price=5.0, currency="EUR", line_items=[],
        created_at=_now() - timedelta(hours=2),
    ))
    for d in range(1, 8):
        db.add(ShopOrder(
            shop_domain=shop, shopify_order_id=f"gid://{shop}/d{d}",
            total_price=200.0, currency="EUR", line_items=[],
            created_at=_now() - timedelta(days=d, hours=12),
        ))
    db.flush()


def _add_beauty_products(db, shop):
    for i, t in enumerate(["Lipstick Red", "Mascara Volume", "Crema Idratante"]):
        db.add(Product(
            shopify_product_id=f"gid://{shop}/p/{i}",
            title=t, price=20.0, currency="EUR", shop_domain=shop,
        ))
    db.flush()


def test_explain_returns_hypotheses_when_signals_fire(db):
    _add_beauty_products(db, SHOP)
    _plant_revenue_drop(db, SHOP)
    out = explain(db, SHOP)
    assert out["shop_domain"] == SHOP
    assert "vertical" in out
    assert "hypotheses" in out
    assert isinstance(out["hypotheses"], list)
    # With a revenue drop, at least one hypothesis should fire
    assert len(out["hypotheses"]) >= 1


def test_explain_quiet_returns_healthy(db):
    # Two guards for the shared test DB:
    # 1. Remove _signal_anomaly_volume (reads global NULL-shop alerts).
    # 2. Bust fuse() Redis cache for this shop before invoking explain().
    from unittest.mock import patch
    import hashlib
    import app.services.anomaly_fusion as _af
    from app.core.redis_client import _client as _redis_client
    shop = "quiet-shop.myshopify.com"
    rc = _redis_client()
    if rc is not None:
        rc.delete(f"hs:fusion:v1:{hashlib.md5(shop.encode()).hexdigest()[:16]}")
    clean = tuple(f for f in _af._SIGNAL_FUNCS if f.__name__ != "_signal_anomaly_volume")
    with patch.object(_af, "_SIGNAL_FUNCS", clean):
        out = explain(db, shop)
    assert out["hypotheses"] == []
    assert "healthy" in out["narrative"].lower()


def test_explain_uses_vertical_priors(db):
    """A beauty shop should get beauty-specific priors weighting."""
    unique_shop = "causal-beauty-priors.myshopify.com"
    _add_beauty_products(db, unique_shop)
    _plant_revenue_drop(db, unique_shop)
    # Prime classifier cache fresh — Redis may hold a stale 'other' from earlier tests
    from app.services.vertical_classifier import classify_shop
    classify_shop(db, unique_shop, force=True)
    out = explain(db, unique_shop)
    assert out["vertical"] == "beauty"


def test_explain_top_hypothesis_has_action(db):
    _add_beauty_products(db, SHOP)
    _plant_revenue_drop(db, SHOP)
    out = explain(db, SHOP)
    if out["hypotheses"]:
        assert out["next_action"] is not None
        assert out["hypotheses"][0]["rank"] == 1


def test_explain_confidences_sum_close_to_one(db):
    _add_beauty_products(db, SHOP)
    _plant_revenue_drop(db, SHOP)
    out = explain(db, SHOP)
    if out["hypotheses"]:
        total = sum(h["confidence"] for h in out["hypotheses"])
        assert 0.99 <= total <= 1.01


def test_api_causal_endpoint(client, auth_a):
    r = client.get("/pro/causal/explain", cookies=auth_a)
    assert r.status_code == 200
    body = r.json()
    assert "hypotheses" in body
    assert "narrative" in body
