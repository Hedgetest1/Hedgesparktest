"""Tests for structured JSON logging (logging_config.py)."""
import json
import logging

from app.core.logging_config import (
    JSONFormatter,
    set_request_context,
    clear_request_context,
    set_worker_context,
)


def test_json_formatter_basic():
    """JSONFormatter produces valid JSON with required fields."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test.module", level=logging.INFO, pathname="", lineno=0,
        msg="test message", args=None, exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "INFO"
    assert data["logger"] == "test.module"
    assert data["message"] == "test message"
    assert "ts" in data


def test_json_formatter_includes_request_context():
    """Request context (request_id, shop) appears in log output."""
    set_request_context(request_id="abc123", shop_domain="test.myshopify.com")
    try:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="with context", args=None, exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["request_id"] == "abc123"
        assert data["shop"] == "test.myshopify.com"
    finally:
        clear_request_context()


def test_json_formatter_includes_worker_context():
    """Worker context appears in log output."""
    set_worker_context(worker_name="aggregation_worker")
    try:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="worker log", args=None, exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["worker"] == "aggregation_worker"
    finally:
        clear_request_context()


def test_json_formatter_exception_info():
    """Exception info is captured as error_type and error fields."""
    formatter = JSONFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="failed", args=None, exc_info=sys.exc_info(),
        )
    output = formatter.format(record)
    data = json.loads(output)
    assert data["error_type"] == "ValueError"
    assert "test error" in data["error"]
