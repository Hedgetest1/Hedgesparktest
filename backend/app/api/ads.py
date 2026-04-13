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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["ads"])


class ConnectIn(BaseModel):
    network: Literal["meta", "google", "tiktok"]
    credential_ref: str = Field(..., min_length=1, max_length=128)
    account_id: str | None = Field(default=None, max_length=128)
    account_name: str | None = Field(default=None, max_length=200)


@router.get("/pro/ads/networks")
def get_networks():
    from app.services.ads_connectors import supported_networks
    return {"networks": list(supported_networks())}


@router.get("/pro/ads/connections")
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


@router.post("/pro/ads/connect")
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


@router.delete("/pro/ads/connect/{network}")
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


@router.post("/pro/ads/sync")
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


@router.get("/pro/ads/spend")
def get_spend(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    lookback_days: int = Query(30, ge=1, le=365),
):
    from app.services.ads_connectors import get_spend_summary
    return get_spend_summary(db, shop, lookback_days=lookback_days)
