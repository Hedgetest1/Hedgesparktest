"""
Phase Ω ecosystem #2 — universal Ads connector framework tests.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models.ad_spend import AdConnection, AdSpendDaily
from app.services.ads_connectors import (
    NormalizedSpendRow,
    MetaAdsConnector,
    GoogleAdsConnector,
    TikTokAdsConnector,
    supported_networks,
    get_connector,
    connect_network,
    list_connections,
    disconnect_network,
    upsert_rows,
    ingest_for_shop,
    sync_shop_all,
    get_spend_summary,
)


SHOP = "ads-test.myshopify.com"


def test_supported_networks_has_three():
    nets = supported_networks()
    assert "meta" in nets
    assert "google" in nets
    assert "tiktok" in nets


def test_get_connector_returns_correct_class():
    assert isinstance(get_connector("meta", SHOP), MetaAdsConnector)
    assert isinstance(get_connector("google", SHOP), GoogleAdsConnector)
    assert isinstance(get_connector("tiktok", SHOP), TikTokAdsConnector)


def test_get_connector_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        get_connector("nonexistent", SHOP)


def test_stub_connectors_return_empty_without_credentials():
    assert list(MetaAdsConnector(SHOP).fetch_daily_spend(date.today(), date.today())) == []
    assert list(GoogleAdsConnector(SHOP).fetch_daily_spend(date.today(), date.today())) == []
    assert list(TikTokAdsConnector(SHOP).fetch_daily_spend(date.today(), date.today())) == []


def test_normalized_spend_row_to_dict():
    r = NormalizedSpendRow(
        shop_domain=SHOP, date=date(2026, 4, 13), network="meta",
        campaign_id="c1", campaign_name="Spring Sale", spend_eur=120.0,
        impressions=10000, clicks=200, conversions=8, revenue_attributed_eur=480.0,
    )
    d = r.to_dict()
    assert d["network"] == "meta"
    assert d["spend_eur"] == 120.0
    assert d["date"] == "2026-04-13"


def test_connect_network(db):
    c = connect_network(db, SHOP, "meta", "tok_abc", account_id="act_1", account_name="My Account")
    assert c.id is not None
    assert c.network == "meta"
    assert c.status == "connected"
    assert c.account_name == "My Account"


def test_connect_network_idempotent_update(db):
    c1 = connect_network(db, SHOP, "meta", "tok_old")
    c2 = connect_network(db, SHOP, "meta", "tok_new", account_id="act_2")
    assert c1.id == c2.id
    assert c2.credential_ref == "tok_new"
    assert c2.account_id == "act_2"


def test_list_connections_filters_by_shop(db):
    connect_network(db, SHOP, "meta", "tok")
    connect_network(db, SHOP, "google", "tok")
    connect_network(db, "other.myshopify.com", "tiktok", "tok")
    rows = list_connections(db, SHOP)
    assert len(rows) == 2
    assert {r.network for r in rows} == {"meta", "google"}


def test_disconnect_network(db):
    connect_network(db, SHOP, "meta", "tok")
    ok = disconnect_network(db, SHOP, "meta")
    assert ok is True
    rows = list_connections(db, SHOP)
    assert rows[0].status == "disconnected"
    assert rows[0].credential_ref is None


def test_disconnect_unknown_returns_false(db):
    ok = disconnect_network(db, SHOP, "google")
    assert ok is False


def test_upsert_rows_insert_then_update(db):
    rows = [NormalizedSpendRow(
        shop_domain=SHOP, date=date(2026, 4, 10), network="meta",
        campaign_id="c1", campaign_name="A", spend_eur=100.0,
        impressions=1000, clicks=20, conversions=2, revenue_attributed_eur=200.0,
    )]
    ins, upd = upsert_rows(db, rows)
    assert ins == 1
    assert upd == 0

    # Update — same key, new spend
    rows[0].spend_eur = 150.0
    ins2, upd2 = upsert_rows(db, rows)
    assert ins2 == 0
    assert upd2 == 1

    db_row = db.query(AdSpendDaily).filter_by(shop_domain=SHOP).one()
    assert db_row.spend_eur == 150.0


def test_ingest_for_shop_marks_connection_synced(db):
    connect_network(db, SHOP, "meta", "tok")
    rows = [NormalizedSpendRow(
        shop_domain=SHOP, date=date(2026, 4, 10), network="meta",
        campaign_id="c1", campaign_name="A", spend_eur=50.0,
    )]
    res = ingest_for_shop(db, SHOP, "meta", rows)
    assert res.rows_seen == 1
    assert res.rows_inserted == 1
    assert res.error is None
    conn = db.query(AdConnection).filter_by(shop_domain=SHOP).one()
    assert conn.last_synced_at is not None


def test_sync_shop_all_no_connections(db):
    out = sync_shop_all(db, "no-conn.myshopify.com")
    assert out == []


def test_sync_shop_all_with_connection(db):
    connect_network(db, SHOP, "meta", "tok")
    out = sync_shop_all(db, SHOP)
    assert len(out) == 1
    assert out[0].network == "meta"
    # Stub connector returns empty list
    assert out[0].rows_seen == 0


def test_spend_summary_empty(db):
    out = get_spend_summary(db, "empty.myshopify.com")
    assert out["total_spend_eur"] == 0
    assert out["by_network"] == {}
    assert out["blended_roas"] is None


def test_spend_summary_with_data(db):
    today = date.today()
    rows = [
        NormalizedSpendRow(SHOP, today, "meta", "c1", "Sale", 100.0, 1000, 20, 2, 250.0),
        NormalizedSpendRow(SHOP, today, "meta", "c2", "Brand", 50.0, 500, 10, 1, 80.0),
        NormalizedSpendRow(SHOP, today, "google", "g1", "Search", 200.0, 2000, 40, 5, 600.0),
    ]
    upsert_rows(db, rows)
    summary = get_spend_summary(db, SHOP)
    assert summary["total_spend_eur"] == 350.0
    assert summary["total_revenue_eur"] == 930.0
    assert summary["blended_roas"] == round(930 / 350, 2)
    assert "meta" in summary["by_network"]
    assert summary["by_network"]["meta"]["spend_eur"] == 150.0
    assert summary["by_network"]["google"]["roas"] == 3.0


# ---------------------------------------------------------------------------
# API smoke
# ---------------------------------------------------------------------------


def test_api_networks_list(client, auth_a):
    r = client.get("/pro/ads/networks", cookies=auth_a)
    assert r.status_code == 200
    nets = r.json()["networks"]
    assert "meta" in nets


def test_api_connect_and_list(client, auth_a):
    r = client.post(
        "/pro/ads/connect",
        json={"network": "meta", "credential_ref": "tok_xyz", "account_id": "act_1"},
        cookies=auth_a,
    )
    assert r.status_code == 200
    assert r.json()["network"] == "meta"
    r2 = client.get("/pro/ads/connections", cookies=auth_a)
    assert r2.status_code == 200
    assert len(r2.json()["connections"]) == 1


def test_api_spend_summary(client, auth_a):
    r = client.get("/pro/ads/spend", cookies=auth_a)
    assert r.status_code == 200
    assert "total_spend_eur" in r.json()
