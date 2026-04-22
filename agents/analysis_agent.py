"""
Analysis Agent — pipeline de análisis multimodelo.

Orden de preferencia:
  1. Claude Sonnet (si ANTHROPIC_API_KEY tiene créditos)
  2. Qwen3.6 via OpenRouter (gratis, 1M contexto) ← default sin Anthropic
  3. Error claro si ninguno está disponible
"""
from pathlib import Path

from loguru import logger

from agents.utils import build_openrouter_client, extract_json
from config.settings import Settings
from core.models.recommendation import AgentContext, Recommendation, RecommendationSet

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "analysis_agent.txt"

# Modelos disponibles via OpenRouter (todos gratis)
QWEN_MODEL = "qwen/qwen3.6-plus-preview:free"
LLAMA_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


async def run(context: AgentContext, settings: Settings) -> RecommendationSet:
    """
    Genera recomendaciones. Intenta Claude primero, cae en Qwen si no hay créditos.
    """
    prompt = _build_prompt(context)

    # Intentar con Claude si está configurado
    if settings.anthropic_api_key:
        result = await _run_claude(prompt, settings)
        if result is not None:
            return result
        logger.warning("Claude falló — usando Qwen como fallback")

    # Qwen via OpenRouter (gratis)
    if settings.openrouter_api_key:
        result = await _run_openrouter(prompt, settings, QWEN_MODEL)
        if result is not None:
            return result

    return RecommendationSet(
        market_summary="No hay modelos disponibles. Configurá ANTHROPIC_API_KEY o OPENROUTER_API_KEY en .env"
    )


def _build_prompt(context: AgentContext) -> str:
    template = PROMPT_PATH.read_text()
    return template.format(context_block=context.to_claude_prompt_block())


async def _run_claude(prompt: str, settings: Settings) -> RecommendationSet | None:
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text
        logger.info(f"Claude: {message.usage.output_tokens} tokens output")
        return _parse(response_text)
    except Exception as e:
        logger.warning(f"Claude error: {e}")
        return None


async def _run_openrouter(prompt: str, settings: Settings, model: str) -> RecommendationSet | None:
    try:
        client = build_openrouter_client(settings.openrouter_api_key)
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.3,
        )
        response_text = response.choices[0].message.content
        logger.info(f"{model}: respuesta recibida")
        return _parse(response_text)
    except Exception as e:
        logger.warning(f"{model} error: {e}")
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
        logger.error(f"Error parseando respuesta del análisis: {e}")
        return None
