"""
logging_config.py — Structured JSON logging for WishSpark.

Configures Python's root logger to emit JSON lines compatible with
PM2 log files, log aggregation tools, and future AI agent parsing.

Every log line is a single JSON object with standard fields:
    ts, level, logger, message, [request_id], [shop], [worker], [error_type]

Usage:
    Call configure_logging() once at startup (main.py).
    All subsequent logging.getLogger() calls inherit the JSON formatter.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from typing import Any

# Thread-local storage for request context (request_id, shop_domain)
_context = threading.local()


def set_request_context(*, request_id: str | None = None, shop_domain: str | None = None):
    """Set per-request context fields that appear in every log line."""
    _context.request_id = request_id
    _context.shop_domain = shop_domain


def clear_request_context():
    _context.request_id = None
    _context.shop_domain = None


def set_worker_context(*, worker_name: str):
    """Set worker name for background job processes."""
    _context.worker_name = worker_name


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Request context (set by middleware)
        rid = getattr(_context, "request_id", None)
        if rid:
            entry["request_id"] = rid
        shop = getattr(_context, "shop_domain", None)
        if shop:
            entry["shop"] = shop

        # Worker context (set at worker startup)
        worker = getattr(_context, "worker_name", None)
        if worker:
            entry["worker"] = worker

        # Exception info
        if record.exc_info and record.exc_info[1]:
            entry["error_type"] = type(record.exc_info[1]).__name__
            entry["error"] = str(record.exc_info[1])[:500]

        # Extra fields passed via logging.info("msg", extra={...})
        for key in ("action_type", "error_type", "shop", "request_id"):
            val = getattr(record, key, None)
            if val and key not in entry:
                entry[key] = val

        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO):
    """
    Replace the root logger's handlers with a single JSON stderr handler.
    Call once at process startup.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicate output
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
