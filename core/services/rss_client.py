"""
Cliente RSS para fuentes financieras.
Tier A: WSJ, CNBC, MarketWatch, Barron's (editorialmente verificadas)
Tier B: Yahoo Finance, Seeking Alpha (buenas pero menos curadas)
No requieren API key.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from loguru import logger

from core.models.news import NewsItem

TIER_A_FEEDS = {
    "wsj": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "cnbc_investing": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/marketpulse/",
}

TIER_B_FEEDS = {
    "yahoo_finance": "https://finance.yahoo.com/rss/topfinstories",
    "yahoo_top": "https://finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    "seeking_alpha": "https://seekingalpha.com/feed.xml",
    "the_street": "https://www.thestreet.com/.rss/full",
    "barrons": "https://www.barrons.com/xml/rss/3_7_8.xml",
    "benzinga": "https://www.benzinga.com/feed",
    "investing_com": "https://www.investing.com/rss/news_25.rss",
}


async def fetch_rss_feed(url: str, source_name: str, tier: str = "A") -> list[NewsItem]:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 finwatch/0.1"})
            resp.raise_for_status()
            xml = resp.text
    except Exception as e:
        logger.warning(f"Error descargando RSS {source_name}: {e}")
        return []
    return _parse_rss_xml(xml, source_name, tier)


def _parse_rss_xml(xml: str, source_name: str, tier: str = "A") -> list[NewsItem]:
    import re
    items = []
    entries = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    for entry in entries[:20]:
        try:
            title = _extract_tag(entry, "title")
            link = _extract_tag(entry, "link") or _extract_tag(entry, "guid")
            pub_date = _extract_tag(entry, "pubDate")
            description = _extract_tag(entry, "description")
            if not title or not link:
                continue
            items.append(NewsItem(
                headline=title, summary=description or "",
                source=source_name, url=link,
                published_at=_parse_date(pub_date),
                source_tier=tier, corroborated_by=1,
            ))
        except Exception as e:
            logger.debug(f"RSS item descartado ({source_name}): {e}")
    return items


def _extract_tag(text: str, tag: str) -> str:
    import re
    match = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


async def fetch_all_tier_a_news() -> list[NewsItem]:
    import asyncio
    tasks = (
        [fetch_rss_feed(url, name, "A") for name, url in TIER_A_FEEDS.items()] +
        [fetch_rss_feed(url, name, "B") for name, url in TIER_B_FEEDS.items()]
    )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
        elif isinstance(result, Exception):
            logger.warning(f"Feed RSS falló: {result}")
    return items
