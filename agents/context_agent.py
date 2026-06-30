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

# 300 días calendario ≈ 200 ruedas — necesario para SMA150 (Weinstein Stage 2),
# no solo SMA20/50. Antes se pedían 60 días y el analysis_agent nunca veía el
# checklist técnico real, solo el precio: podía recomendar BUY en Stage 4.
_TECHNICAL_HISTORY_DAYS = 300


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
        filtered_news, technical_summary = await asyncio.gather(
            _filter_news(news_items, all_tickers, market_overview, settings),
            _enrich_with_technicals(market_overview, tickers_usa),
        )
        if cache:
            cache.set(news_key, [n.model_dump(mode="json") for n in filtered_news])
    else:
        technical_summary = await _enrich_with_technicals(market_overview, tickers_usa)

    news_collection = NewsCollection(
        items=filtered_news,
        tickers_queried=all_tickers,
        hours_back=actual_hours,
    )

    return AgentContext(
        news=news_collection,
        market=market_overview,
        query_tickers=all_tickers,
        technical_summary=technical_summary,
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
    from datetime import datetime, timedelta
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

    # Pre-filtrar artículos viejos que se cuelan por RSS (ej: artículos "destacados" de hace meses)
    # Se agrega 24h de buffer para no descartar artículos del límite de la ventana
    cutoff = datetime.utcnow() - timedelta(hours=window + 24)

    def _pub_naive(item: NewsItem) -> datetime:
        try:
            pub = item.published_at
            if pub is None:
                return datetime.utcnow()  # sin fecha → asumir reciente
            return pub.replace(tzinfo=None) if getattr(pub, "tzinfo", None) else pub
        except Exception:
            return datetime.utcnow()

    fresh = [n for n in unique if _pub_naive(n) >= cutoff]
    old_count = len(unique) - len(fresh)
    if old_count:
        logger.info(f"Noticias descartadas por antigüedad: {old_count} artículos fuera de ventana de {window+24}h")

    # Ordenar por recencia (más reciente primero) para que el AI vea lo más nuevo
    result = sorted(fresh if len(fresh) >= 5 else unique, key=_pub_naive, reverse=True)

    logger.info(f"Noticias únicas antes de filtrar: {len(result)}")
    return result


# gpt-4o-mini: barato (~$0.001/run), siempre disponible en OpenRouter
_OPENROUTER_FILTER_MODEL = "openai/gpt-4o-mini"

# Límites de noticias por proveedor según capacidad de tokens
_MAX_NEWS_OR_PAID = 80    # OpenRouter pago: sin límite práctico — más candidatas, mejor selección
_MAX_NEWS_GROQ = 20       # Groq llama-3.1-8b: límite 6000 TPM → ~20 items seguros
_MAX_NEWS_OR_FREE = 15    # OpenRouter free: modelos pequeños, conservador

# Si el LLM selecciona menos de esto, se completa con las noticias Tier A/B
# más recientes que no haya elegido, para no quedar con muy pocas en la UI.
_MIN_NEWS_FLOOR = 20


def _build_filter_prompt(
    news_items: list[NewsItem], tickers: list[str], market: MarketOverview, max_items: int
) -> str:
    raw_news_text = "\n".join(
        f"- [{n.source_tier}] {n.headline[:80]} | {n.source} | {n.url}"
        for n in news_items[:max_items]
    )
    return (
        PROMPT_PATH.read_text()
        .replace("{tickers}", ", ".join(tickers))
        .replace("{raw_news}", raw_news_text)
        .replace("{market_data}", market.to_context_block())
    )


async def _filter_news(
    news_items: list[NewsItem],
    tickers: list[str],
    market: MarketOverview,
    settings: Settings,
) -> list[NewsItem]:
    """
    Orden: OpenRouter pago (gemini-2.0-flash, 40 items)
           → Groq llama-3.1-8b (20 items — bajo TPM limit de 6000)
           → OpenRouter modelos free (15 items — último recurso)
    Si nada responde → tier A directo.
    """
    if not news_items:
        return []

    content: str | None = None

    # 1. OpenRouter pago (gpt-4o-mini) — sin rate-limit, consume créditos de OR
    if settings.openrouter_api_key:
        prompt = _build_filter_prompt(news_items, tickers, market, _MAX_NEWS_OR_PAID)
        try:
            client = build_openrouter_client(settings.openrouter_api_key)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_OPENROUTER_FILTER_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                ),
                timeout=30.0,
            )
            content = response.choices[0].message.content
            logger.info(f"Contexto: filtrado con OpenRouter {_OPENROUTER_FILTER_MODEL}")
        except asyncio.TimeoutError:
            logger.warning(f"OpenRouter filtrado: timeout de 30s con {_OPENROUTER_FILTER_MODEL}")
        except Exception as e:
            logger.warning(f"OpenRouter filtrado error ({type(e).__name__}): {e}")

    # 2. Groq fallback — 20 items para no exceder 6000 TPM de llama-3.1-8b-instant
    if not content and settings.groq_api_key:
        prompt = _build_filter_prompt(news_items, tickers, market, _MAX_NEWS_GROQ)
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
        prompt = _build_filter_prompt(news_items, tickers, market, _MAX_NEWS_OR_FREE)
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

            def _norm_url(url: str) -> str:
                return url.rstrip("/").replace("https://", "http://").lower().strip()

            url_to_original = {_norm_url(n.url): n for n in news_items}
            # Fallback por headline (primeros 70 chars) cuando la URL cambia
            headline_to_original = {n.headline[:70]: n for n in news_items}

            items = []
            for f in filtered:
                url = _norm_url(f.get("url", ""))
                headline = f.get("headline", "")[:70]
                original = url_to_original.get(url) or headline_to_original.get(headline)
                if original:
                    items.append(original.model_copy(update={
                        "sentiment_score": f.get("sentiment_score", original.sentiment_score),
                        "related_tickers": f.get("related_tickers", original.related_tickers),
                    }))

            logger.info(f"Contexto: {len(items)}/{len(news_items)} noticias seleccionadas (LLM propuso {len(filtered)})")

            if 0 < len(items) < _MIN_NEWS_FLOOR:
                existing_urls = {it.url for it in items}
                backfill_pool = sorted(
                    (n for n in news_items if n.url not in existing_urls),
                    key=lambda n: 0 if n.source_tier == "A" else 1,
                )
                need = _MIN_NEWS_FLOOR - len(items)
                backfilled = backfill_pool[:need]
                items.extend(backfilled)
                if backfilled:
                    logger.info(f"Contexto: backfill +{len(backfilled)} noticias tier A/B para alcanzar el mínimo de {_MIN_NEWS_FLOOR}")

            if items:
                return items
        except Exception as e:
            logger.warning(f"Error parseando respuesta de contexto: {e}")

    logger.warning("Contexto: sin IA disponible — usando noticias tier A")
    tier_a = [n for n in news_items if n.source_tier == "A"]
    return tier_a[:20] or news_items[:20]


