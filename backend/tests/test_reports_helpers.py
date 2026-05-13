"""
Unit tests for the pure helpers extracted from `execute_report`
in the 2026-05-13 A3 refactor.

End-to-end coverage exists at /merchant/reports/{id}/data via
test_reports_endpoints.py (28 tests) + test_reports_moat_wiring.py
(8 tests). This file locks the new structural-unit helpers:
metric-meta resolver + base-filter builder + 3 overlay applicators
(forecast / holdout-lift / peer-percentile).
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api import reports as rp
from app.api.reports import (
    _apply_forecast_overlay,
    _apply_holdout_lift_overlay,
    _apply_peer_percentile_overlay,
    _PEER_METRIC_KEY_MAP,
    _resolve_metric_meta,
    _update_last_run,
    ReportDataRow,
)


# ---------------------------------------------------------------------------
# _resolve_metric_meta
# ---------------------------------------------------------------------------


class TestMetricMeta:
    def test_known_metric(self):
        label, unit = _resolve_metric_meta("revenue")
        assert label == "Revenue"
        assert unit == "money"

    def test_pct_unit_metric(self):
        label, unit = _resolve_metric_meta("conversion_rate")
        assert unit == "pct"

    def test_formula_special_case(self):
        label, unit = _resolve_metric_meta("formula")
        assert label == "Custom formula"
        assert unit == "money"

    def test_unknown_metric_falls_back(self):
        label, unit = _resolve_metric_meta("mystery_metric")
        # Falls back to using key as label + money unit
        assert label == "mystery_metric"
        assert unit == "money"


# ---------------------------------------------------------------------------
# _PEER_METRIC_KEY_MAP — constant contract
# ---------------------------------------------------------------------------


class TestPeerMetricKeyMap:
    def test_keys(self):
        # The 3 metrics that route to peer percentile
        assert set(_PEER_METRIC_KEY_MAP.keys()) == {"revenue", "aov", "orders"}

    def test_values_are_benchmarks_vertical_keys(self):
        # Values must match the keys returned by get_vertical_benchmark_report
        assert _PEER_METRIC_KEY_MAP["revenue"] == "monthly_revenue"
        assert _PEER_METRIC_KEY_MAP["aov"] == "aov"
        assert _PEER_METRIC_KEY_MAP["orders"] == "orders_per_day"


# ---------------------------------------------------------------------------
# _apply_forecast_overlay
# ---------------------------------------------------------------------------


def _row_obj(metric, dimensions, forecast_horizon=None):
    return SimpleNamespace(
        metric=metric, dimensions=dimensions,
        forecast_horizon=forecast_horizon,
    )


class TestForecastOverlay:
    def test_skipped_when_no_horizon(self):
        rows_out = [ReportDataRow(label="Revenue", value=100.0)]
        chart, notes = _apply_forecast_overlay(
            db=None, shop="x", row=_row_obj("revenue", ["time"], forecast_horizon=None),
            rows_out=rows_out, chart_type="bar",
        )
        assert chart == "bar"
        assert notes == []
        assert len(rows_out) == 1  # untouched

    def test_skipped_when_metric_not_revenue(self):
        rows_out = [ReportDataRow(label="Orders", value=10.0)]
        chart, notes = _apply_forecast_overlay(
            db=None, shop="x", row=_row_obj("orders", ["time"], forecast_horizon=14),
            rows_out=rows_out, chart_type="bar",
        )
        assert chart == "bar"
        assert notes == []

    def test_skipped_when_dimension_not_time(self):
        rows_out = [ReportDataRow(label="Revenue", value=100.0)]
        chart, notes = _apply_forecast_overlay(
            db=None, shop="x", row=_row_obj("revenue", ["country"], forecast_horizon=14),
            rows_out=rows_out, chart_type="bar",
        )
        assert chart == "bar"
        assert notes == []

    def test_applied_when_revenue_time_with_horizon(self, monkeypatch):
        # Stub the forecast service to return populated payload
        import app.services.revenue_forecast as rf
        monkeypatch.setattr(
            rf, "get_revenue_forecast",
            lambda db, s, horizon_days: {
                "point": 1500.0, "low": 1200.0, "high": 1800.0,
                "confidence_label": "90%",
            },
        )
        rows_out = [ReportDataRow(label="Revenue", value=100.0)]
        chart, notes = _apply_forecast_overlay(
            db=None, shop="x",
            row=_row_obj("revenue", ["time"], forecast_horizon=14),
            rows_out=rows_out, chart_type="bar",
        )
        assert chart == "line"
        assert len(rows_out) == 2
        forecast_row = rows_out[1]
        assert "Forecast (next 14d)" == forecast_row.label
        assert forecast_row.value == 1500.0
        assert forecast_row.forecast_low == 1200.0
        assert forecast_row.forecast_high == 1800.0
        assert any("90%" in n for n in notes)

    def test_silent_on_forecast_failure(self, monkeypatch):
        import app.services.revenue_forecast as rf
        def _explode(db, s, horizon_days):
            raise RuntimeError("forecast service down")
        monkeypatch.setattr(rf, "get_revenue_forecast", _explode)
        rows_out = [ReportDataRow(label="Revenue", value=100.0)]
        chart, notes = _apply_forecast_overlay(
            db=None, shop="x",
            row=_row_obj("revenue", ["time"], forecast_horizon=14),
            rows_out=rows_out, chart_type="bar",
        )
        # Failure swallowed — chart unchanged, no notes added
        assert chart == "bar"
        assert notes == []

    def test_skipped_when_forecast_returns_no_band(self, monkeypatch):
        import app.services.revenue_forecast as rf
        monkeypatch.setattr(
            rf, "get_revenue_forecast",
            lambda db, s, horizon_days: {"point": 1500.0, "low": None, "high": None},
        )
        rows_out = [ReportDataRow(label="Revenue", value=100.0)]
        chart, notes = _apply_forecast_overlay(
            db=None, shop="x",
            row=_row_obj("revenue", ["time"], forecast_horizon=14),
            rows_out=rows_out, chart_type="bar",
        )
        assert chart == "bar"  # no overlay added
        assert notes == []


# ---------------------------------------------------------------------------
# _apply_holdout_lift_overlay
# ---------------------------------------------------------------------------


class TestHoldoutLiftOverlay:
    def test_skipped_when_metric_not_revenue_or_orders(self):
        rows_out = [ReportDataRow(label="AOV", value=50.0)]
        notes = _apply_holdout_lift_overlay(
            db=None, shop="x", row=_row_obj("aov", []),
            rows_out=rows_out,
            start_inclusive=datetime(2025, 1, 1),
            end_inclusive=datetime(2025, 1, 31),
        )
        assert notes == []
        assert rows_out[0].holdout_lift_eur is None

    def test_skipped_when_rows_empty(self):
        notes = _apply_holdout_lift_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=[],
            start_inclusive=datetime(2025, 1, 1),
            end_inclusive=datetime(2025, 1, 31),
        )
        assert notes == []

    def test_applied_when_lift_present(self, monkeypatch):
        import app.services.report_holdout_lift as rhl
        monkeypatch.setattr(
            rhl, "holdout_lift_for_shop_window",
            lambda db, s, start, end: {"lift_eur": 250.0, "p_value": 0.03},
        )
        rows_out = [ReportDataRow(label="Revenue", value=1000.0)]
        notes = _apply_holdout_lift_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=rows_out,
            start_inclusive=datetime(2025, 1, 1),
            end_inclusive=datetime(2025, 1, 31),
        )
        assert rows_out[0].holdout_lift_eur == 250.0
        assert rows_out[0].holdout_p_value == 0.03
        assert any("Holdout-measured lift" in n for n in notes)

    def test_silent_on_failure(self, monkeypatch):
        import app.services.report_holdout_lift as rhl
        def _explode(db, s, start, end):
            raise RuntimeError("holdout service down")
        monkeypatch.setattr(rhl, "holdout_lift_for_shop_window", _explode)
        rows_out = [ReportDataRow(label="Revenue", value=1000.0)]
        notes = _apply_holdout_lift_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=rows_out,
            start_inclusive=datetime(2025, 1, 1),
            end_inclusive=datetime(2025, 1, 31),
        )
        assert notes == []
        assert rows_out[0].holdout_lift_eur is None


# ---------------------------------------------------------------------------
# _apply_peer_percentile_overlay
# ---------------------------------------------------------------------------


class TestPeerPercentileOverlay:
    def test_skipped_when_metric_not_in_map(self):
        rows_out = [ReportDataRow(label="x", value=1.0)]
        notes = _apply_peer_percentile_overlay(
            db=None, shop="x", row=_row_obj("repeat_rate", []),
            rows_out=rows_out,
        )
        assert notes == []
        assert rows_out[0].peer_percentile is None

    def test_applied_when_peer_data_present(self, monkeypatch):
        import app.services.benchmarks_vertical as bv
        monkeypatch.setattr(
            bv, "get_vertical_benchmark_report",
            lambda db, s: {
                "peers_status": "ok",
                "metrics": {"monthly_revenue": {"percentile_rank": 75}},
            },
        )
        rows_out = [ReportDataRow(label="Revenue", value=1000.0)]
        notes = _apply_peer_percentile_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=rows_out,
        )
        assert rows_out[0].peer_percentile == 75
        assert any("Peer percentile" in n for n in notes)

    def test_skipped_when_peers_status_not_ok(self, monkeypatch):
        import app.services.benchmarks_vertical as bv
        monkeypatch.setattr(
            bv, "get_vertical_benchmark_report",
            lambda db, s: {
                "peers_status": "insufficient",
                "metrics": {"monthly_revenue": {"percentile_rank": 75}},
            },
        )
        rows_out = [ReportDataRow(label="Revenue", value=1000.0)]
        notes = _apply_peer_percentile_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=rows_out,
        )
        assert notes == []
        assert rows_out[0].peer_percentile is None

    def test_skipped_when_percentile_missing(self, monkeypatch):
        import app.services.benchmarks_vertical as bv
        monkeypatch.setattr(
            bv, "get_vertical_benchmark_report",
            lambda db, s: {
                "peers_status": "ok",
                "metrics": {"monthly_revenue": {"percentile_rank": None}},
            },
        )
        rows_out = [ReportDataRow(label="Revenue", value=1000.0)]
        notes = _apply_peer_percentile_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=rows_out,
        )
        assert notes == []

    def test_silent_on_failure(self, monkeypatch):
        import app.services.benchmarks_vertical as bv
        def _explode(db, s):
            raise RuntimeError("benchmark service down")
        monkeypatch.setattr(bv, "get_vertical_benchmark_report", _explode)
        rows_out = [ReportDataRow(label="Revenue", value=1000.0)]
        notes = _apply_peer_percentile_overlay(
            db=None, shop="x", row=_row_obj("revenue", []),
            rows_out=rows_out,
        )
        assert notes == []


# ---------------------------------------------------------------------------
# _update_last_run
# ---------------------------------------------------------------------------


class TestUpdateLastRun:
    def test_sets_last_run_at_and_commits(self):
        row = SimpleNamespace(last_run_at=None)
        db = MagicMock()
        _update_last_run(db, row)
        assert row.last_run_at is not None
        db.commit.assert_called_once()

    def test_rollback_on_commit_failure(self):
        row = SimpleNamespace(last_run_at=None)
        db = MagicMock()
        db.commit.side_effect = RuntimeError("db down")
        # Must NOT raise — best-effort
        _update_last_run(db, row)
        db.rollback.assert_called_once()
