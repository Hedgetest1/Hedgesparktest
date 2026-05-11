"""alert_source_blocklist.py — Pattern-based detection of synthetic /
retired alert source strings so production code paths don't accumulate
orphan noise in `ops_alerts`.

Born 2026-05-11 Senior+++ close after the competitor-CTO audit
disposed 7 unresolved alerts manually — one of them
(id=109688, alert_type=lite_nav_section_missing, source=
phase_c_synthetic_test) had persisted 16 days as orphan noise from a
retired Phase C synthetic test fixture. The shop-side blocklist
(`app/core/test_shop_blocklist.py`) only filters by shop_domain;
synthetic alerts with NULL shop_domain (typical for global-scope test
fixtures) escape that guard.

Layered like the shop-side blocklist:
  L1 — `is_synthetic_alert_source(source)` — pattern predicate used
       by `app.services.alerting.write_alert` to early-return without
       DB persist.
  L2 — periodic auto-resolve of already-leaked orphan alerts older
       than 7 days (in `app/workers/aggregation_worker.py` 5-min loop).
  L3 — preventer audit (future): track ops_alerts source distribution
       to flag any new accumulating orphan source pattern.

The patterns below are ANCHORED suffixes/prefixes — a real production
source ("onboarding_health", "merchant_brain", "fe:DailyBrief:abc123")
will never accidentally collide because real sources are documented
modules, not synthetic test names.

Add a new entry ONLY when:
  1. Disposed orphan alerts referencing a NEW source pattern accumulate
     (audit reminder), AND
  2. The source is confirmed to be a retired/synthetic test fixture
     (not a real production module being temporarily noisy).
"""
from __future__ import annotations

import re

# Compiled once. Each pattern matches a known retired/synthetic alert
# source string. DO NOT add patterns matching real production sources
# (e.g., "onboarding_*" — that's a real production module).
_SYNTHETIC_SOURCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Phase C synthetic test (retired). One alert (id=109688) persisted
    # 16d before disposal 2026-05-11; producer no longer in codebase.
    re.compile(r"^phase_[a-z]_synthetic_test$"),
    # Generic "*_synthetic_test" suffix — covers any future end-to-end
    # synthetic test fixture that emits ops_alerts globally.
    re.compile(r"_synthetic_test$"),
    # Synthetic prefix with dash — e.g., "synthetic-loadtest-runner".
    re.compile(r"^synthetic[-_]"),
    # Generic load-test source prefix.
    re.compile(r"^_loadtest_"),
)


def is_synthetic_alert_source(source: str | None) -> bool:
    """Return True iff `source` matches a known synthetic/retired test
    source string. Conservative — returns False on None/empty input.

    Used by `app.services.alerting.write_alert` to drop alerts from
    synthetic sources before they hit `ops_alerts`. A real production
    source string will never accidentally match because the patterns
    require literal anchors specific to test code.

    The function is intentionally cheap (compiled regex set, single
    pass). Safe to call on every alert write."""
    if not source or not isinstance(source, str):
        return False
    s = source.strip().lower()
    for pat in _SYNTHETIC_SOURCE_PATTERNS:
        if pat.search(s):
            return True
    return False


_AUTO_RESOLVE_AGE_DAYS = 7

# Exposed for callers that need to run the periodic L2 sweep.
AUTO_RESOLVE_SQL = """
UPDATE ops_alerts
   SET resolved = true,
       resolved_at = now()
 WHERE resolved = false
   AND created_at < now() - interval '%d days'
   AND (
        source ~ '^phase_[a-z]_synthetic_test$'
     OR source ~ '_synthetic_test$'
     OR source ~ '^synthetic[-_]'
     OR source ~ '^_loadtest_'
   )
""" % _AUTO_RESOLVE_AGE_DAYS
