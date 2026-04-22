"""
Analysis Agent — pipeline de análisis multimodelo.

Orden de ejecución:
  1. Gemini Flash 2.0 via OpenRouter (gratis, análisis principal)
  2. Llama 3.3 70B via OpenRouter (gratis, verificación/consenso)
  3. Claude Sonnet (si ANTHROPIC_API_KEY disponible, desempate)
  Fusiona resultados por consenso de acción.
"""
from pathlib import Path

from loguru import logger

from agents.utils import build_openrouter_client, extract_json
from config.settings import Settings
from core.models.recommendation import AgentContext, Recommendation, RecommendationSet, Action

PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "analysis_agent.txt"

GEMINI_MODEL = "google/gemini-2.0-flash-exp:free"
LLAMA_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
CLAUDE_MODEL = "claude-sonnet-4-6"


async def run(context: AgentContext, settings: Settings) -> RecommendationSet:
    """
    Pipeline multimodelo: Gemini Flash → Llama 3.3 70B → fusión por consenso.
    Claude actúa como desempate si ANTHROPIC_API_KEY está disponible.
    """
    prompt = _build_prompt(context)
    results: list[RecommendationSet] = []

    if settings.openrouter_api_key:
        gemini_result = await _run_openrouter(prompt, settings, GEMINI_MODEL)
        if gemini_result:
            logger.info(f"Gemini Flash: {len(gemini_result.recommendations)} recomendaciones")
            results.append(gemini_result)

        llama_result = await _run_openrouter(prompt, settings, LLAMA_MODEL)
        if llama_result:
            logger.info(f"Llama 3.3 70B: {len(llama_result.recommendations)} recomendaciones")
            results.append(llama_result)

    if settings.anthropic_api_key and len(results) < 2:
        claude_result = await _run_claude(prompt, settings)
        if claude_result:
            logger.info("Claude Sonnet: recomendaciones recibidas")
            results.append(claude_result)

    if not results:
        return RecommendationSet(
            market_summary="No hay modelos disponibles. Configurá OPENROUTER_API_KEY en .env"
        )

    if len(results) == 1:
        return results[0]

    return _merge_by_consensus(results)


def _build_prompt(context: AgentContext) -> str:
    template = PROMPT_PATH.read_text()
    return template.replace("{context_block}", context.to_claude_prompt_block())


def _merge_by_consensus(results: list[RecommendationSet]) -> RecommendationSet:
    """
    Fusiona recomendaciones de múltiples modelos por consenso de acción.
    Si coinciden → mantiene la del primer modelo con ese ticker.
    Si discrepan → usa WAIT como posición conservadora.
    """
    ticker_votes: dict[str, list[RecommendationSet]] = {}
    for rs in results:
        for rec in rs.recommendations:
            ticker_votes.setdefault(rec.ticker, []).append(rs)

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
            # Consenso — usar la primera recomendación (mejor contexto)
            merged.append(ticker_recs[0])
        else:
            # Discrepancia — posición conservadora
            action_counts: dict[str, int] = {}
            for a in actions:
                action_counts[a.value] = action_counts.get(a.value, 0) + 1
            majority_action = max(action_counts, key=lambda k: action_counts[k])
            best = ticker_recs[0]
            disagreement_note = f"[Modelos discrepan: {'/'.join(a.value for a in actions)}] {best.reasoning}"
            merged.append(best.model_copy(update={
                "action": Action(majority_action),
                "reasoning": disagreement_note,
                "confidence": "LOW",
            }))

    # Market summary: usar el del primer modelo que tenga uno
    summary = next((rs.market_summary for rs in results if rs.market_summary), "")
    model_note = f" [Consenso: {len(results)} modelos]" if len(results) > 1 else ""

    return RecommendationSet(
        recommendations=merged,
        market_summary=summary + model_note,
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
