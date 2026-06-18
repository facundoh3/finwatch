from datetime import datetime, timedelta

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from core.models.market import MarketSnapshot
from core.models.news import NewsItem

FINNHUB_BASE = "https://finnhub.io/api/v1"


class FinnhubClient:
    def __init__(self, api_key: str):
        self._headers = {"X-Finnhub-Token": api_key}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def get_quote(self, ticker: str) -> MarketSnapshot:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/quote",
                params={"symbol": ticker},
                headers=self._headers,
            )
            resp.raise_for_status()
            d = resp.json()
            if d.get("c", 0) == 0:
                raise ValueError(f"Ticker sin datos: {ticker}")
            return MarketSnapshot(
                ticker=ticker,
                current_price=d["c"],
                previous_close=d["pc"],
                change_amount=d["d"],
                change_pct=d["dp"],
                high_today=d["h"] or d["c"],
                low_today=d["l"] or d["c"],
                open_price=d["o"] or d["c"],
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def get_company_news(self, ticker: str, hours_back: int = 24) -> list[NewsItem]:
        since = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%d")
        to = datetime.utcnow().strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/company-news",
                params={"symbol": ticker, "from": since, "to": to},
                headers=self._headers,
            )
            resp.raise_for_status()
            items = []
            for article in resp.json()[:8]:
                url = article.get("url", "")
                if not url:
                    continue
                try:
                    items.append(NewsItem(
                        headline=article["headline"],
                        summary=article.get("summary", ""),
                        source=article.get("source", "finnhub"),
                        url=url,
                        published_at=datetime.fromtimestamp(article["datetime"]),
                        related_tickers=[ticker],
                        source_tier="B",
                    ))
                except Exception as e:
                    logger.debug(f"Noticia descartada ({ticker}): {e}")
            return items

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def get_market_news(self, category: str = "general") -> list[NewsItem]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/news",
                params={"category": category},
                headers=self._headers,
            )
            resp.raise_for_status()
            items = []
            for article in resp.json()[:10]:
                url = article.get("url", "")
                if not url:
                    continue
                try:
                    items.append(NewsItem(
                        headline=article["headline"],
                        summary=article.get("summary", ""),
                        source=article.get("source", "finnhub"),
                        url=url,
                        published_at=datetime.fromtimestamp(article["datetime"]),
                        source_tier="B",
                    ))
                except Exception as e:
                    logger.debug(f"Noticia general descartada: {e}")
            return items
