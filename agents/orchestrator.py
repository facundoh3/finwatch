"""
Orquestador del pipeline multiagente finwatch.

Flujo:
  context_agent (Qwen3.6 gratis) → analysis_agent (Claude Sonnet) → RecommendationSet
"""
from pathlib import Path

import yaml
from loguru import logger

from agents import analysis_agent, context_agent
from config.settings import Settings, get_settings
from core.models.recommendation import AgentContext, RecommendationSet
from core.services.cache_service import CacheService

_cache: CacheService | None = None


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


async def analyze(
    tickers_usa: list[str] | None = None,
    tickers_byma: list[str] | None = None,
    force_refresh: bool = False,
) -> tuple[AgentContext, RecommendationSet]:
    """
    Ejecuta el pipeline completo.

    Args:
        tickers_usa: tickers de mercado USA. Si None, usa config/tickers.yaml.
        tickers_byma: tickers BYMA (mercado ARG). Si None, usa config/tickers.yaml.
        force_refresh: si True, ignora el cache.

    Returns:
        (AgentContext, RecommendationSet) listos para el frontend.
    """
    settings = get_settings()
    cache = _get_cache(settings)

    if force_refresh:
        logger.info("Forzando refresh del cache")
        cache.clear_all()

    if tickers_usa is None or tickers_byma is None:
        default_usa, default_byma = _load_tickers()
        tickers_usa = tickers_usa or default_usa
        tickers_byma = tickers_byma or default_byma

    logger.info(f"Pipeline: USA={tickers_usa} | BYMA={tickers_byma}")

    ctx = await context_agent.run(
        tickers_usa=tickers_usa,
        tickers_byma=tickers_byma,
        settings=settings,
        cache=cache,
    )

    recs = await analysis_agent.run(context=ctx, settings=settings)

    logger.info(
        f"Pipeline completo: {len(recs.recommendations)} recomendaciones generadas"
    )
    return ctx, recs
