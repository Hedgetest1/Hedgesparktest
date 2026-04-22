"""
ops_email_preview.py — operator-only email-content preview endpoints.

Per founder 2026-04-20: "tutto dentro a pipeline debug in modo che
gli invii funzionino e che il contenuto delle mail sia davvero
perfetto". These endpoints let an operator preview any scheduled
merchant email for a given shop without actually sending it — so
content QA is a query, not a cron wait.

Endpoints:
  GET /ops/email/preview?shop=X&email_type=lite_morning_digest
      → returns the rendered HTML directly (Content-Type text/html)
        for visual inspection in a browser.
  GET /ops/email/preview?shop=X&email_type=lite_morning_digest&format=json
      → returns {subject, html, plain_text} JSON for scripted QA.

Gate: require_operator (X-API-Key). Never exposed to merchants.
include_in_schema=False so these don't surface in the OpenAPI / types.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_operator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ops/email", tags=["ops"])


def _preview_lite_morning_digest(db: Session, shop: str):
    from app.services.lite_morning_digest import _build_email
    from app.services.brief_engine import generate_brief
    brief = generate_brief(db, shop)
    return _build_email(shop, brief, db)


def _preview_weekly_digest(db: Session, shop: str):
    from app.services.weekly_digest import assemble_digest
    from app.services.digest_formatter import format_digest
    from app.models.merchant import Merchant
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    plan = (m.plan if m else None) or "lite"
    digest = assemble_digest(db, shop, merchant_plan=plan)
    if not digest:
        return (
            f"Weekly digest — {shop}",
            "<h3>No data yet for weekly digest.</h3>",
            "No data yet for weekly digest.",
        )
    html, plain = format_digest(digest)
    shop_name = shop.replace(".myshopify.com", "").replace("-", " ").title()
    return f"Your Weekly Intelligence — {shop_name}", html, plain


_PREVIEWS = {
    "lite_morning_digest": _preview_lite_morning_digest,
    "weekly_digest": _preview_weekly_digest,
}


@router.get("/preview", include_in_schema=False)
def preview_email(
    shop: str = Query(..., description="shop_domain, e.g. foo.myshopify.com"),
    email_type: str = Query(..., description="one of: lite_morning_digest | weekly_digest"),
    format: str = Query("html", description="html | json"),
    _op: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Operator preview — renders the email for the given shop and
    email_type WITHOUT sending. Returns HTML by default so the
    operator can open the URL in a browser. `format=json` returns
    subject + html + plain_text as JSON for scripted QA.

    Supported email_types (all scheduled merchant-facing emails we
    build ourselves):
      - lite_morning_digest (daily Lite)
      - weekly_digest (Monday Pro)

    Transactional lifecycle emails (welcome, setup_incomplete,
    trigger_*, reengagement, etc.) are rendered by `render_email()`
    in email_templates.py with context that varies per-intent; those
    are previewable via the individual producer smoke tests, not
    through this endpoint.
    """
    builder = _PREVIEWS.get(email_type)
    if not builder:
        raise HTTPException(
            status_code=400,
            detail=f"unknown email_type '{email_type}'. Supported: {sorted(_PREVIEWS)}",
        )
    try:
        subject, html, plain = builder(db, shop)
    except Exception as exc:
        log.warning("ops_email_preview: %s render failed for %s: %s", email_type, shop, exc)
        raise HTTPException(status_code=500, detail=f"render failed: {type(exc).__name__}: {exc}")

    if format == "json":
        return {"subject": subject, "html": html, "plain_text": plain, "email_type": email_type, "shop": shop}

    # Default: render the HTML inline so operator can eyeball it.
    banner = (
        '<div style="position:sticky;top:0;z-index:100;background:#fbbf24;color:#0a0a14;'
        'padding:10px 16px;font-family:sans-serif;font-size:12px;font-weight:700;text-align:center;">'
        f'EMAIL PREVIEW — {email_type} for {shop} · Subject: {subject}'
        '</div>'
    )
    return HTMLResponse(banner + html, status_code=200)
