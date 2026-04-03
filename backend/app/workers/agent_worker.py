import logging
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.append("/opt/wishspark/backend")

from app.core.logging_config import configure_logging, set_worker_context
configure_logging()
set_worker_context(worker_name="agent_worker")

from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.models.product_opportunity import ProductOpportunity
from app.models.price_intelligence import PriceIntelligence
from app.models.worker_log import WorkerLog
from app.models.worker_state import WorkerState
from app.sandbox.sandbox_executor import create_sandbox_run, update_sandbox_status
from app.services.product_intelligence_engine import build_product_intelligence

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKER_NAME = "agent_worker"
SLEEP_SECONDS = 900     # 15 minutes between cycles


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log = logging.getLogger("worker.agent")

def log(msg):
    _log.info(msg)


# ---------------------------------------------------------------------------
# WorkerState helpers
# ---------------------------------------------------------------------------

def _load_state(db) -> WorkerState:
    """
    Return the WorkerState row for this worker, creating it if absent.
    last_watermark is unused by this worker; last_run_at is the only
    field updated each cycle.
    """
    state = (
        db.query(WorkerState)
        .filter(WorkerState.worker_name == WORKER_NAME)
        .first()
    )
    if state is None:
        state = WorkerState(
            worker_name=WORKER_NAME,
            last_watermark=None,
            last_run_at=None,
        )
        db.add(state)
        db.commit()
        db.refresh(state)
        log("created new worker_state row (first run)")
    return state


def _save_state(db, state: WorkerState) -> None:
    state.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


# ---------------------------------------------------------------------------
# WorkerLog helper
# ---------------------------------------------------------------------------

def _write_log(
    db,
    started_at: datetime,
    shops_processed: int,
    rows_written: int,
    errors: int,
    error_detail: str | None,
) -> None:
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    entry = WorkerLog(
        worker_name=WORKER_NAME,
        started_at=started_at,
        finished_at=finished_at,
        shops_processed=shops_processed,
        rows_written=rows_written,
        errors=errors,
        error_detail=error_detail,
        duration_ms=duration_ms,
    )
    db.add(entry)
    db.commit()


# ---------------------------------------------------------------------------
# Business logic (unchanged)
# ---------------------------------------------------------------------------

def fetch_targets():
    db = SessionLocal()
    try:
        opportunities = db.query(ProductOpportunity).order_by(ProductOpportunity.priority_score.desc()).limit(3).all()
        targets = []

        for opp in opportunities:
            price = db.query(PriceIntelligence).filter(
                PriceIntelligence.product_url == opp.product_url
            ).first()

            targets.append({
                "goal": "analyze product opportunity",
                "product_name": opp.product_url,
                "product_url": opp.product_url,
                "avg_intent_score": float(opp.avg_intent_score or 0),
                "confidence": float(opp.priority_score or 0),
                "recommended_action": opp.recommended_action or "NONE",
                "price_opportunity": getattr(price, "price_opportunity", "UNKNOWN") if price else "UNKNOWN",
            })

        if not targets:
            targets = [
                {"goal": "analyze pricing strategy"},
                {"goal": "analyze product opportunity"},
                {"goal": "analyze conversion opportunity"},
            ]

        return targets
    finally:
        db.close()


def write_report(run_path: str, goal: str, analysis: dict):
    report_path = Path(run_path) / "report.md"

    content = f"""# WishSpark Sandbox Report

Generated at: {datetime.now(timezone.utc).isoformat()} UTC

## Goal
{goal}

## Analysis Summary
{analysis.get("summary", "No summary available")}

## Commercial Priority
{analysis.get("commercial_priority", "UNKNOWN")}

## Recommended Action
{analysis.get("recommended_action", "NONE")}

## Status
planned
"""

    report_path.write_text(content, encoding="utf-8")


