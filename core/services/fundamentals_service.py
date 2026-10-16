"""
Números reales de las empresas desde SEC EDGAR (XBRL company facts): ventas,
ganancia por acción y su crecimiento contra el mismo trimestre del año anterior.
Completa la mitad "C" y "A" de CAN SLIM que el checklist técnico no ve.

La lógica de trimestres (Q4 derivado del 10-K, restatements, etiquetas fiscales)
está adaptada de trader-skill/trader-analyst/facts.py — MIT, (c) 2026 Jake
Schincariol, ver third_party/trader-skill/LICENSE.

Solo cubre empresas que reportan en us-gaap: ETFs y ADRs extranjeros que
presentan 20-F en IFRS (YPF, GGAL, BMA...) no tienen estos datos.
"""
import asyncio
import datetime as _dt
import threading
import time

import httpx
from loguru import logger

from core.models.fundamentals import FundamentalSnapshot
from core.services.cache_service import CacheService
from core.services.technical_service import TechnicalSignal

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

ONEIL_GROWTH_PCT = 25.0
_CACHE_TTL_MIN = 24 * 60
_TICKERS_TTL_MIN = 7 * 24 * 60
_NO_DATA_TTL_MIN = 30 * 24 * 60

CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
}
_FORMS = ("10-Q", "10-K", "20-F", "40-F")


# ------------------------------------------------------------- XBRL puro

def _date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def _facts_for(companyfacts: dict, concept: str, unit: str) -> list[dict]:
    node = companyfacts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    units = node.get("units", {})
    chosen = unit if unit in units else next(iter(units), None)
    return units.get(chosen, []) if chosen else []


def _periods(facts: list[dict]) -> dict[tuple[str, str], dict]:
    """{(inicio, fin): fact}. El valor sale del último filing (restatements ganan);
    la etiqueta fiscal (fy, fp) del primero que reportó ese período."""
    latest, first = {}, {}
    for f in facts:
        if "start" not in f or f.get("form", "").split("/")[0] not in _FORMS:
            continue
        key = (f["start"], f["end"])
        stamp = (f.get("filed", ""), f.get("accn", ""))
        if key not in latest or stamp > (latest[key].get("filed", ""), latest[key].get("accn", "")):
            latest[key] = f
        if key not in first or stamp < (first[key].get("filed", ""), first[key].get("accn", "")):
            first[key] = f
    return {key: dict(f, fy=first[key].get("fy"), fp=first[key].get("fp")) for key, f in latest.items()}


