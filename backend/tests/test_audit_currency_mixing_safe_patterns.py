"""
Regression tests for `audit_currency_mixing_sum.py` safe-pattern recognition.

Born 2026-05-13 after Agent-review caught that the prior `[^\\n]*`
form of the GROUP BY pattern didn't cross newlines — fine for the
single test case (`data_integrity_probe.py:235`), broken for any real
SQL author who formats multi-line.

These tests drive synthetic Python source through the audit's regex
patterns directly (no file I/O) so future regex changes are locked
against the multi-line idioms HedgeSpark engineers actually write.
"""
from __future__ import annotations

import importlib.util
import pathlib


def _load_audit_module():
    """Load audit_currency_mixing_sum as a module so we can call its
    internal regex patterns directly (script is not normally importable)."""
    path = pathlib.Path("/opt/wishspark/backend/scripts/audit_currency_mixing_sum.py")
    spec = importlib.util.spec_from_file_location("audit_ccy", path)
    mod = importlib.util.module_from_spec(spec)
    # Patch the _audit_telemetry_shim import so module loads in test
    import sys
    class _Shim:
        @staticmethod
        def telemetered(name):
            def deco(fn):
                return fn
            return deco
    sys.modules.setdefault("_audit_telemetry_shim", _Shim())
    spec.loader.exec_module(mod)
    return mod


class TestCurrencyFilterPatternMultiLine:
    """`_CURRENCY_FILTER_PATTERN` MUST recognize `GROUP BY ... currency`
    across newlines so legitimate multi-line aggregators don't get
    flagged as currency-mixing bugs."""

    def test_inline_group_by_currency_matches(self):
        mod = _load_audit_module()
        text = "GROUP BY shop_domain, currency"
        assert mod._CURRENCY_FILTER_PATTERN.search(text) is not None

    def test_multi_line_group_by_currency_matches(self):
        # AGENT-REVIEW FINDING — pre-fix `[^\n]*` failed this case.
        mod = _load_audit_module()
        text = """
            SELECT shop_domain, currency, SUM(total_price)
            FROM shop_orders
            GROUP BY
                shop_domain,
                currency
        """
        assert mod._CURRENCY_FILTER_PATTERN.search(text) is not None, (
            "Multi-line GROUP BY ... currency MUST match — real SQL "
            "authors split clauses across lines"
        )

    def test_group_by_only_shop_no_currency_does_not_match(self):
        # Real bug pattern — GROUP BY shop_domain without currency in
        # the GROUP BY clause is genuinely currency-mixing risk.
        mod = _load_audit_module()
        text = "GROUP BY shop_domain"
        assert mod._CURRENCY_FILTER_PATTERN.search(text) is None

    def test_where_currency_filter_still_matches(self):
        # Pre-existing pattern — must not regress
        mod = _load_audit_module()
        assert mod._CURRENCY_FILTER_PATTERN.search(
            "WHERE shop_domain = :s AND currency = :currency"
        ) is not None

    def test_primary_currency_still_matches(self):
        # Pre-existing pattern
        mod = _load_audit_module()
        assert mod._CURRENCY_FILTER_PATTERN.search(
            "JOIN merchants m USING (shop_domain) WHERE so.currency = m.primary_currency"
        ) is not None

    def test_400_char_bound_prevents_runaway(self):
        # The non-greedy `{0,400}` bound prevents the match running
        # away into a completely unrelated `currency` token later in
        # the file (defense against false-negative-style allowlisting).
        mod = _load_audit_module()
        far_away = "GROUP BY shop_domain\n" + (" " * 500) + "AND currency = :x"
        # The `currency` is >400 chars from `GROUP BY` so should NOT
        # be considered part of the same GROUP BY clause
        assert mod._CURRENCY_FILTER_PATTERN.search(far_away) is None


class TestAuditFiresOnRealBug:
    """The whole point of the audit — it MUST still fire on real
    currency-mixing patterns. Otherwise the regex extension would
    be over-allowlisting."""

    def test_real_currency_mixing_pattern_detected(self, tmp_path):
        # Synthetic file: SUM(total_price) across multiple shops with
        # GROUP BY shop_domain but NO currency anywhere → real bug
        bad = tmp_path / "bad_service.py"
        bad.write_text("""
from sqlalchemy import text
def get_total_revenue(db, shops):
    return db.execute(text('''
        SELECT shop_domain, SUM(total_price) AS rev
        FROM shop_orders
        WHERE shop_domain = ANY(:shops)
        GROUP BY shop_domain
    '''), {"shops": shops}).fetchall()
""")
        mod = _load_audit_module()
        text_content = bad.read_text()
        # Verify the pattern detects SUM
        assert mod._SQL_SUM_PATTERN.search(text_content) is not None
        # Verify the pattern detects multi-shop
        assert mod._MULTI_SHOP_GROUP_PATTERN.search(text_content) is not None
        # Verify NO currency filter matches (this is the bug — should fire)
        assert mod._CURRENCY_FILTER_PATTERN.search(text_content) is None

    def test_safe_aggregator_routing_exempts_file(self, tmp_path):
        # File-level safe-aggregator signal — file routes through
        # the shared helper, audit skips. Pre-existing behavior.
        safe = tmp_path / "safe_service.py"
        safe.write_text("""
from app.services.multi_currency_rollup import aggregate_by_currency
def get_total_revenue(db, shops):
    return aggregate_by_currency(db, shops)
""")
        mod = _load_audit_module()
        text_content = safe.read_text()
        assert mod._SAFE_AGGREGATOR_PATTERN.search(text_content) is not None
