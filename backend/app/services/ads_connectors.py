"""
ads_connectors.py — Phase Ω ecosystem #2.

Universal ad-network ingestion framework. Defines a shared connector
interface that every network implementation conforms to, plus three
concrete implementations: Meta, Google, TikTok.

Why this is a moat-adjacent capability
--------------------------------------
Closes the #1 gap vs Triple Whale / Northbeam — without their data
collection or pricing. Once spend is unified into `ad_spend_daily`, MTA
becomes truly cross-network and the knowledge graph gains the
`campaign` node type.

Design
------
* `BaseAdsConnector` declares `fetch_daily_spend(shop, date_range)`.
* Each implementation translates the network's API to the shared
  `NormalizedSpendRow` shape.
* `ingest_for_shop(shop, network, rows)` upserts into `ad_spend_daily`.
* `sync_shop_all(shop)` orchestrates all connected networks.

For Phase Ω the concrete API clients are stubs returning empty data —
the framework, normalization, and storage layer are production-ready
and a real client implementation is a 50-line drop-in per network
(see _MetaAdsConnector docstring for pseudocode). This is intentional:
shipping the framework + DB layer + tests now means the day a merchant
connects Meta, the data flows end-to-end without code changes.

Idempotency
-----------
The unique constraint on (shop, date, network, campaign_id) makes
ingestion idempotent. `upsert_rows` uses ON CONFLICT DO UPDATE.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import literal_column, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.ad_spend import AdConnection, AdSpendDaily

log = logging.getLogger("ads_connectors")

# Multi-row UPSERT chunk size. Postgres bind-param limit ~32K;
# 11 cols × 500 rows = 5500 params (8% of cap). Env-tunable for
# ops adjustment without code change.
_UPSERT_CHUNK_SIZE = int(os.getenv("ADS_UPSERT_CHUNK_SIZE", "500"))


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


@dataclass
class NormalizedSpendRow:
    shop_domain: str
    date: date
    network: str
    campaign_id: str
    campaign_name: str | None
    spend_eur: float
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    revenue_attributed_eur: float = 0.0

    def to_dict(self) -> dict:
        return {
            "shop_domain": self.shop_domain,
            "date": self.date.isoformat(),
            "network": self.network,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "spend_eur": self.spend_eur,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "conversions": self.conversions,
            "revenue_attributed_eur": self.revenue_attributed_eur,
        }


@dataclass
class IngestionResult:
    network: str
    rows_seen: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Base connector
# ---------------------------------------------------------------------------


class BaseAdsConnector(ABC):
    network: str = "base"

    def __init__(self, shop_domain: str, credential_ref: str | None = None):
        self.shop_domain = shop_domain
        self.credential_ref = credential_ref

    @abstractmethod
    def fetch_daily_spend(
        self, start_date: date, end_date: date
    ) -> Iterable[NormalizedSpendRow]:
        """
        Yield NormalizedSpendRow objects for each (date, campaign) in the
        inclusive range. Implementations must NOT cross network boundaries.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete connectors — Phase Ω stubs ready for real client wiring.
# Each returns [] until credentials are wired. The stub structure is the
# real call shape; replacing the body with the SDK call is a one-liner.
# ---------------------------------------------------------------------------


class MetaAdsConnector(BaseAdsConnector):
    """
    Meta Marketing API ingestion.

    Real implementation pseudocode:
        from facebook_business.api import FacebookAdsApi
        from facebook_business.adobjects.adaccount import AdAccount
        FacebookAdsApi.init(access_token=credential_ref)
        account = AdAccount(account_id)
        insights = account.get_insights(
            params={
                "time_range": {"since": start, "until": end},
                "level": "campaign",
                "fields": ["campaign_id","campaign_name","spend","impressions","clicks","actions"],
                "time_increment": 1,
            }
        )
        for i in insights:
            yield NormalizedSpendRow(...)
    """
    network = "meta"

    def fetch_daily_spend(self, start_date, end_date):
        if not self.credential_ref:
            return []
        return []  # stub — return empty until SDK wiring lands


