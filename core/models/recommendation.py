from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from core.models.market import MarketOverview
from core.models.news import NewsCollection


class Action(str, Enum):
    BUY = "BUY"
    WAIT = "WAIT"
    AVOID = "AVOID"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Recommendation(BaseModel):
    """
    Recomendación de inversión generada por Claude.
    Claude responde en JSON y este modelo lo valida.
    """

    ticker: str
    action: Action
    wait_days: int | None = Field(
        default=None,
        ge=1,
        le=90,
        description="Solo presente si action == WAIT. Días sugeridos de espera.",
    )
    confidence: Confidence
    reasoning: str = Field(
        ...,
        min_length=20,
        description="Explicación en español simple de por qué se recomienda esta acción",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="URLs de las noticias que sustentan la recomendación",
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("wait_days", mode="before")
    @classmethod
    def validate_wait_days(cls, v, info) -> int | None:
        action = info.data.get("action")
        if action == Action.WAIT and v is None:
            raise ValueError("wait_days es requerido cuando action es WAIT")
        if action != Action.WAIT and v is not None:
            return None  # ignorar wait_days si no aplica
        return v

    def to_display_dict(self) -> dict:
        """Para renderizar en Streamlit sin lógica en el frontend."""
        action_emoji = {"BUY": "✅", "WAIT": "⏳", "AVOID": "❌"}
        confidence_color = {"HIGH": "green", "MEDIUM": "orange", "LOW": "red"}
        return {
            "ticker": self.ticker,
            "action_label": f"{action_emoji.get(self.action, '')} {self.action}",
            "wait_info": f"Esperar {self.wait_days} días" if self.wait_days else None,
            "confidence_color": confidence_color.get(self.confidence, "gray"),
            "confidence_label": self.confidence,
            "reasoning": self.reasoning,
            "sources": self.sources,
            "generated_at": self.generated_at.strftime("%d/%m/%Y %H:%M"),
        }


class RecommendationSet(BaseModel):
    """Conjunto de recomendaciones generado en un análisis."""

    recommendations: list[Recommendation] = Field(default_factory=list)
    market_summary: str = Field(
        default="",
        description="Resumen general del estado del mercado en español, generado por Claude",
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def get(self, ticker: str) -> Recommendation | None:
        ticker = ticker.upper()
        return next((r for r in self.recommendations if r.ticker == ticker), None)

    def by_action(self, action: Action) -> list[Recommendation]:
        return [r for r in self.recommendations if r.action == action]


class AgentContext(BaseModel):
    """
    Payload que el context_agent le entrega al analysis_agent.
    Es el punto de acoplamiento entre los dos agentes.
    Tiene que ser lo más compacto posible para minimizar tokens de Claude.
    """

    news: NewsCollection
    market: MarketOverview
    query_tickers: list[str]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def to_claude_prompt_block(self) -> str:
        """
        Genera el bloque de contexto comprimido que se le envía a Claude.
        Formato diseñado para máxima densidad de información con mínimos tokens.
        """
        tickers_str = ", ".join(self.query_tickers)
        news_bullets = self.news.to_context_bullets()
        market_block = self.market.to_context_block()

        return f"""TICKERS A ANALIZAR: {tickers_str}

PRECIOS ACTUALES:
{market_block}

NOTICIAS RELEVANTES (últimas {self.news.hours_back}hs):
{news_bullets}

FECHA DE ANÁLISIS: {self.timestamp.strftime("%d/%m/%Y %H:%M")} UTC"""
