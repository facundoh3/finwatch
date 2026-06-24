"""
Tests unitarios para agents/context_agent.py.
Solo cubre lógica pura (formateo) — el resto del módulo depende de red.
Correr con: pytest tests/unit/test_context_agent.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from agents.context_agent import _enrich_with_technicals, _format_technical_line
from core.models.market import MarketOverview, MarketSnapshot
from core.services.technical_service import TechnicalReport, TechnicalSignal


def test_format_technical_line_all_signals_ok():
    report = TechnicalReport(
        ticker="GOOGL",
        signals=[TechnicalSignal("SMA50 (O'Neil)", True, "")],
        score=1,
        max_score=1,
        stage="Stage 2 (alcista)",
    )
    line = _format_technical_line(report)
    assert line.startswith("GOOGL: Stage 2 (alcista)")
    assert "todas las señales OK" in line
    assert "falla" not in line


def test_format_technical_line_lists_failed_signals():
    report = TechnicalReport(
        ticker="TSLA",
        signals=[
            TechnicalSignal("Weinstein Stage 2", False, ""),
            TechnicalSignal("RSI 14", False, ""),
            TechnicalSignal("SMA50 (O'Neil)", True, ""),
        ],
        score=1,
        max_score=3,
        stage="Stage 4 (bajista)",
    )
    line = _format_technical_line(report)
    assert "Stage 4 (bajista)" in line
    assert "falla: Weinstein Stage 2, RSI 14" in line
    assert "SMA50" not in line.split("falla:")[1]


def _make_snapshot(ticker: str, price: float) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        current_price=price,
        previous_close=price,
        change_amount=0.0,
        change_pct=0.0,
        high_today=price,
        low_today=price,
        open_price=price,
        volume=1_000_000,
    )


class TestEnrichWithTechnicals:
    """
    Regresión: GGAL/BBAR/LOMA cotizan como ADR en USA (USD) y en BYMA (ARS)
    con el mismo string de ticker. _fetch_market_data agrega el snapshot de
    USA antes que el de BYMA, así que market.get(ticker) debe devolver (y
    enriquecer) el snapshot en dólares — nunca el de pesos, aunque comparta
    el mismo nombre de ticker.
    """

    @pytest.mark.asyncio
    async def test_only_usa_snapshot_gets_enriched_when_ticker_is_duplicated(self):
        usa_snapshot = _make_snapshot("GGAL", price=35.0)        # ADR en USD
        byma_snapshot = _make_snapshot("GGAL", price=7200.0)     # mismo ticker, en ARS
        market = MarketOverview(snapshots=[usa_snapshot, byma_snapshot])

        fake_df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
        fake_report = TechnicalReport(
            ticker="GGAL",
            signals=[TechnicalSignal("SMA50 (O'Neil)", True, "")],
            score=1,
            max_score=1,
            stage="Stage 2 (alcista)",
        )

        with patch(
            "core.services.chart_service.get_histories",
            new=AsyncMock(return_value={"GGAL": fake_df}),
        ), patch(
            "core.services.chart_service.get_sma_values",
            new=MagicMock(return_value=(34.0, 32.0)),
        ), patch(
            "core.services.technical_service.analyze",
            new=MagicMock(return_value=fake_report),
        ):
            summary = await _enrich_with_technicals(market, tickers_usa=["GGAL"])

        # El snapshot ADR (USD) recibe los SMA calculados sobre su propio historial.
        assert usa_snapshot.sma20 == 34.0
        assert usa_snapshot.sma50 == 32.0

        # El snapshot BYMA (ARS) con el mismo ticker no debe tocarse.
        assert byma_snapshot.sma20 is None
        assert byma_snapshot.sma50 is None

        assert "GGAL" in summary
