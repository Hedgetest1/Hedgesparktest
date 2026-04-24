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
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TYPES_PATH = REPO_ROOT / "dashboard" / "src" / "app" / "lib" / "api-types.ts"
OPENAPI_URL = "http://127.0.0.1:8000/openapi.json"


def fetch_openapi_paths() -> tuple[set[str] | None, str | None]:
    """Return (paths, reason_for_skip). `paths` is None only when the
    backend is unreachable — in which case `reason_for_skip` explains
    why (for logging). Any other failure (non-200, malformed JSON,
    permission error) returns (None, "<reason>") so the caller can
    distinguish fail-open from genuine skip.

    MED-11 closure 2026-04-24: pre-fix this returned None on ANY
    exception, silently masking malformed responses or auth errors
    behind "backend unreachable" — a fail-open blindspot.
    """
    try:
        with urllib.request.urlopen(OPENAPI_URL, timeout=3) as resp:
            if resp.status != 200:
                return None, f"backend returned HTTP {resp.status}"
            body = resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, f"backend unreachable: {type(exc).__name__}: {exc}"
    except Exception as exc:
        return None, f"fetch error: {type(exc).__name__}: {exc}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        # This is a fail-CLOSED case now: if the backend returns malformed
        # JSON, that's a real bug (corrupted openapi.json) — surface loud,
        # not silently skip.
        return None, f"openapi.json malformed: {exc}"

    paths = data.get("paths")
    if not isinstance(paths, dict):
        return None, "openapi.json missing or malformed 'paths' field"
    return set(paths.keys()), None


def extract_types_paths(types_text: str) -> set[str]:
    """Pull path keys from the generated api-types.ts body. The
    openapi-typescript generator emits each path as a string key
    like:    "/analytics/live-opportunities": { ... }
    inside the `paths` interface.

    MED-11 closure 2026-04-24: pre-fix the regex hardcoded `^\\s+"`
    which worked for 4-space indent (the generator's 2024 default) but
    silently failed if a user configured 2-space indent, tabs, or if a
    future generator version changed formatting. We now accept any
    leading whitespace AND strip it, which is what "indent-agnostic"
    means in practice."""
    # Indent-agnostic: match any leading whitespace (spaces, tabs, mixed).
    # Root path "/" is captured by `[^"]*` (not `+`) — has no character
    # after the leading slash.
    pattern = re.compile(r'^[ \t]+"(/[^"]*)":\s*\{', re.MULTILINE)
    return set(m.group(1) for m in pattern.finditer(types_text))


@telemetered("audit_openapi_types_fresh")
def main(argv: list[str]) -> int:
    openapi_paths, skip_reason = fetch_openapi_paths()
    if openapi_paths is None:
        # Graceful skip ONLY when backend is genuinely unreachable.
        # Malformed responses are reported loudly so the operator
        # investigates instead of assuming "all good" from a silent skip.
        is_unreachable = skip_reason and "unreachable" in skip_reason
        if is_unreachable:
            print(
                f"audit_openapi_types_fresh: {skip_reason} "
                f"at {OPENAPI_URL} — skipping (start backend + re-run "
                "preflight to enable this check)."
            )
            return 0
        print(
            f"audit_openapi_types_fresh: FAIL — {skip_reason or 'unknown fetch error'}",
            file=sys.stderr,
        )
        return 2

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