def _format_technical_line(report) -> str:
    """Línea compacta del checklist técnico para el prompt del analysis_agent."""
    failed = [s.name for s in report.signals if not s.ok]
    falla_str = f" | falla: {', '.join(failed)}" if failed else " | todas las señales OK"
    return f"{report.ticker}: {report.stage} · {report.summary}{falla_str}"


async def _enrich_with_technicals(market: MarketOverview, tickers_usa: list[str]) -> str:
    """
    Agrega SMA20/SMA50 a los snapshots y calcula el checklist técnico completo
    (Weinstein Stage + O'Neil) por ticker — la misma lógica determinística que
    ve el usuario en la UI. Antes el analysis_agent solo veía SMA20/50 sueltos
    y podía recomendar BUY sin chequear si el ticker está en tendencia bajista.

    Itera sobre tickers_usa (no sobre market.snapshots) y usa market.get(ticker)
    a propósito: GGAL/BBAR/LOMA están tanto en la lista de ADRs como en la de
    BYMA con el mismo string de ticker, y _fetch_market_data agrega los
    snapshots de USA antes que los de BYMA — iterar snapshots directamente
    aplicaba el historial en USD del ADR al snapshot en pesos de BYMA.
    """
    try:
        from core.services import technical_service
        from core.services.chart_service import get_histories, get_sma_values
        histories = await asyncio.wait_for(
            get_histories(tickers_usa, days=_TECHNICAL_HISTORY_DAYS),
            timeout=15.0,
        )
        lines = []
        for ticker in tickers_usa:
            df = histories.get(ticker)
            snap = market.get(ticker)
            if df is None or snap is None:
                continue
            snap.sma20, snap.sma50 = get_sma_values(df)
            report = technical_service.analyze(ticker, df, snap.current_price, snap.high_52w)
            if report.signals:
                lines.append(_format_technical_line(report))
        logger.info(f"Técnicos: checklist Weinstein/O'Neil calculado para {len(lines)}/{len(tickers_usa)} tickers")
        return "\n".join(lines)
    except asyncio.TimeoutError:
        logger.warning("Enriquecimiento técnico: timeout de 15s")
        return ""
    except Exception as e:
        logger.warning(f"Enriquecimiento técnico fallido: {e}")
        return ""


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
