"""SurveyResponse — Gap #7 post-purchase attribution survey.

One row per (shop, order, question_key). Written by the Shopify
Checkout UI Extension via POST /survey/response. Read by the
dashboard "How customers find you" card via the merchant aggregate
endpoint.

Privacy posture: no PII columns. `client_ip_hash` and
`user_agent_hash` are sha256 digests (64 hex chars) — raw values
never stored. `answer_text` is filtered through `llm_pii_guard` at
the API boundary; PII-positive rows land with `answer_text=NULL`
and a counter increment.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    text,
)

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurveyResponse(Base):
    __tablename__ = "survey_responses"
    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "order_id",
            "question_key",
            name="uq_survey_responses_shop_order_key",
        ),
        Index(
            "idx_survey_responses_shop_created",
            "shop_domain",
            text("created_at DESC"),
        ),
        Index(
            "idx_survey_responses_shop_question_choice",
            "shop_domain",
            "question_key",
            "answer_choice",
            postgresql_where=text("answer_choice IS NOT NULL"),
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Indexes are declared via Alembic on (shop_domain, created_at DESC)
    # and the partial choice index — no single-column index here.
    shop_domain = Column(String, nullable=False)
    order_id = Column(String, nullable=False)
    question_key = Column(
        String(64),
        nullable=False,
        default="how_did_you_hear",
        server_default=text("'how_did_you_hear'"),
    )
    answer_choice = Column(String(64), nullable=True)
    answer_text = Column(String(500), nullable=True)
    consent_given = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    client_ip_hash = Column(String(64), nullable=True)
    user_agent_hash = Column(String(64), nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=_now_utc,
        server_default=text("now()"),
    )