def _split(facts: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """(trimestres, años) indexados por fecha de fin. El Q4 se deriva: año − 3 trimestres."""
    quarters, years = {}, {}
    for (start, end), f in _periods(facts).items():
        days = (_date(end) - _date(start)).days
        row = {"value": f["val"], "start": start, "end": end, "derived": False,
               "fy": f.get("fy"), "fp": f.get("fp")}
        if 80 <= days <= 100:
            quarters[end] = row
        elif 350 <= days <= 380:
            years[end] = row
    for end, y in years.items():
        if end in quarters:
            continue
        inside = sorted((q for q in quarters.values() if q["start"] >= y["start"] and q["end"] < end),
                        key=lambda q: q["end"])
        if len(inside) == 3:
            quarters[end] = dict(y, value=y["value"] - sum(q["value"] for q in inside),
                                 start=(_date(inside[-1]["end"]) + _dt.timedelta(days=1)).isoformat(),
                                 derived=True, fp="Q4")
    return quarters, years


def _series(companyfacts: dict, key: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Trimestres y años de una métrica, probando sus conceptos en orden y llenando huecos."""
    unit = "USD/shares" if key == "eps_diluted" else "USD"
    quarters, years = {}, {}
    for concept in CONCEPTS[key]:
        q, y = _split(_facts_for(companyfacts, concept, unit))
        for end, v in q.items():
            quarters.setdefault(end, v)
        for end, v in y.items():
            years.setdefault(end, v)
    return quarters, years


def _year_ago(series: dict[str, dict], end: str, slack: int = 10) -> dict | None:
    target = _date(end) - _dt.timedelta(days=365)
    best = min(series, key=lambda e: abs((_date(e) - target).days), default=None)
    if best is None or abs((_date(best) - target).days) > slack:
        return None
    return series[best]


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def analyze_facts(ticker: str, companyfacts: dict) -> FundamentalSnapshot | None:
    rev_q, _ = _series(companyfacts, "revenue")
    ni_q, _ = _series(companyfacts, "net_income")
    eps_q, eps_y = _series(companyfacts, "eps_diluted")
    if not rev_q and not ni_q:
        return None

    end = max(set(rev_q) | set(ni_q))
    base = rev_q.get(end) or ni_q[end]
    revenue = rev_q.get(end, {}).get("value")
    net_income = ni_q.get(end, {}).get("value")
    eps = eps_q.get(end, {}).get("value")

    annual_growth, annual_fy = None, None
    fy_ends = sorted(eps_y)
    if len(fy_ends) >= 2:
        last, prev = eps_y[fy_ends[-1]], _year_ago(eps_y, fy_ends[-1])
        if prev:
            annual_growth = _pct(last["value"], prev["value"])
            annual_fy = f"FY{last.get('fy') or fy_ends[-1][:4]}"

    return FundamentalSnapshot(
        ticker=ticker,
        entity=companyfacts.get("entityName"),
        period_end=end,
        fiscal=f"{base.get('fp') or ''} {base.get('fy') or ''}".strip(),
        derived=base.get("derived", False),
        revenue=revenue,
        revenue_yoy=_pct(revenue, (_year_ago(rev_q, end) or {}).get("value")),
        eps=round(eps, 2) if eps is not None else None,
        eps_yoy=_pct(eps, (_year_ago(eps_q, end) or {}).get("value")),
        net_margin=round(100.0 * net_income / revenue, 1) if net_income is not None and revenue else None,
        annual_eps_growth=annual_growth,
        annual_fy=annual_fy,
    )


# ------------------------------------------------------------ señales O'Neil

def _growth_signal(name: str, value: float | None, label: str) -> TechnicalSignal:
    if value is None:
        return TechnicalSignal(name, False, f"Sin dato de {label}")
    ok = value >= ONEIL_GROWTH_PCT
    return TechnicalSignal(name, ok, f"{label} {value:+.1f}% (O'Neil pide +{ONEIL_GROWTH_PCT:.0f}%)")


def fundamental_signals(f: FundamentalSnapshot) -> list[TechnicalSignal]:
    return [
        _growth_signal("Ganancia trimestral (O'Neil C)", f.eps_yoy, "EPS vs mismo trimestre año anterior"),
        _growth_signal("Ganancia anual (O'Neil A)", f.annual_eps_growth, f"EPS {f.annual_fy or 'anual'} vs año anterior"),
        _growth_signal("Ventas (O'Neil)", f.revenue_yoy, "Ventas vs mismo trimestre año anterior"),
    ]


def format_line(f: FundamentalSnapshot) -> str:
    """Línea compacta para el prompt del analysis_agent."""
    def p(v):
        return "s/d" if v is None else f"{v:+.0f}%"

    signals = fundamental_signals(f)
    ok = sum(s.ok for s in signals)
    margin = "s/d" if f.net_margin is None else f"{f.net_margin:.0f}%"
    return (f"{f.ticker} ({f.fiscal or f.period_end}): EPS trim. {p(f.eps_yoy)} a/a · ventas {p(f.revenue_yoy)} a/a · "
            f"EPS anual {p(f.annual_eps_growth)} · margen neto {margin} → {ok}/{len(signals)} O'Neil")


# ------------------------------------------------------------------- red

# Un solo ritmo para toda la app: los fundamentos y los 13F corren en paralelo y la SEC
# bloquea la IP unos minutos si pasan de 10 pedidos por segundo entre todos.
_PACE_LOCK = threading.Lock()
_next_slot = 0.0
SEC_PAUSE = 0.2  # 5 pedidos por segundo como máximo


def _reserve_slot(pause: float) -> float:
    """Segundos a esperar para respetar el ritmo global (vale entre hilos y event loops)."""
    global _next_slot
    with _PACE_LOCK:
        now = time.monotonic()
        slot = max(now, _next_slot)
        _next_slot = slot + pause
        return slot - now


class SECBlockedError(Exception):
    """La SEC respondió 403: bloqueo temporal por exceso de pedidos o User-Agent rechazado."""


SEC_BLOCKED_HELP = (
    "La SEC rechazó el pedido (error 403). Suele ser un bloqueo temporal por demasiados pedidos seguidos "
    "y se levanta solo en unos 10 minutos. Si sigue, revisá que SEC_USER_AGENT en tu .env tenga tu nombre "
    "y tu email, por ejemplo: SEC_USER_AGENT=Facundo Perez facundo@gmail.com"
)


class SECClient:
    """SEC pide un User-Agent con nombre y email, y como mucho 10 pedidos por segundo."""

    def __init__(self, user_agent: str, pause: float = SEC_PAUSE):
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._pause = pause
        self.blocked = False  # después de un 403 no se insiste: seguir pidiendo alarga el bloqueo

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        wait = _reserve_slot(self._pause)
        if wait > 0:
            await asyncio.sleep(wait)
        if self.blocked:
            raise SECBlockedError(SEC_BLOCKED_HELP)
        resp = await client.get(url, headers=self._headers)
        if resp.status_code == 403:
            self.blocked = True
            raise SECBlockedError(SEC_BLOCKED_HELP)
        resp.raise_for_status()
        return resp

    async def get_json(self, client: httpx.AsyncClient, url: str) -> dict:
        return (await self._get(client, url)).json()

    async def get_bytes(self, client: httpx.AsyncClient, url: str) -> bytes:
        return (await self._get(client, url)).content


def valid_user_agent(ua: str) -> bool:
    return bool(ua) and "@" in ua and "example.com" not in ua


async def get_fundamentals(
    tickers: list[str], user_agent: str, cache: CacheService | None = None, timeout: float = 60.0,
) -> dict[str, FundamentalSnapshot]:
    """
    Fundamentos por ticker; los que no tienen datos en la SEC (ETFs, ADRs en IFRS) se omiten.
    Cachea 24hs los datos y 30 días los "sin datos". Lo que no llega antes de `timeout`
    queda para el próximo análisis.
    """
    if not valid_user_agent(user_agent):
        return {}
    out: dict[str, FundamentalSnapshot] = {}
    pending = []
    for t in tickers:
        if cache and cache.get(f"sec_fund_none_{t}", override_ttl_minutes=_NO_DATA_TTL_MIN) is not None:
            continue
        hit = cache.get(f"sec_fund_{t}", override_ttl_minutes=_CACHE_TTL_MIN) if cache else None
        if hit:
            out[t] = FundamentalSnapshot.model_validate(hit)
        else:
            pending.append(t)
    if not pending:
        return out

    sec = SECClient(user_agent)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            ciks = cache.get("sec_company_tickers", override_ttl_minutes=_TICKERS_TTL_MIN) if cache else None
            if ciks is None:
                raw = await sec.get_json(client, TICKERS_URL)
                ciks = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
                if cache:
                    cache.set("sec_company_tickers", ciks)
        except Exception as e:
            logger.warning(f"SEC: no se pudo bajar la lista de tickers: {e}")
            return out

        async def fetch(t: str) -> None:
            cik = ciks.get(t.upper()) or ciks.get(t.upper().replace(".", "-"))
            snap = None
            if cik:
                try:
                    snap = analyze_facts(t, await sec.get_json(client, FACTS_URL.format(cik=cik)))
                except SECBlockedError:
                    return
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 404:
                        logger.warning(f"SEC {t}: HTTP {e.response.status_code}")
                        return
            if snap:
                out[t] = snap
                if cache:
                    cache.set(f"sec_fund_{t}", snap.model_dump())
            elif cache:
                cache.set(f"sec_fund_none_{t}", {"none": True})

        tasks = [asyncio.create_task(fetch(t)) for t in pending]
        done, late = await asyncio.wait(tasks, timeout=timeout)
        for task in late:
            task.cancel()
        for task in done:
            if task.exception():
                logger.warning(f"SEC: {task.exception()}")
        if late:
            logger.warning(f"SEC: {len(late)} tickers no llegaron en {timeout:.0f}s — quedan para el próximo análisis")
        if sec.blocked:
            logger.warning(f"SEC: {SEC_BLOCKED_HELP}")
    return out