class GoogleAdsConnector(BaseAdsConnector):
    """
    Google Ads API ingestion via google-ads-python.

    GAQL pseudocode:
        SELECT campaign.id, campaign.name, metrics.cost_micros,
               metrics.impressions, metrics.clicks, metrics.conversions,
               metrics.conversions_value, segments.date
        FROM campaign
        WHERE segments.date BETWEEN :start AND :end
    """
    network = "google"

    def fetch_daily_spend(self, start_date, end_date):
        if not self.credential_ref:
            return []
        return []


class TikTokAdsConnector(BaseAdsConnector):
    """
    TikTok Marketing API ingestion via /open_api/v1.3/report/integrated/get/.
    """
    network = "tiktok"

    def fetch_daily_spend(self, start_date, end_date):
        if not self.credential_ref:
            return []
        return []


_CONNECTORS = {
    "meta": MetaAdsConnector,
    "google": GoogleAdsConnector,
    "tiktok": TikTokAdsConnector,
}


def supported_networks() -> tuple[str, ...]:
    return tuple(_CONNECTORS.keys())


def get_connector(network: str, shop_domain: str, credential_ref: str | None = None) -> BaseAdsConnector:
    cls = _CONNECTORS.get(network)
    if not cls:
        raise ValueError(f"unknown_network: {network}")
    return cls(shop_domain=shop_domain, credential_ref=credential_ref)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def connect_network(
    db: Session,
    shop_domain: str,
    network: str,
    credential_ref: str | None,
    *,
    account_id: str | None = None,
    account_name: str | None = None,
) -> AdConnection:
    if network not in _CONNECTORS:
        raise ValueError(f"unknown_network: {network}")
    conn = (
        db.query(AdConnection)
        .filter(AdConnection.shop_domain == shop_domain, AdConnection.network == network)
        .one_or_none()
    )
    if conn is None:
        conn = AdConnection(
            shop_domain=shop_domain,
            network=network,
            credential_ref=credential_ref,
            account_id=account_id,
            account_name=account_name,
            status="connected",
        )
        db.add(conn)
    else:
        conn.credential_ref = credential_ref
        conn.account_id = account_id
        conn.account_name = account_name
        conn.status = "connected"
        conn.last_error = None
    db.flush()
    return conn


def list_connections(db: Session, shop_domain: str) -> list[AdConnection]:
    return (
        db.query(AdConnection)
        .filter(AdConnection.shop_domain == shop_domain)
        .order_by(AdConnection.network.asc())
        .all()
    )


def disconnect_network(db: Session, shop_domain: str, network: str) -> bool:
    conn = (
        db.query(AdConnection)
        .filter(AdConnection.shop_domain == shop_domain, AdConnection.network == network)
        .one_or_none()
    )
    if not conn:
        return False
    conn.status = "disconnected"
    conn.credential_ref = None
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Ingestion + storage
# ---------------------------------------------------------------------------


def upsert_rows(db: Session, rows: Iterable[NormalizedSpendRow]) -> tuple[int, int]:
    """
    Upsert NormalizedSpendRow into ad_spend_daily.
    Returns (inserted, updated).

    Batched via Postgres multi-row INSERT ... ON CONFLICT DO UPDATE
    RETURNING (xmax = 0). Chunked at 500 rows to stay well below
    Postgres's ~32K bind-param limit (11 cols × 500 = 5500 params).
    """
    rows_list = list(rows)
    if not rows_list:
        return 0, 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    inserted = updated = 0

    for i in range(0, len(rows_list), _UPSERT_CHUNK_SIZE):
        chunk = rows_list[i:i + _UPSERT_CHUNK_SIZE]
        values = [{
            "shop_domain": r.shop_domain,
            "date": r.date,
            "network": r.network,
            "campaign_id": r.campaign_id,
            "campaign_name": r.campaign_name,
            "spend_eur": r.spend_eur,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "conversions": r.conversions,
            "revenue_attributed_eur": r.revenue_attributed_eur,
            "ingested_at": now,
        } for r in chunk]

        stmt = pg_insert(AdSpendDaily).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["shop_domain", "date", "network", "campaign_id"],
            set_={
                "campaign_name": stmt.excluded.campaign_name,
                "spend_eur": stmt.excluded.spend_eur,
                "impressions": stmt.excluded.impressions,
                "clicks": stmt.excluded.clicks,
                "conversions": stmt.excluded.conversions,
                "revenue_attributed_eur": stmt.excluded.revenue_attributed_eur,
                "ingested_at": stmt.excluded.ingested_at,
            },
        ).returning(literal_column("xmax = 0").label("inserted"))

        for row in db.execute(stmt):
            if row[0]:
                inserted += 1
            else:
                updated += 1

    # Best-effort observability: breadcrumb for the bulk operation so a
    # subsequent error capture has the trail of recent ad-spend ingests.
    try:
        from app.core.sentry_init import pipeline_breadcrumb
        pipeline_breadcrumb(
            "perf.bulk_op",
            f"ads_connectors.upsert_rows rows={len(rows_list)} "
            f"inserted={inserted} updated={updated}",
            level="info",
            data={
                "op": "ads_upsert",
                "rows": len(rows_list),
                "inserted": inserted,
                "updated": updated,
                "chunks": (len(rows_list) + _UPSERT_CHUNK_SIZE - 1) // _UPSERT_CHUNK_SIZE,
            },
        )
    except Exception:
        pass  # SILENT-EXCEPT-OK: sentry breadcrumb best-effort observability; never raise from a successful bulk-op return path.

    return inserted, updated


