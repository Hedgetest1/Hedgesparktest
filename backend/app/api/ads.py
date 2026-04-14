"""
ads.py — Phase Ω Ads connector API.

  GET    /pro/ads/networks            — supported networks
  GET    /pro/ads/connections         — list current connections
  POST   /pro/ads/connect             — register a connection (credential_ref opaque)
  DELETE /pro/ads/connect/{network}   — disconnect
  POST   /pro/ads/sync                — manual sync trigger
  GET    /pro/ads/spend               — spend summary by network
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["ads"])


class ConnectIn(BaseModel):
    network: Literal["meta", "google", "tiktok"]
    credential_ref: str = Field(..., min_length=1, max_length=128)
    account_id: str | None = Field(default=None, max_length=128)
    account_name: str | None = Field(default=None, max_length=200)


class AdsNetworksResponse(BaseModel):
    networks: list[str] = Field(default_factory=list)


class AdsConnectionRow(BaseModel):
    network: str
    status: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    last_synced_at: str | None = None
    last_error: str | None = None


class AdsConnectionsResponse(BaseModel):
    connections: list[AdsConnectionRow] = Field(default_factory=list)


class AdsConnectResponse(BaseModel):
    ok: bool
    network: str
    status: str | None = None
    account_id: str | None = None
    account_name: str | None = None


class AdsSyncRow(BaseModel):
    network: str
    rows_seen: int | None = None
    rows_inserted: int | None = None
    rows_updated: int | None = None
    error: str | None = None


class AdsSyncResponse(BaseModel):
    results: list[AdsSyncRow] = Field(default_factory=list)


class AdsSpendResponse(BaseModel):
    shop_domain: str
    lookback_days: int
    by_network: dict[str, dict[str, Any]] = Field(default_factory=dict)
    total_spend_eur: float
    total_revenue_eur: float
    blended_roas: float | None = None


@router.get("/pro/ads/networks", response_model=AdsNetworksResponse)
def get_networks():
    from app.services.ads_connectors import supported_networks
    return {"networks": list(supported_networks())}


@router.get("/pro/ads/connections", response_model=AdsConnectionsResponse)
def get_connections(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.ads_connectors import list_connections
    rows = list_connections(db, shop)
    return {
        "connections": [
            {
                "network": r.network,
                "status": r.status,
                "account_id": r.account_id,
                "account_name": r.account_name,
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
                "last_error": r.last_error,
            }
            for r in rows
        ]
    }


@router.post("/pro/ads/connect", response_model=AdsConnectResponse)
def post_connect(
    payload: ConnectIn,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.ads_connectors import connect_network
    try:
        conn = connect_network(
            db,
            shop_domain=shop,
            network=payload.network,
            credential_ref=payload.credential_ref,
            account_id=payload.account_id,
            account_name=payload.account_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "network": conn.network,
        "status": conn.status,
        "account_id": conn.account_id,
        "account_name": conn.account_name,
    }


@router.delete("/pro/ads/connect/{network}", response_model=OkResponse)
def delete_connect(
    network: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.ads_connectors import disconnect_network
    ok = disconnect_network(db, shop, network)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@router.post("/pro/ads/sync", response_model=AdsSyncResponse)
def post_sync(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.ads_connectors import sync_shop_all
    results = sync_shop_all(db, shop)
    return {
        "results": [
            {
                "network": r.network,
                "rows_seen": r.rows_seen,
                "rows_inserted": r.rows_inserted,
                "rows_updated": r.rows_updated,
                "error": r.error,
            }
            for r in results
        ]
    }


@router.get("/pro/ads/spend", response_model=AdsSpendResponse)
def get_spend(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    lookback_days: int = Query(30, ge=1, le=365),
):
    from app.services.ads_connectors import get_spend_summary
    return get_spend_summary(db, shop, lookback_days=lookback_days)
