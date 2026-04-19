"""
dashboard_drift_scope.py — auto-extract the set of Next.js asset
classes that the drift preventer probe *would* currently see, and
surface any classes the probe regex does NOT match.

Why this exists
---------------
The dashboard-drift preventer (see feature_dashboard_drift_preventer.md)
watches for stale Next.js in-memory manifests by HEAD-probing every
`/_next/static/(?:chunks|media)/...` reference in the landing HTML. The
probe regex was written against what Next.js 16 emits *today*. If a new
Next.js feature starts emitting a new asset class (service workers,
middleware bundles, edge runtime chunks, route-level CSS outside the
probed routes), the probe will silently ignore that class.

Before 2026-04-19 the Monthly Opus self-audit asked about these drift
modes by reading a **hardcoded** list of suspected cases in
`monthly_evolution_audit._build_drift_preventer_state`. Opus had to
creatively imagine whether any of them applied. This module replaces
that creativity with a live scan of the real build.

What it does
------------
1. Reads `/opt/wishspark/dashboard/.next/build-manifest.json` and
   collects every static-asset path the build knows about.
2. Fetches the three probe-routes from the running dashboard and
   extracts every `/_next/...` reference from the served HTML.
3. For each path, checks the strict probe regex match.
4. Classifies the asset by its top-level subdirectory under
   `/_next/static/` (e.g., `chunks`, `media`, `{BUILD_ID}`).
5. Returns a dict with: covered paths, uncovered paths, and a summary
   per class.

Called from the Monthly Opus audit context builder only. No impact on
runtime probe behavior. All failure paths return an "unavailable"
string so an unreachable dashboard or missing build never breaks the
monthly audit.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

import httpx

_log = logging.getLogger("services.dashboard_drift_scope")

_BUILD_MANIFEST = Path("/opt/wishspark/dashboard/.next/build-manifest.json")
_APP_BUILD_MANIFEST = Path(
    "/opt/wishspark/dashboard/.next/app-build-manifest.json"
)

_DASHBOARD_HOST = "http://127.0.0.1:3000"
_PROBE_PATHS = ("/", "/app", "/pricing")
_HTML_TIMEOUT_S = 3.0

# Must stay in lockstep with
# `app/workers/tasks/dashboard_asset_probe_task.py::_ASSET_RE` +
# `scripts/audit_dashboard_live.py::ASSET_RE`. The structural preventer
# `audit_dashboard_drift_probe_parity.py` enforces that all three match.
_STRICT_PROBE_RE = re.compile(
    r'/_next/static/(?:chunks|media)/[A-Za-z0-9_~.\-]+\.[A-Za-z0-9]+'
)
# Liberal regex to discover ANY `/_next/...` reference. Used to catch
# paths the strict probe regex doesn't match.
_LIBERAL_NEXT_RE = re.compile(r'/_next/[A-Za-z0-9_~./\-]+\.[A-Za-z0-9]+')

# Next.js BUILD_IDs are base64-URL-ish slugs, typically 16–24 characters
# including underscores and hyphens. Collapse to a stable class label
# so the scope scan doesn't treat every build as a "new" uncovered class.
_BUILD_ID_LIKE_RE = re.compile(r'^[A-Za-z0-9_\-]{12,}$')


def _collect_manifest_paths() -> set[str]:
    """Read build-manifest.json + app-build-manifest.json and return
    the union of every static-asset path they reference.

    Returns paths with leading slash: `/_next/static/...`. Empty set on
    any read or parse error — the caller treats that as
    "manifests unavailable" and surfaces only the HTML-scan signal.
    """
    paths: set[str] = set()
    for manifest in (_BUILD_MANIFEST, _APP_BUILD_MANIFEST):
        if not manifest.exists():
            continue
        try:
            doc = json.loads(manifest.read_text())
        except Exception as exc:  # noqa: BLE001
            _log.debug("manifest parse failed %s: %s", manifest, exc)
            continue

        def _walk(node: object) -> None:
            if isinstance(node, str):
                # Skip entries that are not asset paths (e.g. version tags).
                if "/" in node and "." in node:
                    paths.add("/_next/" + node.lstrip("/"))
            elif isinstance(node, list):
                for item in node:
                    _walk(item)
            elif isinstance(node, dict):
                for value in node.values():
                    _walk(value)

        _walk(doc)
    return paths


def _collect_html_paths() -> tuple[set[str], int]:
    """Fetch probe routes from the running dashboard and return
    (distinct /_next/ paths referenced, number of routes actually
    reached). Per-route failures are tolerated silently — this is a
    diagnostic scan, not a health check. If the outer httpx.Client
    context itself raises (e.g., network stack down), the caller in
    compute_scope_report catches it and reports the scan as
    unavailable."""
    hits: set[str] = set()
    reached = 0
    with httpx.Client(
        timeout=_HTML_TIMEOUT_S, follow_redirects=True
    ) as client:
        for route in _PROBE_PATHS:
            try:
                r = client.get(f"{_DASHBOARD_HOST}{route}")
            except Exception:  # noqa: BLE001
                continue
            if r.status_code != 200:
                continue
            reached += 1
            for m in _LIBERAL_NEXT_RE.finditer(r.text):
                hits.add(m.group(0))
    return hits, reached


def _classify(paths: Iterable[str]) -> dict[str, dict[str, object]]:
    """Group asset paths by top-level subdirectory under
    `/_next/static/` and report whether each class is covered by the
    strict probe regex. Non-static `/_next/` paths (e.g., data routes)
    are grouped under `_next/other`.

    Returns:
        {
          class_label: {
            "covered": bool,
            "count": int,
            "example": str,
          },
          ...
        }
    """
    classes: dict[str, dict[str, object]] = {}
    for p in paths:
        if p.startswith("/_next/static/"):
            rest = p[len("/_next/static/"):]
            # Top-level subdirectory is the class.
            parts = rest.split("/", 1)
            sub = parts[0]
            ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
            # Normalize BUILD_ID-style directories (24-char base64) into a
            # single class label so the class set stays stable across
            # builds. A BUILD_ID is always the middle token.
            label = (
                f"static/{sub}/*.{ext}" if ext else f"static/{sub}/*"
            )
            # Collapse 12+ char BUILD_ID-like directories so the class
            # label is stable across builds.
            if _BUILD_ID_LIKE_RE.match(sub) and not sub.isdigit():
                label = f"static/{{BUILD_ID}}/*.{ext}" if ext else (
                    "static/{BUILD_ID}/*"
                )
        else:
            label = "_next/other"
        entry = classes.setdefault(
            label,
            {"covered": False, "count": 0, "example": p},
        )
        entry["count"] = int(entry["count"]) + 1
        if _STRICT_PROBE_RE.search(p):
            entry["covered"] = True
        if not entry.get("example"):
            entry["example"] = p
    return classes


def compute_scope_report() -> dict[str, object]:
    """Produce the structured report the monthly audit consumes.

    Contract:
        {
          "manifest_paths": int,
          "html_paths": int,
          "routes_reached": int,
          "uncovered_classes": [
              {"class": str, "count": int, "example": str},
              ...
          ],
          "covered_classes": [str, ...],
          "unavailable": bool,
          "reason": str | None,
        }
    """
    try:
        manifest_paths = _collect_manifest_paths()
        html_paths, routes_reached = _collect_html_paths()
        all_paths = manifest_paths | html_paths
        if not all_paths:
            return {
                "manifest_paths": 0,
                "html_paths": 0,
                "routes_reached": routes_reached,
                "uncovered_classes": [],
                "covered_classes": [],
                "unavailable": True,
                "reason": (
                    "no build manifest on disk and no dashboard route "
                    "reachable"
                ),
            }
        classes = _classify(all_paths)
        uncovered = [
            {
                "class": label,
                "count": int(info["count"]),
                "example": str(info["example"]),
            }
            for label, info in sorted(classes.items())
            if not info["covered"]
        ]
        covered = sorted(
            label for label, info in classes.items() if info["covered"]
        )
        return {
            "manifest_paths": len(manifest_paths),
            "html_paths": len(html_paths),
            "routes_reached": routes_reached,
            "uncovered_classes": uncovered,
            "covered_classes": covered,
            "unavailable": False,
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "manifest_paths": 0,
            "html_paths": 0,
            "routes_reached": 0,
            "uncovered_classes": [],
            "covered_classes": [],
            "unavailable": True,
            "reason": f"scope scan raised: {type(exc).__name__}",
        }


def format_scope_report(report: dict[str, object]) -> list[str]:
    """Format a scope report into lines suitable for appending to the
    Monthly Opus audit context string."""
    if report.get("unavailable"):
        return [
            "  Scope scan unavailable: "
            + str(report.get("reason") or "unknown"),
        ]
    lines: list[str] = []
    lines.append(
        f"  Scope scan: {report['manifest_paths']} manifest paths, "
        f"{report['html_paths']} HTML-referenced paths, "
        f"{report['routes_reached']}/3 probe routes reached"
    )
    covered = report.get("covered_classes") or []
    if covered:
        lines.append("  Covered asset classes (probe regex matches):")
        for label in covered:
            lines.append(f"    ✓ {label}")
    uncovered = report.get("uncovered_classes") or []
    if uncovered:
        lines.append(
            "  ⚠️ UNCOVERED asset classes (probe regex does NOT match):"
        )
        for item in uncovered:
            lines.append(
                f"    ✗ {item['class']} ({item['count']} paths, "
                f"e.g. {item['example']})"
            )
        lines.append(
            "  → If any uncovered class is referenced by served HTML "
            "(not only by the build manifest), this is a genuine probe "
            "gap and justifies a scope-extension bet."
        )
    else:
        lines.append(
            "  → All asset classes referenced by the live build are "
            "covered by the probe regex. No scope-extension bet needed."
        )
    return lines
