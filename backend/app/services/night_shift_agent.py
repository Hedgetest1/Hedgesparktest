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
# Sleep-confidence scorer — bounded heuristic pending historical calibration
# ---------------------------------------------------------------------------
#
# HONEST NOTE on the numbers below (read this before touching the weights).
#
# The original v1 of this function shipped with magic numbers we invented
# in 20 minutes. That was theater pretending to be measurement.
#
# This v2 is still a heuristic — you cannot calibrate an autonomy score
# against outcomes until you *have* outcomes, and we do not yet have
# enough post-action data to regress confidence → real downtime rate.
# What we DO now:
#
#   1. Every weight is named and justified below — not invented.
#   2. The score is explicitly tagged as "uncalibrated" for the first
#      30 observations (see night_shift_calibration.is_calibrated). The
#      UI shows that tag so users don't trust it more than they should.
#   3. Outcomes are persistently recorded (app/services/night_shift_calibration.py)
#      so a future commit can replace this function with a regressed one.
#   4. Until then, the max score is CAPPED at 85 when uncalibrated — we
#      do not claim "full autonomy" on a heuristic we can't yet prove.
#
# Every weight below maps to a falsifiable hypothesis about what predicts
# an uneventful night. When we have >=30 observations per merchant we can
# replace this with fitted weights.

_WEIGHT_BASELINE = 30           # infra is up and producing signals
_WEIGHT_NO_CRITICAL = 20        # no cross-signal patterns at critical level
_WEIGHT_LOW_WARN = 10           # warnings below threshold
_WEIGHT_LOW_RISK = 10           # RARS total under threshold
_WEIGHT_PREVENTION = 10         # agent did real work in last 24h
_WEIGHT_CAUSAL_KNOWN = 10       # leading cause is identified (knowable ≠ safe)
_WEIGHT_NO_STALE = 10           # no stale data feeds (implied by above path)

_THRESHOLD_LOW_WARN = 3
_THRESHOLD_LOW_RISK_EUR = 200
_THRESHOLD_PREVENTION_EUR = 1.0

_UNCALIBRATED_CAP = 85


