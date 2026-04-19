"""
Unit tests for app/services/dashboard_drift_scope.py — the C-9
auto-extraction helper that feeds the Monthly Opus audit with a live
scan of Next.js asset classes vs the preventer probe regex.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services.dashboard_drift_scope import (
    _LIBERAL_NEXT_RE,
    _STRICT_PROBE_RE,
    _classify,
    compute_scope_report,
    format_scope_report,
)


class TestClassify:

    def test_chunks_js_covered(self):
        result = _classify(["/_next/static/chunks/abc.js"])
        assert "static/chunks/*.js" in result
        assert result["static/chunks/*.js"]["covered"] is True

    def test_chunks_css_covered(self):
        result = _classify(["/_next/static/chunks/abc.css"])
        assert result["static/chunks/*.css"]["covered"] is True

    def test_media_covered(self):
        result = _classify(["/_next/static/media/font.woff2"])
        assert result["static/media/*.woff2"]["covered"] is True

    def test_build_id_directory_uncovered_and_collapsed(self):
        paths = [
            "/_next/static/xKq4I0fxAkz_fWqb2oH3T/_buildManifest.js",
            "/_next/static/otherDIFFERENT_hash1234/_ssgManifest.js",
        ]
        result = _classify(paths)
        # Both BUILD_IDs collapse to the same stable class label.
        assert "static/{BUILD_ID}/*.js" in result
        assert result["static/{BUILD_ID}/*.js"]["count"] == 2
        # Uncovered because the probe regex only matches chunks/media.
        assert result["static/{BUILD_ID}/*.js"]["covered"] is False

    def test_new_asset_class_surfaced_as_uncovered(self):
        """If Next.js ships a service worker at /_next/static/sw.js, the
        class shows up distinct from chunks/media and not covered."""
        result = _classify(["/_next/static/sw/service-worker.js"])
        label = "static/sw/*.js"
        assert label in result
        assert result[label]["covered"] is False

    def test_non_static_next_paths_grouped_as_other(self):
        result = _classify(["/_next/data/build/route.json"])
        assert "_next/other" in result
        assert result["_next/other"]["covered"] is False

    def test_example_is_first_path_seen(self):
        paths = [
            "/_next/static/chunks/a.js",
            "/_next/static/chunks/b.js",
        ]
        result = _classify(paths)
        assert result["static/chunks/*.js"]["example"] in paths


class TestRegexes:

    def test_strict_matches_chunks(self):
        assert _STRICT_PROBE_RE.search("/_next/static/chunks/abc123.js")
        assert _STRICT_PROBE_RE.search("/_next/static/chunks/abc~def.css")

    def test_strict_rejects_build_id_dir(self):
        assert not _STRICT_PROBE_RE.search(
            "/_next/static/abcd1234/_buildManifest.js"
        )

    def test_strict_rejects_outside_static(self):
        assert not _STRICT_PROBE_RE.search("/_next/data/build/x.json")

    def test_liberal_catches_build_id_dir(self):
        """Liberal regex must catch the paths the strict one misses —
        otherwise the scope-scan would have no signal to surface."""
        assert _LIBERAL_NEXT_RE.search(
            "/_next/static/abcd1234/_buildManifest.js"
        )

    def test_liberal_catches_data_paths(self):
        assert _LIBERAL_NEXT_RE.search("/_next/data/abc/route.json")


class TestComputeScopeReport:

    def test_unavailable_when_no_manifest_and_dashboard_down(self):
        """No build on disk + no route reachable → unavailable=True,
        so the caller surfaces the gap instead of silently reporting
        'all covered' (which would be a false negative)."""
        with patch(
            "app.services.dashboard_drift_scope._collect_manifest_paths",
            return_value=set(),
        ), patch(
            "app.services.dashboard_drift_scope._collect_html_paths",
            return_value=(set(), 0),
        ):
            r = compute_scope_report()
        assert r["unavailable"] is True
        assert r["reason"]

    def test_all_covered_under_clean_build(self):
        """Synthetic build with only chunks/* + media/* references
        → no uncovered classes."""
        fake_paths = {
            "/_next/static/chunks/a.js",
            "/_next/static/chunks/b.css",
            "/_next/static/media/font.woff2",
        }
        with patch(
            "app.services.dashboard_drift_scope._collect_manifest_paths",
            return_value=fake_paths,
        ), patch(
            "app.services.dashboard_drift_scope._collect_html_paths",
            return_value=(fake_paths, 3),
        ):
            r = compute_scope_report()
        assert r["unavailable"] is False
        assert r["uncovered_classes"] == []
        assert "static/chunks/*.js" in r["covered_classes"]

    def test_uncovered_surfaces_when_build_id_assets_present(self):
        """Inject a BUILD_ID-directory path (the real gap the current
        probe regex misses) and confirm it surfaces as uncovered."""
        manifest = {
            "/_next/static/chunks/a.js",
            "/_next/static/xKq4I0fxAkz_fWqb2oH3T/_buildManifest.js",
        }
        with patch(
            "app.services.dashboard_drift_scope._collect_manifest_paths",
            return_value=manifest,
        ), patch(
            "app.services.dashboard_drift_scope._collect_html_paths",
            return_value=(set(), 0),
        ):
            r = compute_scope_report()
        assert r["unavailable"] is False
        labels = {item["class"] for item in r["uncovered_classes"]}
        assert "static/{BUILD_ID}/*.js" in labels

    def test_helper_exception_returns_unavailable_not_raise(self):
        """Defensive fallback — the Monthly audit must never crash
        because the scope scan raised."""
        with patch(
            "app.services.dashboard_drift_scope._collect_manifest_paths",
            side_effect=RuntimeError("boom"),
        ):
            r = compute_scope_report()
        assert r["unavailable"] is True
        assert "RuntimeError" in (r["reason"] or "")


class TestFormatScopeReport:

    def test_unavailable_renders_single_line(self):
        r = {
            "unavailable": True,
            "reason": "manifest missing",
            "manifest_paths": 0,
            "html_paths": 0,
            "routes_reached": 0,
            "uncovered_classes": [],
            "covered_classes": [],
        }
        lines = format_scope_report(r)
        assert len(lines) == 1
        assert "unavailable" in lines[0].lower()

    def test_all_covered_surfaces_no_bet_line(self):
        r = {
            "unavailable": False,
            "reason": None,
            "manifest_paths": 5,
            "html_paths": 10,
            "routes_reached": 3,
            "uncovered_classes": [],
            "covered_classes": ["static/chunks/*.js"],
        }
        out = "\n".join(format_scope_report(r))
        assert "No scope-extension bet needed" in out
        assert "✓ static/chunks/*.js" in out

    def test_uncovered_renders_warning_with_example(self):
        r = {
            "unavailable": False,
            "reason": None,
            "manifest_paths": 6,
            "html_paths": 12,
            "routes_reached": 3,
            "uncovered_classes": [
                {
                    "class": "static/{BUILD_ID}/*.js",
                    "count": 3,
                    "example": "/_next/static/abc/_buildManifest.js",
                }
            ],
            "covered_classes": ["static/chunks/*.js"],
        }
        out = "\n".join(format_scope_report(r))
        assert "UNCOVERED" in out
        assert "static/{BUILD_ID}/*.js" in out
        assert "_buildManifest.js" in out
        assert "justifies a scope-extension bet" in out
