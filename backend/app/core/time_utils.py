"""
UTC clock helpers.

`datetime.utcnow()` emits a `DeprecationWarning` on Python 3.12+ and is
scheduled for removal. The modern replacement is `datetime.now(tz)`,
which returns a **timezone-aware** object. Our ORM model columns are
declared as `DateTime` (naive `TIMESTAMP WITHOUT TIME ZONE` in Postgres)
and consumer code across workers/services routinely subtracts, compares,
and serializes these values against other naive datetimes — mixing
aware+naive raises `TypeError: can't subtract offset-naive and
offset-aware datetimes` and the failures surface at runtime in whichever
code path the naive side lands in.

`utc_now_naive()` is a drop-in replacement for `datetime.utcnow` that
- emits no deprecation warning on 3.12/3.13/3.14,
- returns a naive UTC datetime identical in value to `utcnow()`,
- is safe as a SQLAlchemy `Column(..., default=...)` callable,
- is evaluated per-row (never at class-load time).

Prefer `utc_now_aware()` for NEW business-logic call sites that compare
or persist against timezone-aware columns. Do not mix the two on the
same column without explicit awareness.
"""

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    """Return a naive UTC datetime — drop-in replacement for
    `datetime.utcnow()`. Use as a SQLAlchemy column default wherever
    the column is `DateTime` (naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now_aware() -> datetime:
    """Return a timezone-aware UTC datetime. Prefer this for new code
    that operates on aware datetimes end-to-end."""
    return datetime.now(timezone.utc)
