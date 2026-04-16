"""
scoring_calibration.py — Self-improving scoring intelligence.

Adaptive layer on top of deterministic priority + confidence scoring.
Computes calibration offsets from real outcome data. Every adjustment is:
    - bounded (hard min/max on every offset)
    - evidence-gated (minimum sample size before adapting)
    - logged (every calibration change is explainable)
    - reversible (offsets return to 0 when evidence is insufficient)

Architecture:
    Pure computation from DB outcome data — no mutable state stored.
    Called each time scores are computed. Results are deterministic:
    same outcomes → same calibration → same scores.

Public interface:
    get_scoring_calibration(db) -> ScoringCalibration
    run_self_evaluation(db) -> SelfEvalReport
    compute_impact_signal(db, shop_domain, incident_created_at) -> float | None
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, func
from sqlalchemy.orm import Session

log = logging.getLogger("scoring_calibration")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum measured outcomes before any calibration is applied
_MIN_EVIDENCE = 5

# Offset bounds — calibration can never push scores beyond these deltas
_PRIORITY_OFFSET_BOUNDS = {"min": -15, "max": 15}
_CONFIDENCE_OFFSET_BOUNDS = {"min": -20, "max": 20}

# Per-domain calibration bounds
_DOMAIN_CONFIDENCE_OFFSET_BOUNDS = {"min": -15, "max": 15}

# Impact signal weight in priority scoring (bounded)
_IMPACT_SIGNAL_MAX_BONUS = 10

# Remediation class calibration bounds
_REMEDIATION_CONFIDENCE_OFFSET_BOUNDS = {"min": -15, "max": 15}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Calibration result types
# ---------------------------------------------------------------------------

@dataclass
class CalibrationOffset:
    """A single calibration adjustment with context."""
    dimension: str        # e.g., "severity:critical", "domain:webhooks"
    offset: int           # points to add/subtract
    reason: str           # why this offset was computed
    evidence_count: int   # how many outcomes informed this
    adapted: bool         # True if offset != 0


@dataclass
class ScoringCalibration:
    """Complete calibration state — all offsets + evidence."""

    # Priority calibration: per severity level
    severity_offsets: dict[str, CalibrationOffset] = field(default_factory=dict)

    # Priority calibration: per subsystem
    subsystem_offsets: dict[str, CalibrationOffset] = field(default_factory=dict)

    # Confidence calibration: per domain
    domain_confidence_offsets: dict[str, CalibrationOffset] = field(default_factory=dict)

    # Confidence calibration: per remediation class
    remediation_confidence_offsets: dict[str, CalibrationOffset] = field(default_factory=dict)

    # Global confidence offset (system-wide prediction accuracy)
    global_confidence_offset: CalibrationOffset | None = None

    # Evidence summary
    evidence: dict = field(default_factory=dict)

    def get_priority_offset(
        self,
        severity: str | None,
        subsystem_class: str | None,
    ) -> int:
        """Get total priority calibration offset for given dimensions."""
        total = 0
        sev_cal = self.severity_offsets.get(severity or "error")
        if sev_cal:
            total += sev_cal.offset
        sub_cal = self.subsystem_offsets.get(subsystem_class or "unknown")
        if sub_cal:
            total += sub_cal.offset
        return total

    def get_confidence_offset(self, domain: str | None, remediation_class: str | None = None) -> int:
        """Get confidence calibration offset for a domain + remediation class."""
        total = 0
        if self.global_confidence_offset:
            total += self.global_confidence_offset.offset
        dom_cal = self.domain_confidence_offsets.get(domain or "unknown")
        if dom_cal:
            total += dom_cal.offset
        if remediation_class:
            rem_cal = self.remediation_confidence_offsets.get(remediation_class)
            if rem_cal:
                total += rem_cal.offset
        return total

    def to_dict(self) -> dict:
        return {
            "severity_offsets": {k: {"offset": v.offset, "reason": v.reason, "evidence": v.evidence_count}
                                 for k, v in self.severity_offsets.items() if v.adapted},
            "subsystem_offsets": {k: {"offset": v.offset, "reason": v.reason, "evidence": v.evidence_count}
                                  for k, v in self.subsystem_offsets.items() if v.adapted},
            "domain_confidence_offsets": {k: {"offset": v.offset, "reason": v.reason, "evidence": v.evidence_count}
                                          for k, v in self.domain_confidence_offsets.items() if v.adapted},
            "remediation_confidence_offsets": {k: {"offset": v.offset, "reason": v.reason, "evidence": v.evidence_count}
                                               for k, v in self.remediation_confidence_offsets.items() if v.adapted},
            "global_confidence_offset": {"offset": self.global_confidence_offset.offset,
                                          "reason": self.global_confidence_offset.reason} if self.global_confidence_offset else None,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Priority calibration — adapt based on what actually gets fixed
# ---------------------------------------------------------------------------

def _calibrate_severity_offsets(db: Session) -> dict[str, CalibrationOffset]:
    """
    Compute priority offsets per severity level based on fix outcomes.

    Logic:
    - If high-priority (critical) candidates have low effectiveness → reduce their priority
    - If lower-priority candidates consistently produce effective fixes → boost them
    - Offset = (effectiveness_pct - 60) / 10, clamped to bounds

    The 60% baseline means: if a severity level has exactly 60% effective fixes,
    no offset is applied. Above → positive offset. Below → negative.
    """
    offsets: dict[str, CalibrationOffset] = {}
    cutoff = _now() - timedelta(days=90)

    try:
        # ISOLATION GATE: Only real_merchant outcomes drive calibration offsets.
        rows = db.execute(text("""
            SELECT
                si.severity,
                bc.outcome_status,
                COUNT(*) AS cnt
            FROM bugfix_candidates bc
            JOIN sentry_incidents si ON si.linked_bugfix_candidate_id = bc.id
            WHERE bc.outcome_status IS NOT NULL
              AND bc.outcome_measured_at >= :cutoff
              AND si.severity IS NOT NULL
              AND si.family_head_id IS NULL
              AND bc.evidence_source = 'real_merchant'
            GROUP BY si.severity, bc.outcome_status
        """), {"cutoff": cutoff}).fetchall()

        # Aggregate per severity
        severity_data: dict[str, dict] = {}
        for sev, outcome, cnt in rows:
            if sev not in severity_data:
                severity_data[sev] = {"effective": 0, "total": 0}
            severity_data[sev]["total"] += cnt
            if outcome == "effective":
                severity_data[sev]["effective"] += cnt

        for sev, data in severity_data.items():
            total = data["total"]
            if total < _MIN_EVIDENCE:
                offsets[sev] = CalibrationOffset(
                    f"severity:{sev}", 0,
                    f"insufficient evidence ({total}/{_MIN_EVIDENCE})",
                    total, adapted=False,
                )
                continue

            eff_pct = data["effective"] / total * 100
            # Offset: each 10% above/below 60% baseline = 1 point
            raw_offset = int((eff_pct - 60) / 10)
            offset = max(_PRIORITY_OFFSET_BOUNDS["min"],
                         min(_PRIORITY_OFFSET_BOUNDS["max"], raw_offset))

            offsets[sev] = CalibrationOffset(
                f"severity:{sev}", offset,
                f"effectiveness={eff_pct:.0f}% from {total} outcomes",
                total, adapted=(offset != 0),
            )

    except Exception as exc:
        log.warning("scoring_calibration: severity calibration failed: %s", exc)

    return offsets


def _calibrate_subsystem_offsets(db: Session) -> dict[str, CalibrationOffset]:
    """
    Compute priority offsets per subsystem based on fix outcomes.

    Same logic as severity offsets but grouped by subsystem_class.
    """
    offsets: dict[str, CalibrationOffset] = {}
    cutoff = _now() - timedelta(days=90)

    try:
        # ISOLATION GATE: Only real_merchant outcomes drive calibration offsets.
        rows = db.execute(text("""
            SELECT
                si.subsystem_class,
                bc.outcome_status,
                COUNT(*) AS cnt
            FROM bugfix_candidates bc
            JOIN sentry_incidents si ON si.linked_bugfix_candidate_id = bc.id
            WHERE bc.outcome_status IS NOT NULL
              AND bc.outcome_measured_at >= :cutoff
              AND si.subsystem_class IS NOT NULL
              AND si.family_head_id IS NULL
              AND bc.evidence_source = 'real_merchant'
            GROUP BY si.subsystem_class, bc.outcome_status
        """), {"cutoff": cutoff}).fetchall()

        subsystem_data: dict[str, dict] = {}
        for sub, outcome, cnt in rows:
            if sub not in subsystem_data:
                subsystem_data[sub] = {"effective": 0, "total": 0}
            subsystem_data[sub]["total"] += cnt
            if outcome == "effective":
                subsystem_data[sub]["effective"] += cnt

        for sub, data in subsystem_data.items():
            total = data["total"]
            if total < _MIN_EVIDENCE:
                offsets[sub] = CalibrationOffset(
                    f"subsystem:{sub}", 0,
                    f"insufficient evidence ({total}/{_MIN_EVIDENCE})",
                    total, adapted=False,
                )
                continue

            eff_pct = data["effective"] / total * 100
            raw_offset = int((eff_pct - 60) / 10)
            offset = max(_PRIORITY_OFFSET_BOUNDS["min"],
                         min(_PRIORITY_OFFSET_BOUNDS["max"], raw_offset))

            offsets[sub] = CalibrationOffset(
                f"subsystem:{sub}", offset,
                f"effectiveness={eff_pct:.0f}% from {total} outcomes",
                total, adapted=(offset != 0),
            )

    except Exception as exc:
        log.warning("scoring_calibration: subsystem calibration failed: %s", exc)

    return offsets


# ---------------------------------------------------------------------------
# Confidence calibration — make predictions match reality
# ---------------------------------------------------------------------------

def _calibrate_global_confidence(db: Session) -> CalibrationOffset | None:
    """
    Compute global confidence offset based on prediction accuracy.

    Logic:
    - Compare predicted confidence to actual outcomes
    - If system is overconfident (high confidence + failures) → negative offset
    - If system is underconfident (low confidence + successes) → positive offset
    """
    cutoff = _now() - timedelta(days=90)

    try:
        # ISOLATION GATE: Only real_merchant outcomes drive calibration offsets.
        rows = db.execute(text("""
            SELECT fix_confidence, outcome_status
            FROM bugfix_candidates
            WHERE fix_confidence IS NOT NULL
              AND outcome_status IN ('effective', 'ineffective')
              AND outcome_measured_at >= :cutoff
              AND evidence_source = 'real_merchant'
        """), {"cutoff": cutoff}).fetchall()

        if len(rows) < _MIN_EVIDENCE:
            return CalibrationOffset(
                "global_confidence", 0,
                f"insufficient evidence ({len(rows)}/{_MIN_EVIDENCE})",
                len(rows), adapted=False,
            )

        # Compute calibration: average prediction error
        # For each outcome: predicted confidence vs binary result (1.0 or 0.0)
        total_error = 0.0
        for confidence, outcome in rows:
            predicted = confidence / 100.0  # normalize to 0-1
            actual = 1.0 if outcome == "effective" else 0.0
            total_error += (actual - predicted)

        avg_error = total_error / len(rows)
        # Convert to points: avg_error of 0.1 = 10 points underconfident
        raw_offset = int(avg_error * 100)
        offset = max(_CONFIDENCE_OFFSET_BOUNDS["min"],
                     min(_CONFIDENCE_OFFSET_BOUNDS["max"], raw_offset))

        direction = "underconfident" if offset > 0 else "overconfident" if offset < 0 else "calibrated"

        return CalibrationOffset(
            "global_confidence", offset,
            f"{direction}: avg_error={avg_error:.2f} from {len(rows)} outcomes",
            len(rows), adapted=(offset != 0),
        )

    except Exception as exc:
        log.warning("scoring_calibration: global confidence calibration failed: %s", exc)
        return None


def _calibrate_domain_confidence(db: Session) -> dict[str, CalibrationOffset]:
    """
    Per-domain confidence calibration.

    Same logic as global but computed per affected_domain.
    """
    offsets: dict[str, CalibrationOffset] = {}
    cutoff = _now() - timedelta(days=90)

    try:
        # ISOLATION GATE: Only real_merchant outcomes drive calibration offsets.
        rows = db.execute(text("""
            SELECT affected_domain, fix_confidence, outcome_status
            FROM bugfix_candidates
            WHERE fix_confidence IS NOT NULL
              AND outcome_status IN ('effective', 'ineffective')
              AND outcome_measured_at >= :cutoff
              AND affected_domain IS NOT NULL
              AND evidence_source = 'real_merchant'
        """), {"cutoff": cutoff}).fetchall()

        domain_data: dict[str, list] = {}
        for domain, conf, outcome in rows:
            if domain not in domain_data:
                domain_data[domain] = []
            domain_data[domain].append((conf, outcome))

        for domain, pairs in domain_data.items():
            if len(pairs) < _MIN_EVIDENCE:
                offsets[domain] = CalibrationOffset(
                    f"domain:{domain}", 0,
                    f"insufficient evidence ({len(pairs)}/{_MIN_EVIDENCE})",
                    len(pairs), adapted=False,
                )
                continue

            total_error = sum(
                (1.0 if o == "effective" else 0.0) - (c / 100.0)
                for c, o in pairs
            )
            avg_error = total_error / len(pairs)
            raw_offset = int(avg_error * 100)
            offset = max(_DOMAIN_CONFIDENCE_OFFSET_BOUNDS["min"],
                         min(_DOMAIN_CONFIDENCE_OFFSET_BOUNDS["max"], raw_offset))

            direction = "underconfident" if offset > 0 else "overconfident" if offset < 0 else "calibrated"
            offsets[domain] = CalibrationOffset(
                f"domain:{domain}", offset,
                f"{direction}: avg_error={avg_error:.2f} from {len(pairs)} outcomes",
                len(pairs), adapted=(offset != 0),
            )

    except Exception as exc:
        log.warning("scoring_calibration: domain confidence calibration failed: %s", exc)

    return offsets


# ---------------------------------------------------------------------------
# Impact feedback — connect bugs to merchant revenue signals
# ---------------------------------------------------------------------------

def compute_impact_signal(
    db: Session,
    shop_domain: str | None,
    incident_created_at: datetime | None,
) -> tuple[int, dict] | tuple[None, None]:
    """
    Compute a merchant impact signal from REAL order/revenue data.

    Compares order count and revenue in a 24h window before vs after
    the incident appeared. A significant drop → higher priority.

    Falls back to cart-rate proxy if no order data exists.

    Returns (bonus_points, detail_dict) or (None, None) if insufficient data.
    Bonus is bounded to [0, _IMPACT_SIGNAL_MAX_BONUS].
    """
    if not shop_domain or not incident_created_at:
        return None, None

    try:
        detail: dict = {"shop": shop_domain, "method": "none"}

        # --- Primary signal: real orders from shop_orders ---
        before_start = incident_created_at - timedelta(hours=24)
        after_end = incident_created_at + timedelta(hours=24)

        from app.services.revenue_metrics import get_shop_currency
        currency = get_shop_currency(db, shop_domain)
        row = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE created_at < :incident_at) AS orders_before,
                COUNT(*) FILTER (WHERE created_at >= :incident_at) AS orders_after,
                COALESCE(SUM(total_price) FILTER (WHERE created_at < :incident_at), 0) AS rev_before,
                COALESCE(SUM(total_price) FILTER (WHERE created_at >= :incident_at), 0) AS rev_after
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at >= :before_start
              AND created_at < :after_end
              AND (:currency IS NULL OR currency = :currency)
        """), {
            "shop": shop_domain,
            "incident_at": incident_created_at,
            "before_start": before_start,
            "after_end": after_end,
            "currency": currency,
        }).fetchone()

        if row and (row[0] or 0) >= 3:
            # We have enough order data for a real comparison
            orders_before = row[0] or 0
            orders_after = row[1] or 0
            rev_before = float(row[2] or 0)
            rev_after = float(row[3] or 0)

            detail["method"] = "order_comparison"
            detail["orders_before_24h"] = orders_before
            detail["orders_after_24h"] = orders_after
            detail["revenue_before_24h"] = round(rev_before, 2)
            detail["revenue_after_24h"] = round(rev_after, 2)

            # Compute drop percentage (order count as primary, revenue as secondary)
            if orders_before > 0:
                order_drop_pct = (orders_before - orders_after) / orders_before * 100
            else:
                order_drop_pct = 0

            if rev_before > 0:
                rev_drop_pct = (rev_before - rev_after) / rev_before * 100
            else:
                rev_drop_pct = 0

            detail["order_drop_pct"] = round(order_drop_pct, 1)
            detail["revenue_drop_pct"] = round(rev_drop_pct, 1)

            # Scoring: significant order drop = high impact
            #   >50% drop = 10 points (max)
            #   30-50% drop = 7 points
            #   15-30% drop = 4 points
            #   <15% drop or growth = 0 points
            # Use the worse of order_drop and rev_drop
            worst_drop = max(order_drop_pct, rev_drop_pct)

            if worst_drop >= 50:
                bonus = _IMPACT_SIGNAL_MAX_BONUS       # 10
            elif worst_drop >= 30:
                bonus = 7
            elif worst_drop >= 15:
                bonus = 4
            else:
                bonus = 0

            detail["bonus"] = bonus
            return bonus, detail

        # --- Fallback: cart-rate proxy from store_metrics ---
        from app.models.store_metrics import StoreMetrics
        metrics = (
            db.query(StoreMetrics)
            .filter(StoreMetrics.shop_domain == shop_domain)
            .first()
        )

        if metrics:
            new_cart = metrics.new_visitor_cart_rate
            returning_cart = metrics.returning_visitor_cart_rate
            cart_values = [v for v in [new_cart, returning_cart] if v is not None]

            if cart_values:
                avg_cart = sum(cart_values) / len(cart_values)
                detail["method"] = "cart_rate_proxy"
                detail["avg_cart_rate"] = round(avg_cart, 4)

                if avg_cart < 0.01:
                    bonus = _IMPACT_SIGNAL_MAX_BONUS
                elif avg_cart < 0.03:
                    bonus = _IMPACT_SIGNAL_MAX_BONUS // 2
                else:
                    bonus = 0

                detail["bonus"] = bonus
                return bonus, detail

        return None, None

    except Exception as exc:
        log.warning("scoring_calibration: impact signal failed for %s: %s", shop_domain, exc)
        return None, None


