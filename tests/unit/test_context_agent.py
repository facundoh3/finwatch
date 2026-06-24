"""
Tests unitarios para agents/context_agent.py.
Solo cubre lógica pura (formateo) — el resto del módulo depende de red.
Correr con: pytest tests/unit/test_context_agent.py -v
"""

from agents.context_agent import _format_technical_line
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
