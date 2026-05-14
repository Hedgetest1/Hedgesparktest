#!/usr/bin/env python
"""
audit_ssr_body_size.py — lock in the landing-SSR regression fix.

Policy
------
Every prerendered route in `.next/server/app/*.html` must ship a
meaningful HTML body (> ~3 KB). Anything smaller is the shape the
landing had on 2026-04-15 before the fix: `<body><div hidden>
<!--$--><!--/$--></div><script ... async></script></body>` —
an empty shell that hydrates client-side, leaving the crawler and
every cold visitor staring at a blank page until the full JS
bundle downloads.

That regression was caught by Lighthouse (NO_LCP → Perf score 0),
fixed in commit `fix(landing): render SSR content unconditionally`,
and this gate now blocks its reintroduction at build time. No
lighthouse, no chromium, no e2e harness — just a filesystem read
of the prerendered HTML that Next.js produced during `next build`.

Why 3 KB: the smallest known legitimate prerendered page on this
dashboard is ~10 KB (/proof). A broken page is ~40 bytes (just
`<div hidden>`). 3 KB is the honest floor: anything below is almost
certainly a regression; anything above is a real page. Adjust the
threshold if a legitimate small page is ever added, but do so
consciously — this gate is exactly the trip-wire the landing
regression needed.

Skips
-----
* `_global-error.html`, `_not-found.html` — Next.js internal error
  pages, intentionally minimal.
* Files missing the `<body>` tag — not our format, skip.

Usage
-----
    ./venv/bin/python scripts/audit_ssr_body_size.py
    ./venv/bin/python scripts/audit_ssr_body_size.py --detail
    ./venv/bin/python scripts/audit_ssr_body_size.py --min 5000
"""
from __future__ import annotations

import pathlib
import re
import sys
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

SERVER_APP = pathlib.Path("/opt/wishspark/dashboard/.next/server/app")

# Pages that are legitimately tiny and never serve real content.
SKIP_NAMES = {"_global-error.html", "_not-found.html"}

# Body size floor — see module docstring. Override with --min N.
DEFAULT_MIN_BYTES = 3000

# Extract the content between <body ...> and </body> without a DOM parser.
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL)


def _body_size(html: str) -> int | None:
    m = _BODY_RE.search(html)
    if not m:
        return None
    return len(m.group(1))


@telemetered("audit_ssr_body_size")
def main() -> int:
    args = sys.argv[1:]
    detail = "--detail" in args
    min_bytes = DEFAULT_MIN_BYTES
    if "--min" in args:
        idx = args.index("--min")
        try:
            min_bytes = int(args[idx + 1])
        except (IndexError, ValueError):
            print("audit_ssr_body_size: --min requires an integer", file=sys.stderr)
            return 2

    if not SERVER_APP.exists():
        print(f"audit_ssr_body_size: no prerendered output at {SERVER_APP}")
        print("  run `cd dashboard && npx next build` to produce one")
        return 0

    results: list[tuple[str, int]] = []
    skipped = 0
    for html_path in sorted(SERVER_APP.glob("*.html")):
        if html_path.name in SKIP_NAMES:
            skipped += 1
            continue
        text = safe_read_text(html_path)
        if text is None:
            continue
        size = _body_size(text)
        if size is None:
            continue
        results.append((html_path.name, size))

    if not results:
        print("audit_ssr_body_size: no prerendered HTML files found")
        return 0

    failures = [(n, s) for n, s in results if s < min_bytes]

    print(f"audit_ssr_body_size: checked {len(results)} routes (floor {min_bytes:,} B)")
    if detail or failures:
        for name, size in sorted(results, key=lambda kv: kv[1]):
            marker = "OVER" if size < min_bytes else "OK  "
            print(f"  {marker}  {size:>8,} B  {name}")

    if failures:
        print()
        print(f"FAIL: {len(failures)} route(s) below SSR body floor")
        for name, size in failures:
            print(f"  {name}: {size:,} B < {min_bytes:,} B")
        print()
        print(
            "This is the exact shape the landing had before the 2026-04-15 SSR "
            "fix: a client-rendered shell with empty <body>. Find the offending "
            "page.tsx and remove any top-level `if (!x) return null` gate that "
            "depends on a useState flag flipped inside a useEffect — `useEffect` "
            "does not run during SSR so the guard always trips on the server."
        )
        return 1

    print(f"OK: all {len(results)} routes above SSR body floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
