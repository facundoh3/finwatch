"""
Analysis Agent — usa Claude Sonnet para generar recomendaciones de inversión.
Solo recibe el contexto comprimido del context_agent (<= 4000 tokens).
"""
import json
from pathlib import Path

import anthropic
from loguru import logger

from config.settings import Settings
from core.models.recommendation import AgentContext, Recommendation, RecommendationSet

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "analysis_agent.txt"
CLAUDE_MODEL = "claude-sonnet-4-6"


async def run(context: AgentContext, settings: Settings) -> RecommendationSet:
    """
    Genera recomendaciones de inversión usando Claude Sonnet.
    Recibe el AgentContext comprimido del context_agent.
    """
    if not settings.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY no configurada")
        return RecommendationSet(
            market_summary="Error: ANTHROPIC_API_KEY no configurada. Configurá .env para obtener análisis."
        )

    prompt_template = PROMPT_PATH.read_text()
    context_block = context.to_claude_prompt_block()
    prompt = prompt_template.format(context_block=context_block)

    logger.info(f"Enviando a Claude: ~{len(prompt.split())} palabras")

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text
        logger.info(f"Claude respondió: {message.usage.output_tokens} tokens")

        return _parse_response(response_text)

    except anthropic.APIError as e:
        logger.error(f"Error API Claude: {e}")
        return RecommendationSet(
            market_summary=f"Error al conectar con Claude: {str(e)}"
        )


def _parse_response(response_text: str) -> RecommendationSet:
    """Parsea la respuesta JSON de Claude a RecommendationSet."""
    try:
        # Claude puede incluir texto antes del JSON si falla el formato
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No se encontró JSON en la respuesta")

        data = json.loads(response_text[start:end])
        recommendations = []
        for r in data.get("recommendations", []):
            try:
                recommendations.append(Recommendation(
                    ticker=r["ticker"],
                    action=r["action"],
                    wait_days=r.get("wait_days"),
                    confidence=r["confidence"],
                    reasoning=r["reasoning"],
                    sources=r.get("sources", []),
                ))
            except Exception as e:
                logger.warning(f"Recomendación inválida descartada ({r.get('ticker')}): {e}")

        return RecommendationSet(
            recommendations=recommendations,
            market_summary=data.get("market_summary", ""),
        )
    except Exception as e:
        logger.error(f"Error parseando respuesta de Claude: {e}\nRespuesta: {response_text[:500]}")
        return RecommendationSet(
            market_summary="Error al interpretar la respuesta del análisis. Intentá de nuevo."
        )
