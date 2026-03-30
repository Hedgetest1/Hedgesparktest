"""
execution_mode.py — Global execution mode for production safety.

ENV:
    EXECUTION_MODE = "real" | "dry_run"  (default: "real")

Rules:
    dry_run  — no external API calls, no real mutations, Telegram messages prefixed [DRY RUN]
    real     — only send success messages AFTER verified real execution
"""
from __future__ import annotations

import os

EXECUTION_MODE: str = os.getenv("EXECUTION_MODE", "real").strip().lower()

if EXECUTION_MODE not in ("real", "dry_run"):
    EXECUTION_MODE = "real"


def is_dry_run() -> bool:
    return EXECUTION_MODE == "dry_run"


def is_real() -> bool:
    return EXECUTION_MODE == "real"
