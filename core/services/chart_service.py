"""Historial de precios con indicadores técnicos (SMA20, SMA50)."""
import asyncio

import pandas as pd
from loguru import logger


def get_price_history(ticker: str, days: int = 60, interval: str = "1d") -> pd.DataFrame | None:
    """
    Descarga historial OHLCV y calcula SMA20/SMA50.
    interval="1h" → velas horarias (últimos 5 días de mercado, ignora `days`)
    interval="1d" → velas diarias (usa `days`)
    """
    try:
        import yfinance as yf
        period = "5d" if interval == "1h" else f"{days}d"
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df.empty:
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df["SMA20"] = df["Close"].rolling(20).mean()
        df["SMA50"] = df["Close"].rolling(50).mean()
        return df
    except Exception as e:
        logger.debug(f"chart_service: {ticker}: {e}")
        return None


async def get_histories(tickers: list[str], days: int = 60) -> dict[str, pd.DataFrame]:
    """Descarga historiales para múltiples tickers en paralelo usando thread pool."""
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *[loop.run_in_executor(None, get_price_history, t, days) for t in tickers],
        return_exceptions=True,
    )
    return {
        ticker: df
        for ticker, df in zip(tickers, results)
        if isinstance(df, pd.DataFrame)
    }


def get_sma_values(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """Retorna (sma20, sma50) del último registro no-nulo del dataframe."""
    sma20 = None
    sma50 = None
    if "SMA20" in df.columns:
        val = df["SMA20"].dropna()
        if not val.empty:
            sma20 = float(val.iloc[-1])
    if "SMA50" in df.columns:
        val = df["SMA50"].dropna()
        if not val.empty:
            sma50 = float(val.iloc[-1])
    return sma20, sma50
