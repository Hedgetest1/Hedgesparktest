"""test_shop_blocklist.py — Pattern-based detection of synthetic test
shop domains so production code paths don't pollute prod tables when a
test triggers them.

Born 2026-05-06 after a brutal capillary audit found 1079 orphan
alert rows in `ops_alerts` accumulated over ~25 days. Root cause:
tests open the production DB session via SAVEPOINT (per
`feedback_test_hermeticity_prod_db.md`), but services like
`risk_forecast._maybe_emit_volatility_alert` and
`signal_webhooks.fanout_event` open their OWN `SessionLocal()` to
persist write_alert outside the caller's transaction (a real
production semantic — alerts should survive caller rollback).
That nested session bypasses the test's SAVEPOINT and writes a real
row that escapes test cleanup.

The class can re-emerge any time someone:
    a) writes a test that exercises a service path that calls
       `write_alert` via a nested SessionLocal, OR
    b) accidentally runs a test with a real-merchant-looking
       domain.

Fix layered approach:
  L1 — `is_synthetic_test_shop(shop_domain)` — pattern predicate
       used by `write_alert` to early-return without DB persist.
  L2 — periodic cleanup of orphan alerts (already-leaked rows).
  L3 — preventer audit `audit_orphan_alerts_no_growth.py` that
       fails if `ops_alerts` accumulates rows referencing shops
       absent from the merchants table.

The patterns below are ANCHORED suffixes/prefixes: a real merchant
will never accidentally collide.
"""
from __future__ import annotations

import re

# Compiled once. Each pattern matches a *known-leak* test-fixture shop
# generation pattern — that is, the test exercises a service path that
# opens its own `SessionLocal()` and persists an alert outside the
# caller's transaction (bypassing test SAVEPOINT cleanup). The 2026-
# 05-06 audit confirmed these by counting accumulated rows in
# `ops_alerts` referencing shops absent from the merchants table.
#
# DO NOT add broader patterns ("test-*", "fixture-*", "lonely-*",
# "empty-*") — many legitimate tests use those names AND intentionally
# exercise the same in-session write_alert path to verify alert-write
# behavior (e.g. test_onboarding.py::test_no_token_fails). Their
# alerts roll back via SAVEPOINT cleanly. Over-blocking those breaks
# valid test contracts.
#
# Add a new entry ONLY when:
#   1. The audit `audit_orphan_alerts_no_growth.py` flags accumulating
#      orphans on the new pattern, AND
#   2. Investigation confirms the test triggers a nested SessionLocal
#      that escapes SAVEPOINT cleanup.
_SYNTHETIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Risk-forecast tests (tests/test_risk_forecast.py:_shop) generate
    # `<prefix>-<8 hex chars>.myshopify.com`. The 8 hex chars are
    # uuid4().hex[:8], so the pattern is unambiguous.
    # Service path: risk_forecast._maybe_emit_volatility_alert opens
    # SessionLocal() to persist outside caller transaction.
    re.compile(r"^rforecast-[a-z]+-[0-9a-f]{8}\.myshopify\.com$"),
    # Signal-webhook tests (tests/test_signal_webhooks.py:210) — fixed
    # shop name. Service path: signal_webhooks fanout opens its own
    # session for alert persistence.
    re.compile(r"^webhook-fail\.myshopify\.com$"),
    # Generic load-test prefix (CLAUDE.md §12.2 mentions `_loadtest_*`).
    # The harness creates these and is cleanup-aware, but the
    # belt-and-suspenders block here ensures any escape-hatch
    # alerts also get filtered.
    re.compile(r"^_loadtest_"),
    # Hedgespark dev tenant placeholder — blocklisted across the
    # codebase (CLAUDE.md §16). Mirrored here for completeness.
    re.compile(r"^legacy\.myshopify\.com$"),
    # Trust-contracts test fixture (tests/test_trust_contracts.py:33
    # SHOP="test-trust-suite.myshopify.com"). Service path:
    # event_bus._emit_postgres opens its own SessionLocal via _get_db()
    # to persist analytics_events outside caller transaction. Surfaced
    # 2026-05-07 by audit_db_table_growth (88 → 541 row spike during
    # pytest run; 542 orphan rows cleaned + guard added in same commit).
    re.compile(r"^test-trust-suite\.myshopify\.com$"),
)


def is_synthetic_test_shop(shop_domain: str | None) -> bool:
    """Return True iff `shop_domain` matches a known test-fixture
    pattern. Conservative — returns False on None/empty input.

    Used by `app.services.alerting.write_alert` to drop alerts
    targeting synthetic shops before they hit `ops_alerts`. A real
    merchant will never accidentally match because the patterns
    require literal prefix + suffix anchors specific to test code.

    The function is intentionally cheap (compiled regex set, single
    pass). It is safe to call on every alert write."""
    if not shop_domain or not isinstance(shop_domain, str):
        return False
    s = shop_domain.strip().lower()
    for pat in _SYNTHETIC_PATTERNS:
        if pat.search(s):
            return True
    return False
