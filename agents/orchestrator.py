"""
Orquestador del pipeline multiagente finwatch.

Flujo:
  context_agent (Qwen3.6 gratis) → analysis_agent (Claude Sonnet) → RecommendationSet
"""
import json
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger

from agents import analysis_agent, context_agent
from config.settings import Settings, get_settings
from core.models.recommendation import AgentContext, RecommendationSet
from core.services.cache_service import CacheService

_cache: CacheService | None = None
_ANALYSIS_FILE = Path(__file__).parent.parent / "config" / "last_analysis.json"
_NEWS_HOURS_MIN = 24
_NEWS_HOURS_MAX = 96


def _get_cache(settings: Settings) -> CacheService:
    global _cache
    if _cache is None:
        _cache = CacheService(
            cache_dir=Path("data/cache"),
            ttl_minutes=settings.cache_ttl_minutes,
        )
    return _cache


def _load_tickers() -> tuple[list[str], list[str]]:
    tickers_path = Path(__file__).parent.parent / "config" / "tickers.yaml"
    if not tickers_path.exists():
        return ["AAPL", "NVDA", "TSLA", "MSFT", "SPY"], ["YPFD", "GGAL"]
    data = yaml.safe_load(tickers_path.read_text())
    return data.get("tickers_usa", []), data.get("tickers_byma", [])


def _calc_dynamic_news_hours() -> int:
    """
    Calcula cuántas horas de noticias pedir basándose en el tiempo desde el último análisis.
    Si hay un gap de días (ej: lunes→miércoles), pide más horas para no perder noticias.
    """
    try:
        if not _ANALYSIS_FILE.exists():
            return _NEWS_HOURS_MIN
        data = json.loads(_ANALYSIS_FILE.read_text())
        saved_at = datetime.fromisoformat(data["saved_at"])
        hours_since = (datetime.now() - saved_at).total_seconds() / 3600
        # Agrega 4h de buffer y limita entre 24 y 96h
        dynamic = int(hours_since) + 4
        result = max(_NEWS_HOURS_MIN, min(dynamic, _NEWS_HOURS_MAX))
        if result > _NEWS_HOURS_MIN:
            logger.info(f"News window dinámica: {result}h (último análisis hace {hours_since:.0f}h)")
        return result
    except Exception:
        return _NEWS_HOURS_MIN


async def analyze(
    tickers_usa: list[str] | None = None,
    tickers_byma: list[str] | None = None,
    force_refresh: bool = False,
) -> tuple[AgentContext, RecommendationSet]:
    settings = get_settings()
    cache = _get_cache(settings)

    if force_refresh:
        logger.info("Re-analizando (noticias del día preservadas — se actualizan al cierre NYSE)")

    if tickers_usa is None or tickers_byma is None:
        default_usa, default_byma = _load_tickers()
        tickers_usa = tickers_usa or default_usa
        tickers_byma = tickers_byma or default_byma

    logger.info(f"Pipeline: USA={tickers_usa} | BYMA={tickers_byma}")

    news_hours = _calc_dynamic_news_hours()

    ctx = await context_agent.run(
        tickers_usa=tickers_usa,
        tickers_byma=tickers_byma,
        settings=settings,
        cache=cache,
        news_hours_back=news_hours,
    )

    recs = await analysis_agent.run(context=ctx, settings=settings)

    logger.info(f"Pipeline completo: {len(recs.recommendations)} recomendaciones generadas")
    return ctx, recs


async def analyze_emergency(tickers_usa: list[str]) -> tuple[AgentContext, RecommendationSet]:
    """Análisis de emergencia: datos en tiempo real, sin cache, solo los tickers dados."""
    settings = get_settings()
    logger.info(f"⚡ Análisis de emergencia: {tickers_usa}")
    ctx = await context_agent.run(
        tickers_usa=tickers_usa,
        tickers_byma=[],
        settings=settings,
        cache=None,
        news_hours_back=_NEWS_HOURS_MIN,
    )
    recs = await analysis_agent.run(context=ctx, settings=settings)
    return ctx, recs

