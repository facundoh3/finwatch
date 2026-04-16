from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, field_validator


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class NewsItem(BaseModel):
    """
    Noticia financiera con sentiment analysis.
    Puede venir de Finnhub o Marketaux — el cliente HTTP normaliza al mismo formato.
    """

    headline: str = Field(..., min_length=1)
    summary: str = Field(default="")
    source: str = Field(..., min_length=1)
    url: str = Field(..., description="URL de la noticia original")
    published_at: datetime
    sentiment_score: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Score de sentiment: -1.0 (muy negativo) a 1.0 (muy positivo)",
    )
    sentiment_label: SentimentLabel = Field(default=SentimentLabel.NEUTRAL)
    related_tickers: list[str] = Field(default_factory=list)
    impact_explanation: str | None = Field(
        default=None,
        description="Explicación en español generada por Claude sobre el impacto en el mercado",
    )

    @field_validator("related_tickers", mode="before")
    @classmethod
    def normalize_tickers(cls, v: list[str]) -> list[str]:
        """Asegura que todos los tickers estén en mayúsculas."""
        return [ticker.upper().strip() for ticker in v if ticker.strip()]

    @field_validator("sentiment_label", mode="before")
    @classmethod
    def derive_label_from_score(cls, v: str, info) -> SentimentLabel:
        """
        Si viene un label explícito lo usa. Si no, lo deriva del score.
        Útil cuando Finnhub da el score pero no el label.
        """
        if v and v != SentimentLabel.NEUTRAL:
            return SentimentLabel(v)
        score = info.data.get("sentiment_score", 0.0)
        if score >= 0.1:
            return SentimentLabel.POSITIVE
        if score <= -0.1:
            return SentimentLabel.NEGATIVE
        return SentimentLabel.NEUTRAL

    def to_context_bullet(self) -> str:
        """
        Convierte la noticia a un bullet point compacto para el payload del context_agent.
        Objetivo: minimizar tokens enviados a Claude.

        Ejemplo de output:
        [NEGATIVE] TSLA: Tesla recalls 50k vehicles over brake issue (reuters.com)
        """
        tickers_str = ", ".join(self.related_tickers) if self.related_tickers else "general"
        source_short = self.source.replace("www.", "").split("/")[0]
        return f"[{self.sentiment_label}] {tickers_str}: {self.headline} ({source_short})"


class NewsCollection(BaseModel):
    """Colección de noticias con metadata de la consulta."""

    items: list[NewsItem] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    tickers_queried: list[str] = Field(default_factory=list)
    hours_back: int = Field(default=24)

    def filter_by_ticker(self, ticker: str) -> list[NewsItem]:
        ticker = ticker.upper()
        return [n for n in self.items if ticker in n.related_tickers]

    def filter_by_sentiment(self, label: SentimentLabel) -> list[NewsItem]:
        return [n for n in self.items if n.sentiment_label == label]

    def to_context_bullets(self) -> str:
        """
        Convierte toda la colección a bullets compactos para el context_agent.
        Ordena por score: más negativos y positivos primero (los neutrales aportan menos).
        """
        sorted_items = sorted(self.items, key=lambda n: abs(n.sentiment_score), reverse=True)
        return "\n".join(item.to_context_bullet() for item in sorted_items)
