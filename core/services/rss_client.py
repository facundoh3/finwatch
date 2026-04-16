"""
Cliente RSS para fuentes financieras tier A (Reuters, Bloomberg, WSJ).
Estas fuentes son las más confiables para noticias financieras.
No requieren API key.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from loguru import logger

from core.models.news import NewsItem

# Feeds RSS de fuentes tier A — editorialmente verificadas
TIER_A_FEEDS = {
    "reuters": "https://feeds.reuters.com/reuters/businessNews",
    "wsj": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
}

# Bloomberg requiere suscripción para RSS completo; se omite por defecto
# "bloomberg": "https://feeds.bloomberg.com/markets/news.rss"


async def fetch_rss_feed(url: str, source_name: str) -> list[NewsItem]:
    """Descarga y parsea un feed RSS, retorna NewsItems con source_tier='A'."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "finwatch/0.1"})
            resp.raise_for_status()
            xml = resp.text
    except Exception as e:
        logger.warning(f"Error descargando RSS {source_name}: {e}")
        return []

    return _parse_rss_xml(xml, source_name)


def _parse_rss_xml(xml: str, source_name: str) -> list[NewsItem]:
    """Parser RSS minimalista sin dependencias externas."""
    import re
    items = []
    entries = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    for entry in entries[:15]:
        try:
            title = _extract_tag(entry, "title")
            link = _extract_tag(entry, "link") or _extract_tag(entry, "guid")
            pub_date = _extract_tag(entry, "pubDate")
            description = _extract_tag(entry, "description")
            if not title or not link:
                continue
            published_at = _parse_date(pub_date)
            items.append(NewsItem(
                headline=title,
                summary=description or "",
                source=source_name,
                url=link,
                published_at=published_at,
                source_tier="A",
                corroborated_by=1,
            ))
        except Exception as e:
            logger.debug(f"RSS item descartado ({source_name}): {e}")
    return items


def _extract_tag(text: str, tag: str) -> str:
    import re
    match = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


async def fetch_all_tier_a_news() -> list[NewsItem]:
    """Descarga noticias de todas las fuentes tier A configuradas."""
    import asyncio
    tasks = [fetch_rss_feed(url, name) for name, url in TIER_A_FEEDS.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
        elif isinstance(result, Exception):
            logger.warning(f"Feed RSS falló: {result}")
    return items
