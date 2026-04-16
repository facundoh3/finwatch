from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    source_tier: Literal["A", "B", "C"] = Field(
        default="B",
        description="A=Reuters/Bloomberg/WSJ, B=Finnhub/otros agregadores, C=desconocida",
    )
    corroborated_by: int = Field(
        default=1,
        ge=1,
        description="Cantidad de fuentes independientes que reportan la misma noticia",
    )

    @field_validator("related_tickers", mode="before")
    @classmethod
    def normalize_tickers(cls, v: list[str]) -> list[str]:
        """Asegura que todos los tickers estén en mayúsculas."""
        return [ticker.upper().strip() for ticker in v if ticker.strip()]

    @model_validator(mode="after")
    def derive_label_from_score(self) -> "NewsItem":
        """
        Deriva el sentiment_label del score si no fue explícitamente sobrescrito.
        En Pydantic v2, @field_validator no corre sobre defaults — se usa model_validator.
        """
        if self.sentiment_label == SentimentLabel.NEUTRAL:
            if self.sentiment_score >= 0.1:
                object.__setattr__(self, "sentiment_label", SentimentLabel.POSITIVE)
            elif self.sentiment_score <= -0.1:
                object.__setattr__(self, "sentiment_label", SentimentLabel.NEGATIVE)
        return self

    def to_context_bullet(self) -> str:
        """
        Convierte la noticia a un bullet point compacto para el payload del context_agent.
        Objetivo: minimizar tokens enviados a Claude.

        Ejemplo de output:
        [NEGATIVE] TSLA: Tesla recalls 50k vehicles over brake issue (reuters.com)
        """
        from urllib.parse import urlparse
        tickers_str = ", ".join(self.related_tickers) if self.related_tickers else "general"
        try:
            domain = urlparse(self.url).netloc.replace("www.", "") or self.source
        except Exception:
            domain = self.source
        return f"[{self.sentiment_label.value}] {tickers_str}: {self.headline} ({domain})"


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
