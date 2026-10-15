from pydantic import BaseModel


class Holding(BaseModel):
    name: str
    ticker: str | None = None
    ticker_check: bool = False  # el ticker se dedujo por nombre: puede estar mal
    cusip: str
    value: float  # USD
    pct: float  # % del total del 13F
    shares: float
    status: str  # NUEVA, AUMENTÓ, REDUJO, SIN CAMBIOS, VENDIÓ TODO
    change_pct: float | None = None  # % de cambio en cantidad de acciones vs el trimestre anterior


class FundHoldings(BaseModel):
    cik: str
    name: str
    person: str = ""
    period: str  # fecha de las tenencias (fin del trimestre)
    filed: str  # fecha en que se presentó
    accession: str
    url: str
    prior_period: str | None = None
    total_value: float
    holdings: list[Holding]  # tenencias actuales, solo acciones, de mayor a menor
    sold: list[Holding] = []  # las que vendió completas este trimestre
