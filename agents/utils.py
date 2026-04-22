"""
Utilidades compartidas entre agentes.
"""
import json
import re
import time

from loguru import logger

# Cache de modelos gratuitos disponibles (TTL 10 minutos)
_models_cache: dict = {"models": [], "ts": 0.0}

# Fallback estático — solo el que confirmamos que existe (aunque esté rate-limited)
_FALLBACK_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.1-70b-instruct:free",
    "google/gemini-flash-1.5:free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat:free",
]

# Patrones de modelos preferidos para ordenar (más capaz primero)
_PREFER = [
    "llama-3.3",
    "llama-3.1-70b",
    "qwen3",
    "qwen-2.5-72b",
    "gemini",
    "deepseek",
    "mistral",
    "phi",
]


async def get_free_models(api_key: str) -> list[str]:
    """
    Devuelve la lista de modelos gratuitos disponibles en OpenRouter ahora mismo.
    Cachea el resultado 10 minutos para no repetir la llamada.
    """
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
            pricing = m.get("pricing", {})
            prompt_cost = str(pricing.get("prompt", "1"))
            completion_cost = str(pricing.get("completion", "1"))

            # Solo modelos con sufijo :free explícito (excluye Lyria y otros sin sufijo)
            if ":free" not in mid:
                continue
            # Solo modelos que generan texto como output
            if "->text" not in modality:
                continue

            free.append(mid)

        # Ordenar por preferencia de calidad
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
    """
    Extrae JSON de la respuesta de cualquier LLM de forma robusta.
    Maneja: <think> reasoning tokens, bloques markdown, texto extra, errores comunes de LLMs.
    """
    # Quitar bloques de razonamiento <think>...</think> (Qwen, DeepSeek R1)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    # Quitar bloques markdown ```json ... ```
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

    # Intentar parse directo
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Reparar errores comunes de LLMs
    fixed = candidate
    fixed = re.sub(r"\bNone\b", "null", fixed)
    fixed = re.sub(r"\bTrue\b", "true", fixed)
    fixed = re.sub(r"\bFalse\b", "false", fixed)
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)   # trailing commas

    return json.loads(fixed)


def build_openrouter_client(api_key: str):
    """Cliente OpenAI-compatible apuntando a OpenRouter."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
