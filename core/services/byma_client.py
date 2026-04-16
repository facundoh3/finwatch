"""
Cliente para BYMA BYMADATA — mercado argentino.
API pública y gratuita: https://open.bymadata.com.ar/
Soporta acciones locales, CEDEARs, bonos.
"""
from datetime import datetime

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from core.models.market import MarketSnapshot

BYMA_BASE = "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free"


class BYMAClient:
    """
    Cliente para el mercado de capitales argentino vía BYMADATA.
    No requiere API key — es la API pública oficial de BYMA.
    Cache interno de 5 min respetado por BYMA.
    """

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def get_equities(self) -> list[MarketSnapshot]:
        """Panel general de acciones BYMA (panel líder)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BYMA_BASE}/bnown/security/history",
                params={"excludeZeroPxAndQty": "false", "T2": "true", "T1": "false", "T0": "false"},
            )
            resp.raise_for_status()
            return self._parse_list(resp.json())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def get_cedears(self) -> list[MarketSnapshot]:
        """Lista de CEDEARs disponibles en BYMA con precios actuales."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BYMA_BASE}/cedears",
                params={"excludeZeroPxAndQty": "false"},
            )
            resp.raise_for_status()
            return self._parse_list(resp.json())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def get_quote(self, ticker: str) -> MarketSnapshot | None:
        """Precio de un ticker específico en BYMA."""
        snapshots = await self.get_equities()
        for snap in snapshots:
            if snap.ticker == ticker.upper():
                return snap
        # Intentar en CEDEARs si no está en acciones
        cedears = await self.get_cedears()
        for snap in cedears:
            if snap.ticker == ticker.upper():
                return snap
        logger.warning(f"Ticker BYMA no encontrado: {ticker}")
        return None

    def _parse_list(self, data: dict) -> list[MarketSnapshot]:
        snapshots = []
        securities = data.get("data", data if isinstance(data, list) else [])
        for item in securities:
            try:
                last = float(item.get("px", item.get("last", item.get("c", 0))) or 0)
                if last <= 0:
                    continue
                prev = float(item.get("previousClosingPrice", item.get("ppc", last)) or last)
                change = last - prev
                change_pct = (change / prev * 100) if prev else 0.0
                ticker = (
                    item.get("symbolCode", item.get("symbol", item.get("ticker", "")))
                    .strip()
                    .upper()
                )
                if not ticker:
                    continue
                snapshots.append(MarketSnapshot(
                    ticker=ticker,
                    current_price=last,
                    previous_close=prev or last,
                    change_amount=change,
                    change_pct=change_pct,
                    high_today=float(item.get("max", last) or last),
                    low_today=float(item.get("min", last) or last),
                    open_price=float(item.get("openingPrice", prev) or prev),
                    volume=int(item.get("nominalVolume", item.get("volume", 0)) or 0),
                ))
            except Exception as e:
                logger.debug(f"BYMA item descartado: {e}")
        return snapshots
