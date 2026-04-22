"""
Context Agent — usa Qwen3.6 (gratis via OpenRouter) para filtrar y resumir
noticias y datos de mercado antes de enviárselos a Claude.

Estrategia de tokens:
- Qwen procesa el volumen (1M contexto, gratis)
- Solo pasa < 4000 tokens comprimidos al analysis_agent
"""
import asyncio
from pathlib import Path

from loguru import logger

from agents.utils import build_openrouter_client, extract_json

from config.settings import Settings
from core.models.market import MarketOverview, MarketSnapshot
from core.models.news import NewsCollection, NewsItem
from core.models.recommendation import AgentContext
from core.services.byma_client import BYMAClient
from core.services.cache_service import CacheService
from core.services.finnhub_client import FinnhubClient
from core.services.marketaux_client import MarketauxClient
from core.services.rss_client import fetch_all_tier_a_news

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "context_agent.txt"
QWEN_MODEL = "qwen/qwen3.6-plus-preview:free"


async def run(
    tickers_usa: list[str],
    tickers_byma: list[str],
    settings: Settings,
    cache: CacheService | None = None,
) -> AgentContext:
    """
    Ejecuta el pipeline de contexto:
    1. Busca en cache (30 min TTL)
    2. Fetch datos de mercado USA (Finnhub) + ARG (BYMA)
    3. Fetch noticias (Marketaux + RSS tier A + Finnhub)
    4. Llama a Qwen para filtrar/resumir
    5. Retorna AgentContext comprimido
    """
    all_tickers = list(set(tickers_usa + tickers_byma))
    cache_key = f"context_{'_'.join(sorted(all_tickers))}"

    if cache:
        cached = cache.get(cache_key)
        if cached:
            logger.info("AgentContext desde cache")
            return AgentContext.model_validate(cached)

    logger.info(f"Fetcheando datos para: {all_tickers}")

    market_overview, news_items = await asyncio.gather(
        _fetch_market_data(tickers_usa, tickers_byma, settings),
        _fetch_all_news(tickers_usa, settings),
        return_exceptions=False,
    )

    # Filtrar y resumir con Qwen
    filtered_news = await _filter_with_qwen(news_items, all_tickers, market_overview, settings)

    news_collection = NewsCollection(
        items=filtered_news,
        tickers_queried=all_tickers,
        hours_back=settings.news_hours_back,
    )

    context = AgentContext(
        news=news_collection,
        market=market_overview,
        query_tickers=all_tickers,
    )

    if cache:
        cache.set(cache_key, context.model_dump(mode="json"))

    return context


async def _fetch_market_data(
    tickers_usa: list[str], tickers_byma: list[str], settings: Settings
) -> MarketOverview:
    snapshots: list[MarketSnapshot] = []

    # USA via Finnhub
    if settings.finnhub_api_key:
        finnhub = FinnhubClient(settings.finnhub_api_key)
        tasks = [finnhub.get_quote(t) for t in tickers_usa]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, MarketSnapshot):
                snapshots.append(r)
            elif isinstance(r, Exception):
                logger.warning(f"Error Finnhub quote: {r}")
    else:
        logger.warning("FINNHUB_API_KEY no configurada — usando yfinance como fallback")
        snapshots.extend(await _fetch_yfinance(tickers_usa))

    # ARG via BYMA
    if tickers_byma:
        try:
            byma = BYMAClient()
            byma_snapshots = await byma.get_equities()
            relevant = {s.ticker for s in byma_snapshots}
            for t in tickers_byma:
                if t.upper() in relevant:
                    snap = next(s for s in byma_snapshots if s.ticker == t.upper())
                    snapshots.append(snap)
        except Exception as e:
            logger.warning(f"Error BYMA: {e}")

    return MarketOverview(snapshots=snapshots)


