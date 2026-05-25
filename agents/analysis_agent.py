"""
Analysis Agent — genera recomendaciones BUY/WAIT/AVOID.
Usa race_models: corre 4 modelos en paralelo, toma el primero que responda (max 25s).
"""
from pathlib import Path

from loguru import logger

from agents.utils import build_openrouter_client, extract_json, get_free_models, race_models
from config.settings import Settings
from core.models.recommendation import Action, AgentContext, Recommendation, RecommendationSet

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "analysis_agent.txt"
CLAUDE_MODEL = "claude-sonnet-4-6"


async def run(context: AgentContext, settings: Settings) -> RecommendationSet:
    """
    Corre hasta 6 modelos en paralelo (max 25s). Si todos fallan, intenta Claude.
    """
    prompt = _build_prompt(context)

    if settings.openrouter_api_key:
        client = build_openrouter_client(settings.openrouter_api_key)
        models = await get_free_models(settings.openrouter_api_key)
        content = await race_models(
            client,
            messages=[{"role": "user", "content": prompt}],
            models=models,
            max_tokens=4000,
            temperature=0.3,
            total_timeout=25.0,
        )
        if content:
            result = _parse(content)
            if result and result.recommendations:
                logger.info(f"Análisis listo ({len(result.recommendations)} recomendaciones)")
                return result

    if settings.anthropic_api_key:
        result = await _run_claude(prompt, settings)
        if result and result.recommendations:
            return result

    return RecommendationSet(
        market_summary=(
            "Los modelos de IA no respondieron a tiempo. "
            "Esperá 2-3 minutos y volvé a analizar."
        )
    )


def _build_prompt(context: AgentContext) -> str:
    template = PROMPT_PATH.read_text()
    return template.replace("{context_block}", context.to_claude_prompt_block())


async def _run_claude(prompt: str, settings: Settings) -> RecommendationSet | None:
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text
        logger.info(f"Claude: {message.usage.output_tokens} tokens output")
        return _parse(response_text)
    except Exception as e:
        logger.warning(f"Claude error: {e}")
        return None


def _parse(text: str) -> RecommendationSet | None:
    try:
        data = extract_json(text)
        recommendations = []
        for r in data.get("recommendations", []):
            try:
                recommendations.append(Recommendation(
                    ticker=r["ticker"],
                    action=r["action"],
                    wait_days=r.get("wait_days"),
                    confidence=r.get("confidence", "LOW"),
                    reasoning=r.get("reasoning", "Sin detalle"),
                    sources=r.get("sources", []),
                ))
            except Exception as e:
                logger.debug(f"Recomendación descartada ({r.get('ticker')}): {e}")
        return RecommendationSet(
            recommendations=recommendations,
            market_summary=data.get("market_summary", ""),
        )
    except Exception as e:
        logger.error(f"Error parseando respuesta: {e}")
        return None
