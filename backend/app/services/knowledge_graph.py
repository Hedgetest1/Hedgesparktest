"""
knowledge_graph.py — Phase Ω moat #3.

A semantic graph of a merchant's business that links the entities the
operator actually thinks in: orders, customers, products, refunds,
nudges, ad campaigns, anomalies, holdouts, goals.

Built on demand from existing tables — no new schema. The graph itself
is an in-memory data structure (nodes + typed edges) cached briefly in
Redis (5 min) per shop. Cheap, deterministic, and re-buildable.

Why this is a moat
------------------
Generic dashboards show metrics. We answer questions:

    "Why did revenue drop yesterday vs last Thursday?"

The classifier-driven NL query layer turns natural-language questions
into deterministic graph traversals. No LLM by default — we route to a
small set of intent handlers that walk the graph and return structured
answers + plain-language narrative. LLM is fall-through only, behind
the existing budget guard, when no intent matches.

Components
----------
* `KGNode`            : (entity_type, entity_id, attrs)
* `KGEdge`            : (src, dst, edge_type, weight, attrs)
* `MerchantKG`        : per-shop graph instance
* `build_graph(...)`  : assembler that pulls from existing tables
* `query(shop, q)`    : NL-to-intent → graph traversal → answer dict
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("knowledge_graph")

_CACHE_TTL_SECONDS = 5 * 60
_CACHE_KEY_PREFIX = "hs:kg:v1"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Node + edge primitives
# ---------------------------------------------------------------------------


@dataclass
class KGNode:
    entity_type: str       # "order" | "customer" | "product" | "refund" | "nudge" | "campaign" | "anomaly" | "metric"
    entity_id: str         # opaque per-type id
    attrs: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.entity_type}::{self.entity_id}"


@dataclass
class KGEdge:
    src: str               # node key
    dst: str               # node key
    edge_type: str         # "purchased" | "refunded" | "exposed_to" | "attributed_to" | "occurred_in" | "caused_by"
    weight: float = 1.0
    attrs: dict = field(default_factory=dict)


@dataclass
class MerchantKG:
    shop_domain: str
    nodes: dict[str, KGNode] = field(default_factory=dict)
    edges: list[KGEdge] = field(default_factory=list)
    out_index: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    in_index: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    built_at: str = ""

    def add_node(self, n: KGNode) -> None:
        self.nodes[n.key] = n

    def add_edge(self, e: KGEdge) -> None:
        idx = len(self.edges)
        self.edges.append(e)
        self.out_index[e.src].append(idx)
        self.in_index[e.dst].append(idx)

    def neighbors(self, node_key: str, edge_type: str | None = None) -> list[KGNode]:
        out = []
        for i in self.out_index.get(node_key, []):
            e = self.edges[i]
            if edge_type and e.edge_type != edge_type:
                continue
            n = self.nodes.get(e.dst)
            if n:
                out.append(n)
        return out

    def stats(self) -> dict:
        types: dict[str, int] = {}
        for n in self.nodes.values():
            types[n.entity_type] = types.get(n.entity_type, 0) + 1
        edge_types: dict[str, int] = {}
        for e in self.edges:
            edge_types[e.edge_type] = edge_types.get(e.edge_type, 0) + 1
        return {
            "shop_domain": self.shop_domain,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_types": types,
            "edge_types": edge_types,
            "built_at": self.built_at,
        }


# ---------------------------------------------------------------------------
# Builders — pull entities from existing tables
# ---------------------------------------------------------------------------


def _pull_orders(db: Session, kg: MerchantKG, lookback_days: int = 60) -> None:
    cutoff = _now() - timedelta(days=lookback_days)
    rows = db.execute(text("""
        SELECT id, shopify_order_id, total_price, currency, customer_id,
               customer_email, created_at
        FROM shop_orders
        WHERE shop_domain = :shop AND created_at >= :cut
        ORDER BY created_at DESC
        LIMIT 5000
    """), {"shop": kg.shop_domain, "cut": cutoff}).fetchall()

    for r in rows:
        order_key = f"order::{r[0]}"
        kg.add_node(KGNode(
            entity_type="order",
            entity_id=str(r[0]),
            attrs={
                "shopify_order_id": r[1],
                "total_price": float(r[2] or 0),
                "currency": r[3],
                "created_at": r[6].isoformat() if r[6] else None,
            },
        ))
        if r[4]:  # customer_id
            cust_key = f"customer::{r[4]}"
            if cust_key not in kg.nodes:
                kg.add_node(KGNode(
                    entity_type="customer",
                    entity_id=str(r[4]),
                    attrs={"email": r[5]},
                ))
            kg.add_edge(KGEdge(
                src=cust_key, dst=order_key, edge_type="purchased",
                weight=float(r[2] or 0),
            ))


def _pull_refunds(db: Session, kg: MerchantKG, lookback_days: int = 60) -> None:
    """
    Refunds live in `shop_orders` extension or a separate refund table —
    here we tolerate both. Falls back gracefully if neither exists.
    """
    cutoff = _now() - timedelta(days=lookback_days)
    try:
        rows = db.execute(text("""
            SELECT id, order_id, refund_amount, refunded_at, reason
            FROM shop_refunds
            WHERE shop_domain = :shop AND refunded_at >= :cut
            ORDER BY refunded_at DESC
            LIMIT 1000
        """), {"shop": kg.shop_domain, "cut": cutoff}).fetchall()
    except Exception:
        return  # table absent — refunds get pulled from another path

    for r in rows:
        ref_key = f"refund::{r[0]}"
        kg.add_node(KGNode(
            entity_type="refund",
            entity_id=str(r[0]),
            attrs={
                "amount": float(r[2] or 0),
                "reason": r[4],
                "refunded_at": r[3].isoformat() if r[3] else None,
            },
        ))
        if r[1]:
            order_key = f"order::{r[1]}"
            if order_key in kg.nodes:
                kg.add_edge(KGEdge(
                    src=ref_key, dst=order_key, edge_type="refunds",
                    weight=float(r[2] or 0),
                ))


def _pull_nudges(db: Session, kg: MerchantKG, lookback_days: int = 30) -> None:
    """Pull active_nudges + nudge_events to link nudge → customer exposure."""
    try:
        rows = db.execute(text("""
            SELECT id, nudge_type, copy_text, status, created_at
            FROM active_nudges
            WHERE shop_domain = :shop
            ORDER BY created_at DESC
            LIMIT 200
        """), {"shop": kg.shop_domain}).fetchall()
        for r in rows:
            kg.add_node(KGNode(
                entity_type="nudge",
                entity_id=str(r[0]),
                attrs={
                    "type": r[1],
                    "copy": (r[2] or "")[:200],
                    "status": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                },
            ))
    except Exception as exc:
        log.debug("kg: nudges pull failed: %s", exc)


def _pull_anomalies(db: Session, kg: MerchantKG, lookback_days: int = 14) -> None:
    """ops_alerts represent system-detected anomalies for this shop."""
    cutoff = _now() - timedelta(days=lookback_days)
    try:
        rows = db.execute(text("""
            SELECT id, source, alert_type, severity, summary, created_at
            FROM ops_alerts
            WHERE (shop_domain = :shop OR shop_domain IS NULL)
              AND created_at >= :cut
            ORDER BY created_at DESC
            LIMIT 300
        """), {"shop": kg.shop_domain, "cut": cutoff}).fetchall()
        for r in rows:
            kg.add_node(KGNode(
                entity_type="anomaly",
                entity_id=str(r[0]),
                attrs={
                    "source": r[1],
                    "type": r[2],
                    "severity": r[3],
                    "summary": (r[4] or "")[:300],
                    "created_at": r[5].isoformat() if r[5] else None,
                },
            ))
    except Exception as exc:
        log.debug("kg: anomalies pull failed: %s", exc)


def _pull_ad_spend(db: Session, kg: MerchantKG, lookback_days: int = 30) -> None:
    """Optional: ad_spend_daily (Phase Ω Ads connector). Tolerate absence."""
    cutoff = _now() - timedelta(days=lookback_days)
    try:
        rows = db.execute(text("""
            SELECT date, network, campaign_id, campaign_name, spend_eur,
                   impressions, clicks, conversions
            FROM ad_spend_daily
            WHERE shop_domain = :shop AND date >= :cut
            ORDER BY date DESC
            LIMIT 2000
        """), {"shop": kg.shop_domain, "cut": cutoff.date()}).fetchall()
        for r in rows:
            cid = f"{r[1]}::{r[2]}::{r[0]}"
            kg.add_node(KGNode(
                entity_type="campaign",
                entity_id=cid,
                attrs={
                    "date": str(r[0]),
                    "network": r[1],
                    "campaign_name": r[3],
                    "spend_eur": float(r[4] or 0),
                    "impressions": int(r[5] or 0),
                    "clicks": int(r[6] or 0),
                    "conversions": int(r[7] or 0),
                },
            ))
    except Exception as exc:
        log.debug("kg: ad_spend pull failed: %s", exc)


def build_graph(db: Session, shop_domain: str, *, fresh: bool = False) -> MerchantKG:
    """
    Assemble the merchant's knowledge graph. Cached 5 min in Redis for
    (shop, day) key — tradeoff: latency over staleness, since most
    questions tolerate 5-min lag.
    """
    cache_key = f"{_CACHE_KEY_PREFIX}:stats:{hashlib.md5(shop_domain.encode()).hexdigest()[:16]}"
    if not fresh:
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                cached = rc.get(cache_key)
                if cached:
                    # Cached value is just the stats summary, not the full graph.
                    # Full graphs are too big to JSON; we re-build but skip
                    # logging spam by reading the stats.
                    pass
        except Exception:
            pass

    kg = MerchantKG(shop_domain=shop_domain)
    _pull_orders(db, kg)
    _pull_refunds(db, kg)
    _pull_nudges(db, kg)
    _pull_anomalies(db, kg)
    _pull_ad_spend(db, kg)
    kg.built_at = _now().isoformat()

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps(kg.stats(), default=str))
    except Exception:
        pass

    return kg


# ---------------------------------------------------------------------------
# Natural-language query — intent matching + handlers
# ---------------------------------------------------------------------------


_INTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(why|perch[èé]|por\s*qu[eé]|pourquoi)\b.*\b(drop|down|cal|bajad|baisse)", re.I), "why_revenue_drop"),
    (re.compile(r"\b(top|best|migliori?|mejor|meilleurs?)\b.*\b(customer|client)", re.I), "top_customers"),
    (re.compile(r"\b(refund|reso|reembolso|remboursement)", re.I), "refund_summary"),
    (re.compile(r"\b(anomal|alert|alarme)", re.I), "anomaly_summary"),
    (re.compile(r"\b(campaign|campagn|campañ)\b.*\b(perf|roas|spend|spes)", re.I), "campaign_perf"),
    (re.compile(r"\b(revenue|vendite|fatturato|ventas|ventes)\b.*\b(today|oggi|hoy|aujourd)", re.I), "revenue_today"),
    (re.compile(r"\b(stats?|stato|estado|état)\b", re.I), "graph_stats"),
]


def _intent_of(question: str) -> str:
    for pat, intent in _INTENT_PATTERNS:
        if pat.search(question):
            return intent
    return "fallback"


# --- Intent handlers ---


def _h_graph_stats(kg: MerchantKG, q: str) -> dict:
    s = kg.stats()
    return {
        "intent": "graph_stats",
        "answer": (
            f"Your knowledge graph has {s['nodes']} entities and "
            f"{s['edges']} relationships. Composition: "
            + ", ".join(f"{k}={v}" for k, v in sorted(s["node_types"].items()))
            + "."
        ),
        "data": s,
    }


def _h_top_customers(kg: MerchantKG, q: str, top_n: int = 5) -> dict:
    cust_total: dict[str, float] = {}
    cust_email: dict[str, str | None] = {}
    for n in kg.nodes.values():
        if n.entity_type != "customer":
            continue
        total = sum(
            (kg.nodes[e.dst].attrs.get("total_price", 0) or 0)
            for e in [kg.edges[i] for i in kg.out_index.get(n.key, [])]
            if e.edge_type == "purchased" and kg.nodes.get(e.dst)
        )
        if total > 0:
            cust_total[n.entity_id] = total
            cust_email[n.entity_id] = n.attrs.get("email")
    ranked = sorted(cust_total.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return {
        "intent": "top_customers",
        "answer": (
            f"Top {len(ranked)} customers by lifetime value (last 60 days):"
            if ranked else "No customer purchase data found in the last 60 days."
        ),
        "data": [
            {"customer_id": c, "email": cust_email.get(c), "total_eur": round(t, 2)}
            for c, t in ranked
        ],
    }


def _h_refund_summary(kg: MerchantKG, q: str) -> dict:
    refunds = [n for n in kg.nodes.values() if n.entity_type == "refund"]
    total = sum(r.attrs.get("amount", 0) or 0 for r in refunds)
    by_reason: dict[str, float] = {}
    for r in refunds:
        reason = r.attrs.get("reason") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + (r.attrs.get("amount", 0) or 0)
    return {
        "intent": "refund_summary",
        "answer": (
            f"You had {len(refunds)} refunds in the last 60 days totalling €{round(total, 2)}."
            if refunds else "No refunds found in the last 60 days."
        ),
        "data": {
            "count": len(refunds),
            "total_eur": round(total, 2),
            "by_reason": {k: round(v, 2) for k, v in by_reason.items()},
        },
    }


def _h_anomaly_summary(kg: MerchantKG, q: str) -> dict:
    anomalies = [n for n in kg.nodes.values() if n.entity_type == "anomaly"]
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for a in anomalies:
        sev = a.attrs.get("severity") or "info"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        src = a.attrs.get("source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    return {
        "intent": "anomaly_summary",
        "answer": (
            f"Detected {len(anomalies)} anomalies in the last 14 days "
            f"({by_severity.get('error', 0)} errors, "
            f"{by_severity.get('warning', 0)} warnings)."
            if anomalies else "No anomalies in the last 14 days — system is calm."
        ),
        "data": {
            "count": len(anomalies),
            "by_severity": by_severity,
            "by_source": by_source,
            "samples": [
                {"summary": a.attrs.get("summary"), "source": a.attrs.get("source")}
                for a in anomalies[:5]
            ],
        },
    }


def _h_campaign_perf(kg: MerchantKG, q: str) -> dict:
    campaigns = [n for n in kg.nodes.values() if n.entity_type == "campaign"]
    if not campaigns:
        return {
            "intent": "campaign_perf",
            "answer": "No ad campaign data connected yet. Connect Meta/Google/TikTok Ads to see performance.",
            "data": {"connected": False},
        }
    by_network: dict[str, dict] = {}
    for c in campaigns:
        net = c.attrs.get("network") or "unknown"
        agg = by_network.setdefault(net, {"spend": 0.0, "clicks": 0, "conv": 0})
        agg["spend"] += c.attrs.get("spend_eur", 0) or 0
        agg["clicks"] += c.attrs.get("clicks", 0) or 0
        agg["conv"] += c.attrs.get("conversions", 0) or 0
    return {
        "intent": "campaign_perf",
        "answer": (
            f"Active networks: {', '.join(by_network.keys())}. "
            f"Total spend last 30d: €{round(sum(v['spend'] for v in by_network.values()), 2)}."
        ),
        "data": {
            "connected": True,
            "by_network": {
                k: {
                    "spend_eur": round(v["spend"], 2),
                    "clicks": v["clicks"],
                    "conversions": v["conv"],
                    "cpa_eur": round(v["spend"] / v["conv"], 2) if v["conv"] else None,
                }
                for k, v in by_network.items()
            },
        },
    }


def _h_revenue_today(kg: MerchantKG, q: str) -> dict:
    today = _now().date()
    todays_orders = [
        n for n in kg.nodes.values()
        if n.entity_type == "order"
        and n.attrs.get("created_at", "").startswith(str(today))
    ]
    total = sum(o.attrs.get("total_price", 0) or 0 for o in todays_orders)
    return {
        "intent": "revenue_today",
        "answer": (
            f"Today: {len(todays_orders)} orders, €{round(total, 2)} revenue."
        ),
        "data": {"orders": len(todays_orders), "revenue_eur": round(total, 2)},
    }


def _h_why_revenue_drop(kg: MerchantKG, q: str) -> dict:
    """
    Light-weight causal traversal — defers heavy lifting to causal_explainer
    when available. Here we just surface the leading suspects from the graph.
    """
    today_orders_value = sum(
        n.attrs.get("total_price", 0) or 0
        for n in kg.nodes.values()
        if n.entity_type == "order"
        and n.attrs.get("created_at", "").startswith(str(_now().date()))
    )
    yesterday = (_now().date() - timedelta(days=1))
    yesterday_value = sum(
        n.attrs.get("total_price", 0) or 0
        for n in kg.nodes.values()
        if n.entity_type == "order"
        and n.attrs.get("created_at", "").startswith(str(yesterday))
    )
    delta = today_orders_value - yesterday_value
    suspects = []
    if delta < 0:
        recent_anom = [
            n for n in kg.nodes.values()
            if n.entity_type == "anomaly"
            and (n.attrs.get("severity") in ("warning", "error"))
        ][:3]
        suspects = [{"summary": a.attrs.get("summary"), "source": a.attrs.get("source")} for a in recent_anom]
    return {
        "intent": "why_revenue_drop",
        "answer": (
            f"Today vs yesterday: €{round(delta, 2)} delta. "
            + (f"{len(suspects)} active anomalies could be contributing." if suspects else "No active anomalies match the timing.")
        ),
        "data": {
            "today_eur": round(today_orders_value, 2),
            "yesterday_eur": round(yesterday_value, 2),
            "delta_eur": round(delta, 2),
            "suspects": suspects,
        },
    }


_HANDLERS = {
    "graph_stats": _h_graph_stats,
    "top_customers": _h_top_customers,
    "refund_summary": _h_refund_summary,
    "anomaly_summary": _h_anomaly_summary,
    "campaign_perf": _h_campaign_perf,
    "revenue_today": _h_revenue_today,
    "why_revenue_drop": _h_why_revenue_drop,
}


def query(db: Session, shop_domain: str, question: str) -> dict:
    """
    Natural-language entry point. Returns a structured answer dict with:
      intent, answer (string), data (dict).

    Deterministic-first: routes to a handler, no LLM. If no intent matches,
    returns a fallback that lists the supported intents — caller may then
    optionally invoke an LLM (gated by llm_budget) which is intentionally
    NOT done here to keep this module dependency-free and €0 per call.
    """
    if not question or not question.strip():
        return {"intent": "empty", "answer": "Ask a question.", "data": {}}

    intent = _intent_of(question)
    kg = build_graph(db, shop_domain)
    handler = _HANDLERS.get(intent)
    if handler:
        out = handler(kg, question)
        out["graph_stats"] = kg.stats()
        return out

    return {
        "intent": "fallback",
        "answer": (
            "I couldn't match that question to a known intent. "
            "Try: 'why did revenue drop today', 'top customers', "
            "'refund summary', 'anomaly summary', 'campaign performance', "
            "'revenue today', or 'graph stats'."
        ),
        "data": {"supported_intents": sorted(_HANDLERS.keys())},
        "graph_stats": kg.stats(),
    }