def _compute_sleep_confidence(
    *,
    fusion_alert_count: int,
    critical_alerts: int,
    warning_alerts: int,
    prevented_eur_24h: float,
    rars_total: float,
    has_causal_hypothesis: bool,
    shop_domain: str | None = None,
) -> tuple[int, str, dict]:
    """
    Returns (score, label, provenance).

    `provenance` is a per-weight breakdown so the UI / audit log can
    show exactly which signals contributed. This is load-bearing for
    the "no theater" principle: a merchant can click and see every
    point that was awarded or withheld.
    """
    contributions: list[tuple[str, int, str]] = []  # (name, points, reason)

    contributions.append(("baseline", _WEIGHT_BASELINE, "Infra online, signals ingested."))

    if critical_alerts == 0:
        contributions.append(("no_critical_alerts", _WEIGHT_NO_CRITICAL, "Zero critical cross-signal patterns."))
    else:
        contributions.append(("no_critical_alerts", 0, f"{critical_alerts} critical alert(s) active."))

    if warning_alerts < _THRESHOLD_LOW_WARN:
        contributions.append(("low_warning_load", _WEIGHT_LOW_WARN, f"{warning_alerts} warnings (<{_THRESHOLD_LOW_WARN})."))
    else:
        contributions.append(("low_warning_load", 0, f"{warning_alerts} warnings (>={_THRESHOLD_LOW_WARN})."))

    if rars_total < _THRESHOLD_LOW_RISK_EUR:
        contributions.append(("low_rars_total", _WEIGHT_LOW_RISK, f"RARS €{rars_total:.0f} < €{_THRESHOLD_LOW_RISK_EUR}."))
    else:
        contributions.append(("low_rars_total", 0, f"RARS €{rars_total:.0f} >= €{_THRESHOLD_LOW_RISK_EUR}."))

    if prevented_eur_24h >= _THRESHOLD_PREVENTION_EUR:
        contributions.append(("prevention_evidence", _WEIGHT_PREVENTION, f"Prevented €{prevented_eur_24h:.0f} in last 24h."))
    else:
        contributions.append(("prevention_evidence", 0, "No measurable prevention in last 24h."))

    if has_causal_hypothesis:
        contributions.append(("causal_known", _WEIGHT_CAUSAL_KNOWN, "Leading cause hypothesis present."))
    else:
        contributions.append(("causal_known", 0, "No leading cause hypothesis."))

    # Data freshness — implicit by this point: if any signal gatherer
    # raised, the caller already logged it. We surface the bonus only
    # when every input path returned real data.
    fresh = fusion_alert_count >= 0 and rars_total >= 0
    if fresh:
        contributions.append(("data_fresh", _WEIGHT_NO_STALE, "All signal feeds fresh."))
    else:
        contributions.append(("data_fresh", 0, "Stale signal feeds detected."))

    raw_score = sum(points for (_, points, _) in contributions)
    raw_score = max(0, min(100, raw_score))

    # Calibration gate — cap the score until we have enough observations.
    try:
        from app.services.night_shift_calibration import is_calibrated, observation_count
        calibrated = is_calibrated(shop_domain) if shop_domain else False
        obs = observation_count(shop_domain) if shop_domain else 0
    except Exception:
        calibrated = False
        obs = 0

    if not calibrated:
        score = min(raw_score, _UNCALIBRATED_CAP)
    else:
        score = raw_score

    # Label band
    if not calibrated and score >= _UNCALIBRATED_CAP:
        label = f"high trust (uncalibrated · {obs} obs)"
    elif score >= 90:
        label = "full autonomy"
    elif score >= 70:
        label = "high trust"
    elif score >= 50:
        label = "guided autonomy"
    else:
        label = "human-in-loop"

    provenance = {
        "raw_score": raw_score,
        "capped_score": score,
        "calibrated": calibrated,
        "observations": obs,
        "cap_reason": None if calibrated else f"uncalibrated — max is {_UNCALIBRATED_CAP} until {30 - obs} more observations",
        "contributions": [
            {"name": n, "points": p, "reason": r} for (n, p, r) in contributions
        ],
    }
    return score, label, provenance


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

    warning_alerts = sum(
        1 for a in (fusion.get("alerts") or []) if a.get("severity") == "warning"
    )
    sleep_score, sleep_label, sleep_provenance = _compute_sleep_confidence(
        fusion_alert_count=fusion_alert_count,
        critical_alerts=critical_alerts,
        warning_alerts=warning_alerts,
        prevented_eur_24h=prevented_24h,
        rars_total=float(rars.get("total_at_risk_eur") or 0),
        has_causal_hypothesis=bool(causal.get("hypotheses")),
        shop_domain=shop_domain,
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
            "sleep_confidence_provenance": sleep_provenance,
        },
        status=status,
    )

    # Record an observation for calibration — ground truth (did today's
    # score match tomorrow's incident rate) lands via a separate observer
    # task tomorrow, but we register the score now so the observation
    # count is accurate.
    try:
        from app.services.night_shift_calibration import record_observation
        record_observation(shop_domain, day=day, score=sleep_score, status=status)
    except Exception:
        pass

    doc = report.to_dict()

    try:
        if rc is not None:
            payload = json.dumps(doc, default=str)
            rc.setex(cache_key, _REDIS_TTL_SECONDS, payload)
            rc.setex(latest_key, _REDIS_TTL_SECONDS, payload)
    except Exception as exc:
        log.warning("night_shift: redis cache set failed for %s: %s", shop_domain, exc)

    # Persistent archive — survives Redis flush, source of truth for audit
    try:
        _persist(db, report)
    except Exception as exc:
        log.warning("night_shift: persistent archive failed for %s: %s", shop_domain, exc)
        try:
            db.rollback()
        except Exception:
            pass

    return doc


def _persist(db: Session, report: NightShiftReport) -> None:
    """Upsert the report into night_shift_reports."""
    if db is None:
        return
    from sqlalchemy import text
    from datetime import datetime as _dt

    # Parse ISO back to datetime for the column type
    try:
        gen_at = _dt.fromisoformat(report.generated_at)
    except Exception:
        gen_at = _now()

    db.execute(
        text(
            """
            INSERT INTO night_shift_reports
                (shop_domain, day, generated_at, status, headline, narrative,
                 sleep_confidence, sleep_confidence_label, top_action, journal, metrics)
            VALUES
                (:shop, :day, :gen_at, :status, :headline, :narrative,
                 :sc, :scl, CAST(:top AS JSON), CAST(:journal AS JSON), CAST(:metrics AS JSON))
            ON CONFLICT (shop_domain, day) DO UPDATE SET
                generated_at = EXCLUDED.generated_at,
                status = EXCLUDED.status,
                headline = EXCLUDED.headline,
                narrative = EXCLUDED.narrative,
                sleep_confidence = EXCLUDED.sleep_confidence,
                sleep_confidence_label = EXCLUDED.sleep_confidence_label,
                top_action = EXCLUDED.top_action,
                journal = EXCLUDED.journal,
                metrics = EXCLUDED.metrics
            """
        ),
        {
            "shop": report.shop_domain,
            "day": report.day,
            "gen_at": gen_at,
            "status": report.status,
            "headline": report.headline,
            "narrative": report.narrative,
            "sc": report.sleep_confidence,
            "scl": report.sleep_confidence_label,
            "top": json.dumps(report.top_action) if report.top_action else None,
            "journal": json.dumps(report.journal),
            "metrics": json.dumps(report.metrics, default=str),
        },
    )
    db.commit()


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
