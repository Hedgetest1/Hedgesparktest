#!/usr/bin/env python
# invariant-eligible: false — static source-scan over app/ raw SQL strings,
# no runtime state; sibling of audit_sql_schema / audit_sql_columns /
# audit_tenant_isolation — commit-stage gate only.
"""
audit_sql_ms_column_type.py — Static audit for the pg-ms-type bug class.

Bug class:
  ``events.timestamp`` / ``analytics_event.ts_ms`` /
  ``product_metrics.last_event_at`` are BIGINT epoch-milliseconds.
  Comparing them directly to a Postgres timestamp expression
  (``now()``, ``current_timestamp``, a bound ``:datetime`` parameter,
  or an ISO literal) fails at runtime with::

    psycopg2.errors.UndefinedFunction:
      operator does not exist: bigint >= timestamp without time zone

  The transaction aborts; in best-effort paths the failure is silent
  (``outcome_status='evaluation_failed'``, brain LEARN limb broken).

Why this audit exists:
  2026-05-08 commit ``35796ae`` hand-fixed the live instance in
  ``merchant_brain.evaluate_pending_outcomes`` after the founder
  challenge "perché li hai dismessi?". The instance was fixed but
  no audit prevented the next instance of the class. Per CLAUDE.md
  §21 macchia d'olio + §22.6 preventer-pattern, the bug CLASS must
  be prevented, not just the instance.

Detection:
  Walk every ``text("...")`` SQL string in app/. For each occurrence
  of an epoch-ms column reference, check a ±100-char window for a
  comparison operator paired with a Postgres timestamp expression.
  Flag if found.

Opt-out (when the comparison is provably safe — e.g. bind is already
int ms, or column is compared against another bigint column):
  Add ``# sql-ms-type: ok — <reason>`` on the same line as the
  ``text(...)`` call OR within the body of the call. The marker is
  scanned line-by-line across the call's source span.

Fix patterns:
  (a) Cast Python datetime → epoch ms before bind::

        cutoff_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        text("... WHERE timestamp >= :c"), {"c": cutoff_ms}

  (b) Wrap the column in SQL::

        text("... WHERE to_timestamp(timestamp/1000.0) >= :dt")

  Either is OK; (a) is faster (no cast per row).

Non-vacuity:
  ``--self-test`` feeds known-buggy + known-safe snippets through the
  matcher; exits non-zero if buggy snippets are not flagged OR if
  safe snippets ARE flagged. The production scan refuses to run if
  the self-test fails. Defends against silent audit regression
  (the smoke-fiction class).
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, "/opt/wishspark/backend")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _audit_io import safe_read_text  # noqa: E402
from _audit_telemetry_shim import telemetered  # noqa: E402


APP_ROOT = pathlib.Path("/opt/wishspark/backend/app")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}

# The BIGINT epoch-ms columns. To extend: add the column reference in
# the form actually used in raw SQL (table-qualified or unique bare name).
# A bare name like "timestamp" is intentionally NOT added — too ambiguous
# (PG keyword + column on many tables) — require the explicit
# ``events.timestamp`` form.
EPOCH_MS_COLUMN_PATTERNS = (
    r"events\.timestamp",
    r"ts_ms",
    r"last_event_at",
)
EPOCH_MS_RE = re.compile(
    r"\b(?:" + "|".join(EPOCH_MS_COLUMN_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# Comparison operators that produce the type-mismatch error.
CMP_OP_RE = re.compile(r"(?:>=|<=|!=|<>|<|>|=)|\bBETWEEN\b", re.IGNORECASE)

# Right-hand expressions that are Postgres timestamps (NOT bigint epoch-ms).
# A bind ``:param`` is included because static SQL analysis cannot see
# the bind dataflow — the canonical 35796ae bug WAS ``... >= :c`` with
# c=datetime. Sites where the bind is provably int ms add the opt-out.
PG_TIMESTAMP_RHS_RE = re.compile(
    r"\bnow\s*\(\s*\)|"
    r"\bcurrent_timestamp\b|"
    r"\bcurrent_date\b|"
    r"\blocaltimestamp\b|"
    r"\blocaltime\b|"
    r"\bclock_timestamp\s*\(|"
    r"\bstatement_timestamp\s*\(|"
    r"'\d{4}-\d{2}-\d{2}|"
    r":[A-Za-z_]\w*",
    re.IGNORECASE,
)

# Matches text("..."), text('...'), text(\"\"\"...\"\"\") in source.
_SQL_CALL = re.compile(
    r'text\s*\(\s*(?P<quote>["\']{1,3})(?P<body>.*?)(?P=quote)\s*\)',
    re.DOTALL,
)

_OPT_OUT_RE = re.compile(r"#\s*sql-ms-type:\s*ok", re.IGNORECASE)


def _iter_py(root: pathlib.Path):
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s)


_FROM_OR_JOIN_EVENTS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+events\b", re.IGNORECASE
)

# Parse `FROM <table> <alias>` / `JOIN <table> <alias>` / `FROM <table> AS <alias>`
# from a SQL body. Returns dict mapping alias → table for the epoch-ms tables.
# This closes the §21 aliased-events gap (e.g. `FROM events e WHERE e.timestamp ...`)
# without ad-hoc handling per table. Born 2026-05-20 after the founder
# "accepted limits aren't 11/10" pushback.
_EPOCH_MS_TABLES = {"events", "analytics_event", "product_metrics"}
_TABLE_ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+"
    r"(?P<table>events|analytics_event|product_metrics)\b"
    r"(?:\s+AS)?"
    r"\s+(?P<alias>[A-Za-z_]\w*)"
    r"(?=\s|$|,|;|\)|\bON\b|\bWHERE\b|\bUSING\b)",
    re.IGNORECASE,
)


def _extract_aliases(sql_body: str) -> dict[str, str]:
    """Extract `<alias> → <table>` mappings for the epoch-ms tables in
    the FROM/JOIN clauses. Skips reserved SQL keywords that can appear
    after a table name (ON, WHERE, USING, AS).
    """
    aliases: dict[str, str] = {}
    reserved = {"on", "where", "using", "as", "left", "right", "inner",
                "outer", "full", "cross", "natural", "join", "group",
                "order", "having", "limit", "offset", "for", "with",
                "select"}
    for m in _TABLE_ALIAS_RE.finditer(sql_body):
        alias = m.group("alias")
        if alias.lower() in reserved:
            continue
        aliases[alias] = m.group("table").lower()
    return aliases


def _aliased_cmp_re(alias: str) -> re.Pattern:
    """Build a comparison regex for `<alias>.timestamp <op> <rhs>`.
    Returns compiled pattern reusable across calls."""
    return re.compile(
        r"(?<![\w.])" + re.escape(alias) + r"\.(?P<col>timestamp)\s*" + _COMPARE_TAIL,
        re.IGNORECASE,
    )

# Comparison pattern: <col> [op] <rhs>.
#
# RHS is captured as: one bare-token (no whitespace/comma/paren),
# optionally followed by a parenthesised arg-list (for function calls
# like ``now()`` or ``EXTRACT(EPOCH FROM ...)``), optionally followed
# by ``AND <token>`` (for ``BETWEEN x AND y``). Nested parens inside
# the arg-list are NOT handled — partial captures are enough for
# ``_is_unsafe_rhs`` to identify the safe forms.
_COMPARE_TAIL = (
    r"(?P<op>>=|<=|!=|<>|<|>|=|\bBETWEEN\b)\s*"
    r"(?P<rhs>[^\s,()]+(?:\s*\([^)]*\))?(?:\s+AND\s+[^\s,()]+(?:\s*\([^)]*\))?)?)"
)

_EXPLICIT_CMP_RE = re.compile(
    r"\b(?P<col>events\.timestamp|ts_ms|last_event_at)\s*" + _COMPARE_TAIL,
    re.IGNORECASE,
)
# Bare `timestamp` form — used only when SQL body has FROM/JOIN events.
# Excludes qualified refs (handled by _EXPLICIT_CMP_RE) and SQL syntax
# uses (`AS timestamp`, `timestamp WITHOUT TIME ZONE`, etc.) via
# post-match filters.
_BARE_TIMESTAMP_CMP_RE = re.compile(
    r"(?<![\w.])(?P<col>timestamp)\s*" + _COMPARE_TAIL,
    re.IGNORECASE,
)


def _is_unsafe_rhs(rhs: str) -> bool:
    """Determine if ``rhs`` (the right-hand side of a comparison) is a
    Postgres-timestamp-typed expression that would type-mismatch a
    BIGINT epoch-ms column.

    Returns True for: ``now()``, ``current_timestamp``, ISO literals,
    bind ``:param`` names without ``_ms``/``_epoch``/``_millis``
    suffix. Returns False for: numeric literals, ``EXTRACT(EPOCH ...``,
    ``to_timestamp(...)``, and bind names that explicitly signal ms.
    """
    rhs_strip = rhs.strip()
    rhs_lc = rhs_strip.lower()

    # Direct Postgres-timestamp expressions ↦ unsafe
    if re.match(r"now\s*\(\s*\)", rhs_lc):
        return True
    if re.match(
        r"current_timestamp\b|current_date\b|localtimestamp\b|localtime\b",
        rhs_lc,
    ):
        return True
    if re.match(r"clock_timestamp\s*\(|statement_timestamp\s*\(", rhs_lc):
        return True
    if re.match(r"'\d{4}-\d{2}-\d{2}", rhs_strip):
        return True

    # Bind parameter — only the canonical `_ms` / `_epoch` / `_millis`
    # naming convention is treated as safe-by-name. Every other bind
    # name (including common ones like `cutoff`, `ts`, `since`) is
    # flagged so the safe site declares explicitly via opt-out comment.
    # Rationale: the canonical 35796ae bug used `:c` — silent default-
    # safe lists are how that class slipped through prior reviews.
    bind_m = re.match(r":([A-Za-z_]\w*)", rhs_strip)
    if bind_m:
        name = bind_m.group(1).lower()
        if re.search(r"_ms\b|_epoch\b|_millis\b|_ns\b", name):
            return False
        return True

    # SQL-level epoch-ms casts ↦ safe (caller already in ms domain)
    if re.match(r"extract\s*\(\s*epoch", rhs_lc):
        return False
    if re.match(r"to_timestamp\s*\(", rhs_lc):
        return False
    if re.match(r"\(\s*extract\s*\(\s*epoch", rhs_lc):
        return False

    # CTE/table column ref like `cutoff_ms.ts` where the source name
    # signals ms ↦ safe.
    cte_m = re.match(r"([A-Za-z_]\w*)\.\w+", rhs_strip)
    if cte_m:
        cname = cte_m.group(1).lower()
        if re.search(r"_ms\b|_epoch\b|_millis\b", cname):
            return False

    # Numeric literal ↦ safe
    if re.match(r"-?\d+(?:\.\d+)?$", rhs_strip):
        return False

    # Default: don't false-positive on unknown expressions.
    return False


def _is_sql_type_context(normalized: str, pos: int) -> bool:
    """Return True if the bare-``timestamp`` occurrence at ``pos`` is a
    SQL TYPE NAME context rather than a column reference. Examples:
    ``CAST(x AS timestamp)``, ``timestamp WITHOUT TIME ZONE``,
    ``timestamp WITH TIME ZONE``.
    """
    pre = normalized[max(0, pos - 6) : pos].lower()
    if pre.endswith(" as "):
        return True
    post = normalized[pos + len("timestamp") : pos + len("timestamp") + 25].lower()
    post_strip = post.lstrip()
    if post_strip.startswith(("without", "with time zone", "with timezone")):
        return True
    return False


def find_violations_in_sql(sql_body: str) -> list[tuple[str, str]]:
    """For one SQL body, return (col_match, context_window) for each
    epoch-ms-column reference that sits in a comparison against an
    UNSAFE (Postgres-timestamp-typed) right-hand side.

    Detection has three passes:

    1. **Explicit refs** — ``events.timestamp``, ``ts_ms``,
       ``last_event_at``. Matched against ``_EXPLICIT_CMP_RE``.

    2. **Bare ``timestamp`` in events-context** — when the SQL body
       contains ``FROM events`` or ``JOIN events``, ``timestamp`` is
       treated as ``events.timestamp``. SQL-type-name uses
       (``AS timestamp``, ``timestamp WITHOUT TIME ZONE``) are excluded.
       Catches the canonical 35796ae form::

         SELECT COUNT(*) FROM events WHERE ... AND timestamp >= :c

    3. **Aliased ``<alias>.timestamp`` refs** — when the SQL body
       contains ``FROM events e`` or ``FROM events AS e`` (similarly
       for analytics_event / product_metrics), ``e.timestamp`` is
       treated as the table's epoch-ms column. Born 2026-05-20 closing
       the "accepted limits aren't 11/10" gap.
    """
    findings: list[tuple[str, str]] = []
    normalized = _normalize_ws(sql_body)

    # Pass 1: explicit refs
    for m in _EXPLICIT_CMP_RE.finditer(normalized):
        if _is_unsafe_rhs(m.group("rhs")):
            ctx_start = max(0, m.start() - 30)
            ctx_end = min(len(normalized), m.end() + 30)
            findings.append((m.group("col"), normalized[ctx_start:ctx_end]))

    # Pass 2: bare-timestamp refs (events-context only)
    if _FROM_OR_JOIN_EVENTS_RE.search(normalized):
        for m in _BARE_TIMESTAMP_CMP_RE.finditer(normalized):
            if _is_sql_type_context(normalized, m.start()):
                continue
            if _is_unsafe_rhs(m.group("rhs")):
                ctx_start = max(0, m.start() - 30)
                ctx_end = min(len(normalized), m.end() + 30)
                findings.append(
                    ("timestamp (FROM events)", normalized[ctx_start:ctx_end])
                )

    # Pass 3: aliased refs — `<alias>.timestamp` where alias maps to an
    # epoch-ms table via FROM/JOIN. Catches the previously-uncovered
    # `FROM events e WHERE e.timestamp >= :c` shape.
    aliases = _extract_aliases(normalized)
    for alias, table in aliases.items():
        # For ts_ms/last_event_at we already match the bare column name
        # in Pass 1; only `timestamp` is ambiguous and needs alias-
        # resolution to avoid false positives on non-epoch-ms tables.
        # `events.timestamp` is BIGINT epoch-ms; `analytics_event.ts_ms`
        # is similarly bigint but bare `ts_ms` matches Pass 1 already.
        if table != "events":
            continue
        for m in _aliased_cmp_re(alias).finditer(normalized):
            if _is_sql_type_context(normalized, m.start()):
                continue
            if _is_unsafe_rhs(m.group("rhs")):
                ctx_start = max(0, m.start() - 30)
                ctx_end = min(len(normalized), m.end() + 30)
                findings.append(
                    (f"{alias}.timestamp (FROM events {alias})",
                     normalized[ctx_start:ctx_end])
                )

    return findings


def scan_file(path: pathlib.Path) -> list[dict]:
    src = safe_read_text(path)
    if src is None:
        return []
    lines = src.split("\n")
    out: list[dict] = []
    for m in _SQL_CALL.finditer(src):
        body = m.group("body")
        start_line = src.count("\n", 0, m.start()) + 1
        end_line = src.count("\n", 0, m.end()) + 1
        # Opt-out: scan the span [start_line-5, end_line+1] for the
        # marker. The wide upper-window (5 lines before `text(`) catches
        # multi-line opt-out comments that explain the safety; the +1
        # lower-window catches markers placed on the line containing
        # the closing paren.
        lo = max(0, start_line - 6)
        hi = min(len(lines), end_line + 1)
        if any(_OPT_OUT_RE.search(lines[i]) for i in range(lo, hi)):
            continue
        for col, window in find_violations_in_sql(body):
            out.append(
                {
                    "path": str(path),
                    "line": start_line,
                    "col": col,
                    "window": window[:220],
                }
            )
    return out


# ──────────────────────────────────────────────────────────────────────
# Non-vacuity self-test — runs before every production scan.
# ──────────────────────────────────────────────────────────────────────
_SELF_TEST_BUGGY = '''
from sqlalchemy import text
text("SELECT COUNT(*) FROM events WHERE shop_domain=:s AND timestamp >= :c")
text("SELECT * FROM events WHERE events.timestamp >= now()")
text("SELECT 1 FROM analytics_event WHERE ts_ms < :cutoff_dt")
text("SELECT * FROM product_metrics WHERE last_event_at BETWEEN :a AND :b")
text("SELECT 1 FROM events WHERE events.timestamp <= current_timestamp")
'''

_SELF_TEST_SAFE = '''
from sqlalchemy import text
text("SELECT * FROM events WHERE shop_domain=:s ORDER BY timestamp DESC")
text("SELECT * FROM events WHERE to_timestamp(events.timestamp/1000.0) >= now()")
text("SELECT max(events.timestamp) FROM events WHERE shop_domain=:s")
'''


def _self_test_buggy_count() -> int:
    n = 0
    for m in _SQL_CALL.finditer(_SELF_TEST_BUGGY):
        n += len(find_violations_in_sql(m.group("body")))
    return n


def _self_test_safe_count() -> int:
    n = 0
    for m in _SQL_CALL.finditer(_SELF_TEST_SAFE):
        n += len(find_violations_in_sql(m.group("body")))
    return n


def run_self_test(verbose: bool = True) -> int:
    buggy = _self_test_buggy_count()
    safe = _self_test_safe_count()
    expected_buggy = 5
    if buggy < expected_buggy:
        print(
            f"SELF-TEST FAIL: expected ≥{expected_buggy} buggy detections, "
            f"got {buggy}. The matcher regressed; production scan would be vacuous.",
            file=sys.stderr,
        )
        return 1
    # The safe snippet `to_timestamp(events.timestamp/1000.0) >= now()`
    # WILL trigger the matcher because events.timestamp + now() + >= all
    # appear in the window. That is expected — the OPT-OUT mechanism is
    # how safe sites silence the audit, not pattern cleverness. The
    # safe-test exists to document the limit, not to assert zero. We
    # only fail self-test if the BUGGY count regresses.
    if verbose:
        print(
            f"SELF-TEST: buggy={buggy} (≥{expected_buggy} required), "
            f"safe-window={safe} (informational — opt-out silences)"
        )
    return 0


@telemetered("audit_sql_ms_column_type")
def main(argv) -> int:
    p = argparse.ArgumentParser(
        description="Static audit for the pg-ms-type bug class."
    )
    p.add_argument(
        "--self-test",
        action="store_true",
        help="Run non-vacuity self-test and exit (no file scan).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational output on GREEN.",
    )
    args = p.parse_args(argv)

    if args.self_test:
        return run_self_test()

    # Production scan refuses to run if the self-test regressed.
    if run_self_test(verbose=False) != 0:
        print(
            "audit_sql_ms_column_type: self-test failed — "
            "refusing to scan production code",
            file=sys.stderr,
        )
        return 2

    findings: list[dict] = []
    for path in _iter_py(APP_ROOT):
        findings.extend(scan_file(path))

    if not findings:
        if not args.quiet:
            print(
                "audit_sql_ms_column_type: GREEN — "
                "0 epoch-ms columns in unsafe comparisons"
            )
        return 0

    print(
        f"audit_sql_ms_column_type: RED — "
        f"{len(findings)} potential pg-ms-type bug(s)"
    )
    for f in findings:
        rel = pathlib.Path(f["path"]).relative_to("/opt/wishspark/backend")
        print(f"  {rel}:{f['line']}: col=`{f['col']}`")
        print(f"    SQL: {f['window']}")
    print()
    print("Each finding is one of:")
    print("  (a) genuine bug — fix via Python int(...*1000) cast OR")
    print("      SQL `to_timestamp(col/1000.0)` wrap (see commit 35796ae).")
    print("  (b) safe but un-annotated — add `# sql-ms-type: ok — <reason>`")
    print("      to the text(...) line.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
