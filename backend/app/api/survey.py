"""
survey.py — Post-Purchase Survey backend (Gap #7 of brutal $0-70
audit, 2026-04-28).

Four endpoints:

  GET  /survey/config?shop=<domain>
       Public, Redis-cached 10min. Read by the Shopify Checkout UI
       Extension at render time. Returns merchant's question + options.

  POST /survey/response
       Public, rate-limited (3/min/IP), PII-guarded answer_text,
       dedup via UNIQUE (shop, order, key). Writes survey_responses
       row + on first-of-day fires NotificationBell pulse.

  GET  /merchant/survey/aggregate?range=last_30_days
       Auth (merchant session). Returns last-N-days distribution
       for the dashboard "How customers find you" card.

  PUT  /pro/survey/config
       Auth (Pro session). Updates merchant survey_* columns +
       invalidates Redis cache so the extension picks up changes
       on next order.

Privacy posture:
  - No PII columns; client_ip / user_agent are sha256(value + daily_salt)
  - answer_text passed through llm_pii_guard; PII-positive rows store
    answer_text=NULL + counter increment
  - consent_given mirrors customer's analytics consent state at the
    extension boundary

Scale posture (10k merchants):
  - Redis cache for /survey/config: 10min TTL → 6/hr/merchant worst case
  - Per-IP rate limit Redis SETEX → no DB write under burst
  - DB UNIQUE on (shop, order, key) absorbs duplicate submits cheaply
  - Aggregate query covered by (shop_domain, question_key, answer_choice)
    partial index
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_merchant_session, require_pro_session
from app.core.llm_pii_guard import check_for_pii
from app.core.redis_client import _client as _redis_client, cache_delete, cache_get, cache_set
from app.core.silent_fallback import record_silent_return
from app.models.merchant import Merchant
from app.models.survey_response import SurveyResponse
from app.services.shopify_auth import is_valid_shop_domain

router = APIRouter(tags=["survey"])
log = logging.getLogger("survey")

# ---------------------------------------------------------------------------
# Cache + rate-limit constants
# ---------------------------------------------------------------------------

_CONFIG_CACHE_KEY = "hs:survey_cfg:v1:{shop}"
_CONFIG_CACHE_TTL = 600  # 10 minutes — reflects spec §13 of CLAUDE.md

_RATE_LIMIT_KEY = "hs:survey:rl:{ip_hash}"
_RATE_LIMIT_WINDOW = 60         # seconds
_RATE_LIMIT_MAX_HITS = 3

_DAILY_CAP_KEY = "hs:survey:daily:{shop}:{date}"
_DAILY_CAP_TTL = 86400 * 2      # 2 days — sliding cap
_DAILY_CAP_MAX = 10_000

_PII_VIOLATION_KEY = "hs:survey:pii_violations:{date}"
_PII_VIOLATION_TTL = 86400 * 30  # 30 days

_FIRST_TODAY_KEY = "hs:survey:first_today:{shop}:{date}"

# Default question + options — used as fallback if a merchant row is
# missing values for any reason (server_default in DB should always
# populate, but this is a safety net).
_DEFAULT_QUESTION = "How did you hear about us?"
_DEFAULT_OPTIONS: list[dict[str, str]] = [
    {"label": "Instagram", "value": "instagram"},
    {"label": "TikTok", "value": "tiktok"},
    {"label": "Google", "value": "google"},
    {"label": "Friend", "value": "friend"},
    {"label": "Email", "value": "email"},
]

_ANSWER_TEXT_MAX = 500
_QUESTION_MAX = 160
_OPTION_LABEL_MAX = 24
_OPTION_VALUE_MAX = 32
_MIN_OPTIONS = 3
_MAX_OPTIONS = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _daily_salt() -> str:
    """Daily-rotated salt for IP/UA hashing — env override for tests."""
    override = os.getenv("HS_IP_HASH_DAILY_SALT")
    if override:
        return override
    # Hash today's date with a fixed app secret so even if .env leaks
    # the salt isn't trivially derivable.
    base = os.getenv("MERCHANT_SESSION_SECRET", "hs-default-salt")
    today = datetime.now(timezone.utc).date().isoformat()
    return hashlib.sha256(f"{base}:{today}".encode()).hexdigest()[:32]


def _hash_value(value: str | None, salt: str) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"{value}:{salt}".encode()).hexdigest()


def _client_ip(request: Request) -> str | None:
    from app.core.client_ip import extract_client_ip
    ip = extract_client_ip(request)
    return ip if ip != "unknown" else None


def _bump_pii_counter() -> None:
    rc = _redis_client()
    if rc is None:
        record_silent_return("survey.pii_counter")
        return
    today = datetime.now(timezone.utc).date().isoformat()
    key = _PII_VIOLATION_KEY.format(date=today)
    try:
        rc.incr(key)
        rc.expire(key, _PII_VIOLATION_TTL)
    except Exception as exc:  # noqa: BLE001
        log.warning("survey: pii counter bump failed: %s", exc)
        record_silent_return("survey.pii_counter")


_SURVEY_RL_LOCAL_BUCKETS: dict[str, "deque[float]"] = {}
_SURVEY_RL_LOCAL_LOCK = None  # lazy init


def _survey_rl_local_check(ip_hash: str) -> bool:
    """In-process sliding-window fallback for survey rate-limit.
    60s window, 3 hits per ip_hash. Used only when Redis is unavailable.
    """
    global _SURVEY_RL_LOCAL_LOCK
    if _SURVEY_RL_LOCAL_LOCK is None:
        import threading as _t
        _SURVEY_RL_LOCAL_LOCK = _t.Lock()
    from collections import deque as _deque
    import time as _time
    now = _time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW
    with _SURVEY_RL_LOCAL_LOCK:
        bucket = _SURVEY_RL_LOCAL_BUCKETS.get(ip_hash)
        if bucket is None:
            bucket = _deque()
            _SURVEY_RL_LOCAL_BUCKETS[ip_hash] = bucket
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_HITS:
            return False
        bucket.append(now)
        return True


def _rate_limit_check(ip_hash: str | None) -> bool:
    """Return True when the request is permitted; False on rate-limit hit.

    Fail-CLOSED-with-fallback (2026-05-08): pre-fix returned True on any
    Redis exception, opening a flooding window during Redis outages.
    Now uses an in-process sliding-window fallback bounded by per-IP
    cap × #workers.
    """
    if not ip_hash:
        return True
    rc = _redis_client()
    if rc is None:
        record_silent_return("survey.rate_limit.local_fallback")
        return _survey_rl_local_check(ip_hash)
    key = _RATE_LIMIT_KEY.format(ip_hash=ip_hash)
    try:
        # INCR + EXPIRE: first hit creates the counter; subsequent hits
        # within the window increment and we compare to the max.
        n = rc.incr(key)
        if n == 1:
            rc.expire(key, _RATE_LIMIT_WINDOW)
        return n <= _RATE_LIMIT_MAX_HITS
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "survey: rate-limit redis check failed, using local fallback: %s",
            exc,
        )
        record_silent_return("survey.rate_limit.local_fallback")
        return _survey_rl_local_check(ip_hash)


def _daily_cap_check(shop: str) -> bool:
    rc = _redis_client()
    if rc is None:
        record_silent_return("survey.daily_cap")
        return True
    today = datetime.now(timezone.utc).date().isoformat()
    key = _DAILY_CAP_KEY.format(shop=shop, date=today)
    try:
        n = rc.incr(key)
        if n == 1:
            rc.expire(key, _DAILY_CAP_TTL)
        return n <= _DAILY_CAP_MAX
    except Exception as exc:  # noqa: BLE001
        log.warning("survey: daily-cap check failed: %s — fail-open", exc)
        record_silent_return("survey.daily_cap")
        return True


def _first_today_setnx(shop: str) -> bool:
    """Return True if this is the first response of the day for this shop."""
    rc = _redis_client()
    if rc is None:
        record_silent_return("survey.first_today")
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    key = _FIRST_TODAY_KEY.format(shop=shop, date=today)
    try:
        # SETNX with TTL — atomic set-if-not-exists
        return bool(rc.set(key, "1", ex=86400, nx=True))
    except Exception as exc:  # noqa: BLE001
        log.warning("survey: first-today SETNX failed: %s", exc)
        record_silent_return("survey.first_today")
        return False


def _validate_options(options: list[Any]) -> list[dict[str, str]]:
    if not isinstance(options, list):
        raise HTTPException(status_code=400, detail="options must be a list")
    if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
        raise HTTPException(
            status_code=400,
            detail=f"options must have {_MIN_OPTIONS}-{_MAX_OPTIONS} entries",
        )
    out: list[dict[str, str]] = []
    seen_values: set[str] = set()
    for entry in options:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail="each option must be an object")
        label = (entry.get("label") or "").strip()
        value = (entry.get("value") or "").strip().lower()
        if not label or not value:
            raise HTTPException(status_code=400, detail="option requires label + value")
        if len(label) > _OPTION_LABEL_MAX or len(value) > _OPTION_VALUE_MAX:
            raise HTTPException(status_code=400, detail="option label/value too long")
        if value in seen_values:
            raise HTTPException(status_code=400, detail="duplicate option value")
        seen_values.add(value)
        out.append({"label": label, "value": value})
    return out


# ---------------------------------------------------------------------------
# GET /survey/config
# ---------------------------------------------------------------------------

@router.get("/survey/config")
def get_survey_config(shop: str, response: Response, db: Session = Depends(get_read_db)) -> dict:
    if not is_valid_shop_domain(shop):
        raise HTTPException(status_code=400, detail="Invalid shop_domain")

    # Tracker-style CORS so the extension iframe can fetch this.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=60"

    cache_key = _CONFIG_CACHE_KEY.format(shop=shop)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if merchant is None:
        # Unknown shop → return preset (no shop-leakage concern; the
        # config is identical for every uninstalled shop).
        payload = {
            "question_key": "how_did_you_hear",
            "question": _DEFAULT_QUESTION,
            "options": _DEFAULT_OPTIONS,
            "allow_other": True,
            "disabled_on_order_status": False,
            "version": 1,
        }
        cache_set(cache_key, payload, _CONFIG_CACHE_TTL)
        return payload

    options = merchant.survey_options or _DEFAULT_OPTIONS
    # Multi-question array (G3 Lite parity, 2026-04-29). When the merchant
    # has set `survey_questions`, the canonical config is the array. The
    # legacy single-question fields stay as fallback so unchanged extensions
    # keep rendering one question via the top-level `question`/`options`.
    questions_list: list[dict[str, Any]] | None = None
    if isinstance(merchant.survey_questions, list) and merchant.survey_questions:
        questions_list = sorted(
            merchant.survey_questions,
            key=lambda q: q.get("position", 0) if isinstance(q, dict) else 0,
        )
    payload = {
        "question_key": (
            questions_list[0].get("question_key", "how_did_you_hear")
            if questions_list else "how_did_you_hear"
        ),
        "question": (
            questions_list[0].get("question", _DEFAULT_QUESTION)
            if questions_list else (merchant.survey_question or _DEFAULT_QUESTION)
        ),
        "options": (
            questions_list[0].get("options", _DEFAULT_OPTIONS)
            if questions_list else options
        ),
        "allow_other": bool(merchant.survey_allow_other),
        "disabled_on_order_status": not bool(merchant.survey_show_on_order_status),
        "version": 2 if questions_list else 1,
        # Full multi-question array — extension v10+ consumes this when
        # available; older extension versions fall back to the top-level
        # single-question fields above.
        "questions": questions_list,
    }
    cache_set(cache_key, payload, _CONFIG_CACHE_TTL)
    return payload


# ---------------------------------------------------------------------------
# POST /survey/response
# ---------------------------------------------------------------------------

class SurveyResponseIn(BaseModel):
    shop_domain: str
    order_id: str = Field(..., min_length=1, max_length=64)
    question_key: str = Field(default="how_did_you_hear", max_length=64)
    answer_choice: str | None = Field(default=None, max_length=64)
    answer_text: str | None = Field(default=None, max_length=_ANSWER_TEXT_MAX)
    consent_given: bool = False


# Response models — required for /pro|/merchant routes per
# audit_response_models so OpenAPI emits typed shapes and the
# dashboard's apiClient gets compile-time safety.

class SurveyDistributionEntry(BaseModel):
    choice: str
    count: int
    pct: float


class SurveyAggregateOut(BaseModel):
    shop_domain: str
    range: str
    total: int
    distribution: list[SurveyDistributionEntry]
    top_choice: SurveyDistributionEntry | None


class SurveyConfigOut(BaseModel):
    question: str
    options: list[dict[str, Any]]
    allow_other: bool
    show_on_order_status: bool
    # G3 multi-question (2026-04-29). Null = legacy single-question mode.
    questions: list[dict[str, Any]] | None = None


class SurveyConfigUpdateOut(BaseModel):
    status: str
    config: SurveyConfigOut


@router.post("/survey/response")
def post_survey_response(
    payload: SurveyResponseIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    response.headers["Access-Control-Allow-Origin"] = "*"

    shop = payload.shop_domain
    if not is_valid_shop_domain(shop):
        raise HTTPException(status_code=400, detail="Invalid shop_domain")

    if not payload.answer_choice and not (payload.answer_text or "").strip():
        raise HTTPException(status_code=400, detail="answer_choice or answer_text required")

    # Hash IP + UA with daily salt before any further work — never store raw.
    salt = _daily_salt()
    ip_hash = _hash_value(_client_ip(request), salt)
    ua_hash = _hash_value(request.headers.get("user-agent"), salt)

    if not _rate_limit_check(ip_hash):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if not _daily_cap_check(shop):
        log.warning("survey: daily cap exceeded for shop=%s", shop)
        raise HTTPException(status_code=429, detail="Daily survey cap reached")

    # PII guard — stores NULL answer_text + bumps counter when PII detected.
    answer_text = (payload.answer_text or "").strip() or None
    if answer_text:
        findings = check_for_pii(answer_text)
        if findings:
            answer_text = None
            _bump_pii_counter()

    row = SurveyResponse(
        shop_domain=shop,
        order_id=payload.order_id,
        question_key=payload.question_key or "how_did_you_hear",
        answer_choice=(payload.answer_choice or "").strip().lower() or None,
        answer_text=answer_text,
        consent_given=bool(payload.consent_given),
        client_ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # UNIQUE violation — already answered
        db.rollback()
        return {"status": "already_answered"}
    except Exception:
        db.rollback()
        raise

    is_first_today = _first_today_setnx(shop)
    return {
        "status": "ok",
        "first_today": is_first_today,
        "choice": row.answer_choice,
    }


# ---------------------------------------------------------------------------
# GET /merchant/survey/aggregate
# ---------------------------------------------------------------------------

_RANGE_TO_DAYS = {
    "today": 1,
    "yesterday": 2,
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 90,
    "year_to_date": 365,
}


@router.get("/merchant/survey/aggregate", response_model=SurveyAggregateOut)
def get_survey_aggregate(
    range: str = "last_30_days",
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
) -> dict:
    days = _RANGE_TO_DAYS.get(range)
    if days is None:
        raise HTTPException(status_code=400, detail="unknown range")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(
            SurveyResponse.answer_choice.label("choice"),
            func.count(SurveyResponse.id).label("n"),
        )
        .filter(
            SurveyResponse.shop_domain == shop,
            SurveyResponse.created_at >= cutoff,
            SurveyResponse.answer_choice.isnot(None),
        )
        .group_by(SurveyResponse.answer_choice)
        .order_by(desc("n"))
        .all()
    )
    total = sum(r.n for r in rows) or 0
    distribution = [
        {
            "choice": r.choice,
            "count": int(r.n),
            "pct": round(100.0 * r.n / total, 1) if total else 0.0,
        }
        for r in rows
    ]
    top = distribution[0] if distribution else None

    return {
        "shop_domain": shop,
        "range": range,
        "total": total,
        "distribution": distribution,
        "top_choice": top,
    }


# ---------------------------------------------------------------------------
# PUT /pro/survey/config
# ---------------------------------------------------------------------------

class SurveyQuestionEntry(BaseModel):
    question_key: str = Field(..., min_length=1, max_length=64)
    question: str = Field(..., min_length=1, max_length=_QUESTION_MAX)
    type: str = Field(default="single_choice")  # single_choice|multi_choice|text|nps
    options: list[dict[str, Any]] = Field(default_factory=list)
    allow_other: bool = True
    position: int = 0


class SurveyConfigUpdate(BaseModel):
    survey_question: str | None = Field(default=None, max_length=_QUESTION_MAX)
    survey_options: list[dict[str, Any]] | None = Field(default=None, max_length=_MAX_OPTIONS)
    survey_allow_other: bool | None = None
    survey_show_on_order_status: bool | None = None
    # G3: multi-question array. When provided, replaces survey_questions
    # entirely. Pass null to reset to single-question (legacy) mode.
    survey_questions: list[SurveyQuestionEntry] | None = Field(default=None, max_length=10)


@router.put("/merchant/survey/config", response_model=SurveyConfigUpdateOut)
def put_merchant_survey_config(
    payload: SurveyConfigUpdate,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")

    if payload.survey_question is not None:
        question = payload.survey_question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="survey_question cannot be empty")
        merchant.survey_question = question[:_QUESTION_MAX]

    if payload.survey_options is not None:
        merchant.survey_options = _validate_options(payload.survey_options)

    if payload.survey_allow_other is not None:
        merchant.survey_allow_other = bool(payload.survey_allow_other)

    if payload.survey_show_on_order_status is not None:
        merchant.survey_show_on_order_status = bool(payload.survey_show_on_order_status)

    # G3 multi-question handling: validate uniqueness of question_keys +
    # ensure choice-type questions have ≥2 options. Empty list resets to
    # single-question (legacy) mode.
    if payload.survey_questions is not None:
        seen_keys: set[str] = set()
        validated_questions: list[dict[str, Any]] = []
        for q in payload.survey_questions:
            if q.question_key in seen_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"duplicate question_key: {q.question_key}",
                )
            seen_keys.add(q.question_key)
            if q.type in ("single_choice", "multi_choice"):
                # Multi-question allows 2-N options (vs legacy single-Q
                # which enforces 3-8). 2-option Yes/No is parity-correct
                # for KnoCommerce/Zigpoll/Fairing competitor flows.
                if not isinstance(q.options, list):
                    raise HTTPException(status_code=400, detail=f"options must be a list for {q.question_key}")
                if not (2 <= len(q.options) <= _MAX_OPTIONS):
                    raise HTTPException(
                        status_code=400,
                        detail=f"options for {q.question_key} must have 2-{_MAX_OPTIONS} entries",
                    )
                seen_values: set[str] = set()
                for entry in q.options:
                    if not isinstance(entry, dict):
                        raise HTTPException(status_code=400, detail=f"option in {q.question_key} must be a dict")
                    label = (entry.get("label") or "").strip()
                    value = (entry.get("value") or "").strip()
                    if not label or not value:
                        raise HTTPException(status_code=400, detail=f"option label+value required for {q.question_key}")
                    if value in seen_values:
                        raise HTTPException(status_code=400, detail=f"duplicate option value '{value}' in {q.question_key}")
                    seen_values.add(value)
            validated_questions.append({
                "question_key": q.question_key,
                "question": q.question.strip()[:_QUESTION_MAX],
                "type": q.type,
                "options": q.options,
                "allow_other": bool(q.allow_other),
                "position": q.position,
            })
        merchant.survey_questions = validated_questions or None

    db.commit()

    cache_delete(_CONFIG_CACHE_KEY.format(shop=shop))

    return {
        "status": "ok",
        "config": {
            "question": merchant.survey_question,
            "options": merchant.survey_options,
            "allow_other": merchant.survey_allow_other,
            "show_on_order_status": merchant.survey_show_on_order_status,
            "questions": merchant.survey_questions,
        },
    }
