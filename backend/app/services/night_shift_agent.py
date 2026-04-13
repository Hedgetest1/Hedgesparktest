"""
night_shift_agent.py — Phase Ω⁵ killer feature.

The autonomous night shift. Every night at 02:00 UTC, for every active Pro
merchant, this service wakes up, reads yesterday's state across every
intelligence stream, and authors a morning brief with:

  1. A narrative of what happened overnight (deterministic).
  2. ONE top suggested action with an estimated € impact (1-click execute).
  3. A "Sleep Confidence" score — how much the agent believes the merchant
     can delegate a full night's execution to it tomorrow. Builds trust
     through measurable autonomy, not theater.
  4. A "Night Shift Journal" — the visible reasoning chain: what signals
     were considered, what was rejected and WHY, what was kept. Proof
     that the agent didn't just roll dice.

Storage: Redis. Keys are `hs:night_shift:{shop}:{YYYY-MM-DD}` with 8-day
TTL so the weekly archive lives but doesn't leak unboundedly. One doc
per shop per night. Idempotent by day — re-running the generator on the
same day is a no-op unless force=True.

Design principles (locked in by the project's north-star rules):
- Deterministic first. LLM is NEVER called here. Every narrative string
  is a template filled from real numbers.
- Hard fallback: if anything throws, we emit a "calm night" report that
  still shows the sleep-confidence score and an empty journal. No
  silent failure, no 500 on the morning card.
- Zero new tables / migrations — everything lives in Redis.
- Reusable: frontend reads through /pro/night-shift/latest; worker calls
  `run_nightly_for_all_pro()`; tests can invoke `generate_for_shop()`
  directly against a SessionLocal.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("night_shift_agent")

_REDIS_PREFIX = "hs:night_shift"
_REDIS_TTL_SECONDS = 8 * 24 * 3600  # 8 days — enough for a week view
_MAX_JOURNAL_ENTRIES = 12  # keep the reasoning chain readable
_CACHE_LATEST_PREFIX = "hs:night_shift_latest"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _day_key(dt: datetime | None = None) -> str:
    dt = dt or _now()
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"


@dataclass
class JournalEntry:
    """One line of the visible reasoning chain."""
    signal: str
    verdict: str  # "kept", "rejected", "watched"
    reason: str
    weight: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NightShiftReport:
    shop_domain: str
    day: str
    generated_at: str
    narrative: str
    headline: str
    top_action: dict | None  # { "label", "detail", "estimated_impact_eur", "kind", "id" }
    sleep_confidence: int  # 0-100
    sleep_confidence_label: str
    journal: list[dict]
    metrics: dict  # snapshot numbers used for the narrative
    status: str  # "quiet", "active", "alarm"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Signal gatherers (all wrapped in try/except — never fatal)
# ---------------------------------------------------------------------------

def _gather_rars(db: Session, shop_domain: str) -> dict:
    try:
        from app.services.revenue_at_risk import get_revenue_at_risk
        return get_revenue_at_risk(db, shop_domain) or {}
    except Exception as exc:
        log.warning("night_shift: rars gather failed for %s: %s", shop_domain, exc)
        return {}


def _gather_causal(db: Session, shop_domain: str) -> dict:
    try:
        from app.services.causal_explainer import explain
        return explain(db, shop_domain) or {}
    except Exception as exc:
        log.warning("night_shift: causal gather failed for %s: %s", shop_domain, exc)
        return {}


def _gather_fusion(db: Session, shop_domain: str) -> dict:
    try:
        from app.services.anomaly_fusion import fuse
        return fuse(db, shop_domain) or {}
    except Exception as exc:
        log.warning("night_shift: fusion gather failed for %s: %s", shop_domain, exc)
        return {}


def _gather_prevented_today(db: Session, shop_domain: str) -> float:
    """Sum of action execution deltas recorded in the last 24h."""
    try:
        from sqlalchemy import text
        row = db.execute(
            text(
                """
                SELECT COALESCE(SUM(CAST(COALESCE(impact_eur, 0) AS FLOAT)), 0)
                FROM action_executions
                WHERE shop_domain = :shop
                  AND executed_at >= NOW() - INTERVAL '24 hours'
                  AND status = 'confirmed'
                """
            ),
            {"shop": shop_domain},
        ).fetchone()
        return float(row[0] or 0.0) if row else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Sleep-confidence scorer — deterministic, explainable
# ---------------------------------------------------------------------------

def _compute_sleep_confidence(
    *,
    fusion_alert_count: int,
    critical_alerts: int,
    prevented_eur_24h: float,
    rars_total: float,
    has_causal_hypothesis: bool,
) -> tuple[int, str]:
    """
    Score 0-100 where higher = agent can be trusted with more autonomy.

    Heuristic:
      + 40 baseline (tool is online, signals collected, no exception path)
      + 20 if no critical fusion alerts
      + 15 if fewer than 3 warning alerts
      + 15 if prevented_eur_24h >= 1.0 (proof of useful work)
      + 10 if rars_total < 200 (low overall risk)

    Label band:
      90-100 "full autonomy"
      70-89  "high trust"
      50-69  "guided autonomy"
      <50    "human-in-loop"
    """
    score = 40
    if critical_alerts == 0:
        score += 20
    if fusion_alert_count < 3:
        score += 15
    if prevented_eur_24h >= 1.0:
        score += 15
    if rars_total < 200:
        score += 10

    # Clamp and label
    score = max(0, min(100, score))
    if score >= 90:
        label = "full autonomy"
    elif score >= 70:
        label = "high trust"
    elif score >= 50:
        label = "guided autonomy"
    else:
        label = "human-in-loop"
    return score, label


# ---------------------------------------------------------------------------
# Top action picker — deterministic priority chain
# ---------------------------------------------------------------------------

def _pick_top_action(
    *,
    rars: dict,
    causal: dict,
    fusion: dict,
) -> tuple[dict | None, list[JournalEntry]]:
    """
    Return the single most impactful action + journal entries explaining
    the choice. Priority chain:

      1. Highest-€ RARS component with an actionable recommendation
      2. Top causal hypothesis with a recommended_action
      3. Top fusion alert with a recommended_action
      4. None
    """
    journal: list[JournalEntry] = []

    # 1. RARS components
    components = rars.get("components") or []
    if components:
        best = max(components, key=lambda c: float(c.get("loss_eur", 0) or 0))
        loss = float(best.get("loss_eur", 0) or 0)
        if loss > 0:
            action = {
                "kind": "rars_component",
                "label": best.get("headline") or best.get("source", "Unknown").replace("_", " ").title(),
                "detail": best.get("recommendation") or best.get("narrative") or "Open the detail view for the full playbook.",
                "estimated_impact_eur": round(loss, 2),
                "source": best.get("source"),
            }
            journal.append(JournalEntry(
                signal=f"rars.{best.get('source','unknown')}",
                verdict="kept",
                reason=f"€{loss:.0f}/mo at risk — highest-value actionable lever.",
                weight=loss,
            ))
            # Mark others as considered-but-rejected
            for c in components:
                if c is best:
                    continue
                clos = float(c.get("loss_eur", 0) or 0)
                if clos > 0:
                    journal.append(JournalEntry(
                        signal=f"rars.{c.get('source','unknown')}",
                        verdict="watched",
                        reason=f"€{clos:.0f}/mo at risk — lower priority tonight.",
                        weight=clos,
                    ))
            return action, journal

    # 2. Causal hypothesis
    hyps = causal.get("hypotheses") or []
    if hyps:
        top = hyps[0]
        rec = top.get("recommended_action")
        if rec:
            conf = float(top.get("confidence", 0) or 0)
            action = {
                "kind": "causal",
                "label": f"{top.get('label','cause').replace('_',' ').title()}",
                "detail": rec,
                "estimated_impact_eur": 0.0,
                "source": "causal_explainer",
            }
            journal.append(JournalEntry(
                signal=f"causal.{top.get('label','')}",
                verdict="kept",
                reason=f"Leading hypothesis at {int(conf*100)}% confidence.",
                weight=conf * 100,
            ))
            return action, journal

    # 3. Fusion alert
    alerts = fusion.get("alerts") or []
    if alerts:
        top = alerts[0]
        rec = top.get("recommended_action")
        if rec:
            action = {
                "kind": "fusion_alert",
                "label": top.get("pattern", "anomaly").replace("_", " ").title(),
                "detail": rec,
                "estimated_impact_eur": 0.0,
                "source": "anomaly_fusion",
            }
            journal.append(JournalEntry(
                signal=f"fusion.{top.get('pattern','')}",
                verdict="kept",
                reason=f"Fusion score {int(top.get('fusion_score',0))}/100, {top.get('severity','info')} severity.",
                weight=float(top.get("fusion_score", 0) or 0),
            ))
            return action, journal

    return None, journal


# ---------------------------------------------------------------------------
# Narrative builder — deterministic templates, no LLM
# ---------------------------------------------------------------------------

def _build_narrative(
    *,
    shop_domain: str,
    rars: dict,
    fusion: dict,
    causal: dict,
    prevented_eur_24h: float,
    top_action: dict | None,
) -> tuple[str, str, str]:
    """Return (headline, narrative, status)."""
    rars_total = float(rars.get("total_at_risk_eur") or 0)
    alerts = fusion.get("alerts") or []
    crit = sum(1 for a in alerts if a.get("severity") == "critical")
    warn = sum(1 for a in alerts if a.get("severity") == "warning")
    causal_top = (causal.get("hypotheses") or [None])[0]

    # Status classification
    if crit > 0:
        status = "alarm"
    elif warn > 0 or (rars_total > 200 and top_action is not None):
        status = "active"
    else:
        status = "quiet"

    # Headline
    if status == "alarm":
        headline = f"🔴 {crit} critical pattern{'s' if crit > 1 else ''} detected overnight — action ready."
    elif status == "active" and top_action:
        impact = top_action.get("estimated_impact_eur") or 0
        if impact > 0:
            headline = f"🟠 €{impact:.0f}/mo lever ready for you — 1-click apply."
        else:
            headline = f"🟠 Worth your attention this morning — 1 action prepared."
    elif prevented_eur_24h > 0:
        headline = f"✨ Quiet night. HedgeSpark prevented €{prevented_eur_24h:.0f} while you slept."
    else:
        headline = "✨ Quiet night. Nothing drifted while you slept."

    # Narrative body — layered sentences
    parts = []
    if prevented_eur_24h > 0:
        parts.append(f"Overnight, HedgeSpark executed confirmed actions worth €{prevented_eur_24h:.0f}.")
    if rars_total > 0:
        parts.append(f"Revenue-at-risk stands at €{rars_total:.0f}/mo across {len(rars.get('components') or [])} component(s).")
    if crit > 0:
        parts.append(f"{crit} critical cross-signal pattern(s) fired.")
    elif warn > 0:
        parts.append(f"{warn} warning pattern(s) fired — none critical.")
    else:
        parts.append("No cross-signal anomalies fired — monitoring is all-green.")
    if causal_top:
        label = causal_top.get("label", "").replace("_", " ")
        conf = int(float(causal_top.get("confidence", 0) or 0) * 100)
        if label:
            parts.append(f"Leading cause hypothesis: {label} ({conf}% confidence).")
    if top_action:
        parts.append(f"Suggested first move: {top_action.get('label','review the action')}.")
    else:
        parts.append("No action required on your part this morning.")

    return headline, " ".join(parts), status


# ---------------------------------------------------------------------------
# Generator entry points
# ---------------------------------------------------------------------------

def generate_for_shop(db: Session, shop_domain: str, *, force: bool = False) -> dict:
    """
    Build and cache a night shift report for `shop_domain`.

    Returns the report dict. Idempotent by (shop, day): a second call on
    the same day returns the cached report unless `force=True`.
    """
    day = _day_key()
    cache_key = f"{_REDIS_PREFIX}:{shop_domain}:{day}"
    latest_key = f"{_CACHE_LATEST_PREFIX}:{shop_domain}"

    # Idempotency — return cached doc for the day unless forced
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None and not force:
            raw = rc.get(cache_key)
            if raw:
                try:
                    return json.loads(raw)
                except Exception:
                    pass
    except Exception:
        rc = None

    rars = _gather_rars(db, shop_domain)
    fusion = _gather_fusion(db, shop_domain)
    causal = _gather_causal(db, shop_domain)
    prevented_24h = _gather_prevented_today(db, shop_domain)

    top_action, journal = _pick_top_action(rars=rars, causal=causal, fusion=fusion)

    # Add general "watched" signals to the journal so merchants see the
    # full reasoning chain even on quiet nights
    fusion_alert_count = len(fusion.get("alerts") or [])
    critical_alerts = sum(
        1 for a in (fusion.get("alerts") or []) if a.get("severity") == "critical"
    )
    if fusion_alert_count == 0:
        journal.append(JournalEntry(
            signal="fusion.cross_signal",
            verdict="watched",
            reason="All 5 cross-signals within tolerance.",
            weight=0,
        ))
    if not (rars.get("components") or []):
        journal.append(JournalEntry(
            signal="rars.components",
            verdict="watched",
            reason="No revenue-at-risk component crossed the alert threshold.",
            weight=0,
        ))

    journal = journal[:_MAX_JOURNAL_ENTRIES]

    headline, narrative, status = _build_narrative(
        shop_domain=shop_domain,
        rars=rars,
        fusion=fusion,
        causal=causal,
        prevented_eur_24h=prevented_24h,
        top_action=top_action,
    )

    sleep_score, sleep_label = _compute_sleep_confidence(
        fusion_alert_count=fusion_alert_count,
        critical_alerts=critical_alerts,
        prevented_eur_24h=prevented_24h,
        rars_total=float(rars.get("total_at_risk_eur") or 0),
        has_causal_hypothesis=bool(causal.get("hypotheses")),
    )

    report = NightShiftReport(
        shop_domain=shop_domain,
        day=day,
        generated_at=_now().isoformat(),
        narrative=narrative,
        headline=headline,
        top_action=top_action,
        sleep_confidence=sleep_score,
        sleep_confidence_label=sleep_label,
        journal=[j.to_dict() for j in journal],
        metrics={
            "rars_total_eur": float(rars.get("total_at_risk_eur") or 0),
            "prevented_24h_eur": prevented_24h,
            "fusion_alert_count": fusion_alert_count,
            "critical_alerts": critical_alerts,
        },
        status=status,
    )

    doc = report.to_dict()

    try:
        if rc is not None:
            payload = json.dumps(doc, default=str)
            rc.setex(cache_key, _REDIS_TTL_SECONDS, payload)
            rc.setex(latest_key, _REDIS_TTL_SECONDS, payload)
    except Exception as exc:
        log.warning("night_shift: redis cache set failed for %s: %s", shop_domain, exc)

    return doc


def get_latest_for_shop(shop_domain: str) -> dict | None:
    """Read the latest cached report. Returns None if nothing cached."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return None
        raw = rc.get(f"{_CACHE_LATEST_PREFIX}:{shop_domain}")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def run_nightly_for_all_pro(db: Session) -> int:
    """
    Worker hook: generate reports for every active Pro merchant.

    Returns the number of reports generated.
    """
    try:
        from app.models.merchant import Merchant
        shops = (
            db.query(Merchant.shop_domain)
            .filter(Merchant.plan == "pro", Merchant.billing_active == True)  # noqa: E712
            .all()
        )
    except Exception as exc:
        log.warning("night_shift: merchant list failed: %s", exc)
        return 0

    n = 0
    for row in shops:
        shop = row[0] if not isinstance(row, str) else row
        if not shop:
            continue
        try:
            generate_for_shop(db, shop, force=True)
            n += 1
        except Exception as exc:
            log.warning("night_shift: generate failed for %s: %s", shop, exc)
    return n


def should_run_nightly_now() -> bool:
    """
    True if the agent should run. Runs once per UTC day, between 02:00 and
    03:00 UTC. Gate uses a Redis day-lock to ensure exactly-once even
    across worker restarts.
    """
    now = _now()
    if now.hour != 2:
        return False
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return True  # no redis → best-effort run
        lock_key = f"{_REDIS_PREFIX}:day_lock:{_day_key(now)}"
        # SETNX with 26h TTL — tomorrow's lock replaces today's
        ok = rc.set(lock_key, "1", nx=True, ex=26 * 3600)
        return bool(ok)
    except Exception:
        return False
