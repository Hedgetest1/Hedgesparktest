"""
_types.py — shared Pydantic response shapes for API routes.

Tier 3.1 consolidation: most DELETE / POST action endpoints return a
thin status object (`{"ok": True}`, `{"deleted": True, "id": X}`,
etc). Rather than author a bespoke model per route, they share the
shapes below. GET endpoints that return actual data still author
their own typed models.
"""
from __future__ import annotations

from pydantic import BaseModel


class OkResponse(BaseModel):
    """Generic status-ok response for POST/PATCH/DELETE actions.

    Any of the common success flags may be set depending on the
    endpoint. All optional so the shape accommodates every status
    dict the service layer returns.
    """
    ok: bool | None = None
    deleted: bool | None = None
    removed: bool | None = None
    updated: bool | None = None
    created: bool | None = None
    status: str | None = None
    id: str | int | None = None
    metric: str | None = None


class MessageResponse(BaseModel):
    """Endpoint that returns a human-readable message."""
    message: str
    detail: str | None = None
