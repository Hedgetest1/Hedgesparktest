#!/usr/bin/env python3
"""audit_openapi_types_fresh.py — catch stale api-types.ts drift.

Problem class: backend adds/changes an endpoint, frontend ships a
component that hardcodes the URL + declares a local type, because
`dashboard/src/app/lib/api-types.ts` hasn't been regenerated. The
typed `apiClient` silently stops covering the new endpoint.

Detected 2026-04-19 on `/analytics/visitor-intent-classification`:
backend endpoint live for 6+ hours, missing from api-types.ts, the
VisitorIntentCard fetched via hardcoded URL. audit_dashboard_fetches
didn't catch it because the component used `useCardFetch` (not bare
`fetch()`).

This audit compares the set of paths in the LIVE `openapi.json`
against the set of paths committed in `api-types.ts`. Any missing
path = drift. Blocks commit until codegen is re-run.

Requirements:
- Backend must be running at http://127.0.0.1:8000
- Dashboard `api-types.ts` must exist

Skip gracefully if backend is unreachable (local dev without backend
process). The preflight `audit_dashboard_live.py` handles the
"backend must be up for full preflight" requirement separately.

Exit codes:
    0  clean (or backend unreachable → skip)
    1  drift: endpoints in openapi not in api-types
    2  script error
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TYPES_PATH = REPO_ROOT / "dashboard" / "src" / "app" / "lib" / "api-types.ts"
OPENAPI_URL = "http://127.0.0.1:8000/openapi.json"


def fetch_openapi_paths() -> set[str] | None:
    """Return the set of route paths from live openapi.json, or None
    if the backend is unreachable (then skip the check)."""
    try:
        with urllib.request.urlopen(OPENAPI_URL, timeout=3) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    return set(data.get("paths", {}).keys())


def extract_types_paths(types_text: str) -> set[str]:
    """Pull path keys from the generated api-types.ts body. The
    openapi-typescript generator emits each path as a string key
    like:    "/analytics/live-opportunities": { ... }
    inside the `paths` interface. We grep exactly that pattern."""
    # Only matches inside the top-level `paths` interface — the
    # schema interface can also have string keys but different
    # indentation (generator always uses 4 spaces for paths). Err on
    # the side of broader matching; false positives are fine here
    # since we only CARE about backend paths that are missing.
    # `(/[^"]*)` not `(/[^"]+)` — the root path "/" has no character
    # after the leading slash; `+` would miss it.
    pattern = re.compile(r'^\s+"(/[^"]*)":\s*\{', re.MULTILINE)
    return set(m.group(1) for m in pattern.finditer(types_text))


def main(argv: list[str]) -> int:
    openapi_paths = fetch_openapi_paths()
    if openapi_paths is None:
        print(
            "audit_openapi_types_fresh: backend unreachable at "
            f"{OPENAPI_URL} — skipping (start backend + re-run preflight "
            "to enable this check)."
        )
        return 0

    if not TYPES_PATH.exists():
        print(
            f"audit_openapi_types_fresh: {TYPES_PATH} not found",
            file=sys.stderr,
        )
        return 2

    types_text = TYPES_PATH.read_text()
    types_paths = extract_types_paths(types_text)

    # Paths that are in the backend but NOT in the types file → drift.
    missing = openapi_paths - types_paths
    # Paths that are in types but not in backend are usually legacy
    # leftovers (endpoint removed, types not regenerated yet) — we
    # warn but don't block, since regenerating eventually cleans them.
    orphan = types_paths - openapi_paths

    if missing:
        print(
            f"audit_openapi_types_fresh: {len(missing)} backend path(s) "
            "missing from dashboard/src/app/lib/api-types.ts"
        )
        print()
        print("These endpoints are live in the backend but the typed")
        print("api-client doesn't know about them. Run:")
        print()
        print("    cd dashboard && npm run api:types")
        print()
        print("Missing paths:")
        for p in sorted(missing):
            print(f"  {p}")
        print()
        return 1

    if orphan:
        print(
            f"audit_openapi_types_fresh: {len(openapi_paths)} paths in "
            f"sync; {len(orphan)} type-only paths (removed endpoints, "
            "cleanup on next codegen regen)"
        )
    else:
        print(
            f"audit_openapi_types_fresh: clean — {len(openapi_paths)} "
            "backend paths all present in api-types.ts"
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_openapi_types_fresh: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
