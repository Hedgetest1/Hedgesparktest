"""Tests for the regulatory feed monitor."""
import pytest
from unittest.mock import patch, MagicMock


def test_feeds_registry_not_empty():
    """Feed registry must contain sources."""
    from app.services.regulatory_feed_monitor import FEEDS
    assert len(FEEDS) >= 5


def test_feeds_have_required_fields():
    """Every feed must have all required fields."""
    from app.services.regulatory_feed_monitor import FEEDS
    for feed in FEEDS:
        assert feed.feed_id, f"Feed missing feed_id"
        assert feed.name, f"Feed {feed.feed_id} missing name"
        assert feed.url, f"Feed {feed.feed_id} missing url"
        assert feed.region, f"Feed {feed.feed_id} missing region"


def test_feed_ids_are_unique():
    """Every feed must have a unique feed_id."""
    from app.services.regulatory_feed_monitor import FEEDS
    ids = [f.feed_id for f in FEEDS]
    assert len(ids) == len(set(ids)), "Duplicate feed IDs"


def test_regions_covered():
    """Feeds must cover key regions."""
    from app.services.regulatory_feed_monitor import FEEDS
    regions = {f.region for f in FEEDS}
    assert "EU" in regions
    assert "UK" in regions
    assert "US" in regions


def test_keyword_matching_en():
    """English keyword matching must detect privacy-related text."""
    from app.services.regulatory_feed_monitor import _is_relevant
    assert _is_relevant("New GDPR guidelines for cookie consent", "en")
    assert _is_relevant("Data protection authority issues fine", "en")
    assert _is_relevant("Personal data breach notification rules", "en")
    assert not _is_relevant("Weather forecast for tomorrow", "en")
    assert not _is_relevant("Stock market update Q3 2026", "en")


def test_keyword_matching_multilingual():
    """Non-English keyword sets must detect local terms."""
    from app.services.regulatory_feed_monitor import _is_relevant
    assert _is_relevant("La CNIL sanctionne le traitement des donnees personnelles", "fr")
    assert _is_relevant("Datenschutz: Neue Regelung zur Einwilligung", "de")
    assert _is_relevant("Proteccion de datos personales: nueva sancion", "es")
    assert _is_relevant("Garante privacy: protezione dei dati personali", "it")


def test_parse_rss_feed():
    """Parser must extract items from RSS 2.0 XML."""
    from app.services.regulatory_feed_monitor import _parse_feed_items
    xml = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <item>
          <title>GDPR Update: New Consent Rules</title>
          <link>https://example.com/gdpr-update</link>
          <description>The EU has published new consent guidelines.</description>
          <pubDate>Mon, 01 Apr 2026 00:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Weather Report</title>
          <link>https://example.com/weather</link>
          <description>Sunny skies expected.</description>
        </item>
      </channel>
    </rss>"""
    items = _parse_feed_items(xml)
    assert len(items) == 2
    assert items[0]["title"] == "GDPR Update: New Consent Rules"
    assert items[0]["link"] == "https://example.com/gdpr-update"


def test_parse_atom_feed():
    """Parser must extract items from Atom XML."""
    from app.services.regulatory_feed_monitor import _parse_feed_items
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Test Atom Feed</title>
      <entry>
        <title>Data Protection Fine Issued</title>
        <link href="https://example.com/fine"/>
        <summary>DPA issues record fine for privacy violations.</summary>
        <updated>2026-04-01T00:00:00Z</updated>
      </entry>
    </feed>"""
    items = _parse_feed_items(xml)
    assert len(items) == 1
    assert items[0]["title"] == "Data Protection Fine Issued"
    assert items[0]["link"] == "https://example.com/fine"


def test_item_hash_deterministic():
    """Same item must produce same hash."""
    from app.services.regulatory_feed_monitor import _item_hash
    item = {"link": "https://example.com/test", "title": "Test"}
    h1 = _item_hash(item)
    h2 = _item_hash(item)
    assert h1 == h2
    assert len(h1) == 16


def test_item_hash_different_for_different_items():
    """Different items must produce different hashes."""
    from app.services.regulatory_feed_monitor import _item_hash
    h1 = _item_hash({"link": "https://example.com/a"})
    h2 = _item_hash({"link": "https://example.com/b"})
    assert h1 != h2


def test_run_feed_monitor_respects_cooldown():
    """Monitor must respect 24h cooldown."""
    from app.services.regulatory_feed_monitor import run_feed_monitor
    from datetime import datetime, timezone
    mock_rc = MagicMock()
    now_ts = datetime.now(timezone.utc).timestamp()
    mock_rc.get.return_value = str(now_ts).encode()
    with patch("app.services.regulatory_feed_monitor._redis", return_value=mock_rc):
        report = run_feed_monitor()
    assert report.get("skipped") is True


def test_run_feed_monitor_respects_pause():
    """Monitor must respect PAUSED flag."""
    from app.services.regulatory_feed_monitor import run_feed_monitor
    with patch("app.services.regulatory_feed_monitor._PAUSED", True):
        report = run_feed_monitor()
    assert report.get("skipped") is True


def test_get_recent_updates_returns_list():
    """get_recent_updates must return a list."""
    from app.services.regulatory_feed_monitor import get_recent_updates
    result = get_recent_updates(days=7)
    assert isinstance(result, list)
