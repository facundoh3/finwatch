"""
Context Agent — filtra y resume noticias antes de enviárselas al analysis_agent.
Usa race_models: corre 4 modelos en paralelo, toma el primero que responda (max 15s).
"""
import asyncio
from pathlib import Path

from loguru import logger

from agents.utils import build_groq_client, build_openrouter_client, extract_json, get_free_models, race_models
from config.settings import Settings
from core.models.market import MarketOverview, MarketSnapshot
from core.models.news import NewsCollection, NewsItem
from core.models.recommendation import AgentContext
from core.services.byma_client import BYMAClient
from core.services.cache_service import CacheService
from core.services.finnhub_client import FinnhubClient
from core.services.market_calendar import get_last_close_date
from core.services.marketaux_client import MarketauxClient
from core.services.rss_client import fetch_all_tier_a_news

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "context_agent.txt"

_NEWS_CACHE_TTL = 1440      # minutos — días de semana, se invalida al cambiar cierre NYSE
_WEEKEND_CACHE_TTL = 360    # minutos — fin de semana, se refresca cada 6h


async def run(
    tickers_usa: list[str],
    tickers_byma: list[str],
    settings: Settings,
    cache: CacheService | None = None,
    news_hours_back: int | None = None,
) -> AgentContext:
    from datetime import date
    all_tickers = sorted(set(tickers_usa + tickers_byma))
    close_date = get_last_close_date()
    today = date.today()
    is_weekend = today.weekday() >= 5
    actual_hours = news_hours_back or settings.news_hours_back

    if is_weekend:
        # Fin de semana: caché por día real (no por cierre NYSE) y ventana de 72h para capturar todo
        news_key = f"news_weekend_{today.isoformat()}_{'_'.join(all_tickers)}"
        cache_ttl = _WEEKEND_CACHE_TTL
        actual_hours = max(actual_hours, 72)
        logger.info(f"Fin de semana: ventana de noticias ampliada a {actual_hours}h")
    else:
        news_key = f"news_{close_date}_{'_'.join(all_tickers)}"
        cache_ttl = _NEWS_CACHE_TTL

    logger.info(f"Fetcheando datos para: {all_tickers} | cierre: {close_date} | ventana: {actual_hours}h")

    # Precios: siempre frescos
    market_overview = await _fetch_market_data(tickers_usa, tickers_byma, settings)

    # Noticias: cacheadas por cierre NYSE (días hábiles) o por día (fin de semana)
    filtered_news: list[NewsItem] | None = None
    if cache:
        cached_news = cache.get(news_key, override_ttl_minutes=cache_ttl)
        if cached_news:
            filtered_news = [NewsItem.model_validate(n) for n in cached_news]
            logger.info(f"Noticias: cache {'fin de semana' if is_weekend else f'cierre {close_date}'} ({len(filtered_news)} items)")

    if filtered_news is None:
        news_items = await _fetch_all_news(tickers_usa, settings, actual_hours)
        filtered_news, _ = await asyncio.gather(
            _filter_news(news_items, all_tickers, market_overview, settings),
            _enrich_with_technicals(market_overview, tickers_usa),
        )
        if cache:
            cache.set(news_key, [n.model_dump(mode="json") for n in filtered_news])
    else:
        await _enrich_with_technicals(market_overview, tickers_usa)

    news_collection = NewsCollection(
        items=filtered_news,
        tickers_queried=all_tickers,
        hours_back=actual_hours,
    )

    return AgentContext(
        news=news_collection,
        market=market_overview,
        query_tickers=all_tickers,
    )


async def _fetch_market_data(
    tickers_usa: list[str], tickers_byma: list[str], settings: Settings
) -> MarketOverview:
    snapshots: list[MarketSnapshot] = []

    if settings.finnhub_api_key:
        finnhub = FinnhubClient(settings.finnhub_api_key)
        results = await asyncio.gather(*[finnhub.get_quote(t) for t in tickers_usa], return_exceptions=True)
        for r in results:
            if isinstance(r, MarketSnapshot):
                snapshots.append(r)
            elif isinstance(r, Exception):
                logger.warning(f"Error Finnhub quote: {r}")
    else:
        logger.warning("FINNHUB_API_KEY no configurada — usando yfinance como fallback")
        snapshots.extend(await _fetch_yfinance(tickers_usa))

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
            logger.warning(f"Error BYMA: {e} — usando yfinance .BA como fallback")
            byma_fallback = await _fetch_yfinance_byma(tickers_byma)
            snapshots.extend(byma_fallback)
            if byma_fallback:
                logger.info(f"BYMA fallback: {len(byma_fallback)} tickers via yfinance .BA")

    return MarketOverview(snapshots=snapshots)


async def _fetch_all_news(tickers: list[str], settings: Settings, hours_back: int | None = None) -> list[NewsItem]:
    window = hours_back or settings.news_hours_back
    tasks = [fetch_all_tier_a_news()]

    if settings.marketaux_api_key:
        maux = MarketauxClient(settings.marketaux_api_key)
        tasks.append(maux.get_news(tickers, window))

    if settings.finnhub_api_key:
        finnhub = FinnhubClient(settings.finnhub_api_key)
        tasks.extend([finnhub.get_company_news(t, window) for t in tickers[:5]])
        tasks.append(finnhub.get_market_news())

    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[NewsItem] = []
    for r in results:
        if isinstance(r, list):
            items.extend(r)
        elif isinstance(r, Exception):
            logger.warning(f"Error fetching news: {r}")

    seen_urls: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique.append(item)

    logger.info(f"Noticias únicas antes de filtrar: {len(unique)}")
    return unique