# ---------------------------------------------------------------------------
# Root cause cluster evolution
# ---------------------------------------------------------------------------

def find_co_occurring_families(db: Session, fingerprint: str) -> list[dict]:
    """
    Find families that consistently co-occur with the given fingerprint.

    Multi-window detection:
      - short (1h): tight temporal coupling (same root cause burst)
      - medium (6h): cascading failures across services
      - long (24h): same-day deployment-correlated issues

    Each window requires progressively more evidence to qualify:
      - short: 2+ co-occurrences
      - medium: 3+ co-occurrences
      - long: 4+ co-occurrences

    Returns list of {fingerprint, co_occurrence_count, error_type, culprit, window}.
    """
    results: list[dict] = []
    seen_fps: set[str] = set()

    _WINDOWS = [
        ("short_1h", "1 hour", 2),
        ("medium_6h", "6 hours", 3),
        ("long_24h", "24 hours", 4),
    ]

    for window_name, interval, min_count in _WINDOWS:
        try:
            rows = db.execute(text(f"""
                SELECT s2.fingerprint, s2.error_type, s2.culprit,
                       COUNT(DISTINCT s2.id) AS co_count
                FROM sentry_incidents s1
                JOIN sentry_incidents s2
                  ON s2.fingerprint != s1.fingerprint
                  AND s2.created_at BETWEEN s1.created_at - INTERVAL '{interval}'
                                         AND s1.created_at + INTERVAL '{interval}'
                  AND s2.family_head_id IS NULL
                WHERE s1.fingerprint = :fp
                  AND s1.family_head_id IS NULL
                GROUP BY s2.fingerprint, s2.error_type, s2.culprit
                HAVING COUNT(DISTINCT s2.id) >= :min_count
                ORDER BY co_count DESC
                LIMIT 5
            """), {"fp": fingerprint, "min_count": min_count}).fetchall()

            for r in rows:
                fp = r[0]
                if fp not in seen_fps:
                    seen_fps.add(fp)
                    results.append({
                        "fingerprint": fp,
                        "error_type": r[1],
                        "culprit": r[2],
                        "co_occurrence_count": r[3],
                        "window": window_name,
                    })
        except Exception as exc:
            log.warning("scoring_calibration: co-occurrence query failed (%s): %s", window_name, exc)

    return results[:10]


