# finwatch — estructura del proyecto

```
finwatch/
│
├── project-spec.yaml          # SDD: fuente de verdad del proyecto
│
├── agents/                    # Agentes de IA
│   ├── __init__.py
│   ├── context_agent.py       # Qwen3.6: fetch + filtrado + resumen
│   ├── analysis_agent.py      # Claude: análisis + recomendaciones
│   └── orchestrator.py        # Conecta ambos agentes, maneja el flujo
│
├── core/
│   ├── models/                # Pydantic models (NewsItem, Recommendation, etc.)
│   │   ├── __init__.py
│   │   ├── news.py
│   │   ├── market.py
│   │   └── recommendation.py
│   └── services/              # Clientes HTTP para APIs externas
│       ├── __init__.py
│       ├── finnhub_client.py
│       ├── marketaux_client.py
│       └── cache_service.py
│
├── frontend/
│   ├── app.py                 # Entry point de Streamlit
│   ├── pages/
│   │   ├── 1_dashboard.py     # Market overview + status global
│   │   ├── 2_noticias.py      # Noticias con impacto explicado
│   │   └── 3_recomendaciones.py
│   └── components/
│       ├── ticker_card.py     # Componente reutilizable por ticker
│       ├── news_card.py
│       └── tradingview_widget.py  # Wrapper para embed de TradingView
│
├── config/
│   ├── settings.py            # Pydantic Settings (lee .env)
│   ├── prompts/
│   │   ├── context_agent.txt  # Prompt template para Qwen
│   │   └── analysis_agent.txt # Prompt template para Claude
│   └── tickers.yaml           # Lista de tickers a monitorear
│
├── data/
│   ├── cache/                 # Cache JSON local (gitignored)
│   └── raw/                   # Respuestas crudas de APIs (dev only)
│
├── tests/
│   ├── unit/
│   │   ├── test_models.py
│   │   └── test_cache.py
│   └── integration/
│       ├── test_finnhub.py
│       └── test_pipeline.py
│
├── docs/
│   ├── arquitectura.md        # Diagrama y decisiones de diseño
│   └── apis.md                # Docs de las APIs usadas + rate limits
│
├── scripts/
│   └── setup.sh               # Script de inicialización
│
├── .env.example               # Variables de entorno documentadas
├── .gitignore
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Decisiones de diseño

**Sin base de datos**: Para uso personal con < 10 tickers, un cache JSON
en disco es más que suficiente. Elimina complejidad de setup.

**Streamlit**: Frontend en Python puro. No hay que mantener un backend
separado + un frontend separado. Todo en un proceso.

**Cache local**: Evita agotar el free tier de las APIs en desarrollo.
TTL configurable en project-spec.yaml.

**Prompts versionados en /config**: Permite iterar los prompts sin tocar
el código. Cada agente lee su template desde disco.

**Qwen hace el volumen, Claude hace el juicio**: Claude nunca ve noticias
crudas. Solo recibe bullet points ya filtrados. Ahorro estimado: 70-80%
de tokens vs mandarle todo directamente.
