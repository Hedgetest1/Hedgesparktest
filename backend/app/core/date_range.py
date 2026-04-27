"""
date_range.py — Shared date-range query parser for analytics endpoints.

Born 2026-04-27 from Phase 3B (brutal Lite vs $0-70 audit closure).
Every analytics endpoint that previously took a `days` window now also
accepts `start_date` / `end_date` / `compare_start` / `compare_end` so
the global frontend DateRangePicker can drive arbitrary ranges across
all 19 Lite tiles and Pro consumers.

Single source of truth: this module's `DateRangeQuery` Pydantic model
+ `get_date_range` FastAPI dependency. Each endpoint imports them via
`Depends(get_date_range)`; the conditional `if range_q.is_explicit()`
branch swaps the legacy `days` window for the explicit range when the
client provides one.

Backward compat: when neither start_date nor end_date is set, the
legacy behavior (compute from `days` query param) is preserved
verbatim. No breakage for any existing dashboard call or integration.

Validation:
- Both `start_date` AND `end_date` required (or neither)
- `end_date >= start_date`
- `end_date <= today` (in shop tz — the dependency takes a shop param)
- Span `<= 730 days` (matches the longest existing window in the app)
- Comparison params: same rules; comparison range can pre-date the
  primary range arbitrarily.

The shop_domain + timezone dependency is intentionally NOT injected
here — that would couple every endpoint's signature to two `Depends`.
Instead, each endpoint resolves shop tz inline (cheap Redis-cached
call) and calls `range_q.resolve(today_in_shop_tz)` to clamp the
end_date if it's drifted past midnight.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field


# Maximum allowed span (matches the longest legacy window in the codebase
# — repeat-cadence accepts up to 730d). Going beyond is intentionally
# blocked: 2-year history is enough for personal-cadence + retention math,
# and unbounded ranges would let a malicious client make a heavy CTE walk
# 10 years of orders (PERCENTILE_CONT × 10M rows = hostile).
_MAX_SPAN_DAYS = 730


class DateRangeQuery(BaseModel):
    """Resolved date-range query. Used by analytics endpoints to swap
    between legacy `days` window and explicit start/end range."""

    start_date: Optional[date] = Field(default=None)
    end_date: Optional[date] = Field(default=None)
    compare_start: Optional[date] = Field(default=None)
    compare_end: Optional[date] = Field(default=None)

    def is_explicit(self) -> bool:
        """True when caller provided an explicit range (both bounds)."""
        return self.start_date is not None and self.end_date is not None

    def has_compare(self) -> bool:
        """True when caller provided a comparison range."""
        return self.compare_start is not None and self.compare_end is not None

    def span_days(self) -> int:
        """Number of days in the primary range (inclusive). 0 when not explicit."""
        if not self.is_explicit():
            return 0
        # +1 because both ends are inclusive (e.g., today→today is 1 day)
        return (self.end_date - self.start_date).days + 1  # type: ignore[operator]

    def cache_key_segment(self) -> str:
        """Stable string for cache key composition. Empty when not explicit
        so cache key shape stays backward-compatible with legacy callers."""
        if not self.is_explicit():
            return ""
        seg = f":r={self.start_date}_{self.end_date}"  # noqa: E501
        if self.has_compare():
            seg += f":c={self.compare_start}_{self.compare_end}"
        return seg


def _validate_range(start: Optional[date], end: Optional[date], label: str) -> None:
    """Validate one of (primary, comparison) — both bounds required or
    neither, end >= start, end <= today (UTC sanity), span <= 730d."""
    if start is None and end is None:
        return
    if start is None or end is None:
        raise HTTPException(
            status_code=400,
            detail=f"{label}_start and {label}_end must both be provided",
        )
    if end < start:
        raise HTTPException(
            status_code=400,
            detail=f"{label}_end ({end}) must be >= {label}_start ({start})",
        )
    # Use UTC today as a sanity ceiling. Per-shop tz fine-tuning happens
    # at the endpoint via range_q.resolve_for_shop_tz(today_in_shop_tz).
    from datetime import datetime, timezone
    today_utc = datetime.now(timezone.utc).date()
    # Allow +1 day slack so a shop 12h ahead of UTC isn't blocked
    if end > today_utc + timedelta(days=1):
        raise HTTPException(
            status_code=400,
            detail=f"{label}_end ({end}) cannot be in the future",
        )
    span = (end - start).days + 1
    if span > _MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{label} span ({span} days) exceeds maximum {_MAX_SPAN_DAYS} days. "
                f"Use a tighter range."
            ),
        )


def get_date_range(
    start_date: Optional[date] = Query(
        None,
        description="Inclusive start date (YYYY-MM-DD). Both start + end required for explicit range.",
    ),
    end_date: Optional[date] = Query(
        None,
        description="Inclusive end date (YYYY-MM-DD). Both start + end required for explicit range.",
    ),
    compare_start: Optional[date] = Query(
        None,
        description="Comparison range start (optional). Both compare bounds required if used.",
    ),
    compare_end: Optional[date] = Query(
        None,
        description="Comparison range end (optional). Both compare bounds required if used.",
    ),
) -> DateRangeQuery:
    """FastAPI dependency: parses and validates the global date-range
    query params. Returns a DateRangeQuery the endpoint consumes via
    `range_q.is_explicit()` + start_date/end_date access."""
    _validate_range(start_date, end_date, "date")
    _validate_range(compare_start, compare_end, "compare")
    return DateRangeQuery(
        start_date=start_date,
        end_date=end_date,
        compare_start=compare_start,
        compare_end=compare_end,
    )


def resolve_window_days(
    range_q: DateRangeQuery, *, fallback_days: int
) -> tuple[date, date, int]:
    """Compute (start, end, days) for an analytics query.

    When the client passed an explicit range → use it verbatim.
    When the client didn't → fall back to the legacy `days` window
    ending today (UTC). The third tuple element is the day count for
    cache-key parity with legacy `days` cache keys.
    """
    if range_q.is_explicit():
        # Type narrowing: is_explicit() guarantees both are non-None.
        assert range_q.start_date is not None and range_q.end_date is not None
        return range_q.start_date, range_q.end_date, range_q.span_days()

    from datetime import datetime, timezone
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=fallback_days - 1)
    return start, end, fallback_days
