from datetime import datetime, timedelta

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from core.models.news import NewsItem

MARKETAUX_BASE = "https://api.marketaux.com/v1"


class MarketauxClient:
    def __init__(self, api_key: str):
        self._api_key = api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def get_news(self, tickers: list[str], hours_back: int = 24) -> list[NewsItem]:
        """
        Obtiene noticias con sentiment scores de Marketaux.
        Fuente tier A: 5000+ fuentes verificadas con scoring de sentimiento.
        """
        since = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M")
        symbols = ",".join(tickers)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MARKETAUX_BASE}/news/all",
                params={
                    "symbols": symbols,
                    "filter_entities": "true",
                    "published_after": since,
                    "language": "en",
                    "api_token": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        items = []
        for article in data.get("data", []):
            url = article.get("url", "")
            if not url:
                continue
            sentiment_score = self._extract_sentiment(article, tickers)
            related = self._extract_tickers(article, tickers)
            try:
                items.append(NewsItem(
                    headline=article["title"],
                    summary=article.get("description", ""),
                    source=article.get("source", "marketaux"),
                    url=url,
                    published_at=datetime.fromisoformat(
                        article["published_at"].replace("Z", "+00:00")
                    ),
                    sentiment_score=sentiment_score,
                    related_tickers=related,
                    source_tier="A",
                    corroborated_by=1,
                ))
            except Exception as e:
                logger.debug(f"Noticia Marketaux descartada: {e}")
        return items

    def _extract_sentiment(self, article: dict, query_tickers: list[str]) -> float:
        """Extrae el sentiment score promedio de las entidades del artículo."""
        entities = article.get("entities", [])
        scores = []
        for entity in entities:
            symbol = entity.get("symbol", "").upper()
            if symbol in [t.upper() for t in query_tickers]:
                score = entity.get("sentiment_score")
                if score is not None:
                    scores.append(float(score))
        if scores:
            return sum(scores) / len(scores)
        overall = article.get("sentiment", None)
        if overall == "positive":
            return 0.3
        if overall == "negative":
            return -0.3
        return 0.0

    def _extract_tickers(self, article: dict, query_tickers: list[str]) -> list[str]:
        entities = article.get("entities", [])
        found = [e.get("symbol", "").upper() for e in entities if e.get("symbol")]
        relevant = [t for t in found if t in [q.upper() for q in query_tickers]]
        return relevant or found[:3]