async def _fetch_all_news(tickers: list[str], settings: Settings) -> list[NewsItem]:
    tasks = [fetch_all_tier_a_news()]

    if settings.marketaux_api_key:
        maux = MarketauxClient(settings.marketaux_api_key)
        tasks.append(maux.get_news(tickers, settings.news_hours_back))

    if settings.finnhub_api_key:
        finnhub = FinnhubClient(settings.finnhub_api_key)
        news_tasks = [finnhub.get_company_news(t, settings.news_hours_back) for t in tickers[:5]]
        tasks.extend(news_tasks)
        tasks.append(finnhub.get_market_news())

    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[NewsItem] = []
    for r in results:
        if isinstance(r, list):
            items.extend(r)
        elif isinstance(r, Exception):
            logger.warning(f"Error fetching news: {r}")

    # Deduplicar por URL
    seen_urls: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique.append(item)

    logger.info(f"Noticias únicas antes de filtrar: {len(unique)}")
    return unique


async def _filter_with_qwen(
    news_items: list[NewsItem],
    tickers: list[str],
    market: MarketOverview,
    settings: Settings,
) -> list[NewsItem]:
    """Llama a Qwen3.6 para filtrar y puntuar noticias. Fallback: retorna tier A."""
    if not settings.openrouter_api_key or not news_items:
        # Sin Qwen, devolver solo tier A y limitar cantidad
        tier_a = [n for n in news_items if n.source_tier == "A"]
        return tier_a[:20] or news_items[:20]

    try:
        prompt_template = PROMPT_PATH.read_text()
        raw_news_text = "\n".join(
            f"- [{n.source_tier}] {n.headline} | {n.source} | {n.url}" for n in news_items[:50]
        )
        market_text = market.to_context_block()
        prompt = prompt_template.format(
            tickers=", ".join(tickers),
            raw_news=raw_news_text,
            market_data=market_text,
        )

        client = build_openrouter_client(settings.openrouter_api_key)
        response = await client.chat.completions.create(
            model=QWEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        result = extract_json(response.choices[0].message.content)
        filtered = result.get("filtered_news", [])
        logger.info(f"Qwen filtró: {len(filtered)}/{len(news_items)} noticias")

        # Reconstruir NewsItems desde la respuesta de Qwen
        items = []
        url_to_original = {n.url: n for n in news_items}
        for f in filtered:
            url = f.get("url", "")
            if url in url_to_original:
                original = url_to_original[url]
                items.append(original.model_copy(update={
                    "sentiment_score": f.get("sentiment_score", original.sentiment_score),
                    "related_tickers": f.get("related_tickers", original.related_tickers),
                }))
        return items if items else news_items[:20]

    except Exception as e:
        logger.warning(f"Qwen falló, usando fallback: {e}")
        tier_a = [n for n in news_items if n.source_tier == "A"]
        return tier_a[:20] or news_items[:20]


async def _fetch_yfinance(tickers: list[str]) -> list[MarketSnapshot]:
    """Fallback con yfinance cuando Finnhub no está disponible."""
    try:
        import yfinance as yf
        snapshots = []
        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                last = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", 0)
                prev = getattr(info, "previous_close", last) or last
                if not last or last <= 0:
                    continue
                snapshots.append(MarketSnapshot(
                    ticker=ticker,
                    current_price=last,
                    previous_close=prev,
                    change_amount=last - prev,
                    change_pct=((last - prev) / prev * 100) if prev else 0.0,
                    high_today=getattr(info, "day_high", last) or last,
                    low_today=getattr(info, "day_low", last) or last,
                    open_price=getattr(info, "open", prev) or prev,
                    volume=int(getattr(info, "three_month_average_volume", 0) or 0),
                    high_52w=getattr(info, "year_high", None),
                    low_52w=getattr(info, "year_low", None),
                ))
            except Exception as e:
                logger.debug(f"yfinance falló para {ticker}: {e}")
        return snapshots
    except ImportError:
        logger.error("yfinance no instalado. Instalá con: pip install yfinance")
        return []
