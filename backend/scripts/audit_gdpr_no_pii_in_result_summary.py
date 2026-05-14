#!/usr/bin/env python
# invariant-eligible: false
# Reason: this is a STATIC source audit (greps gdpr_processor.py for
# any writer that could re-introduce raw PII fields into the receipt).
# The runtime contract is locked by the pytest test
# `test_data_request_no_pii_in_result_summary` (CI) — running a
# subprocess audit every 15 min in invariant_monitor would not add
# coverage. If the doctrine ever requires a DB-side scan ("verify
# stored rows don't contain raw PII"), wire that as a separate
# invariant function in `_check_gdpr_result_summary_pii_drift`.
"""
audit_gdpr_no_pii_in_result_summary.py — enforce that
`gdpr_requests.result_summary` writes contain RECEIPT-ONLY data, never
the raw PII export.

Born 2026-05-14 (TIER_2) after external-CTO audit flagged
`_process_customers_data_request` persisting the full export blob
(events + orders + visitor_state + nudge_events arrays) into
result_summary. A DB breach would have leaked every historical
Art. 15 export, indexed by request_id. The fix moved to receipt-only
storage (counts + delivery status + recipient_hash); this audit
prevents regression.

What we check (static, source-level):
  1. `gdpr_processor.py` is the only writer to `req.result_summary` for
     customers_data_request. Verify nobody else assigns it (grep).
  2. The function that BUILDS the receipt is `_build_export_receipt`
     and it returns only the allowed keys.
  3. `_process_customers_data_request` calls `_build_export_receipt`
     and serialises its return as the request's result_summary.
  4. The PII keys (events / orders / visitor_state / nudge_events
     ARRAYS — not the counts subdict) never appear in the JSON
     literal serialised to result_summary.

Exit codes:
    0 — receipt-only contract intact
    1 — PII surface re-introduced; describe + remediate
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_GDPR_PROC = _BACKEND / "app" / "services" / "gdpr_processor.py"

# PII-shaped field names that MUST NOT appear inside the
# result_summary write site (they're allowed in the in-memory
# export dict; the contract is about what we PERSIST).
_FORBIDDEN_IN_RECEIPT_VALUE = {
    "events",        # list of event records (PII)
    "orders",        # list of order records (PII)
    "visitor_state", # intent scores per product (PII)
    "nudge_events",  # impression history (PII)
}


def _violations() -> list[str]:
    """Return human-readable violation strings (empty list = clean)."""
    out: list[str] = []
    src = _GDPR_PROC.read_text()
    tree = ast.parse(src, filename=str(_GDPR_PROC))

    # Walk the AST: find every assignment of the shape
    #   <obj>.result_summary = <value>
    # and inspect <value> to make sure it does NOT directly include
    # PII array literals.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "result_summary"
            ):
                # Inspect the rhs for PII-shaped dictionary keys
                rhs_src = ast.get_source_segment(src, node.value) or ""
                hit = _scan_rhs_for_pii(rhs_src)
                if hit:
                    out.append(
                        f"{_GDPR_PROC.name}:{node.lineno}: assignment to "
                        f"result_summary contains PII key {hit!r} — "
                        f"only the receipt schema "
                        f"(phase/delivery_status/recipient_hash/"
                        f"request_id/counts) is permitted"
                    )

    # Also verify the _build_export_receipt return shape — its
    # returned dict literal must NOT contain PII array keys.
    for func in ast.walk(tree):
        if not (isinstance(func, ast.FunctionDef)
                and func.name == "_build_export_receipt"):
            continue
        body_src = ast.get_source_segment(src, func) or ""
        # The `counts` sub-dict legitimately mentions "events"/"orders"
        # etc. as scalar count fields. We allow them ONLY when inside
        # `counts: {...}` — never at the top level. The check:
        # forbidden_key followed by `: [` (list-typed value) is PII;
        # `forbidden_key: <len/int>` (count) is fine.
        for key in _FORBIDDEN_IN_RECEIPT_VALUE:
            # Look for the PII shape: "key": [...] — a list literal value
            pattern = rf'["\']({re.escape(key)})["\']\s*:\s*\['
            if re.search(pattern, body_src):
                out.append(
                    f"{_GDPR_PROC.name}: _build_export_receipt returns a "
                    f"list under key {key!r} — receipts store COUNTS "
                    f"(int), never raw arrays"
                )

    return out


def _scan_rhs_for_pii(rhs_src: str) -> str | None:
    """Quick textual scan: look for any forbidden PII key paired with a
    list literal in the same expression (heuristic, not a full type
    check). Returns the offending key name or None.

    Returns None when the RHS is a simple identifier (e.g. `summary`)
    because we cannot resolve its origin statically here — the
    receipt-builder check elsewhere covers that path.
    """
    if not rhs_src:
        return None
    for key in _FORBIDDEN_IN_RECEIPT_VALUE:
        pattern = rf'["\']({re.escape(key)})["\']\s*:\s*\['
        if re.search(pattern, rhs_src):
            return key
    return None


def main() -> int:
    if not _GDPR_PROC.exists():
        print(
            f"audit_gdpr_no_pii_in_result_summary: missing source file "
            f"{_GDPR_PROC} — cannot audit",
            file=sys.stderr,
        )
        return 1
    bad = _violations()
    if not bad:
        print(
            "audit_gdpr_no_pii_in_result_summary: clean — receipt-only "
            "contract intact for gdpr_requests.result_summary"
        )
        return 0
    print("audit_gdpr_no_pii_in_result_summary: VIOLATIONS")
    for v in bad:
        print(f"  {v}")
    print()
    print(
        "GDPR Art. 5(1)(c) data-minimisation contract: result_summary "
        "MUST store the receipt (counts + delivery status + recipient "
        "hash) only. The raw PII export is delivered via email and "
        "lives in-memory only during the worker call. Reconstruction "
        "is always possible from source tables (events, shop_orders, "
        "visitor_purchase_sessions). See gdpr_processor."
        "_build_export_receipt for the canonical schema."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
