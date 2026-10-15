from pydantic import BaseModel


class FundamentalSnapshot(BaseModel):
    """Números reportados a la SEC (XBRL) del último trimestre de una empresa."""

    ticker: str
    entity: str | None = None
    period_end: str  # fin del último trimestre reportado (AAAA-MM-DD)
    fiscal: str = ""  # ej "Q3 2026"
    derived: bool = False  # Q4 calculado como año fiscal menos los 3 trimestres
    revenue: float | None = None
    revenue_yoy: float | None = None  # % contra el mismo trimestre del año anterior
    eps: float | None = None  # ganancia por acción diluida del trimestre
    eps_yoy: float | None = None
    net_margin: float | None = None
    annual_eps_growth: float | None = None  # % último año fiscal vs el anterior
    annual_fy: str | None = None
