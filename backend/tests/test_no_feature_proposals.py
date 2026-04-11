"""
Phase-6 regression: autonomous evolution must never propose new features.

The directive is explicit: the self-healing system perfects what we already
have. Growth / retention / conversion / experiment / product proposals
are business decisions owned by humans. These tests lock in the constraint
so a future refactor cannot silently re-enable feature proposals.
"""
from __future__ import annotations

from app.services.monthly_evolution_audit import (
    _FORBIDDEN_PROPOSAL_TYPES,
    _VALID_TYPES,
)
from app.services import evolution_engine


def test_forbidden_types_explicit():
    """The block-list must include every business/product category."""
    assert "growth" in _FORBIDDEN_PROPOSAL_TYPES
    assert "retention" in _FORBIDDEN_PROPOSAL_TYPES
    assert "conversion" in _FORBIDDEN_PROPOSAL_TYPES
    assert "experiment" in _FORBIDDEN_PROPOSAL_TYPES
    assert "product" in _FORBIDDEN_PROPOSAL_TYPES
    assert "feature" in _FORBIDDEN_PROPOSAL_TYPES


def test_valid_types_are_engineering_only():
    """The positive allow-list may only contain engineering categories."""
    assert _VALID_TYPES == {"architecture", "performance", "reliability", "deprecate"}


def test_valid_and_forbidden_are_disjoint():
    """No category is both allowed and forbidden — the rules must be consistent."""
    assert _VALID_TYPES.isdisjoint(_FORBIDDEN_PROPOSAL_TYPES)


def test_feature_request_scanner_not_invoked_by_audit():
    """The audit runner must not call _scan_feature_requests. Even if the
    function still exists for historical inspection, it must not feed the
    autonomous pipeline."""
    import inspect
    source = inspect.getsource(evolution_engine.run_evolution_audit)
    # The line that extends proposals with feature_requests must not be live.
    # Comments are fine; what we check is the absence of an un-commented call.
    live_lines = [
        ln.strip() for ln in source.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    offending = [
        ln for ln in live_lines
        if "_scan_feature_requests" in ln and "extend" in ln
    ]
    assert offending == [], (
        f"_scan_feature_requests should not be invoked from run_evolution_audit; "
        f"found live lines: {offending}"
    )


def test_feature_request_scanner_still_defined_for_historical_queries():
    """The function stays in the module so tools that query it don't break."""
    assert hasattr(evolution_engine, "_scan_feature_requests")
