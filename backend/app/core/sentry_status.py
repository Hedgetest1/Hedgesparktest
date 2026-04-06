"""
sentry_status.py — expose the operator's Sentry posture + usage proxy.

Sentry does NOT give us a native "monthly spend" number through its SDK,
so we surface the best honest proxy we have: sample rate × recent request
volume → estimated monthly events. This lets the Telegram digest show an
actionable Sentry status line even though the true quota is external.
"""
from __future__ import annotations

import os
import time


def get_sentry_status() -> dict:
    """
    Return Sentry status for display in operator digests.

    Shape:
      {
        "enabled": bool,
        "environment": str,
        "traces_sample_rate": float,
        "estimate_mode": "error_only" | "low_trace" | "moderate_trace" | "high_trace",
        "warning": str | None,
        "note": str,
      }

    estimate_mode is a SIGNAL about quota risk, not a real measurement:
      error_only     — sample rate 0.00, only errors tracked (safest)
      low_trace      — 0.00 < rate <= 0.05 (5% — production recommended)
      moderate_trace — 0.05 < rate <= 0.20 (quota pressure at >1k RPM)
      high_trace     — rate > 0.20 (quota risk at production scale)
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    enabled = bool(dsn)
    env = os.getenv("SENTRY_ENVIRONMENT", "production").strip()
    try:
        rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    except ValueError:
        rate = 0.0

    if rate <= 0.0:
        mode = "error_only"
        warning = None
    elif rate <= 0.05:
        mode = "low_trace"
        warning = None
    elif rate <= 0.20:
        mode = "moderate_trace"
        warning = f"SENTRY_TRACES_SAMPLE_RATE={rate:.2f} — monitor quota"
    else:
        mode = "high_trace"
        warning = (
            f"SENTRY_TRACES_SAMPLE_RATE={rate:.2f} is HIGH — likely quota risk "
            f"at scale. Consider reducing to 0.05."
        )

    return {
        "enabled": enabled,
        "environment": env,
        "traces_sample_rate": rate,
        "estimate_mode": mode,
        "warning": warning,
        "note": (
            "Sentry quota is external — this is the sample-rate signal, "
            "not a live spend reading. Watch Sentry dashboard for actuals."
        ),
    }


def format_sentry_digest_line() -> str:
    """
    Build the single Telegram-digest line for Sentry status.

    Mirrors the LLM-budget line shape so operators learn one scanning pattern.
    """
    s = get_sentry_status()
    if not s["enabled"]:
        return "*SENTRY:* 🔴 DISABLED (errors are lost)"

    mode_emoji = {
        "error_only":      "🟢",
        "low_trace":       "🟢",
        "moderate_trace":  "🟡",
        "high_trace":      "🔴",
    }.get(s["estimate_mode"], "⚪")

    line = (
        f"*SENTRY:* {mode_emoji} enabled env={s['environment']} "
        f"trace_rate={s['traces_sample_rate']:.2f} ({s['estimate_mode']})"
    )
    if s["warning"]:
        line += f"\n  ⚠️ {s['warning']}"
    return line