_OPENROUTER_FILTER_MODEL = "google/gemini-flash-1.5"  # rápido, barato, bueno para filtrado


async def _filter_news(
    news_items: list[NewsItem],
    tickers: list[str],
    market: MarketOverview,
    settings: Settings,
) -> list[NewsItem]:
    """
    Orden: OpenRouter pago (gemini-flash, rápido y sin rate-limit)
           → Groq llama-3.1-8b (fallback gratuito)
           → OpenRouter modelos free (último recurso)
    Si nada responde → tier A directo.
    """
    if not news_items:
        return []

    raw_news_text = "\n".join(
        f"- [{n.source_tier}] {n.headline[:80]} | {n.source} | {n.url}" for n in news_items[:20]
    )
    prompt = (
        PROMPT_PATH.read_text()
        .replace("{tickers}", ", ".join(tickers))
        .replace("{raw_news}", raw_news_text)
        .replace("{market_data}", market.to_context_block())
    )

    content: str | None = None

    # 1. OpenRouter pago (gemini-flash) — sin rate-limit, consume los créditos de OR
    if settings.openrouter_api_key:
        try:
            client = build_openrouter_client(settings.openrouter_api_key)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_OPENROUTER_FILTER_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                ),
                timeout=15.0,
            )
            content = response.choices[0].message.content
            logger.info(f"Contexto: filtrado con OpenRouter {_OPENROUTER_FILTER_MODEL}")
        except Exception as e:
            logger.warning(f"OpenRouter filtrado error: {e}")

    # 2. Groq fallback — gratuito pero con rate limit
    if not content and settings.groq_api_key:
        try:
            client = build_groq_client(settings.groq_api_key)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                ),
                timeout=15.0,
            )
            content = response.choices[0].message.content
            logger.info("Contexto: filtrado con Groq llama-3.1-8b-instant (fallback)")
        except Exception as e:
            logger.warning(f"Groq contexto error: {e}")

    # 3. OpenRouter modelos gratuitos — último recurso
    if not content and settings.openrouter_api_key:
        client = build_openrouter_client(settings.openrouter_api_key)
        models = await get_free_models(settings.openrouter_api_key)
        content = await race_models(
            client,
            messages=[{"role": "user", "content": prompt}],
            models=models,
            max_tokens=4000,
            total_timeout=15.0,
        )

    if content:
        try:
            result = extract_json(content)
            filtered = result.get("filtered_news", [])
            logger.info(f"Contexto: {len(filtered)}/{len(news_items)} noticias seleccionadas")
            url_to_original = {n.url: n for n in news_items}
            items = []
            for f in filtered:
                url = f.get("url", "")
                if url in url_to_original:
                    original = url_to_original[url]
                    items.append(original.model_copy(update={
                        "sentiment_score": f.get("sentiment_score", original.sentiment_score),
                        "related_tickers": f.get("related_tickers", original.related_tickers),
                    }))
            if items:
                return items
        except Exception as e:
            logger.warning(f"Error parseando respuesta de contexto: {e}")

    logger.warning("Contexto: sin IA disponible — usando noticias tier A")
    tier_a = [n for n in news_items if n.source_tier == "A"]
    return tier_a[:20] or news_items[:20]


async def _enrich_with_technicals(market: MarketOverview, tickers_usa: list[str]) -> None:
    """Agrega SMA20/SMA50 a los snapshots de USA en paralelo con el filtrado de noticias."""
    try:
        from core.services.chart_service import get_histories, get_sma_values
        histories = await asyncio.wait_for(
            get_histories(tickers_usa, days=60),
            timeout=12.0,
        )
        for snap in market.snapshots:
            if snap.ticker in histories:
                snap.sma20, snap.sma50 = get_sma_values(histories[snap.ticker])
        logger.info(f"Técnicos: SMAs calculadas para {len(histories)} tickers")
    except asyncio.TimeoutError:
        logger.warning("Enriquecimiento técnico: timeout de 12s")
    except Exception as e:
        logger.warning(f"Enriquecimiento técnico fallido: {e}")


async def _fetch_yfinance_byma(tickers: list[str]) -> list[MarketSnapshot]:
    """Obtiene precios BYMA usando yfinance con sufijo .BA. Retorna snapshots con el ticker original."""
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *[loop.run_in_executor(None, _get_byma_snapshot_sync, t) for t in tickers],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, MarketSnapshot)]


def _get_byma_snapshot_sync(ticker: str) -> MarketSnapshot | None:
    try:
        import yfinance as yf
        t = yf.Ticker(f"{ticker}.BA")
        info = t.fast_info
        last = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", 0)
        prev = getattr(info, "previous_close", last) or last
        if not last or last <= 0:
            return None
        return MarketSnapshot(
            ticker=ticker,  # sin .BA — lo mostramos con nombre original
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
        )
    except Exception as e:
        logger.debug(f"yfinance BYMA {ticker}.BA: {e}")
        return None


async def _fetch_yfinance(tickers: list[str]) -> list[MarketSnapshot]:
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
