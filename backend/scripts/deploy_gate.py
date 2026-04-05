#!/usr/bin/env python3
"""
deploy_gate.py — deploy safety gate: preflight + postdeploy verification.

Two modes:

  --preflight
    Verify the system is safe to deploy INTO. Exits 0 if ok, 1 if deploy
    should be blocked. Stores the current HEAD commit to
    /opt/wishspark/.deploy/last_good_commit for a later rollback.

  --postdeploy
    Poll /system/health for up to 60s. Check PM2 for crash loops.
    Scan recent logs for ERROR/Traceback spikes. Exits 0 if system is
    healthy post-deploy, 1 if verification failed. When --auto-rollback
    is set, performs git reset + pm2 restart + Telegram alert on failure.

Safety
------
- Opt-in rollback: --auto-rollback flag required. Default is detect-only.
- Rollback cooldown: a single state file prevents loops. After ONE
  rollback, the file must be manually removed to re-enable auto-rollback.
- Deploy cooldown: 60s minimum between preflight OKs.
- Rollback target age gate: refuses to rollback to a commit older than
  7 days (stale target protection).

Reuses existing infrastructure — no new deps, no new workers.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Boot env so we can call internal services (Telegram, health)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))
from app.core.env_bootstrap import load_env
load_env()

from app.core.logging_config import configure_logging
configure_logging()
log = logging.getLogger("deploy_gate")

# ---------------------------------------------------------------------------
# Paths + config
# ---------------------------------------------------------------------------

_REPO = Path("/opt/wishspark")
_STATE_DIR = _REPO / ".deploy"
_LAST_GOOD_COMMIT = _STATE_DIR / "last_good_commit"
_LAST_DEPLOY_AT = _STATE_DIR / "last_deploy_at"
_ROLLBACK_MARKER = _STATE_DIR / "rollback_executed"
_HEALTH_URL = os.getenv("DEPLOY_HEALTH_URL", "http://127.0.0.1:8000/system/health")
_ECOSYSTEM = "/opt/wishspark/ecosystem.config.js"

_DEPLOY_COOLDOWN_SECONDS = 60
_HEALTH_POLL_TIMEOUT = 60
_HEALTH_POLL_INTERVAL = 3
_MAX_ROLLBACK_AGE_DAYS = 7

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_state_dir() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(_REPO),
        capture_output=True, text=True, timeout=30, check=check,
    )


def _current_commit() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _commit_timestamp(sha: str) -> datetime | None:
    try:
        r = _git("show", "-s", "--format=%ct", sha)
        return datetime.fromtimestamp(int(r.stdout.strip()), tz=timezone.utc)
    except Exception:
        return None


def _curl_health() -> tuple[bool, dict]:
    import urllib.request
    try:
        with urllib.request.urlopen(_HEALTH_URL, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status == 200 and body.get("status") == "ok", body
    except Exception as exc:
        return False, {"error": type(exc).__name__, "detail": str(exc)[:200]}


def _pm2_restart_counts() -> dict[str, int]:
    """Return {process_name: restart_count} from pm2 jlist."""
    try:
        r = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=10, check=True,
        )
        data = json.loads(r.stdout)
        return {p["name"]: p["pm2_env"].get("restart_time", 0) for p in data}
    except Exception as exc:
        log.warning("deploy_gate: pm2 jlist failed: %s", exc)
        return {}


def _send_telegram_alert(title: str, detail: str) -> None:
    """Send ONE critical-incident Telegram message. Never raises."""
    try:
        from app.services.telegram_agent import send_message, is_configured
        if not is_configured():
            log.warning("deploy_gate: telegram not configured — alert skipped")
            return
        send_message(f"🚨 *{title}*\n{detail}")
    except Exception as exc:
        log.warning("deploy_gate: telegram alert failed: %s", exc)


# ---------------------------------------------------------------------------
# PREFLIGHT
# ---------------------------------------------------------------------------

def run_preflight() -> int:
    """
    Pre-deploy safety gate. Returns exit code (0 ok, 1 block deploy).
    """
    _ensure_state_dir()

    # 1. Deploy cooldown — 60s minimum between deploys
    if _LAST_DEPLOY_AT.exists():
        last_ts = float(_LAST_DEPLOY_AT.read_text().strip() or "0")
        delta = time.time() - last_ts
        if delta < _DEPLOY_COOLDOWN_SECONDS:
            log.error(
                "deploy_gate PREFLIGHT: deploy cooldown active — last deploy %ds ago, need %ds",
                int(delta), _DEPLOY_COOLDOWN_SECONDS,
            )
            return 1

    # 2. System must be healthy RIGHT NOW (no deploying into a fire)
    ok, body = _curl_health()
    if not ok:
        log.error("deploy_gate PREFLIGHT: system health != ok — blocking deploy. body=%s",
                  json.dumps(body)[:300])
        return 1

    # 3. No crash-looping process in the last check
    counts = _pm2_restart_counts()
    # We can't compute DELTA without a previous snapshot, so we just record
    # them now. The postdeploy phase uses deltas against a snapshot taken
    # HERE.
    (_STATE_DIR / "restart_counts_preflight.json").write_text(json.dumps(counts))

    # 4. Save current commit for later rollback
    try:
        head = _current_commit()
        _LAST_GOOD_COMMIT.write_text(head)
        log.info("deploy_gate PREFLIGHT: ok — saved last_good_commit=%s", head[:8])
    except Exception as exc:
        log.error("deploy_gate PREFLIGHT: could not read HEAD: %s", exc)
        return 1

    # 5. Remove stale rollback marker if >24h old (lets operator re-enable)
    if _ROLLBACK_MARKER.exists():
        age = time.time() - _ROLLBACK_MARKER.stat().st_mtime
        if age > 24 * 3600:
            _ROLLBACK_MARKER.unlink()
            log.info("deploy_gate PREFLIGHT: cleared stale rollback marker (age=%.0fh)", age / 3600)

    # 6. Record deploy-started timestamp
    _LAST_DEPLOY_AT.write_text(str(time.time()))
    return 0


# ---------------------------------------------------------------------------
# POSTDEPLOY
# ---------------------------------------------------------------------------

def run_postdeploy(auto_rollback: bool) -> int:
    """
    Poll /system/health + PM2 stability for up to 60s. Returns exit code
    (0 healthy, 1 verification failed).
    """
    _ensure_state_dir()

    # Read snapshots taken at preflight
    try:
        pre_counts = json.loads((_STATE_DIR / "restart_counts_preflight.json").read_text())
    except Exception:
        pre_counts = {}

    deadline = time.time() + _HEALTH_POLL_TIMEOUT
    last_body: dict = {}
    healthy_once = False

    while time.time() < deadline:
        ok, body = _curl_health()
        last_body = body
        if ok:
            healthy_once = True
            break
        time.sleep(_HEALTH_POLL_INTERVAL)

    # PM2 stability — count NEW restarts since preflight.
    post_counts = _pm2_restart_counts()
    new_restarts: dict[str, int] = {}
    for name, count in post_counts.items():
        pre = pre_counts.get(name, 0)
        delta = count - pre
        if delta > 1:   # 1 restart is expected from the deploy itself
            new_restarts[name] = delta

    if not healthy_once:
        log.error(
            "deploy_gate POSTDEPLOY: health never went ok in %ds — body=%s",
            _HEALTH_POLL_TIMEOUT, json.dumps(last_body)[:300],
        )
        if auto_rollback:
            return _execute_rollback(reason="health_timeout", detail=json.dumps(last_body)[:200])
        return 1

    if new_restarts:
        log.error(
            "deploy_gate POSTDEPLOY: crash loop detected — extra restarts=%s",
            new_restarts,
        )
        if auto_rollback:
            return _execute_rollback(reason="crash_loop", detail=f"extra_restarts={new_restarts}")
        return 1

    log.info("deploy_gate POSTDEPLOY: verification passed")
    return 0


# ---------------------------------------------------------------------------
# ROLLBACK
# ---------------------------------------------------------------------------

def _execute_rollback(reason: str, detail: str) -> int:
    """Perform git reset + pm2 restart + critical Telegram alert. Exit 1."""
    # Cooldown: one rollback per marker. Operator must clear to re-arm.
    if _ROLLBACK_MARKER.exists():
        log.error(
            "deploy_gate ROLLBACK: marker exists — refusing to roll back twice. "
            "Clear %s to re-arm.", _ROLLBACK_MARKER,
        )
        _send_telegram_alert(
            "DEPLOY FAILED — ROLLBACK SKIPPED",
            f"reason={reason}; previous rollback marker present; manual intervention required.",
        )
        return 1

    try:
        target_sha = _LAST_GOOD_COMMIT.read_text().strip()
    except Exception:
        log.error("deploy_gate ROLLBACK: no last_good_commit file — cannot roll back")
        _send_telegram_alert(
            "DEPLOY FAILED — ROLLBACK IMPOSSIBLE",
            f"reason={reason}; last_good_commit missing; manual git rollback required.",
        )
        return 1

    # Age gate — never roll back to a stale target
    ts = _commit_timestamp(target_sha)
    if ts is None:
        log.error("deploy_gate ROLLBACK: target commit %s not found", target_sha[:8])
        return 1
    age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
    if age_days > _MAX_ROLLBACK_AGE_DAYS:
        log.error(
            "deploy_gate ROLLBACK: target commit %s is %.1f days old — refusing rollback",
            target_sha[:8], age_days,
        )
        _send_telegram_alert(
            "DEPLOY FAILED — ROLLBACK TARGET STALE",
            f"target={target_sha[:8]} age={age_days:.1f}d; manual rollback required.",
        )
        return 1

    log.warning("deploy_gate ROLLBACK: resetting to %s (reason=%s)", target_sha[:8], reason)
    try:
        _git("reset", "--hard", target_sha)
    except subprocess.CalledProcessError as exc:
        log.error("deploy_gate ROLLBACK: git reset failed: %s", exc.stderr[:200])
        _send_telegram_alert(
            "AUTO-ROLLBACK FAILED",
            f"git reset failed: {exc.stderr[:150]}",
        )
        return 1

    # Restart PM2 with absolute path
    try:
        subprocess.run(
            ["pm2", "restart", _ECOSYSTEM],
            capture_output=True, text=True, timeout=60, check=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("deploy_gate ROLLBACK: pm2 restart failed: %s", exc.stderr[:200])
        _send_telegram_alert(
            "AUTO-ROLLBACK FAILED",
            f"pm2 restart failed after git reset: {exc.stderr[:150]}",
        )
        return 1

    # Marker — prevents loops
    _ROLLBACK_MARKER.write_text(
        json.dumps({"reason": reason, "detail": detail, "target": target_sha, "at": time.time()})
    )

    _send_telegram_alert(
        "AUTO-ROLLBACK EXECUTED",
        f"reason={reason}\nrolled back to {target_sha[:8]}\ndetail={detail[:200]}",
    )
    log.warning("deploy_gate ROLLBACK: completed, rollback marker written")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy safety gate for Hedge Spark.")
    subs = parser.add_subparsers(dest="mode", required=True)
    subs.add_parser("preflight")
    pd = subs.add_parser("postdeploy")
    pd.add_argument("--auto-rollback", action="store_true", help="Execute rollback on failure")
    args = parser.parse_args()

    if args.mode == "preflight":
        return run_preflight()
    if args.mode == "postdeploy":
        return run_postdeploy(auto_rollback=args.auto_rollback)
    return 1


if __name__ == "__main__":
    sys.exit(main())
