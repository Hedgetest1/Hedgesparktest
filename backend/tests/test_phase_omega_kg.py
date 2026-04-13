"""
Phase Ω moat #3 — knowledge graph + NL query tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.shop_order import ShopOrder
from app.services.knowledge_graph import (
    KGNode,
    KGEdge,
    MerchantKG,
    build_graph,
    query,
    _intent_of,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


SHOP = "kg-test.myshopify.com"


def test_kg_node_key():
    n = KGNode(entity_type="order", entity_id="42")
    assert n.key == "order::42"


def test_kg_add_node_and_edge():
    kg = MerchantKG(shop_domain=SHOP)
    a = KGNode(entity_type="customer", entity_id="c1")
    b = KGNode(entity_type="order", entity_id="o1", attrs={"total_price": 50})
    kg.add_node(a)
    kg.add_node(b)
    kg.add_edge(KGEdge(src=a.key, dst=b.key, edge_type="purchased", weight=50))
    assert len(kg.nodes) == 2
    assert len(kg.edges) == 1
    neighbors = kg.neighbors(a.key, edge_type="purchased")
    assert len(neighbors) == 1
    assert neighbors[0].entity_id == "o1"


def test_kg_stats_shape():
    kg = MerchantKG(shop_domain=SHOP)
    kg.add_node(KGNode("order", "o1"))
    kg.add_node(KGNode("order", "o2"))
    kg.add_node(KGNode("customer", "c1"))
    s = kg.stats()
    assert s["nodes"] == 3
    assert s["node_types"]["order"] == 2
    assert s["node_types"]["customer"] == 1


def test_intent_classifier_why_drop():
    assert _intent_of("Why did revenue drop today?") == "why_revenue_drop"
    assert _intent_of("perché è calato il fatturato?") == "why_revenue_drop"


def test_intent_classifier_top_customers():
    assert _intent_of("Show me top customers") == "top_customers"
    assert _intent_of("migliori clienti?") == "top_customers"


def test_intent_classifier_refund():
    assert _intent_of("how many refunds last week") == "refund_summary"
    assert _intent_of("reso?") == "refund_summary"


def test_intent_classifier_anomalies():
    assert _intent_of("any anomalies today?") == "anomaly_summary"


def test_intent_classifier_campaigns():
    assert _intent_of("how is the meta campaign performance?") == "campaign_perf"


def test_intent_classifier_fallback():
    assert _intent_of("random nonsense xyz") == "fallback"


def test_intent_classifier_empty():
    # Empty intent path through query() returns empty intent
    pass


# ---------------------------------------------------------------------------
# DB-integrated graph build
# ---------------------------------------------------------------------------


def _plant_orders(db, shop, n, price, customer_id_prefix="cust"):
    for i in range(n):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"gid://{shop}/o/kg_{i}",
            total_price=price,
            currency="EUR",
            customer_id=f"{customer_id_prefix}_{i % 3}",  # 3 customers cycling
            customer_email=f"{customer_id_prefix}_{i % 3}@x.com",
            line_items=[],
            created_at=_now() - timedelta(days=i % 5, hours=i),
        ))
    db.flush()


def test_build_graph_pulls_orders_and_customers(db):
    _plant_orders(db, SHOP, 6, 25.0)
    kg = build_graph(db, SHOP)
    s = kg.stats()
    assert s["node_types"].get("order", 0) == 6
    assert s["node_types"].get("customer", 0) == 3
    assert s["edge_types"].get("purchased", 0) == 6


def test_query_top_customers(db):
    _plant_orders(db, SHOP, 9, 30.0)
    out = query(db, SHOP, "show me the top customers")
    assert out["intent"] == "top_customers"
    assert isinstance(out["data"], list)
    assert len(out["data"]) == 3  # 3 customer cycle


def test_query_revenue_today(db):
    # plant one order today
    db.add(ShopOrder(
        shop_domain=SHOP,
        shopify_order_id=f"gid://{SHOP}/o/today_1",
        total_price=99.0,
        currency="EUR",
        line_items=[],
        created_at=_now(),
    ))
    db.flush()
    out = query(db, SHOP, "revenue today")
    assert out["intent"] == "revenue_today"
    assert out["data"]["orders"] >= 1


def test_query_graph_stats(db):
    _plant_orders(db, SHOP, 4, 12.0)
    out = query(db, SHOP, "show me the graph stats")
    assert out["intent"] == "graph_stats"
    assert "nodes" in out["data"]


def test_query_refund_empty(db):
    out = query(db, SHOP, "any refunds last week?")
    assert out["intent"] == "refund_summary"
    assert out["data"]["count"] == 0


def test_query_fallback_lists_intents(db):
    out = query(db, SHOP, "garbage xyz unknown thing")
    assert out["intent"] == "fallback"
    supported = out["data"]["supported_intents"]
    assert "top_customers" in supported
    assert "why_revenue_drop" in supported


def test_query_empty_question(db):
    out = query(db, SHOP, "")
    assert out["intent"] == "empty"