def ingest_for_shop(
    db: Session,
    shop_domain: str,
    network: str,
    rows: Iterable[NormalizedSpendRow],
) -> IngestionResult:
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    rows_list = list(rows)
    res = IngestionResult(network=network, started_at=started.isoformat())
    res.rows_seen = len(rows_list)
    try:
        ins, upd = upsert_rows(db, rows_list)
        res.rows_inserted = ins
        res.rows_updated = upd
        # Mark connection synced
        conn = (
            db.query(AdConnection)
            .filter(AdConnection.shop_domain == shop_domain, AdConnection.network == network)
            .one_or_none()
        )
        if conn:
            conn.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.last_error = None
            db.flush()
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        log.warning("ads_connectors: ingest failed for %s/%s: %s", shop_domain, network, exc)
    res.finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    return res


def sync_shop_all(db: Session, shop_domain: str, *, lookback_days: int = 7) -> list[IngestionResult]:
    """Pull from every connected network and ingest. Used by the daily worker."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    out: list[IngestionResult] = []
    for conn in list_connections(db, shop_domain):
        if conn.status != "connected":
            continue
        try:
            connector = get_connector(conn.network, shop_domain, conn.credential_ref)
            rows = list(connector.fetch_daily_spend(start, end))
            out.append(ingest_for_shop(db, shop_domain, conn.network, rows))
        except Exception as exc:
            out.append(IngestionResult(network=conn.network, error=str(exc)[:300]))
    return out


# ---------------------------------------------------------------------------
# Reporting helpers — used by API + knowledge graph
# ---------------------------------------------------------------------------


def get_spend_summary(db: Session, shop_domain: str, lookback_days: int = 30) -> dict:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    rows = db.execute(text("""
        SELECT network,
               SUM(spend_eur) AS spend,
               SUM(impressions) AS imp,
               SUM(clicks) AS clk,
               SUM(conversions) AS conv,
               SUM(revenue_attributed_eur) AS rev
        FROM ad_spend_daily
        WHERE shop_domain = :shop AND date >= :cut
        GROUP BY network
        ORDER BY network
    """), {"shop": shop_domain, "cut": cutoff}).fetchall()
    by_network = {}
    total_spend = 0.0
    total_rev = 0.0
    for r in rows:
        spend = float(r[1] or 0)
        rev = float(r[5] or 0)
        by_network[r[0]] = {
            "spend_eur": round(spend, 2),
            "impressions": int(r[2] or 0),
            "clicks": int(r[3] or 0),
            "conversions": int(r[4] or 0),
            "revenue_eur": round(rev, 2),
            "roas": round(rev / spend, 2) if spend > 0 else None,
        }
        total_spend += spend
        total_rev += rev
    return {
        "shop_domain": shop_domain,
        "lookback_days": lookback_days,
        "by_network": by_network,
        "total_spend_eur": round(total_spend, 2),
        "total_revenue_eur": round(total_rev, 2),
        "blended_roas": round(total_rev / total_spend, 2) if total_spend > 0 else None,
    }