def write_analysis(run_path: str, analysis: dict):
    analysis_path = Path(run_path) / "analysis.json"
    analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def _run_orchestrator():
    """Run the AI orchestrator — decision/execution + outcome evaluation."""
    db = SessionLocal()
    try:
        # Phase A: Decision + execution
        from app.services.orchestrator import run_orchestrator_cycle
        result = run_orchestrator_cycle(db)
        if result.actions_executed > 0 or result.actions_failed > 0:
            log(f"orchestrator: exec={result.actions_executed} skip={result.actions_skipped} fail={result.actions_failed}")

        # Phase B: Evaluate outcomes from previous cycles
        from app.services.outcome_evaluator import evaluate_pending_outcomes
        eval_result = evaluate_pending_outcomes(db)
        db.commit()
        if eval_result.evaluated > 0:
            log(f"outcomes: evaluated={eval_result.evaluated} success={eval_result.success} no_effect={eval_result.no_effect}")

        # Phase B2: Evaluate merge outcomes
        from app.services.merge_intelligence import evaluate_merge_outcomes
        merge_eval = evaluate_merge_outcomes(db)
        db.commit()
        if merge_eval["evaluated"] > 0:
            log(f"merge_eval: healthy={merge_eval['healthy']} regressed={merge_eval['regressed']}")
    except Exception as exc:
        log(f"orchestrator error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_onboarding():
    """Run pending merchant onboarding."""
    db = SessionLocal()
    try:
        from app.services.onboarding import run_pending_onboarding
        summary = run_pending_onboarding(db)
        if summary["processed"] > 0:
            log(f"onboarding: ready={summary['ready']} failed={summary['failed']}")
    except Exception as exc:
        log(f"onboarding error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_onboarding_health():
    """Check onboarding pipeline health + funnel friction detection."""
    db = SessionLocal()
    try:
        from app.services.onboarding_health import write_onboarding_alerts
        result = write_onboarding_alerts(db)
        if result["alerts_written"] > 0:
            log(f"onboarding_health: alerts={result['alerts_written']} stuck={result['stuck']} pixel_abandon={result['pixel_abandon']}")
    except Exception as exc:
        log(f"onboarding_health error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()

    # Funnel friction detection (separate session for isolation)
    db2 = SessionLocal()
    try:
        from app.services.onboarding_funnel import run_friction_detection
        friction = run_friction_detection(db2)
        if friction["friction_signals"] > 0 or friction["alerts_written"] > 0:
            log(f"onboarding_funnel: signals={friction['friction_signals']} alerts={friction['alerts_written']} insights={friction['insights']}")
    except Exception as exc:
        log(f"onboarding_funnel error (non-fatal): {exc}")
        db2.rollback()
    finally:
        db2.close()


def _run_bug_triage(auto_apply_paused: bool = False):
    """Scan for new bug-worthy events, create candidates, auto-propose, auto-apply, auto-promote."""
    db = SessionLocal()
    try:
        from app.services.bugfix_pipeline import run_bug_triage, run_auto_propose, run_auto_apply
        from app.services.promotion_pipeline import run_auto_promotion

        # Build phase list — skip auto_apply and auto_promotion when circuit breaker is tripped
        phases = [
            ("triage", lambda: run_bug_triage(db)),
            ("auto_propose", lambda: run_auto_propose(db)),
        ]
        if not auto_apply_paused:
            phases.append(("auto_apply", lambda: run_auto_apply(db)))
            phases.append(("auto_promotion", lambda: run_auto_promotion(db)))
        else:
            log("bug_triage: auto_apply PAUSED by circuit breaker — triage and propose continue")

        # Each phase is isolated — one failure does not kill the others
        for phase_name, phase_fn in phases:
            try:
                result = phase_fn()
                db.commit()
                # Log only when something happened
                if isinstance(result, dict):
                    interesting = {k: v for k, v in result.items() if v and k not in ("scanned",)}
                    if interesting:
                        log(f"{phase_name}: {interesting}")
            except Exception as exc:
                log(f"{phase_name} error (non-fatal): {exc}")
                db.rollback()

    finally:
        db.close()


def _run_bugfix_outcome_eval():
    """Evaluate applied bugfix outcomes (48h delay, closed-loop learning)."""
    db = SessionLocal()
    try:
        from app.services.evolution_outcomes import evaluate_bugfix_outcomes
        summary = evaluate_bugfix_outcomes(db)
        db.commit()
        if summary["evaluated"] > 0:
            log(f"bugfix_outcomes: evaluated={summary['evaluated']} effective={summary['effective']} ineffective={summary['ineffective']}")

        # Closed-loop: reopen ineffective fixes as new candidates
        from app.services.loop_health import reopen_from_ineffective
        reopen = reopen_from_ineffective(db)
        db.commit()
        if reopen["reopened"] > 0:
            log(f"loop_reopen: reopened={reopen['reopened']} suppressed={reopen['suppressed']}")

        # Self-caused regression detection
        from app.services.evolution_outcomes import detect_self_caused_regressions
        regression = detect_self_caused_regressions(db)
        db.commit()
        if regression["flagged"] > 0:
            log(f"self_regression: flagged={regression['flagged']} checked={regression['checked']}")

        # Auto-resolve thrash alerts for stabilized sources
        from app.services.loop_health import auto_resolve_thrash_alerts
        resolved = auto_resolve_thrash_alerts(db)
        db.commit()
        if resolved["resolved"] > 0:
            log(f"thrash_resolve: resolved={resolved['resolved']} checked={resolved['checked']}")
    except Exception as exc:
        log(f"bugfix_outcomes error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_evolution_audit():
    """Run weekly evolution audit if cooldown expired."""
    from app.services.evolution_engine import should_run_audit, run_evolution_audit, mark_audit_run
    if not should_run_audit():
        return
    db = SessionLocal()
    try:
        summary = run_evolution_audit(db)
        db.commit()
        mark_audit_run()
        if summary["new"] > 0:
            log(f"evolution: new={summary['new']} deduped={summary['deduped']}")
    except Exception as exc:
        log(f"evolution error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_model_upgrade_scan():
    """Scan for model upgrade candidates (weekly, config-driven)."""
    from app.services.model_upgrade_agent import should_run_scan, scan_for_upgrades, mark_scan_run
    if not should_run_scan():
        return
    db = SessionLocal()
    try:
        summary = scan_for_upgrades(db)
        db.commit()
        mark_scan_run()
        if summary["created"] > 0:
            log(f"model_upgrade: created={summary['created']}")
    except Exception as exc:
        log(f"model_upgrade error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_meta_review():
    """Run weekly meta-review for strategic prioritization."""
    from app.services.meta_reviewer import should_run_meta_review, run_meta_review, mark_meta_review_run
    if not should_run_meta_review():
        return
    db = SessionLocal()
    try:
        result = run_meta_review(db)
        db.commit()
        mark_meta_review_run()
        if result.get("status") == "completed":
            review = result.get("review", {})
            log(f"meta_review: focus={review.get('weekly_focus_area', '?')} proposals={result.get('review', {}).get('priorities', []).__len__()} conflicts={len(review.get('conflicts', []))}")
        elif result.get("reason"):
            log(f"meta_review: skipped ({result['reason']})")
    except Exception as exc:
        log(f"meta_review error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_evolution_conversion():
    """Convert eligible LEVEL_1 evolution proposals into bugfix candidates."""
    db = SessionLocal()
    try:
        from app.services.evolution_converter import convert_eligible_proposals
        summary = convert_eligible_proposals(db)
        db.commit()
        if summary["converted"] > 0:
            log(f"evolution_convert: converted={summary['converted']} dedup={summary['skipped_dedup']}")
    except Exception as exc:
        log(f"evolution_convert error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_evolution_gc():
    """Run daily evolution proposal garbage collection if cooldown expired."""
    from app.services.evolution_gc import should_run_gc, run_evolution_gc, mark_gc_run
    if not should_run_gc():
        return
    db = SessionLocal()
    try:
        summary = run_evolution_gc(db)
        db.commit()
        mark_gc_run()
        total = summary["obsolete"] + summary["resolved_indirectly"] + summary["needs_revalidation"]
        if total > 0:
            log(f"evolution_gc: obsolete={summary['obsolete']} resolved={summary['resolved_indirectly']} revalidate={summary['needs_revalidation']}")
    except Exception as exc:
        log(f"evolution_gc error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_monthly_evolution_audit():
    """Run monthly Opus evolution audit if cooldown expired. Send Telegram summary."""
    from app.services.monthly_evolution_audit import should_run_monthly_audit, run_monthly_opus_audit, mark_monthly_audit_run
    if not should_run_monthly_audit():
        return

    # ALWAYS mark cooldown regardless of outcome — prevents infinite retry
    # when LLM keys aren't configured (the "skipped every 15 min" bug).
    mark_monthly_audit_run()

    db = SessionLocal()
    try:
        result = run_monthly_opus_audit(db)
        db.commit()

        status = result.get("status", "skipped")
        actually_ran = status == "completed" and result.get("proposals_created", 0) > 0

        if actually_ran:
            log(f"monthly_opus_audit: created={result['proposals_created']} cycle={result.get('cycle')}")
        elif status == "skipped":
            log(f"monthly_opus_audit: skipped — {result.get('reason', 'unknown')}")

        # Send Telegram ONLY when audit actually produced proposals.
        # "Skipped" is a non-event — log it, don't spam Telegram.
        if actually_ran:
            try:
                from app.services.telegram_agent import send_monthly_report, is_configured
                if is_configured():
                    from app.services.system_summary import build_system_summary
                    summary = build_system_summary(db)
                    send_monthly_report(result.get("proposals", []), summary)
            except Exception as exc:
                log(f"telegram report error (non-fatal): {exc}")

    except Exception as exc:
        log(f"monthly_opus_audit error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_scaling_intelligence():
    """Daily snapshot + scaling forecast/recommendations (cooldown-protected)."""
    from app.services.scaling_intelligence import (
        should_capture_snapshot, capture_daily_snapshot, mark_snapshot_captured,
        should_generate_recommendations, generate_recommendations, mark_recommendations_generated,
        build_forecast,
    )

    # Daily snapshot
    if should_capture_snapshot():
        db = SessionLocal()
        try:
            capture_daily_snapshot(db)
            db.commit()
            mark_snapshot_captured()
        except Exception as exc:
            log(f"scaling snapshot error (non-fatal): {exc}")
            db.rollback()
        finally:
            db.close()

    # Recommendations (daily)
    if should_generate_recommendations():
        db = SessionLocal()
        try:
            recs = generate_recommendations(db)
            db.commit()
            mark_recommendations_generated()

            # Notify via Telegram for significant recommendations (with reviewer context)
            if recs:
                try:
                    from app.services.telegram_agent import send_scaling_alert, send_reviewer_verdict, is_configured
                    if is_configured():
                        forecast = build_forecast(db)
                        # Only send full alert if forecast has real data
                        if forecast.get("status") == "ok":
                            for r in recs[:2]:  # max 2 notifications
                                send_scaling_alert(r, forecast)
                                # Attach reviewer assessment
                                try:
                                    from app.services.reviewer_layer import review_entity
                                    rec_id = r.get("id")
                                    if rec_id:
                                        assessment = review_entity(db, "scaling_recommendation", rec_id)
                                        if assessment:
                                            db.flush()
                                            send_reviewer_verdict(assessment, entity_title=r.get("title"))
                                except Exception:
                                    pass
                        else:
                            from app.services.telegram_agent import send_message
                            send_message(
                                f"*Scaling* \u2014 {len(recs)} recommendation(s) generated\n\n"
                                f"Forecast: insufficient data ({forecast.get('snapshots_available', 0)}/{forecast.get('minimum_required', 5)} days)\n"
                                f"Projections not yet reliable. Use /scaling for details."
                            )
                except Exception as exc:
                    log(f"scaling telegram error (non-fatal): {exc}")

        except Exception as exc:
            log(f"scaling recommendations error (non-fatal): {exc}")
            db.rollback()
        finally:
            db.close()


def _run_entitlement_health_scan():
    """Scan all active merchants for plan/billing mismatches. Create ops alerts for issues."""
    db = SessionLocal()
    try:
        from app.models.merchant import Merchant
        from app.services.merchant_chatbot import check_entitlement_health

        merchants = (
            db.query(Merchant)
            .filter(Merchant.install_status == "active")
            .all()
        )

        issues_found = 0
        for m in merchants:
            health = check_entitlement_health(db, m.shop_domain)
            if not health["healthy"]:
                issues_found += 1
                try:
                    from app.services.alerting import write_alert
                    # Dedup: check if alert already exists for this shop
                    from app.models.ops_alert import OpsAlert
                    existing = (
                        db.query(OpsAlert)
                        .filter(
                            OpsAlert.shop_domain == m.shop_domain,
                            OpsAlert.alert_type == "entitlement_mismatch",
                            OpsAlert.resolved_at.is_(None),
                        )
                        .first()
                    )
                    if not existing:
                        write_alert(
                            db, severity="warning", source="entitlement_scan",
                            alert_type="entitlement_mismatch",
                            summary=f"Entitlement mismatch: {health['issues']}",
                            shop_domain=m.shop_domain,
                        )
                except Exception:
                    pass

        if issues_found > 0:
            log(f"entitlement_scan: {issues_found} mismatches found across {len(merchants)} merchants")
        db.commit()
    except Exception as exc:
        log(f"entitlement_scan error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_brain_refresh():
    """Refresh the project brain snapshot if cooldown expired (daily)."""
    from app.services.project_brain import should_refresh_brain, build_full_snapshot, mark_brain_refreshed
    if not should_refresh_brain():
        return
    db = SessionLocal()
    try:
        snapshot = build_full_snapshot(db)
        db.commit()
        mark_brain_refreshed()
        log(f"brain_refresh: id={snapshot.id} files={snapshot.total_files} critical={snapshot.critical_files}")
    except Exception as exc:
        log(f"brain_refresh error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Daily health digest — DB-based dedup (survives PM2 restart + Redis reset)
# ---------------------------------------------------------------------------

def _today_rome() -> str:
    """Return today's date string in Europe/Rome timezone."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


def _run_daily_digest():
    """
    Send daily health digest to Telegram — ONCE per calendar day (Europe/Rome).

    Dedup: stored in worker_state.last_digest_date (DB column).
    Survives PM2 restarts, Redis resets, process crashes.
    """
    from app.services.telegram_agent import send_daily_digest, is_configured
    if not is_configured():
        return

    today = _today_rome()

    db = SessionLocal()
    try:
        from sqlalchemy import text
        # Check if already sent today (DB is the source of truth)
        row = db.execute(text(
            "SELECT last_digest_date FROM worker_state WHERE worker_name = 'agent_worker'"
        )).fetchone()

        if row and row[0] == today:
            return  # already sent today

        # Send digest
        sent = send_daily_digest(db)

        if sent:
            db.execute(text(
                "UPDATE worker_state SET last_digest_date = :today WHERE worker_name = 'agent_worker'"
            ), {"today": today})
            db.commit()
            log(f"daily_digest: sent for {today}")
        else:
            log("daily_digest: send failed — will retry next cycle")
    except Exception as exc:
        log(f"daily_digest error (non-fatal): {exc}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _run_merchant_digest():
    """Send weekly email digests to eligible merchants (Monday only, Europe/Rome)."""
    from app.services.merchant_digest import _is_monday_rome, run_merchant_digest_cycle

    if not _is_monday_rome():
        return

    db = SessionLocal()
    try:
        summary = run_merchant_digest_cycle(db)
        db.commit()
        if summary["sent"] > 0 or summary["failed"] > 0:
            log(f"merchant_digest: sent={summary['sent']} failed={summary['failed']} no_data={summary['no_data']}")
    except Exception as exc:
        log(f"merchant_digest error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_lifecycle_emails():
    """
    Send lifecycle emails for merchants needing attention.

    Three trigger types, each with its own dedup/cooldown:
      1. setup_incomplete — merchant installed >24h ago, still not ready
      2. first_insight    — first opportunity_signal appeared (once per shop)
      3. connection_issue — merchant stuck in degraded/failed state >2h

    All dedup is handled inside send_lifecycle_email — safe to call every cycle.
    """
    db = SessionLocal()
    try:
        from app.services.merchant_email_service import send_lifecycle_email
        from app.models.merchant import Merchant
        from datetime import timedelta
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        emails_sent = 0

        # --- 1. Setup incomplete: installed >24h ago, not ready ---
        install_cutoff = now - timedelta(hours=24)
        stuck_merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.onboarding_status.in_(["pending", "failed"]),
                Merchant.installed_at < install_cutoff,
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
            )
            .limit(10)
            .all()
        )
        for m in stuck_merchants:
            hours = int((now - m.installed_at).total_seconds() / 3600) if m.installed_at else 24
            result = send_lifecycle_email(db, m.shop_domain, "setup_incomplete", {
                "issue": m.onboarding_error or "setup is incomplete",
                "hours_since_install": hours,
            })
            if result["status"] == "sent":
                emails_sent += 1
            db.commit()

        # --- 2. First insight: shops with signals that haven't received this email ---
        from sqlalchemy import text
        first_insight_shops = db.execute(text("""
            SELECT DISTINCT os.shop_domain
            FROM opportunity_signals os
            JOIN merchants m ON m.shop_domain = os.shop_domain
            WHERE m.install_status = 'active'
              AND m.contact_email IS NOT NULL
              AND m.contact_email != ''
              AND os.expires_at > now()
              AND os.shop_domain NOT IN (
                  SELECT me.shop_domain FROM merchant_emails me
                  WHERE me.email_type = 'first_insight' AND me.status = 'sent'
              )
            LIMIT 10
        """)).fetchall()

        for row in first_insight_shops:
            shop = row[0]
            # Get signal details for context
            top = db.execute(text("""
                SELECT signal_type, explanation, product_url
                FROM opportunity_signals
                WHERE shop_domain = :shop AND expires_at > now()
                ORDER BY signal_strength DESC NULLS LAST
                LIMIT 1
            """), {"shop": shop}).fetchone()

            signal_count = db.execute(text("""
                SELECT COUNT(*) FROM opportunity_signals
                WHERE shop_domain = :shop AND expires_at > now()
            """), {"shop": shop}).scalar() or 1

            ctx = {"signal_count": signal_count}
            if top:
                ctx["top_signal"] = top[1] or top[0] or "a product showing unusual visitor behavior"

            result = send_lifecycle_email(db, shop, "first_insight", ctx)
            if result["status"] == "sent":
                emails_sent += 1
            db.commit()

        # --- 3. Connection issue: stuck merchants (degraded/failed >2h) ---
        stuck_cutoff = now - timedelta(hours=2)
        degraded_merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.onboarding_status.in_(["failed"]),
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
            )
            .limit(5)
            .all()
        )
        for m in degraded_merchants:
            # Only send if they've been stuck for a while (check updated_at or installed_at)
            result = send_lifecycle_email(db, m.shop_domain, "connection_issue", {
                "issue": m.onboarding_error or "the connection to your store was lost",
            })
            if result["status"] == "sent":
                emails_sent += 1
            db.commit()

        if emails_sent > 0:
            log(f"lifecycle_emails: sent={emails_sent}")

    except Exception as exc:
        log(f"lifecycle_emails error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()


def _run_scoring_self_eval():
    """Run scoring intelligence self-evaluation. Lightweight — no LLM calls."""
    db = SessionLocal()
    try:
        from app.services.scoring_calibration import run_self_evaluation
        report = run_self_evaluation(db)
        db.commit()
        if report.degradation_detected:
            log(f"scoring_self_eval: DEGRADATION — {'; '.join(report.degradation_reasons)}")
        elif report.total_outcomes > 0:
            log(f"scoring_self_eval: healthy eff={report.effectiveness_pct}% accuracy={report.avg_confidence_accuracy}% outcomes={report.total_outcomes}")
    except Exception as exc:
        log(f"scoring_self_eval error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()

    # Check Sentry webhook health — alert if webhook goes dark
    db2 = SessionLocal()
    try:
        _check_sentry_webhook_health(db2)
        db2.commit()
    except Exception as exc:
        log(f"sentry_webhook_health error (non-fatal): {exc}")
        db2.rollback()
    finally:
        db2.close()


def _check_sentry_webhook_health(db):
    """Alert if Sentry webhook intake has gone dark while email is still receiving."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func
    from app.models.sentry_incident import SentryIncident

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_6h = now - timedelta(hours=6)

    # Count recent incidents by source
    source_counts = dict(
        db.query(SentryIncident.source_type, func.count(SentryIncident.id))
        .filter(SentryIncident.created_at >= cutoff_6h)
        .group_by(SentryIncident.source_type)
        .all()
    )

    webhook_count = source_counts.get("sentry_webhook", 0)
    email_count = source_counts.get("email", 0)

    # Alert condition: email is receiving but webhook is not
    # This means Sentry is sending alerts but webhook integration is broken/misconfigured
    if email_count > 0 and webhook_count == 0:
        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="agent_worker",
            alert_type="sentry_webhook_dark",
            summary=(
                f"Sentry webhook intake is DARK — {email_count} incidents via email fallback "
                f"in last 6h but 0 via webhook. Check Sentry webhook configuration."
            ),
            detail={
                "email_count_6h": email_count,
                "webhook_count_6h": webhook_count,
                "window_hours": 6,
            },
        )


def _run_sentry_triage():
    """Generate AI triage packets, consume into candidates, re-evaluate skipped."""
    # Phase A: Generate triage packets for newly parsed incidents
    db = SessionLocal()
    try:
        from app.services.sentry_triage import run_triage_generation
        result = run_triage_generation(db)
        db.commit()
        if result["generated"] > 0 or result["errors"] > 0:
            log(f"sentry_triage: generated={result['generated']} errors={result['errors']}")
    except Exception as exc:
        log(f"sentry_triage generation error (non-fatal): {exc}")
        db.rollback()
    finally:
        db.close()

    # Phase B: Consume ready triage packets into bugfix candidates
    db2 = SessionLocal()
    try:
        from app.services.sentry_triage import consume_triage_queue
        result = consume_triage_queue(db2)
        db2.commit()
        if result["consumed"] > 0 or result["errors"] > 0:
            log(
                f"sentry_consume: consumed={result['consumed']} "
                f"deduped={result['deduped']} skipped={result['skipped']} "
                f"suppressed={result['suppressed']} errors={result['errors']}"
            )
    except Exception as exc:
        log(f"sentry_consume error (non-fatal): {exc}")
        db2.rollback()
    finally:
        db2.close()

    # Phase C: Re-evaluate previously skipped incidents that gained recurrences
    db3 = SessionLocal()
    try:
        from app.services.sentry_triage import reevaluate_skipped_families
        result = reevaluate_skipped_families(db3)
        db3.commit()
        if result["promoted"] > 0:
            log(f"sentry_reevaluate: promoted={result['promoted']}")
    except Exception as exc:
        log(f"sentry_reevaluate error (non-fatal): {exc}")
        db3.rollback()
    finally:
        db3.close()


# ---------------------------------------------------------------------------
# Circuit breaker — pause auto-apply when system is unhealthy
# ---------------------------------------------------------------------------

_consecutive_unhealthy_cycles = 0
_CIRCUIT_BREAKER_THRESHOLD = 3  # pause auto-apply after 3 consecutive unhealthy cycles


def _check_circuit_breaker() -> bool:
    """
    Check loop health before proceeding with auto-apply.
    Returns True if auto-apply should be PAUSED.

    Circuit breaker trips after 3 consecutive unhealthy cycles.
    Resets when system returns to healthy.
    """
    global _consecutive_unhealthy_cycles

    db = SessionLocal()
    try:
        from app.services.loop_health import get_loop_health
        health = get_loop_health(db)

        # Get adaptive circuit breaker threshold (bounded, evidence-aware)
        try:
            from app.services.adaptive_governance import get_adaptive_thresholds
            cb_threshold = get_adaptive_thresholds(db).circuit_breaker_threshold
        except Exception:
            cb_threshold = _CIRCUIT_BREAKER_THRESHOLD

        if health["is_healthy"]:
            if _consecutive_unhealthy_cycles > 0:
                log(f"circuit_breaker: system healthy again — resetting after {_consecutive_unhealthy_cycles} unhealthy cycles")
            _consecutive_unhealthy_cycles = 0
            return False

        _consecutive_unhealthy_cycles += 1
        log(
            f"circuit_breaker: UNHEALTHY cycle {_consecutive_unhealthy_cycles}/{cb_threshold} — "
            f"stuck={len(health.get('stuck_items', []))} thrashing={len(health.get('thrashing_sources', []))} "
            f"failure_rate={health.get('failure_rate_30d_pct', 0)}% "
            f"trend={health.get('trend', {}).get('direction', 'unknown')}"
        )

        if _consecutive_unhealthy_cycles >= cb_threshold:
            log("circuit_breaker: TRIPPED — pausing auto-apply until system stabilizes")
            try:
                from app.services.alerting import write_alert
                write_alert(
                    db, severity="critical", source="agent_worker",
                    alert_type="circuit_breaker_tripped",
                    summary=f"Auto-apply paused: system unhealthy for {_consecutive_unhealthy_cycles} consecutive cycles",
                    detail={
                        "consecutive_unhealthy": _consecutive_unhealthy_cycles,
                        "failure_rate": health.get("failure_rate_30d_pct"),
                        "stuck_items": len(health.get("stuck_items", [])),
                        "thrashing_sources": len(health.get("thrashing_sources", [])),
                        "trend": health.get("trend", {}).get("direction"),
                    },
                )
                db.commit()
            except Exception:
                db.rollback()
            return True

        return False
    except Exception as exc:
        log(f"circuit_breaker: health check failed (non-fatal): {exc}")
        return False  # don't block on health check failure
    finally:
        db.close()


def _run_cto_health_check():
    """
    Phase 0: CTO Signal Layer.

    Runs FIRST every cycle. Pure operational intelligence:
    - Synthesizes health dimensions with trend detection
    - Stores state in Redis for /ops/system-health + circuit breaker + daily digest
    - Logs one-line summary for PM2 visibility
    - Telegram: ONLY for CRITICAL (system on fire). Everything else goes in daily digest.

    Strict boundaries:
    - OBSERVES only. Never prescribes, never patches, never overrides.
    - No LLM calls. No heavy queries. No side effects.
    """
    try:
        from app.services.system_health_synthesizer import (
            synthesize_health, format_telegram_signal, send_telegram_signal,
        )
        db = SessionLocal()
        try:
            health = synthesize_health(db)

            # One-line structured log (always)
            dims = " ".join(
                f"{d.name}={'OK' if d.status == 'healthy' else d.status.upper()}"
                for d in health.dimensions
            )
            log(f"[CTO] {health.overall_status.upper()} | {dims}")

            # Store in Redis (consumed by /ops/system-health, circuit breaker, daily digest)
            from app.core.redis_client import cache_set
            cache_set("hs:system_health", health.to_dict(), 900)

            # Telegram: ONLY for CRITICAL. Degraded/healthy goes in daily digest.
            if health.overall_status == "critical":
                send_telegram_signal(health)

        finally:
            db.close()
    except Exception as exc:
        log(f"[CTO] error (non-fatal): {exc}")


def _run_approved_reminders():
    """
    Send Telegram reminders for candidates that are approved but not yet applied.

    Runs every cycle (15 min). Sends reminder every 30 min per candidate
    via Redis cooldown key. Includes tappable Apply button.

    Stops reminding when candidate is applied, discarded, or rolled back.
    """
    try:
        from app.core.redis_client import cache_get, cache_set
        db = SessionLocal()
        try:
            from sqlalchemy import text as sql_text
            approved = db.execute(sql_text("""
                SELECT id, title, decided_at::text
                FROM bugfix_candidates
                WHERE status = 'approved'
                ORDER BY decided_at ASC
            """)).fetchall()

            if not approved:
                return

            from app.services.telegram_agent import send_message_with_buttons, is_configured
            if not is_configured():
                return

            for cand in approved:
                cooldown_key = f"hs:approved_reminder:{cand.id}"
                if cache_get(cooldown_key) is not None:
                    continue  # already reminded within 30 min

                # Send reminder with Apply button
                age = ""
                if cand.decided_at:
                    from datetime import datetime, timezone
                    try:
                        approved_at = datetime.fromisoformat(cand.decided_at)
                        mins = int((datetime.now(timezone.utc).replace(tzinfo=None) - approved_at).total_seconds() / 60)
                        age = f" (approved {mins}m ago)"
                    except Exception:
                        pass

                send_message_with_buttons(
                    f"*Reminder — Bugfix #{cand.id} approved, waiting for apply*{age}\n\n"
                    f"{(cand.title or '')[:100]}\n\n"
                    f"Tap to deploy:",
                    [[{"text": f"Apply #{cand.id}", "callback_data": f"/bugfix_apply {cand.id}"}]],
                )

                # Set 30-min cooldown
                cache_set(cooldown_key, True, 1800)
                log(f"approved_reminder: sent for bugfix #{cand.id}")

            # Also check pending action approvals (TIER_1 orchestrator actions)
            pending_actions = db.execute(sql_text("""
                SELECT id, action_type, target_id, reason, created_at::text, expires_at::text
                FROM action_approvals
                WHERE status = 'pending'
                ORDER BY created_at ASC
            """)).fetchall()

            for action in pending_actions:
                cooldown_key = f"hs:approval_reminder:{action.id}"
                if cache_get(cooldown_key) is not None:
                    continue

                expires_note = ""
                if action.expires_at:
                    try:
                        exp = datetime.fromisoformat(action.expires_at)
                        mins_left = int((exp - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 60)
                        if mins_left <= 0:
                            expires_note = " *EXPIRED*"
                        else:
                            expires_note = f" (expires in {mins_left}m)"
                    except Exception:
                        pass

                send_message_with_buttons(
                    f"*Action Approval #{action.id} pending*{expires_note}\n\n"
                    f"Action: {action.action_type}\n"
                    f"Target: {action.target_id or 'N/A'}\n"
                    f"Reason: {(action.reason or '')[:120]}\n\n"
                    f"Tap to approve and execute:",
                    [[{"text": f"Approve #{action.id}", "callback_data": f"/approve {action.id}"}]],
                )

                cache_set(cooldown_key, True, 1800)
                log(f"approved_reminder: sent for action #{action.id}")

        finally:
            db.close()
    except Exception as exc:
        log(f"approved_reminder error (non-fatal): {exc}")


def run_cycle():
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Phase 0: CTO-level health synthesis (runs first, sets context)
    _run_cto_health_check()

    # Phase 0b: Remind operator about approved-but-not-applied candidates
    _run_approved_reminders()

    # Phase 1: Orchestrator — reads alerts/state, executes safe actions
    _run_orchestrator()

    # Phase 2: Onboarding — ensure new merchants reach "ready" state
    _run_onboarding()

    # Phase 2b: Onboarding health — detect stuck merchants, pixel abandonment, slow activation
    _run_onboarding_health()

    # === CIRCUIT BREAKER CHECK ===
    # If system is unhealthy for 3+ cycles, pause auto-apply but continue
    # triage/outcome evaluation (detection must continue even when action pauses).
    auto_apply_paused = _check_circuit_breaker()

    # Phase 3: Bug triage — scan alerts/outcomes for code-fix candidates
    _run_bug_triage(auto_apply_paused=auto_apply_paused)

    # Phase 3b: Bugfix outcome evaluation (closed-loop learning)
    _run_bugfix_outcome_eval()

    # Phase 4: Evolution audit (weekly) + meta-review + convert eligible proposals + model upgrade scan
    _run_evolution_audit()
    _run_meta_review()
    _run_evolution_conversion()
    _run_model_upgrade_scan()

    # Phase 4b: Evolution GC (daily) — clean stale/duplicate/resolved proposals
    _run_evolution_gc()

    # Phase 4c: Escalate stale LEVEL_2/3 proposals (prevent dead letters)
    try:
        db = SessionLocal()
        from app.services.evolution_engine import escalate_stale_proposals
        esc = escalate_stale_proposals(db)
        db.commit()
        if esc.get("escalated", 0) > 0:
            log(f"evolution_escalation: escalated={esc['escalated']}")
    except Exception as exc:
        log(f"evolution_escalation error (non-fatal): {exc}")
    finally:
        try:
            db.close()
        except Exception:
            pass

    # Phase 4d: Lesson GC — decay, retirement, contradiction detection, promotion
    try:
        from app.services.lesson_gc import should_run_gc, run_lesson_gc, mark_gc_run
        if should_run_gc():
            db = SessionLocal()
            try:
                gc_result = run_lesson_gc(db)
                db.commit()
                mark_gc_run()
                if any(v > 0 for v in gc_result.values()):
                    log(f"lesson_gc: {gc_result}")
            except Exception as exc:
                log(f"lesson_gc error (non-fatal): {exc}")
                db.rollback()
            finally:
                db.close()
    except Exception as exc:
        log(f"lesson_gc import error (non-fatal): {exc}")

    # Phase 5: Monthly Opus evolution audit (30-day cooldown)
    _run_monthly_evolution_audit()

    # Phase 6: Entitlement health scan
    _run_entitlement_health_scan()

    # Phase 7: Daily snapshot + scaling recommendations
    _run_scaling_intelligence()

    # Phase 7b: Project brain refresh (daily)
    _run_brain_refresh()

    # Phase 7c: Daily health digest to Telegram (24h cooldown)
    _run_daily_digest()

    # Phase 7d: Weekly merchant email digest (Monday, Europe/Rome)
    _run_merchant_digest()

    # Phase 7e: Lifecycle emails (setup_incomplete, first_insight, connection_issue)
    _run_lifecycle_emails()

    # Phase 7f: Sentry incident triage — generate AI debugging packets
    _run_sentry_triage()

    # Phase 7g: Scoring intelligence self-evaluation (every cycle, lightweight)
    _run_scoring_self_eval()

    # Phase 8: Sandbox analysis (original agent_worker logic)
    # This worker has no per-shop dimension — it selects the top-N
    # ProductOpportunity rows globally by priority_score.
    rows_written = 0
    errors = 0
    last_error: str | None = None

    log("starting agent cycle")

    # Separate session used only for worker_state and worker_log writes.
    # fetch_targets() manages its own session internally.
    db = SessionLocal()

    try:
        state = _load_state(db)

        targets = fetch_targets()
        log(f"planned targets: {len(targets)}")

        for target in targets:
            try:
                goal = target.get("goal", "analyze product opportunity")

                run = create_sandbox_run(
                    goal=goal,
                    payload=target,
                )

                run_id = run["run_id"]
                run_path = run["sandbox_path"]

                log(f"created sandbox run {run_id}")

                analysis = build_product_intelligence(goal=goal, payload=target)
                write_analysis(run_path, analysis)
                log(f"analysis written for {run_id}")

                write_report(run_path, goal, analysis)
                log(f"report written for {run_id}")

                update_sandbox_status(run_id, "planned")
                log("sandbox status updated to planned")

                rows_written += 1

            except Exception as e:
                errors += 1
                last_error = str(e)
                log(f"error: {e}")

        _save_state(db, state)
        log(f"cycle finished — rows_written={rows_written} errors={errors}")

    except Exception as exc:
        errors += 1
        last_error = str(exc)
        log(f"cycle-level error: {exc}")
        raise   # worker_log is written by finally, then main() re-raises for PM2

    finally:
        try:
            _write_log(
                db,
                started_at=started_at,
                shops_processed=0,
                rows_written=rows_written,
                errors=errors,
                error_detail=last_error,
            )
        except Exception as exc:
            log(f"worker_log write error (non-fatal): {exc}")
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log("agent worker started")

    while True:
        from app.core.distributed_lock import worker_lock
        from app.core.metrics import track_worker_cycle

        with worker_lock(WORKER_NAME, ttl_seconds=SLEEP_SECONDS + 60) as acquired:
            if not acquired:
                log("another instance holds the lock — skipping cycle")
            else:
                try:
                    with track_worker_cycle(WORKER_NAME):
                        run_cycle()
                except Exception as exc:
                    log(f"unhandled exception: {exc}")
                    raise

        log(f"sleeping {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
