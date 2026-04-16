from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class MarketStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PRE_MARKET = "PRE_MARKET"
    AFTER_HOURS = "AFTER_HOURS"


class PriceDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class MarketSnapshot(BaseModel):
    """
    Snapshot de precio de un ticker en un momento dado.
    Normalizado desde la respuesta de Finnhub /quote.
    """

    ticker: str
    current_price: float = Field(..., gt=0)
    previous_close: float = Field(..., gt=0)
    change_amount: float = Field(description="Diferencia en dólares respecto al cierre anterior")
    change_pct: float = Field(description="Cambio porcentual respecto al cierre anterior")
    high_today: float = Field(..., gt=0)
    low_today: float = Field(..., gt=0)
    open_price: float = Field(..., gt=0)
    volume: int = Field(default=0, ge=0)
    high_52w: float | None = Field(default=None)
    low_52w: float | None = Field(default=None)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, v: str) -> str:
        return v.upper().strip()

    @property
    def direction(self) -> PriceDirection:
        if self.change_pct > 0.1:
            return PriceDirection.UP
        if self.change_pct < -0.1:
            return PriceDirection.DOWN
        return PriceDirection.FLAT

    @property
    def is_near_52w_high(self) -> bool:
        """True si el precio está dentro del 5% del máximo anual."""
        if self.high_52w is None:
            return False
        return self.current_price >= self.high_52w * 0.95

    @property
    def is_near_52w_low(self) -> bool:
        """True si el precio está dentro del 5% del mínimo anual."""
        if self.low_52w is None:
            return False
        return self.current_price <= self.low_52w * 1.05

    def to_context_line(self) -> str:
        """
        Línea compacta para el payload del context_agent.

        Ejemplo:
        TSLA: $245.30 (-2.4%) | Vol: 89M | 52w: $138.80 - $299.29
        """
        direction_symbol = "+" if self.change_pct >= 0 else ""
        vol_str = f"{self.volume / 1_000_000:.1f}M" if self.volume >= 1_000_000 else str(self.volume)
        line = f"{self.ticker}: ${self.current_price:.2f} ({direction_symbol}{self.change_pct:.1f}%) | Vol: {vol_str}"
        if self.high_52w and self.low_52w:
            line += f" | 52w: ${self.low_52w:.2f} - ${self.high_52w:.2f}"
        return line


class MarketOverview(BaseModel):
    """Conjunto de snapshots para todos los tickers monitoreados."""

    snapshots: list[MarketSnapshot] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    def get(self, ticker: str) -> MarketSnapshot | None:
        ticker = ticker.upper()
        return next((s for s in self.snapshots if s.ticker == ticker), None)

    def top_movers(self, n: int = 3) -> list[MarketSnapshot]:
        """Retorna los n tickers con mayor movimiento (en valor absoluto)."""
        return sorted(self.snapshots, key=lambda s: abs(s.change_pct), reverse=True)[:n]

    def to_context_block(self) -> str:
        """
        Bloque compacto de precios para el context_agent.
        Un ticker por línea, ordenados por movimiento absoluto.
        """
        sorted_snapshots = sorted(self.snapshots, key=lambda s: abs(s.change_pct), reverse=True)
        return "\n".join(s.to_context_line() for s in sorted_snapshots)
