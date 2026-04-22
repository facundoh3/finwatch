"""
Utilidades compartidas entre agentes.
"""
import json
import re


def extract_json(text: str) -> dict:
    """
    Extrae JSON de la respuesta de cualquier LLM de forma robusta.
    Maneja: bloques markdown ```json, <think> reasoning tokens (Qwen3.6), texto antes/después.
    """
    # Quitar bloques de razonamiento <think>...</think> (Qwen3.6 y otros reasoning models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Quitar bloques markdown ```json ... ```
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Buscar el objeto JSON más externo
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No se encontró JSON en la respuesta: {text[:200]}")

    # Encontrar el cierre correcto contando llaves
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

    return json.loads(text[start:end])


def build_openrouter_client(api_key: str):
    """Cliente OpenAI-compatible apuntando a OpenRouter."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
