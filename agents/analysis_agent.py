"""
Analysis Agent — genera recomendaciones BUY/WAIT/AVOID.
Consenso: Groq (primario) + DeepSeek (secundario) en paralelo, voto mayoritario por ticker.
Si los modelos no coinciden → WAIT (postura conservadora).
Fallback: Claude → OpenRouter (último recurso).
"""
import asyncio
from pathlib import Path

from loguru import logger

from agents.utils import build_groq_client, build_openrouter_client, extract_json, get_free_models, race_models
from config.settings import Settings
from core.models.recommendation import Action, AgentContext, Confidence, Recommendation, RecommendationSet

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "analysis_agent.txt"
CLAUDE_MODEL = "claude-sonnet-4-6"
GROQ_MODEL = "llama-3.3-70b-versatile"
DEEPSEEK_MODEL = "deepseek-chat"  # V3: muy bueno, 4x más barato que R1 (reasoner)


async def run(context: AgentContext, settings: Settings) -> RecommendationSet:
    prompt = _build_prompt(context)

    # Consenso: Groq + DeepSeek en paralelo (ambos confiables y rápidos)
    tasks = []
    labels = []

    if settings.groq_api_key:
        tasks.append(_run_groq(prompt, settings))
        labels.append("groq")

    if settings.deepseek_api_key:
        tasks.append(_run_deepseek(prompt, settings))
        labels.append("deepseek")
    elif settings.openrouter_api_key:
        # DeepSeek no configurado: OpenRouter como secundario (menos confiable)
        tasks.append(_run_openrouter(prompt, settings))
        labels.append("openrouter")

    if not tasks:
        if settings.anthropic_api_key:
            return await _run_claude(prompt, settings) or _fallback()
        return _fallback()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid = []
    for label, r in zip(labels, results):
        if isinstance(r, RecommendationSet) and r.recommendations:
            logger.info(f"Consenso: {label} respondió con {len(r.recommendations)} recomendaciones")
            valid.append((label, r))
        elif isinstance(r, Exception):
            logger.warning(f"Consenso: {label} falló: {r}")

    if not valid:
        if settings.anthropic_api_key:
            return await _run_claude(prompt, settings) or _fallback()
        if settings.openrouter_api_key and "openrouter" not in labels:
            return await _run_openrouter(prompt, settings) or _fallback()
        return _fallback()

    if len(valid) == 1:
        return valid[0][1]

    # Dos resultados: voto mayoritario
    label_a, recs_a = valid[0]
    label_b, recs_b = valid[1]
    logger.info(f"Aplicando consenso: {label_a} vs {label_b}")
    return _merge_consensus(recs_a, recs_b)


def _merge_consensus(primary: RecommendationSet, secondary: RecommendationSet) -> RecommendationSet:
    """
    Compara ticker por ticker.
    Acuerdo → mantiene recomendación original.
    Desacuerdo → WAIT conservador con confidence LOW.
    """
    secondary_map = {r.ticker: r for r in secondary.recommendations}
    merged = []
    agreements = 0
    disagreements = 0

    for rec in primary.recommendations:
        sec = secondary_map.get(rec.ticker)
        if sec and sec.action != rec.action:
            disagreements += 1
            merged.append(rec.model_copy(update={
                "action": Action.WAIT,
                "confidence": Confidence.LOW,
                "reasoning": (
                    f"[Modelos en desacuerdo — postura conservadora] "
                    f"Modelo 1: {rec.action.value}. Modelo 2: {sec.action.value}. "
                    f"{rec.reasoning[:200]}"
                ),
                "wait_days": rec.wait_days or 5,
            }))
        else:
            agreements += 1
            merged.append(rec)

    logger.info(f"Consenso: {agreements} acuerdos, {disagreements} desacuerdos")
    return RecommendationSet(recommendations=merged, market_summary=primary.market_summary)


def _build_prompt(context: AgentContext) -> str:
    return PROMPT_PATH.read_text().replace("{context_block}", context.to_claude_prompt_block())


async def _run_groq(prompt: str, settings: Settings) -> RecommendationSet | None:
    try:
        client = build_groq_client(settings.groq_api_key)
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.0,
        )
        text = response.choices[0].message.content
        logger.info(f"Groq ({GROQ_MODEL}): {response.usage.completion_tokens} tokens")
        return _parse(text)
    except Exception as e:
        logger.warning(f"Groq error: {e}")
        return None


async def _run_deepseek(prompt: str, settings: Settings) -> RecommendationSet | None:
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=settings.deepseek_api_key,
            timeout=30.0,
        )
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.0,
        )
        text = response.choices[0].message.content
        logger.info(f"DeepSeek ({DEEPSEEK_MODEL}): {response.usage.completion_tokens} tokens")
        return _parse(text)
    except Exception as e:
        logger.warning(f"DeepSeek error: {e}")
        return None


async def _run_openrouter(prompt: str, settings: Settings) -> RecommendationSet | None:
    try:
        client = build_openrouter_client(settings.openrouter_api_key)
        models = await get_free_models(settings.openrouter_api_key)
        content = await race_models(
            client,
            messages=[{"role": "user", "content": prompt}],
            models=models,
            max_tokens=2000,
            temperature=0.0,
            total_timeout=25.0,
        )
        return _parse(content) if content else None
    except Exception as e:
        logger.warning(f"OpenRouter error: {e}")
        return None


async def _run_claude(prompt: str, settings: Settings) -> RecommendationSet | None:
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        logger.info(f"Claude: {message.usage.output_tokens} tokens")
        return _parse(message.content[0].text)
    except Exception as e:
        logger.warning(f"Claude error: {e}")
        return None


def _fallback() -> RecommendationSet:
    return RecommendationSet(
        market_summary="Los modelos de IA no respondieron. Configurá GROQ_API_KEY en .env."
    )


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
