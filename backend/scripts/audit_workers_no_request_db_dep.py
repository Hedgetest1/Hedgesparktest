#!/usr/bin/env python
# invariant-eligible: false
#   Static AST/text check of app/workers/ source — code structure, not
#   runtime state. Commit-stage-only by nature.
"""audit_workers_no_request_db_dep.py — structural preventer (10k).

Born 2026-05-16. Commit b35b1ac wired a 20s `SET LOCAL
statement_timeout` + `idle_in_transaction_session_timeout` into the
THREE FastAPI request-scoped DB deps ONLY (`get_db`, `get_read_db`,
`get_lazy_read_db`). Background workers are deliberately EXCLUDED:
they bind their own `SessionLocal` to the shared engine and
legitimately run multi-minute jobs (retention DELETE over ~100M rows,
GDPR erasure cascade) that MUST NOT be killed at 20s.

An independent audit verified all 7 workers + 12 worker tasks acquire
sessions via their own `sessionmaker` / the bare `SessionLocal` —
none import or call the request deps. This preventer LOCKS that
invariant: if a future worker imports `get_db` / `get_read_db` /
`get_lazy_read_db` (or FastAPI `Depends`), its long jobs would
silently start dying at 20s with no test catching it.

FAIL (exit 1) if any file under app/workers/ references one of the
request DB deps or FastAPI `Depends`. Opt-out (should never be
needed): `# request-dep-in-worker: ok — <reason>` on the import line.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_io import safe_read_text  # noqa: E402  TOCTOU-safe glob read

_ROOT = Path(__file__).resolve().parent.parent
_WORKERS = _ROOT / "app" / "workers"
_FORBIDDEN = ("get_lazy_read_db", "get_read_db", "get_db", "Depends")


def main() -> int:
    violations: list[str] = []
    for py in sorted(_WORKERS.rglob("*.py")):
        text = safe_read_text(py)
        if text is None:
            continue  # raced with concurrent delete; next cycle re-scans
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "request-dep-in-worker: ok" in line:
                continue
            for tok in _FORBIDDEN:
                # word-ish match: import / call of the request dep
                if tok in line and (
                    f"import {tok}" in line
                    or f", {tok}" in line
                    or f"{tok}(" in line
                    or f"Depends({tok}" in line
                    or line.strip().endswith(tok)
                ):
                    violations.append(
                        f"  {py.relative_to(_ROOT)}:{i} references "
                        f"request DB dep `{tok}` — workers MUST use their "
                        f"own SessionLocal (b35b1ac 20s timeout is "
                        f"request-only by design): {stripped[:90]}"
                    )
                    break

    if violations:
        print("audit_workers_no_request_db_dep: FAIL — a worker uses a "
              "request-scoped DB dep (its long jobs would die at 20s):")
        print("\n".join(violations))
        return 1
    print("audit_workers_no_request_db_dep: OK — no worker imports "
          "get_db/get_read_db/get_lazy_read_db/Depends.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
