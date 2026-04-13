"""
regulatory_feed_monitor.py — Automated monitoring of worldwide regulatory
sources for new privacy/security/data-protection developments.

The problem: HedgeSpark needs to comply with GDPR, CCPA, LGPD, PIPL, APPI,
PIPA, POPIA, and future laws — but the founder can't read every government
gazette daily. This module does it for them.

How it works:
─────────────
1. Maintains a registry of machine-readable regulatory sources (RSS/Atom feeds,
   official gazette APIs). Each source has a region, language, and relevance
   keywords.

2. Every 24h (via agent_worker), fetches each feed and scans new entries
   against a keyword set: "privacy", "data protection", "consent", "cookie",
   "GDPR", "CCPA", "personal data", "breach notification", etc.

3. Matching entries are stored in Redis (dedup by URL hash) and emitted as
   `regulatory_update` ops_alerts.

4. The daily Telegram digest includes a "Regulatory Updates" section with
   new items from the last 7 days.

5. When the founder sees a relevant update, they can:
   a) Add a new rule to `regulatory_watch.py` (code change)
   b) Ask Claude to analyze the regulation and propose code changes
   c) Dismiss if not applicable

No LLM calls. Pure RSS/HTTP fetch + deterministic keyword matching.

Feed strategy:
─────────────
- EU Official Journal (EUR-Lex) — RSS for new regulations
- UK ICO — news feed for guidance updates
- California AG — CCPA/CPRA updates
- Brazil ANPD — LGPD guidance
- EDPB — European Data Protection Board opinions
- CNIL (France) — DPA decisions (often EU-wide precedent)
- FTC — US federal privacy enforcement
- IAPP — International Association of Privacy Professionals (news aggregator)

Fetch is resilient: any individual feed failure is logged and skipped.
A total failure doesn't block the agent_worker cycle.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("regulatory_feed_monitor")

_COOLDOWN_KEY = "hs:reg_feed_monitor:last_run"
_COOLDOWN_S = 24 * 3600  # once per day
_ITEMS_KEY_PREFIX = "hs:reg_feed:item"
_ITEM_TTL_S = 30 * 24 * 3600  # 30 days retention
_PAUSED = os.getenv("REGULATORY_FEED_MONITOR_PAUSED", "").strip() == "1"
_FETCH_TIMEOUT = 15.0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


# -----------------------------------------------------------------------
# Feed registry
# -----------------------------------------------------------------------

class RegulatoryFeed:
    """A single RSS/Atom feed to monitor."""
    __slots__ = ("feed_id", "name", "url", "region", "language", "enabled")

    def __init__(
        self, *, feed_id: str, name: str, url: str,
        region: str, language: str = "en", enabled: bool = True,
    ):
        self.feed_id = feed_id
        self.name = name
        self.url = url
        self.region = region
        self.language = language
        self.enabled = enabled


FEEDS: list[RegulatoryFeed] = [
    RegulatoryFeed(
        feed_id="eurlex-dp",
        name="EUR-Lex (Data Protection)",
        url="https://eur-lex.europa.eu/search.html?type=act&qid=DPFEED&DTA=2023&SN_SUBJ=DATAPROTECTION&FM_CODED=REG,DIR,DEC&format=rss",
        region="EU",
    ),
    RegulatoryFeed(
        feed_id="edpb",
        name="EDPB (European Data Protection Board)",
        url="https://www.edpb.europa.eu/news/news_en/rss.xml",
        region="EU",
    ),
    RegulatoryFeed(
        feed_id="ico-uk",
        name="UK ICO News",
        url="https://ico.org.uk/about-the-ico/media-centre/news-and-blogs/rss/",
        region="UK",
    ),
    RegulatoryFeed(
        feed_id="cnil-fr",
        name="CNIL (France)",
        url="https://www.cnil.fr/fr/rss.xml",
        region="EU-FR",
        language="fr",
    ),
    RegulatoryFeed(
        feed_id="ftc-privacy",
        name="FTC Privacy & Data Security",
        url="https://www.ftc.gov/feeds/press-release-consumer-protection.xml",
        region="US",
    ),
    RegulatoryFeed(
        feed_id="iapp-daily",
        name="IAPP Daily Dashboard",
        url="https://iapp.org/rss/daily-dashboard/",
        region="Global",
    ),
    RegulatoryFeed(
        feed_id="bfdi-de",
        name="BfDI (Germany)",
        url="https://www.bfdi.bund.de/SiteGlobals/Functions/RSSFeed/RSSNewsfeed/RSSNewsfeed.xml",
        region="EU-DE",
        language="de",
    ),
    RegulatoryFeed(
        feed_id="aepd-es",
        name="AEPD (Spain)",
        url="https://www.aepd.es/canaldocumentacion/rss/rss.xml",
        region="EU-ES",
        language="es",
    ),
    RegulatoryFeed(
        feed_id="garante-it",
        name="Garante Privacy (Italy)",
        url="https://www.garanteprivacy.it/home/rss",
        region="EU-IT",
        language="it",
    ),
]


# -----------------------------------------------------------------------
# Relevance keywords — multilingual
# -----------------------------------------------------------------------

_KEYWORDS_EN = {
    "privacy", "data protection", "personal data", "consent",
    "cookie", "gdpr", "ccpa", "cpra", "breach notification",
    "data processing", "data subject", "right to erasure",
    "right to access", "data portability", "automated decision",
    "profiling", "children", "minors", "tracking", "surveillance",
    "e-privacy", "eprivacy", "digital services act", "ai act",
    "online safety", "cybersecurity", "data breach", "fine",
    "penalty", "enforcement", "dpa", "supervisory authority",
    "cross-border", "transfer", "adequacy", "standard contractual",
    "legitimate interest", "opt-out", "opt out", "global privacy control",
    "do not track", "analytics", "behavioral", "retargeting",
    "shopify", "e-commerce", "ecommerce", "saas",
}

_KEYWORDS_FR = {
    "donnees personnelles", "protection des donnees", "vie privee",
    "consentement", "cookie", "rgpd", "violation de donnees",
    "droit a l'effacement", "traitement", "sous-traitant",
    "amende", "sanction",
}

_KEYWORDS_DE = {
    "datenschutz", "personenbezogene daten", "einwilligung",
    "cookie", "dsgvo", "datenpanne", "recht auf loschung",
    "verarbeitung", "bussgeld", "aufsichtsbehorde",
}

_KEYWORDS_ES = {
    "proteccion de datos", "datos personales", "consentimiento",
    "cookie", "rgpd", "brecha de datos", "derecho al olvido",
    "tratamiento", "sancion", "multa",
}

_KEYWORDS_IT = {
    "protezione dei dati", "dati personali", "consenso",
    "cookie", "gdpr", "violazione dei dati", "diritto alla cancellazione",
    "trattamento", "sanzione", "garante",
}

_KEYWORDS_BY_LANG: dict[str, set[str]] = {
    "en": _KEYWORDS_EN,
    "fr": _KEYWORDS_FR | _KEYWORDS_EN,  # French feeds may have English too
    "de": _KEYWORDS_DE | _KEYWORDS_EN,
    "es": _KEYWORDS_ES | _KEYWORDS_EN,
    "it": _KEYWORDS_IT | _KEYWORDS_EN,
}


def _is_relevant(text: str, language: str = "en") -> bool:
    """Check if text contains any relevance keyword (case-insensitive)."""
    if not text:
        return False
    text_lower = text.lower()
    keywords = _KEYWORDS_BY_LANG.get(language, _KEYWORDS_EN)
    return any(kw in text_lower for kw in keywords)


# -----------------------------------------------------------------------
# Feed parser — handles RSS 2.0 and Atom
# -----------------------------------------------------------------------

def _parse_feed_items(xml_text: str) -> list[dict[str, str]]:
    """Parse an RSS/Atom feed and return a list of items with
    {title, link, description, published}."""
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # RSS 2.0
    for item in root.iter("item"):
        entry: dict[str, str] = {}
        for child in item:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "title":
                entry["title"] = (child.text or "").strip()
            elif tag == "link":
                entry["link"] = (child.text or "").strip()
            elif tag == "description":
                entry["description"] = (child.text or "").strip()[:500]
            elif tag in ("pubDate", "published", "updated"):
                entry["published"] = (child.text or "").strip()
        if entry.get("title") or entry.get("link"):
            items.append(entry)

    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//atom:entry", ns):
        entry = {}
        title_el = item.find("atom:title", ns)
        if title_el is not None:
            entry["title"] = (title_el.text or "").strip()
        link_el = item.find("atom:link[@href]", ns)
        if link_el is not None:
            entry["link"] = link_el.get("href", "").strip()
        summary_el = item.find("atom:summary", ns)
        if summary_el is not None:
            entry["description"] = (summary_el.text or "").strip()[:500]
        updated_el = item.find("atom:updated", ns)
        if updated_el is not None:
            entry["published"] = (updated_el.text or "").strip()
        if entry.get("title") or entry.get("link"):
            items.append(entry)

    return items


# -----------------------------------------------------------------------
# Feed fetcher
# -----------------------------------------------------------------------

def _fetch_feed(feed: RegulatoryFeed) -> list[dict[str, str]]:
    """Fetch and parse a single feed. Returns relevant items only."""
    try:
        import httpx
        resp = httpx.get(
            feed.url,
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "HedgeSpark-RegWatch/1.0"},
        )
        if resp.status_code != 200:
            log.debug(
                "regulatory_feed: %s returned %d",
                feed.feed_id, resp.status_code,
            )
            return []
        items = _parse_feed_items(resp.text)
    except Exception as exc:
        log.debug("regulatory_feed: %s fetch failed: %s", feed.feed_id, exc)
        return []

    relevant = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('description', '')}"
        if _is_relevant(text, feed.language):
            item["feed_id"] = feed.feed_id
            item["feed_name"] = feed.name
            item["region"] = feed.region
            relevant.append(item)

    return relevant


def _item_hash(item: dict) -> str:
    """Deterministic hash for dedup."""
    key = item.get("link") or item.get("title", "")
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# -----------------------------------------------------------------------
# Main runner
# -----------------------------------------------------------------------

def run_feed_monitor() -> dict[str, Any]:
    """Fetch all enabled feeds, filter for relevance, dedup against
    Redis, and emit ops_alerts for genuinely new items.

    Returns a summary report for logging.
    """
    if _PAUSED:
        return {"skipped": True, "reason": "paused"}

    rc = _redis()
    if rc is not None:
        try:
            last = rc.get(_COOLDOWN_KEY)
            if last:
                last_ts = float(last.decode() if isinstance(last, bytes) else last)
                if (_now().timestamp() - last_ts) < _COOLDOWN_S:
                    return {"skipped": True, "reason": "cooldown"}
        except Exception:
            pass

    report: dict[str, Any] = {
        "ran_at": _now().isoformat(),
        "feeds_checked": 0,
        "feeds_failed": 0,
        "items_found": 0,
        "items_new": 0,
        "items_stored": 0,
    }

    all_new_items: list[dict] = []

    for feed in FEEDS:
        if not feed.enabled:
            continue
        report["feeds_checked"] += 1
        items = _fetch_feed(feed)
        if items is None:
            report["feeds_failed"] += 1
            continue
        report["items_found"] += len(items)

        for item in items:
            h = _item_hash(item)
            # Dedup via Redis
            if rc is not None:
                try:
                    redis_key = f"{_ITEMS_KEY_PREFIX}:{h}"
                    if rc.get(redis_key):
                        continue  # already seen
                    rc.setex(redis_key, _ITEM_TTL_S, "1")
                except Exception:
                    pass

            report["items_new"] += 1
            all_new_items.append(item)

    # Emit ops_alerts for new items (max 10 per run to avoid flood)
    if all_new_items:
        try:
            from sqlalchemy.orm import sessionmaker
            from app.core.database import engine
            from app.models.ops_alert import OpsAlert

            SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
            db = SessionLocal()
            try:
                for item in all_new_items[:10]:
                    title = (item.get("title") or "Untitled")[:120]
                    link = item.get("link", "")
                    region = item.get("region", "?")
                    feed_name = item.get("feed_name", "?")

                    # Regulatory updates are informational broadcasts (read
                    # in the daily digest / dashboard), not actionable
                    # incidents. Mark them resolved on create so they do
                    # not inflate the unresolved-alerts pressure metric.
                    from datetime import datetime as _dt, timezone as _tz
                    alert = OpsAlert(
                        severity="info",
                        source=f"reg_feed:{_item_hash(item)}",
                        alert_type="regulatory_update",
                        summary=f"[{region}] {title}",
                        detail=(
                            f"Source: {feed_name}\n"
                            f"Region: {region}\n"
                            f"Link: {link}\n\n"
                            f"{(item.get('description') or '')[:300]}\n\n"
                            f"Review this update and determine if HedgeSpark needs "
                            f"code or policy changes. If so, add a rule to "
                            f"regulatory_watch.py or create a bugfix candidate."
                        ),
                        resolved=True,  # broadcast, not incident
                        resolved_at=_dt.now(_tz.utc).replace(tzinfo=None),
                    )
                    db.add(alert)
                    report["items_stored"] += 1
                db.commit()
            except Exception as exc:
                log.warning("regulatory_feed: alert write failed: %s", exc)
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                db.close()
        except Exception as exc:
            log.warning("regulatory_feed: db session failed: %s", exc)

    # Update cooldown
    if rc is not None:
        try:
            rc.setex(_COOLDOWN_KEY, _COOLDOWN_S, str(_now().timestamp()))
        except Exception:
            pass

    if report["items_new"] > 0:
        log.info(
            "regulatory_feed: %d new items from %d feeds (%d stored)",
            report["items_new"], report["feeds_checked"], report["items_stored"],
        )

    return report


def get_recent_updates(days: int = 7) -> list[dict[str, Any]]:
    """Return recent regulatory_update alerts for the digest.
    Lightweight DB query — no feed fetching."""
    try:
        from sqlalchemy.orm import sessionmaker
        from app.core.database import engine
        from app.models.ops_alert import OpsAlert
        from datetime import timedelta

        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = SessionLocal()
        try:
            cutoff = _now() - timedelta(days=days)
            alerts = (
                db.query(OpsAlert)
                .filter(
                    OpsAlert.alert_type == "regulatory_update",
                    OpsAlert.created_at >= cutoff,
                )
                .order_by(OpsAlert.created_at.desc())
                .limit(10)
                .all()
            )
            return [
                {
                    "id": a.id,
                    "summary": a.summary,
                    "resolved": a.resolved,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in alerts
            ]
        finally:
            db.close()
    except Exception:
        return []