def find_temporal_patterns(db: Session, fingerprint: str) -> dict:
    """
    Detect leading and trailing temporal patterns.

    Leading indicators: other families that tend to PRECEDE this family's incidents.
    Trailing effects: other families that tend to FOLLOW this family's incidents.

    A→B pattern: if B consistently appears 1-6h AFTER A, then A is a leading
    indicator for B. Requires 2+ occurrences.

    Returns {leading_indicators: [...], trailing_effects: [...]}.
    """
    result: dict = {"leading_indicators": [], "trailing_effects": []}

    try:
        # Leading: other families that appear BEFORE this one (1-6h window)
        leading = db.execute(text("""
            SELECT s2.fingerprint, s2.error_type, s2.culprit,
                   COUNT(DISTINCT s2.id) AS lead_count
            FROM sentry_incidents s1
            JOIN sentry_incidents s2
              ON s2.fingerprint != s1.fingerprint
              AND s2.created_at BETWEEN s1.created_at - INTERVAL '6 hours'
                                     AND s1.created_at - INTERVAL '5 minutes'
              AND s2.family_head_id IS NULL
            WHERE s1.fingerprint = :fp
              AND s1.family_head_id IS NULL
            GROUP BY s2.fingerprint, s2.error_type, s2.culprit
            HAVING COUNT(DISTINCT s2.id) >= 2
            ORDER BY lead_count DESC
            LIMIT 3
        """), {"fp": fingerprint}).fetchall()

        result["leading_indicators"] = [
            {"fingerprint": r[0], "error_type": r[1], "culprit": r[2],
             "occurrence_count": r[3], "direction": "precedes_this"}
            for r in leading
        ]

        # Trailing: other families that appear AFTER this one (1-6h window)
        trailing = db.execute(text("""
            SELECT s2.fingerprint, s2.error_type, s2.culprit,
                   COUNT(DISTINCT s2.id) AS trail_count
            FROM sentry_incidents s1
            JOIN sentry_incidents s2
              ON s2.fingerprint != s1.fingerprint
              AND s2.created_at BETWEEN s1.created_at + INTERVAL '5 minutes'
                                     AND s1.created_at + INTERVAL '6 hours'
              AND s2.family_head_id IS NULL
            WHERE s1.fingerprint = :fp
              AND s1.family_head_id IS NULL
            GROUP BY s2.fingerprint, s2.error_type, s2.culprit
            HAVING COUNT(DISTINCT s2.id) >= 2
            ORDER BY trail_count DESC
            LIMIT 3
        """), {"fp": fingerprint}).fetchall()

        result["trailing_effects"] = [
            {"fingerprint": r[0], "error_type": r[1], "culprit": r[2],
             "occurrence_count": r[3], "direction": "follows_this"}
            for r in trailing
        ]

    except Exception as exc:
        log.warning("scoring_calibration: temporal pattern query failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Remediation-class learning
# ---------------------------------------------------------------------------
#
# Derives a fix category from patch files + patch summary text.
# Deterministic: keyword/path matching, no LLM.
# Tracks effectiveness per class and feeds into confidence scoring.

# (keyword_in_file_or_summary, class_name)
_REMEDIATION_RULES: list[tuple[list[str], str]] = [
    (["config", ".env", "settings", "ecosystem"], "config_fix"),
    (["webhook", "shopify_webhook", "ensure_webhook"], "webhook_fix"),
    (["oauth", "session", "cookie", "token", "auth", "jwt", "login"], "auth_fix"),
    (["is not none", "is none", "if not ", "or none", "getattr(", "optional", "nullable", "null check", "none check", "nonetype"], "null_guard"),
    (["retry", "backoff", "exponential", "max_retries"], "retry_logic"),
    (["import ", "from ", "nameerror", "importerror", "modulenotfounderror"], "import_fix"),
    (["query", "select ", "insert ", "update ", " join ", "sqlalchemy", "integrityerror", "cursor", "fetchone", "fetchall"], "query_fix"),
    (["try:", "except", "catch", "raise", "error(", "exception("], "error_handling_fix"),
    (["tracker", "pixel", "spark-tracker", "spark-pixel", "event_type"], "tracking_fix"),
    (["dashboard", "component", "tsx", "jsx", "react", "frontend"], "ui_fix"),
]


def classify_remediation(
    patch_files: str | None,
    patch_summary: str | None,
    patch_diff: str | None = None,
) -> str:
    """
    Classify a fix into a remediation category from patch metadata.

    Deterministic: keyword matching on file paths, summary text, and diff.
    Returns one of the defined classes or "unknown".
    """
    # Build searchable text from all available patch data
    searchable = ""
    if patch_files:
        searchable += patch_files.lower() + " "
    if patch_summary:
        searchable += patch_summary.lower() + " "
    if patch_diff:
        # Only use first 1000 chars of diff for classification speed
        searchable += patch_diff[:1000].lower()

    if not searchable.strip():
        return "unknown"

    for keywords, class_name in _REMEDIATION_RULES:
        for kw in keywords:
            if kw.lower() in searchable:
                return class_name

    return "unknown"


def _calibrate_remediation_confidence(db: Session) -> dict[str, CalibrationOffset]:
    """
    Per-remediation-class confidence calibration.

    Logic: same as domain calibration — compare predicted confidence
    to actual outcomes per remediation_class. Classes with historically
    poor outcomes → lower future confidence for similar fixes.
    """
    offsets: dict[str, CalibrationOffset] = {}
    cutoff = _now() - timedelta(days=90)

    try:
        # ISOLATION GATE: Only real_merchant outcomes drive calibration offsets.
        rows = db.execute(text("""
            SELECT remediation_class, fix_confidence, outcome_status
            FROM bugfix_candidates
            WHERE remediation_class IS NOT NULL
              AND remediation_class != 'unknown'
              AND fix_confidence IS NOT NULL
              AND outcome_status IN ('effective', 'ineffective')
              AND outcome_measured_at >= :cutoff
              AND evidence_source = 'real_merchant'
        """), {"cutoff": cutoff}).fetchall()

        class_data: dict[str, list] = {}
        for cls, conf, outcome in rows:
            if cls not in class_data:
                class_data[cls] = []
            class_data[cls].append((conf, outcome))

        for cls, pairs in class_data.items():
            if len(pairs) < _MIN_EVIDENCE:
                offsets[cls] = CalibrationOffset(
                    f"remediation:{cls}", 0,
                    f"insufficient evidence ({len(pairs)}/{_MIN_EVIDENCE})",
                    len(pairs), adapted=False,
                )
                continue

            # Effectiveness rate for this class
            effective = sum(1 for _, o in pairs if o == "effective")
            eff_pct = effective / len(pairs) * 100

            # Offset: classes with >70% effectiveness → boost confidence
            # Classes with <40% effectiveness → reduce confidence
            if eff_pct >= 70:
                raw_offset = min(15, int((eff_pct - 60) / 3))
            elif eff_pct < 40:
                raw_offset = max(-15, -int((60 - eff_pct) / 3))
            else:
                raw_offset = 0

            offset = max(_REMEDIATION_CONFIDENCE_OFFSET_BOUNDS["min"],
                         min(_REMEDIATION_CONFIDENCE_OFFSET_BOUNDS["max"], raw_offset))

            offsets[cls] = CalibrationOffset(
                f"remediation:{cls}", offset,
                f"effectiveness={eff_pct:.0f}% from {len(pairs)} outcomes",
                len(pairs), adapted=(offset != 0),
            )

    except Exception as exc:
        log.warning("scoring_calibration: remediation calibration failed: %s", exc)

    return offsets


# ---------------------------------------------------------------------------
# Self-evaluation — system audits its own performance
# ---------------------------------------------------------------------------

@dataclass
class SelfEvalReport:
    """Periodic self-evaluation of system intelligence quality."""
    evaluated_at: str
    total_outcomes: int
    effectiveness_pct: float | None
    avg_confidence_accuracy: float | None   # how well confidence predicts outcomes
    priority_alignment_pct: float | None    # do high-priority bugs matter more?
    calibration_active: bool
    degradation_detected: bool
    degradation_reasons: list[str]
    recommendations: list[str]

    def to_dict(self) -> dict:
        return {
            "evaluated_at": self.evaluated_at,
            "total_outcomes": self.total_outcomes,
            "effectiveness_pct": self.effectiveness_pct,
            "avg_confidence_accuracy": self.avg_confidence_accuracy,
            "priority_alignment_pct": self.priority_alignment_pct,
            "calibration_active": self.calibration_active,
            "degradation_detected": self.degradation_detected,
            "degradation_reasons": self.degradation_reasons,
            "recommendations": self.recommendations,
        }


def run_self_evaluation(db: Session) -> SelfEvalReport:
    """
    System self-audit: evaluate whether scoring intelligence is improving.

    Checks:
    1. Overall effectiveness rate (target: >50%)
    2. Confidence accuracy (predicted vs actual)
    3. Priority alignment (do high-priority fixes succeed more?)
    4. Trend: is the system getting better or worse?

    Returns a report with degradation flags and recommendations.
    """
    now = _now()
    cutoff_30d = now - timedelta(days=30)
    cutoff_7d = now - timedelta(days=7)
    degradation_reasons: list[str] = []
    recommendations: list[str] = []

    # --- 1. Overall effectiveness ---
    effectiveness_pct = None
    total_outcomes = 0
    try:
        rows = db.execute(text("""
            SELECT outcome_status, COUNT(*)
            FROM bugfix_candidates
            WHERE outcome_status IS NOT NULL
              AND outcome_measured_at >= :cutoff
            GROUP BY outcome_status
        """), {"cutoff": cutoff_30d}).fetchall()
        outcome_map = {r[0]: r[1] for r in rows}
        total_outcomes = sum(outcome_map.values())
        if total_outcomes > 0:
            effectiveness_pct = round(outcome_map.get("effective", 0) / total_outcomes * 100, 1)
    except Exception as exc:
        log.warning("scoring_calibration: effectiveness query failed: %s", exc)

    if effectiveness_pct is not None and effectiveness_pct < 40:
        degradation_reasons.append(f"effectiveness={effectiveness_pct}% (<40% threshold)")
        recommendations.append("Review recent ineffective fixes for pattern — consider tightening auto-apply confidence threshold")

    # --- 2. Confidence accuracy ---
    avg_confidence_accuracy = None
    try:
        rows = db.execute(text("""
            SELECT fix_confidence, outcome_status
            FROM bugfix_candidates
            WHERE fix_confidence IS NOT NULL
              AND outcome_status IN ('effective', 'ineffective')
              AND outcome_measured_at >= :cutoff
        """), {"cutoff": cutoff_30d}).fetchall()

        if len(rows) >= 3:
            # Mean absolute error between prediction and outcome
            total_abs_error = sum(
                abs((c / 100.0) - (1.0 if o == "effective" else 0.0))
                for c, o in rows
            )
            mae = total_abs_error / len(rows)
            avg_confidence_accuracy = round((1.0 - mae) * 100, 1)  # invert: 100% = perfect

            if avg_confidence_accuracy < 50:
                degradation_reasons.append(f"confidence_accuracy={avg_confidence_accuracy}% (<50%)")
                recommendations.append("Confidence predictions are unreliable — calibration offsets should correct over time")
    except Exception as exc:
        log.warning("scoring_calibration: confidence accuracy query failed: %s", exc)

    # --- 3. Priority alignment ---
    priority_alignment_pct = None
    try:
        rows = db.execute(text("""
            SELECT priority_score, outcome_status
            FROM bugfix_candidates
            WHERE priority_score IS NOT NULL
              AND outcome_status IN ('effective', 'ineffective')
              AND outcome_measured_at >= :cutoff
        """), {"cutoff": cutoff_30d}).fetchall()

        if len(rows) >= 5:
            # Check: do high-priority candidates (>50) succeed more than low-priority?
            high_pri = [(p, o) for p, o in rows if p > 50]
            low_pri = [(p, o) for p, o in rows if p <= 50]

            high_eff = sum(1 for _, o in high_pri if o == "effective") / len(high_pri) * 100 if high_pri else 0
            low_eff = sum(1 for _, o in low_pri if o == "effective") / len(low_pri) * 100 if low_pri else 0

            # Alignment = high_pri effectiveness should exceed low_pri
            if high_eff >= low_eff:
                priority_alignment_pct = round(high_eff, 1)
            else:
                priority_alignment_pct = round(high_eff, 1)
                degradation_reasons.append(
                    f"priority misalignment: high-pri effectiveness={high_eff:.0f}% "
                    f"< low-pri={low_eff:.0f}%"
                )
                recommendations.append("Priority scoring is not predicting impact — review severity/subsystem weights")
    except Exception as exc:
        log.warning("scoring_calibration: priority alignment query failed: %s", exc)

    # --- 4. Calibration status ---
    calibration = get_scoring_calibration(db)
    calibration_active = any(
        o.adapted for o in calibration.severity_offsets.values()
    ) or any(
        o.adapted for o in calibration.domain_confidence_offsets.values()
    ) or (calibration.global_confidence_offset and calibration.global_confidence_offset.adapted)

    degradation_detected = len(degradation_reasons) > 0

    report = SelfEvalReport(
        evaluated_at=now.isoformat() + "Z",
        total_outcomes=total_outcomes,
        effectiveness_pct=effectiveness_pct,
        avg_confidence_accuracy=avg_confidence_accuracy,
        priority_alignment_pct=priority_alignment_pct,
        calibration_active=calibration_active,
        degradation_detected=degradation_detected,
        degradation_reasons=degradation_reasons,
        recommendations=recommendations,
    )

    # Minimum sample gate: statistics on <10 outcomes are noise, not signal.
    # Suppress the degradation alert below the confidence threshold.
    _MIN_OUTCOMES_FOR_ALERT = 10
    sample_sufficient = total_outcomes >= _MIN_OUTCOMES_FOR_ALERT

    if degradation_detected and sample_sufficient:
        log.warning(
            "self_eval: DEGRADATION DETECTED — %s",
            "; ".join(degradation_reasons),
        )
        # Fire ops_alert for degradation
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="scoring_calibration",
                alert_type="intelligence_degradation",
                summary=f"Self-eval: {'; '.join(degradation_reasons)}",
                detail=report.to_dict(),
            )
        except Exception as exc:
            log.warning("scoring_calibration: degradation alert write failed: %s", exc)
    elif degradation_detected and not sample_sufficient:
        log.info(
            "self_eval: degradation signal suppressed (only %d outcomes, need %d)",
            total_outcomes, _MIN_OUTCOMES_FOR_ALERT,
        )
    else:
        log.info(
            "self_eval: healthy — effectiveness=%s%% confidence_accuracy=%s%% outcomes=%d",
            effectiveness_pct, avg_confidence_accuracy, total_outcomes,
        )

    return report


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_scoring_calibration(db: Session) -> ScoringCalibration:
    """
    Compute all scoring calibration offsets from outcome evidence.

    Pure computation — no stored state. Same outcomes → same calibration.
    Safe under failure — returns zero offsets if evidence is insufficient.
    """
    return ScoringCalibration(
        severity_offsets=_calibrate_severity_offsets(db),
        subsystem_offsets=_calibrate_subsystem_offsets(db),
        domain_confidence_offsets=_calibrate_domain_confidence(db),
        remediation_confidence_offsets=_calibrate_remediation_confidence(db),
        global_confidence_offset=_calibrate_global_confidence(db),
    )
