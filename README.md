# finwatch

App financiera personal para análisis de mercado e inversión.

Analiza noticias globales, explica su impacto en acciones específicas
y sugiere timing de entrada en lenguaje simple, con fuentes.

## Arquitectura

```
Finnhub + Marketaux → context_agent (Qwen3.6, gratis)
                              ↓ contexto comprimido
                     analysis_agent (Claude Sonnet)
                              ↓ recomendaciones JSON
                     Streamlit Dashboard
```

El context_agent procesa el volumen de datos (noticias, precios, sentiment).
Claude solo recibe el resumen compacto y genera el análisis final.
Ahorro estimado: 70-80% de tokens vs enviarle todo directamente a Claude.

## Setup

```bash
# 1. Clonar y entrar
git clone <repo>
cd finwatch

# 2. Entorno virtual
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 3. Dependencias
pip install -r requirements.txt

# 4. Variables de entorno
cp .env.example .env
# Completar las API keys en .env

# 5. Correr la app
streamlit run frontend/app.py
```

## APIs necesarias (todas free tier)

| API | Para qué | Dónde obtener |
|-----|----------|---------------|
| Finnhub | Precios + noticias | finnhub.io |
| Marketaux | Noticias + sentiment | marketaux.com |
| OpenRouter | Qwen3.6 gratis | openrouter.ai |
| Anthropic | Claude análisis | console.anthropic.com |
| Alpha Vantage | Fallback | alphavantage.co |

## Stack

- Python 3.11+
- Streamlit (frontend)
- Pydantic v2 (modelos de datos)
- httpx (cliente HTTP)
- Anthropic SDK + OpenRouter (agentes IA)

## Estado del proyecto

- [ ] Fase 1: Core data layer
- [ ] Fase 2: Pipeline multiagente
- [ ] Fase 3: Frontend Streamlit
- [ ] Fase 4: Deploy local con Docker
