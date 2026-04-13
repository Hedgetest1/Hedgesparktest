"""
night_shift_calibration.py — Observation store for Sleep Confidence.

Purpose
-------
Sleep Confidence shipped as a heuristic (documented in night_shift_agent.py).
This module is the honest path to calibrated: persistently record every
score we emit, track the ground truth (did the next day actually contain
a critical event?), and gate the "calibrated" label on N observations.

Storage
-------
Redis sorted sets per shop:

  hs:ns_cal:obs:{shop}   — ZSET(day_key -> score) (all observations)
  hs:ns_cal:truth:{shop} — ZSET(day_key -> incident_count)

Observation counts unblock calibration once we have `_MIN_OBSERVATIONS`
data points *with matching ground truth*. Until then the UI labels the
score as uncalibrated and we cap the max at 85.

Ground truth update
-------------------
A nightly task (run the day *after* a report was generated) walks the
observations list and fills in the incident count for the day that just
completed. That closes the loop deterministically.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("night_shift_calibration")

_OBS_PREFIX = "hs:ns_cal:obs"
_TRUTH_PREFIX = "hs:ns_cal:truth"
_TTL_SECONDS = 90 * 24 * 3600  # keep 90d of history for calibration math

_MIN_OBSERVATIONS = 30


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def record_observation(shop_domain: str | None, *, day: str, score: int, status: str) -> bool:
    """Persist one (shop, day, score) observation."""
    if not shop_domain:
        return False
    rc = _redis()
    if rc is None:
        return False
    try:
        key = f"{_OBS_PREFIX}:{shop_domain}"
        # Encode as "score|status" so truth-update can read status
        rc.hset(key, day, f"{score}|{status}")
        rc.expire(key, _TTL_SECONDS)
        return True
    except Exception as exc:
        log.warning("ns_cal: record_observation failed: %s", exc)
        return False


def record_truth(shop_domain: str, *, day: str, incident_count: int) -> bool:
    """Record ground truth for a day (how many critical events fired)."""
    rc = _redis()
    if rc is None:
        return False
    try:
        key = f"{_TRUTH_PREFIX}:{shop_domain}"
        rc.hset(key, day, str(incident_count))
        rc.expire(key, _TTL_SECONDS)
        return True
    except Exception as exc:
        log.warning("ns_cal: record_truth failed: %s", exc)
        return False


def observation_count(shop_domain: str | None) -> int:
    """Return number of observations (regardless of truth state)."""
    if not shop_domain:
        return 0
    rc = _redis()
    if rc is None:
        return 0
    try:
        key = f"{_OBS_PREFIX}:{shop_domain}"
        return int(rc.hlen(key) or 0)
    except Exception:
        return 0


def matched_observation_count(shop_domain: str | None) -> int:
    """Number of observations that also have a truth value — used for calibration gate."""
    if not shop_domain:
        return 0
    rc = _redis()
    if rc is None:
        return 0
    try:
        obs = rc.hgetall(f"{_OBS_PREFIX}:{shop_domain}")
        truth = rc.hgetall(f"{_TRUTH_PREFIX}:{shop_domain}")
        if not obs or not truth:
            return 0
        obs_days = {k.decode() if isinstance(k, bytes) else k for k in obs.keys()}
        truth_days = {k.decode() if isinstance(k, bytes) else k for k in truth.keys()}
        return len(obs_days & truth_days)
    except Exception:
        return 0


def is_calibrated(shop_domain: str | None) -> bool:
    """True if we have enough (observation, truth) pairs to trust the score."""
    return matched_observation_count(shop_domain) >= _MIN_OBSERVATIONS


def calibration_status(shop_domain: str | None) -> dict:
    """Inspection payload for the UI / debug endpoint."""
    obs = observation_count(shop_domain)
    matched = matched_observation_count(shop_domain)
    return {
        "shop_domain": shop_domain,
        "observations": obs,
        "matched_with_truth": matched,
        "min_required": _MIN_OBSERVATIONS,
        "calibrated": matched >= _MIN_OBSERVATIONS,
        "progress_pct": int(100 * min(1.0, matched / _MIN_OBSERVATIONS)),
    }
