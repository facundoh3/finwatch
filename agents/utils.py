"""
Utilidades compartidas entre agentes.
"""
import asyncio
import json
import re
import time

from loguru import logger

_models_cache: dict = {"models": [], "ts": 0.0}

_FALLBACK_FREE_MODELS = [
    "minimax/minimax-m1:extended",
    "microsoft/mai-ds-r1:free",
    "deepseek/deepseek-r1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-flash-1.5:free",
]

# Venice aloja: llama-3.3, qwen3, dolphin — los pone al final porque aguantan la conexión 30-60s
# Google AI Studio (Gemma): rate-limit frecuente → al final también
_PREFER = [
    "minimax",
    "mai-ds",
    "deepseek-r1",
    "mistral",
    "nemotron",
    "deepseek-v4",
    "llama-3.3",
    "qwen3",
    "dolphin",
    "gemma",
]


async def race_models(
    client,
    messages: list[dict],
    models: list[str],
    max_tokens: int = 4000,
    temperature: float | None = None,
    total_timeout: float = 20.0,
    per_model_timeout: float = 12.0,
) -> str | None:
    """
    Corre los primeros 6 modelos EN PARALELO y retorna la primera respuesta válida.
    Cancela el resto cuando uno gana. Total máximo = total_timeout segundos,
    sin importar cuántos modelos Venice/Google estìn colgados.
    """
    racing = models[:6]
    logger.debug(f"race_models: compitiendo {racing}")

    async def _call(model: str) -> tuple[str, str]:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": per_model_timeout,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = await client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content
        if not content:
            raise ValueError("respuesta vacía")
        return content, model

    tasks = [asyncio.create_task(_call(m)) for m in racing]
    task_model = {t: m for t, m in zip(tasks, racing)}
    pending = set(tasks)

    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total_timeout
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning("race_models: tiempo total agotado")
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    content, model = task.result()
                    logger.info(f"Modelo ganador: {model}")
                    return content
                except Exception as e:
                    logger.warning(f"{task_model.get(task, '?')} falló: {e}")
    finally:
        for t in pending:
            t.cancel()

    return None


async def get_free_models(api_key: str) -> list[str]:
    global _models_cache
    now = time.time()
    if _models_cache["models"] and now - _models_cache["ts"] < 600:
        return _models_cache["models"]

    models = await _fetch_openrouter_free_models(api_key)
    if models:
        _models_cache = {"models": models, "ts": now}
        logger.debug(f"OpenRouter: {len(models)} modelos gratuitos disponibles")
    else:
        logger.warning("No se pudo obtener lista de modelos — usando lista estática")
        models = _FALLBACK_FREE_MODELS

    return models


async def _fetch_openrouter_free_models(api_key: str) -> list[str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])

        free = []
        for m in data:
            mid = m.get("id", "")
            arch = m.get("architecture", {})
            modality = arch.get("modality", "text->text")
            if ":free" not in mid:
                continue
            if "->text" not in modality:
                continue
            free.append(mid)

        def _priority(mid: str) -> int:
            for i, pat in enumerate(_PREFER):
                if pat in mid:
                    return i
            return len(_PREFER)

        free.sort(key=_priority)
        return free

    except Exception as e:
        logger.warning(f"Error obteniendo modelos OpenRouter: {e}")
        return []


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    start = text.find("{")
    if start == -1:
        raise ValueError(f"No se encontró JSON en la respuesta: {text[:200]}")

    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        raise ValueError("JSON incompleto — llaves sin cerrar")

    candidate = text[start:end]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    fixed = candidate
    fixed = re.sub(r"\bNone\b", "null", fixed)
    fixed = re.sub(r"\bTrue\b", "true", fixed)
    fixed = re.sub(r"\bFalse\b", "false", fixed)
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)

    return json.loads(fixed)


def build_openrouter_client(api_key: str):
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=15.0,
    )


def build_groq_client(api_key: str):
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key,
        timeout=30.0,
    )
