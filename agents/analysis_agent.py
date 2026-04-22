"""
Analysis Agent — pipeline de análisis multimodelo.

Prueba modelos gratuitos de OpenRouter en orden hasta obtener resultados.
Si se obtienen 2 resultados, se fusionan por consenso.
Claude es último recurso (requiere créditos).
"""
from pathlib import Path

from loguru import logger

from agents.utils import build_openrouter_client, extract_json, get_free_models
from config.settings import Settings
from core.models.recommendation import Action, AgentContext, Recommendation, RecommendationSet

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "analysis_agent.txt"
CLAUDE_MODEL = "claude-sonnet-4-6"


async def run(context: AgentContext, settings: Settings) -> RecommendationSet:
    """
    Prueba modelos gratuitos en orden. Para después de conseguir 2 resultados
    válidos (para consenso) o devuelve el primero que funcione.
    Claude sólo si todos los gratuitos fallan y hay créditos disponibles.
    """
    prompt = _build_prompt(context)
    results: list[RecommendationSet] = []

    if settings.openrouter_api_key:
        models = await get_free_models(settings.openrouter_api_key)
        for model in models:
            if len(results) >= 2:
                break
            result = await _run_openrouter(prompt, settings, model)
            if result and result.recommendations:
                logger.info(f"Modelo exitoso: {model} ({len(result.recommendations)} recomendaciones)")
                results.append(result)

    if not results and settings.anthropic_api_key:
        result = await _run_claude(prompt, settings)
        if result and result.recommendations:
            results.append(result)

    if not results:
        return RecommendationSet(
            market_summary=(
                "Los modelos gratuitos están temporalmente no disponibles (rate limit). "
                "Esperá unos minutos y volvé a analizar."
            )
        )

    if len(results) == 1:
        return results[0]

    return _merge_by_consensus(results)


def _build_prompt(context: AgentContext) -> str:
    template = PROMPT_PATH.read_text()
    return template.replace("{context_block}", context.to_claude_prompt_block())


def _merge_by_consensus(results: list[RecommendationSet]) -> RecommendationSet:
    """Fusiona recomendaciones por mayoría de votos. Empate → WAIT conservador."""
    all_tickers: set[str] = set()
    for rs in results:
        for rec in rs.recommendations:
            all_tickers.add(rec.ticker)

    merged: list[Recommendation] = []
    for ticker in all_tickers:
        ticker_recs: list[Recommendation] = []
        for rs in results:
            for rec in rs.recommendations:
                if rec.ticker == ticker:
                    ticker_recs.append(rec)
                    break

        if not ticker_recs:
            continue

        actions = [r.action for r in ticker_recs]
        if len(set(a.value for a in actions)) == 1:
            merged.append(ticker_recs[0])
        else:
            action_counts: dict[str, int] = {}
            for a in actions:
                action_counts[a.value] = action_counts.get(a.value, 0) + 1
            majority_action = max(action_counts, key=lambda k: action_counts[k])
            best = ticker_recs[0]
            disagreement_note = (
                f"[Modelos discrepan: {'/'.join(a.value for a in actions)}] {best.reasoning}"
            )
            try:
                action_enum = Action(majority_action)
            except ValueError:
                action_enum = Action.WAIT
            merged.append(best.model_copy(update={
                "action": action_enum,
                "reasoning": disagreement_note,
                "confidence": "LOW",
            }))

    summary = next((rs.market_summary for rs in results if rs.market_summary), "")
    return RecommendationSet(
        recommendations=merged,
        market_summary=f"{summary} [Consenso: {len(results)} modelos]",
    )


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
        logger.debug(f"{model} raw response (first 300): {repr(response_text[:300])}")
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
        logger.error(f"Error parseando respuesta: {e}")
        return None
