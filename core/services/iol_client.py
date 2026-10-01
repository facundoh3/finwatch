"""
Cliente REST para InvertirOnline (IOL).
API documentada en https://api.invertironline.com
Autenticación OAuth2 password-flow con refresh automático.
"""
import time
from dataclasses import dataclass, field

import httpx
from loguru import logger

from core.models.market import MarketSnapshot

_IOL_BASE = "https://api.invertironline.com"

# Mapa de nombres de mercado IOL → mercado finwatch
_MARKET_MAP = {
    "bCBA": "BCBA",
    "nYSE": "NYSE",
    "nASDAQ": "NASDAQ",
    "aMEX": "AMEX",
}


@dataclass
class _Token:
    access_token: str
    refresh_token: str
    expires_at: float  # time.monotonic()


@dataclass
class IOLClient:
    """
    Cliente async para la API REST de IOL.
    El token se renueva automáticamente antes de expirar.
    """
    username: str
    password: str
    _token: _Token | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------ auth

    async def _ensure_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token.expires_at - 60:
            return self._token.access_token
        if self._token and self._token.refresh_token:
            try:
                return await self._refresh(self._token.refresh_token)
            except Exception:
                pass
        return await self._login()

    async def _login(self) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_IOL_BASE}/token",
                data={
                    "grant_type": "password",
                    "username": self.username,
                    "password": self.password,
                    "scope": "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
        self._token = _Token(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.monotonic() + int(data.get("expires_in", 1800)),
        )
        logger.debug("IOL: token obtenido")
        return self._token.access_token

    async def _refresh(self, refresh_token: str) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_IOL_BASE}/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
        self._token = _Token(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.monotonic() + int(data.get("expires_in", 1800)),
        )
        logger.debug("IOL: token refrescado")
        return self._token.access_token

    # --------------------------------------------------------------- quotes

    async def get_quote(self, symbol: str, market: str = "bCBA", term: str = "t1") -> MarketSnapshot | None:
        """
        Precio de un instrumento. market: 'bCBA', 'nYSE', 'nASDAQ', 'aMEX'.
        term: 't0' (contado inmediato) | 't1' (24h) | 't2' (48h — default BCBA).
        """
        token = await self._ensure_token()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_IOL_BASE}/api/v2/{market}/Titulos/{symbol}/cotizacion",
                    params={"plazo": term},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
            return _parse_quote(symbol, data)
        except Exception as e:
            logger.debug(f"IOL quote {symbol}/{market}: {e}")
            return None

    async def get_byma_quotes(self, tickers: list[str]) -> list[MarketSnapshot]:
        """Cotizaciones BCBA para una lista de tickers. Descarta los que fallen."""
        results = []
        for ticker in tickers:
            snap = await self.get_quote(ticker, market="bCBA", term="t1")
            if snap:
                results.append(snap)
        return results

    # ------------------------------------------------------------ portfolio

    async def get_portfolio(self, country: str = "argentina") -> list[dict]:
        """
        Posiciones actuales en la cuenta.
        country: 'argentina' | 'estados_Unidos'
        Devuelve lista raw de IOL; el caller decide cómo mapearla.
        """
        token = await self._ensure_token()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_IOL_BASE}/api/v2/portafolio/{country}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
            return data.get("activos", [])
        except Exception as e:
            logger.warning(f"IOL portfolio error: {e}")
            return []


# ------------------------------------------------------------------ helpers

def _parse_quote(symbol: str, data: dict) -> MarketSnapshot | None:
    try:
        last = float(data.get("ultimoPrecio", 0) or 0)
        if last <= 0:
            return None
        prev = float(data.get("cierreAnterior", last) or last)
        change = last - prev
        change_pct = (change / prev * 100) if prev else 0.0
        return MarketSnapshot(
            ticker=symbol.upper(),
            current_price=last,
            previous_close=prev,
            change_amount=change,
            change_pct=change_pct,
            high_today=float(data.get("maximo", last) or last),
            low_today=float(data.get("minimo", last) or last),
            open_price=float(data.get("apertura", prev) or prev),
            volume=int(data.get("volumen", 0) or 0),
        )
    except Exception as e:
        logger.debug(f"IOL parse_quote {symbol}: {e}")
        return None
