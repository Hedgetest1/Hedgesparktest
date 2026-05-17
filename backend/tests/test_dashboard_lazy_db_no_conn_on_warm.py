"""§19.1 reproduction — dashboard handlers must hold ZERO DB
connections on a warm cache hit.

Born 2026-05-15. Closes the /dashboard/overview pool-timeout cliff
(c≈64 → 100% errors @ exactly pool_timeout=30s). Root cause: the
handlers declared `db: Session = Depends(get_read_db)`. FastAPI
resolves Depends BEFORE the handler body, so the session — and its
pooled PgBouncer connection — was pinned for the ENTIRE request even
on a warm cache hit that issues zero queries. At 10k that wedged the
PgBouncer global 100-conn ceiling.

The structural fix: NO `Depends(get_read_db)` on the handlers; the
read session is opened LAZILY inside the cold-build closure only.

These tests pin the contract that would have caught the regression:
  1. cache-warm fall-through → handler returns cached, ReadSession
     is NEVER constructed (zero DB connection on the warm path).
  2. cache-cold → ReadSession IS constructed exactly once and closed
     (the build legitimately needs a connection).
A future re-introduction of `Depends(get_read_db)` would make test 1
fail because the dependency constructs a session before the body runs.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.merchant_session import SESSION_COOKIE_NAME, create_session_token
from app.models.merchant import Merchant

_SHOP = "test-shop-a.myshopify.com"
_MSV_KEY = f"hs:auth:msv:v1:{_SHOP}"
_DASH_KEY = f"hs:dash:{_SHOP}:lite"
_CACHED = {"summary": {"sessions": 7}, "_sentinel": "warm-hit-no-db-conn"}


def _redis():
    from app.core.redis_client import _client
    return _client()


def _prime_dashboard_cache():
    from app.core.redis_client import cache_set, TTL_DASHBOARD
    cache_set(_DASH_KEY, _CACHED, TTL_DASHBOARD)


def _clear_caches():
    rc = _redis()
    assert rc is not None, "test redis must be available"
    rc.delete(_MSV_KEY)
    rc.delete(_DASH_KEY)


@pytest.fixture()
def _merchant(db):
    m = Merchant(
        shop_domain=_SHOP, plan="scale", billing_active=True,
        install_status="active", session_version=0,
        access_token="x", contact_email="o@test-shop-a.com",
    )
    db.add(m)
    db.flush()
    return m


def test_warm_cache_hit_opens_no_db_session(client, _merchant):
    """msv cache COLD (→ middleware fast-path falls through to the
    handler) but dashboard cache WARM (→ handler's cache_get hits and
    returns before building). The lazy ReadSession must NEVER be
    constructed: a warm request holds ZERO DB connections."""
    _clear_caches()
    _prime_dashboard_cache()  # dashboard warm; msv intentionally cold
    token = create_session_token(_SHOP, 0)

    with patch("app.core.database.ReadSession") as read_session:
        resp = client.get(
            "/dashboard/overview", cookies={SESSION_COOKIE_NAME: token}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == _CACHED
        read_session.assert_not_called(), (
            "REGRESSION: a warm /dashboard/overview hit constructed a "
            "ReadSession — a pooled DB connection is being pinned on the "
            "warm path (the c=64 pool-timeout cliff class). The handler "
            "must NOT declare Depends(get_read_db); open the session "
            "lazily inside the cold-build closure only."
        )


def test_cold_cache_opens_exactly_one_db_session_and_closes_it(client, _merchant):
    """Cache COLD → the lazy build path runs: ReadSession constructed
    exactly once and .close() called (no connection leak)."""
    _clear_caches()  # dashboard + msv both cold
    token = create_session_token(_SHOP, 0)

    fake_db = MagicMock(name="ReadSession()")
    with patch("app.core.database.ReadSession", return_value=fake_db) as rs, \
         patch(
             "app.api.dashboard.build_lite_dashboard_overview",
             return_value={"computed": True},
         ) as build:
        resp = client.get(
            "/dashboard/overview", cookies={SESSION_COOKIE_NAME: token}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"computed": True}
        rs.assert_called_once()                       # lazy session opened
        build.assert_called_once_with(fake_db, _SHOP)  # builder got it
        fake_db.close.assert_called_once()            # and it was closed


def test_build_releases_conn_before_the_non_sql_cache_set_tail():
    """honest-residual #7 contract (MEASURED 2026-05-17: ~87% of the
    cold-build conn-held window is the non-SQL Python + Redis cache_set
    tail). The build MUST close the pooled DB session the moment the
    last query is done, BEFORE the cache_set tail — same shape as the
    already-correct get_dashboard_intelligence. A regression that moves
    cache_set before db.close (or drops db.close) fails here."""
    from unittest.mock import MagicMock, patch
    import app.api.dashboard as d

    order: list[str] = []
    db = MagicMock(name="ReadSession()")
    db.close.side_effect = lambda: order.append("db.close")

    def _cs(*a, **k):
        order.append("cache_set")

    with patch("app.services.revenue_metrics.get_shop_currency",
               return_value="EUR"), \
         patch("app.services.revenue_metrics.get_shop_aov",
               return_value=42.0), \
         patch.object(d, "_build_summary", return_value={}), \
         patch.object(d, "_build_top_products", return_value=[]), \
         patch.object(d, "_build_revenue_window_tease", return_value=None), \
         patch.object(d, "_get_calibration_summary", return_value={}), \
         patch("app.core.redis_client.cache_get", return_value=None), \
         patch("app.core.redis_client.cache_set", _cs), \
         patch("app.services.store_insight_engine.generate_store_brief",
               return_value=None):
        d.build_lite_dashboard_overview(db, "x.myshopify.com")

    assert "db.close" in order, "build must release the pooled conn"
    assert order.index("db.close") < order.index("cache_set"), (
        f"#7 violated: conn held across the non-SQL cache_set tail "
        f"(order={order}) — the all-cold-storm pool-pressure regression"
    )
