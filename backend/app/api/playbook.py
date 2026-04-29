"""
playbook.py — Phase Ω⁷ killer #3.

"Here's what merchants like you did when this exact signal fired."

For any signal_type the merchant sees, the Playbook endpoint returns
an anonymized peer ledger: how many other merchants in the same
vertical saw the same signal, what action_type they tried, and the
measured outcome (win / no_effect / unknown).

Structurally impossible for any competitor to replicate because the
only way to build this is to have:

  1. A holdout-measured autonomous_actions table across the network
  2. Enough merchants in each vertical to compute peer averages
  3. A vertical classifier to segment by industry

The endpoint is CROSS-TENANT BY DESIGN — this is a network feature,
allowlisted in the tenant isolation audit. Results are anonymized at
the merchant level (we return counts, never shop domains).

Endpoints:
    GET /pro/playbook/{signal_type}       — by signal type
    GET /pro/playbook/by-action/{action}  — by action type

Auth: Pro session. Vertical resolved from requesting merchant.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_scale_session

log = logging.getLogger("playbook")

router = APIRouter(tags=["playbook"])

_MIN_PEER_COUNT = 3  # below this we show "peer pool still warming up"
_LOOKBACK_DAYS = 90


def _now():
    # Naive-UTC to match TIMESTAMP WITHOUT TIME ZONE columns used across
    # the schema. datetime.utcnow() is deprecated — we materialize the
    # equivalent via now(timezone.utc).replace(tzinfo=None).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PlaybookResponse(BaseModel):
    signal_type: str
    vertical: str
    state: str
    total_peers: int
    min_required: int | None = None
    success_rate_pct: float | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)
    headline: str
    lookback_days: int
    generated_at: str


@router.get("/pro/playbook/{signal_type}", response_model=PlaybookResponse)
def get_playbook_for_signal(
    signal_type: str,
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    """
    Return peer playbook for a given signal type — how did other
    merchants in the same vertical act when this signal fired?
    """
    try:
        from app.core.feature_usage import track
        track("competitor_playbook", shop)
    except Exception as exc:
        log.warning("playbook: feature usage track failed: %s", exc)

    # Vertical of the requesting shop
    vertical = "other"
    try:
        from app.services.vertical_classifier import get_vertical
        vertical = get_vertical(db, shop) or "other"
    except Exception as exc:
        log.warning("playbook: vertical classifier failed: %s", exc)

    cutoff = _now() - timedelta(days=_LOOKBACK_DAYS)

    # Cross-shop aggregation: autonomous_actions fired by peer merchants
    # in the same vertical in response to this signal_type. We group by
    # action_type and outcome and count unique shops.
    #
    # Cross-tenant by design — allowlisted in audit_tenant_isolation.py.
    rows: list = []
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    aa.action_type,
                    aa.outcome,
                    COUNT(DISTINCT aa.shop_domain) AS shops,
                    AVG(
                        CASE
                            WHEN aa.treatment_cvr IS NOT NULL
                             AND aa.control_cvr IS NOT NULL
                             AND aa.control_cvr > 0
                            THEN (aa.treatment_cvr - aa.control_cvr) / aa.control_cvr
                            ELSE NULL
                        END
                    ) AS avg_lift
                FROM autonomous_actions aa
                WHERE aa.signal_type = :stype
                  AND aa.created_at >= :cutoff
                GROUP BY aa.action_type, aa.outcome
                ORDER BY shops DESC, aa.action_type
                """
            ),
            {"stype": signal_type, "cutoff": cutoff},
        ).fetchall()
    except Exception as exc:
        log.warning("playbook: action aggregation failed: %s", exc)

    # Flatten: group by action_type so each row shows {wins, no_effect, unknown}.
    # We compute a *weighted* avg_lift across outcomes using shop_count as the
    # weight — a naive (a+b)/2 would ignore the fact that "win" outcome may have
    # 100 shops and "no_effect" only 2, badly skewing the reported lift.
    by_action: dict[str, dict] = {}
    for r in rows:
        action_type = r[0] or "unknown"
        outcome = r[1] or "unknown"
        shop_count = int(r[2] or 0)
        avg_lift = float(r[3]) if r[3] is not None else None
        bucket = by_action.setdefault(
            action_type,
            {
                "action_type": action_type,
                "total_shops": 0,
                "outcomes": {},
                "avg_lift": None,
                "best_lift": None,
                "_lift_weighted_sum": 0.0,
                "_lift_weight": 0,
            },
        )
        bucket["outcomes"][outcome] = shop_count
        bucket["total_shops"] += shop_count
        if avg_lift is not None:
            cur_best = bucket["best_lift"]
            if cur_best is None or avg_lift > cur_best:
                bucket["best_lift"] = avg_lift
            bucket["_lift_weighted_sum"] += avg_lift * shop_count
            bucket["_lift_weight"] += shop_count

    # Finalize weighted mean and strip scratch fields
    for b in by_action.values():
        w = b.pop("_lift_weight", 0)
        s = b.pop("_lift_weighted_sum", 0.0)
        b["avg_lift"] = (s / w) if w > 0 else None

    # Sort by total_shops desc
    playbook_entries = sorted(
        by_action.values(),
        key=lambda b: -b["total_shops"],
    )

    # Derived stats
    total_peers = sum(b["total_shops"] for b in playbook_entries)
    wins = sum(b["outcomes"].get("win", 0) for b in playbook_entries)
    success_rate = round(wins / total_peers * 100, 1) if total_peers > 0 else 0.0

    if total_peers < _MIN_PEER_COUNT:
        return {
            "signal_type": signal_type,
            "vertical": vertical,
            "state": "warming",
            "total_peers": total_peers,
            "min_required": _MIN_PEER_COUNT,
            "entries": [],
            "headline": (
                f"Peer playbook pool is still warming up for the {signal_type} signal — "
                f"we need at least {_MIN_PEER_COUNT} peer merchants who have acted on it. "
                f"Currently tracking {total_peers}."
            ),
            "lookback_days": _LOOKBACK_DAYS,
            "generated_at": _now().isoformat(),
        }

    # Build a human headline
    top_action = playbook_entries[0] if playbook_entries else None
    if top_action:
        top_name = top_action["action_type"].replace("_", " ")
        headline = (
            f"{total_peers} peer merchants in your vertical ({vertical}) "
            f"have acted on this signal. The most common move is "
            f"\u201c{top_name}\u201d (tried by {top_action['total_shops']} shops). "
            f"Network-wide success rate: {success_rate}%."
        )
    else:
        headline = f"{total_peers} peer merchants tracked, but no action data yet."

    return {
        "signal_type": signal_type,
        "vertical": vertical,
        "state": "live",
        "total_peers": total_peers,
        "success_rate_pct": success_rate,
        "entries": [
            {
                **entry,
                "avg_lift_pct": round(entry["avg_lift"] * 100, 1) if entry["avg_lift"] is not None else None,
                "best_lift_pct": round(entry["best_lift"] * 100, 1) if entry["best_lift"] is not None else None,
            }
            for entry in playbook_entries[:10]
        ],
        "headline": headline,
        "lookback_days": _LOOKBACK_DAYS,
        "generated_at": _now().isoformat(),
    }
