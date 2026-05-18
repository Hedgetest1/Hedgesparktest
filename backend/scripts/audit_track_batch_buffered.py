#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of app/api/track.py source — code structure, not
#   runtime state. Commit-stage-only (like audit_track_lazy_db).
"""audit_track_batch_buffered.py — structural preventer
(honest-residual #7, jewel J3 macchia-d'olio completion of the write
ingest surface).

Two contracts on app/api/track.py, both born 2026-05-18:

  (A) track_event_batch MUST route its non-purchase items through the
      async ingest buffer (`enqueue_event`). Before the fix the batch
      handler db.add(Event(...))-ed every item synchronously — at 10k
      the batch ingest storm shared the 80-conn PgBouncer pool with
      reads + 8 workers and could pool-cascade (the single-/track
      J3-part-2 fix had no batch sibling). If `enqueue_event` is
      absent from track_event_batch the buffered path regressed.

  (B) NO `Event(` in track.py may be constructed with literal keyword
      arguments — only `Event(**fields)` where `fields` comes from the
      single source `_event_fields_from_payload`. The pre-fix batch
      had its OWN inline Event(shop_domain=..., ...) with 12 of 19
      columns, silently dropping utm_*/click_id/landing_page (an
      attribution-loss drift). One field source ⟹ a column add is one
      edit and can never diverge per-handler again.

  (C) track_event_batch MUST call `_bump_heatmap_bucket` — full
      side-effect parity with single /track's non-purchase branch.
      spark-tracker.js sends click + mousemove (the ONLY events the
      heatmap acts on) via sendEventBatched ⟹ /track/batch; before
      2026-05-18 the batch never bumped the heatmap, so the Lite
      spatial HeatmapCard was structurally starved of ~all its data
      in production. Omitting it again silently re-breaks a shipped
      feature with a green test suite.

Non-vacuous: (A) flags a track_event_batch with no enqueue_event;
(B) flags the exact pre-fix `Event(shop_domain=...)` literal-kwarg
shape; (C) flags a batch handler with no _bump_heatmap_bucket.
GREEN only on the unified tree. A textual
`# event-fields: ok — <reason>` marker on a def line opts that
function out of (B) for a genuine exception.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "api" / "track.py"
_BATCH_FUNC = "track_event_batch"


def _calls(func: ast.AST, name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            f = node.func
            if getattr(f, "id", None) == name or getattr(f, "attr", None) == name:
                return True
    return False


def main() -> int:
    src = TARGET.read_text()
    lines = src.splitlines()
    tree = ast.parse(src)
    violations: list[str] = []

    # ---- (A) the batch handler must buffer non-purchase items -------
    batch_fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == _BATCH_FUNC),
        None,
    )
    if batch_fn is None:
        violations.append(
            f"  {_BATCH_FUNC}() not found in app/api/track.py (renamed? "
            "update _BATCH_FUNC + re-verify the buffered contract)."
        )
    elif not _calls(batch_fn, "enqueue_event"):
        violations.append(
            f"  app/api/track.py:{batch_fn.lineno} {_BATCH_FUNC}() does "
            "not call enqueue_event — non-purchase batch items would "
            "sync-INSERT and pool-cascade at 10k (honest-residual #7 "
            "regressed). Route non-purchase via the ingest buffer."
        )

    # ---- (C) batch must bump the heatmap (side-effect parity) ------
    if batch_fn is not None and not _calls(batch_fn, "_bump_heatmap_bucket"):
        violations.append(
            f"  app/api/track.py:{batch_fn.lineno} {_BATCH_FUNC}() does "
            "not call _bump_heatmap_bucket — batched click/mousemove "
            "(the tracker's heatmap transport) would not feed the Lite "
            "spatial HeatmapCard (shipped feature silently starved). "
            "Mirror single /track's non-purchase branch."
        )

    # ---- (B) every Event(...) must be Event(**fields) --------------
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "Event"):
            continue
        # Opt-out marker scan: nearest enclosing def line window.
        # (Conservative: scan a small window above the call line.)
        window = "\n".join(lines[max(0, node.lineno - 4):node.lineno])
        if "event-fields: ok" in window:
            continue
        literal_kw = [k.arg for k in node.keywords if k.arg is not None]
        if literal_kw:
            violations.append(
                f"  app/api/track.py:{node.lineno} Event(...) built with "
                f"literal keyword arg(s) {literal_kw[:4]}… — reconstructs "
                "the field set outside _event_fields_from_payload (the "
                "utm_*/click_id/landing_page drift class). Use "
                "Event(**_event_fields_from_payload(payload))."
            )

    if violations:
        print("audit_track_batch_buffered: FAIL — the batch write path "
              "regressed (pool-cascade or field-drift class):")
        print("\n".join(violations))
        return 1
    print("audit_track_batch_buffered: OK — /track/batch buffers "
          "non-purchase items + every Event() uses the single "
          "_event_fields_from_payload source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
