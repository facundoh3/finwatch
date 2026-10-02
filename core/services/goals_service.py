"""
Metas de ahorro: proyección mes a mes en dólares con interés compuesto.
Las metas se financian en orden; al alcanzar una, ese monto se aparta
(se gasta o se reserva) y el ahorro sigue hacia la siguiente.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

GOALS_FILE = Path(__file__).parent.parent.parent / "config" / "goals.json"
_MAX_MONTHS = 40 * 12


@dataclass
class Goal:
    name: str
    target_usd: float
    years: float | None = None  # plazo deseado; None = sin fecha


@dataclass(frozen=True)
class Instrument:
    name: str
    examples: str
    annual_return: float  # estimado en USD


# Rendimientos de referencia (oct 2026): FCI dólar ~3-4%, ONs corporativas ~6-8%,
# Bonares/Globales ~10-11% (AL30D 10,6%, GD35D 11,2% según IOL).
EMERGENCY_INSTRUMENT = Instrument("Money market en pesos", "Mercado Pago, FCI money market o caución en IOL", 0.0)
_INSTRUMENTS_BY_HORIZON = [
    (1, Instrument("Fondo en dólares", "IOL Dólar Ahorro Plus, Adcap Renta Dólar", 0.035)),
    (3, Instrument("Fondo en dólares + ONs cortas", "FCI dólar + ONs que vencen antes de la meta", 0.05)),
    (6, Instrument("ONs corporativas", "YPF, Pampa, Vista, TGS", 0.07)),
    (float("inf"), Instrument("ONs + 20% bonos soberanos", "ONs (YPF, Pampa, Vista, TGS) + GD35 / AL30", 0.08)),
]
EMERGENCY_YEARS = 0.5


@dataclass
class GoalsConfig:
    income_ars: float = 0.0
    expenses_ars: float = 0.0
    savings_ars: float = 0.0
    savings_usd: float = 0.0
    extra_income_ars: float = 0.0
    extra_income_months: int = 1
    annual_return: float = 0.07
    emergency_months: int = 3
    goals: list[Goal] = field(default_factory=lambda: [
        Goal("Auto usado", 12_000, 2),
        Goal("Anticipo casa", 30_000, 6),
    ])

    @property
    def monthly_savings_ars(self) -> float:
        return max(self.income_ars - self.expenses_ars, 0.0)

    @property
    def savings_rate(self) -> float:
        return self.monthly_savings_ars / self.income_ars if self.income_ars else 0.0


@dataclass
class GoalResult:
    name: str
    target_usd: float
    month: int | None  # None = no se alcanza dentro del horizonte

    def reached_on(self, start: date) -> date | None:
        if self.month is None:
            return None
        total = start.year * 12 + start.month - 1 + self.month
        return date(total // 12, total % 12 + 1, 1)


@dataclass
class Projection:
    results: list[GoalResult]
    balance_usd: list[float]  # saldo invertido al final de cada mes (después de apartar metas)


def load_goals(path: Path = GOALS_FILE) -> GoalsConfig:
    if not path.exists():
        return GoalsConfig()
    try:
        data = json.loads(path.read_text())
        goals = [Goal(**g) for g in data.pop("goals", [])]
        return GoalsConfig(**data, goals=goals)
    except Exception:
        return GoalsConfig()


def save_goals(cfg: GoalsConfig, path: Path = GOALS_FILE) -> None:
    path.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False))


def emergency_fund_usd(cfg: GoalsConfig, mep: float) -> float:
    return cfg.expenses_ars * cfg.emergency_months / mep if mep else 0.0


def all_goals(cfg: GoalsConfig, mep: float) -> list[Goal]:
    """El fondo de emergencia siempre va primero."""
    goals = list(cfg.goals)
    fund = emergency_fund_usd(cfg, mep)
    if fund > 0:
        months = f"{cfg.emergency_months} mes{'es' if cfg.emergency_months != 1 else ''}"
        goals.insert(0, Goal(f"Fondo de emergencia ({months})", round(fund)))
    return goals


def project(cfg: GoalsConfig, mep: float) -> Projection:
    """
    Supone que el sueldo y los gastos acompañan al dólar (valores reales constantes)
    y que todo lo ahorrado se pasa a dólares al MEP actual.
    """
    goals = all_goals(cfg, mep)
    results: list[GoalResult] = []
    balances: list[float] = []
    if mep <= 0:
        return Projection([GoalResult(g.name, g.target_usd, None) for g in goals], [])

    monthly_rate = (1 + cfg.annual_return) ** (1 / 12) - 1
    monthly_usd = cfg.monthly_savings_ars / mep
    extra_months = max(cfg.extra_income_months, 1)
    extra_usd = cfg.extra_income_ars / extra_months / mep
    balance = cfg.savings_usd + cfg.savings_ars / mep
    idx = 0

    for month in range(0, _MAX_MONTHS + 1):
        if month > 0:
            balance = balance * (1 + monthly_rate) + monthly_usd
            if month <= extra_months:
                balance += extra_usd
        while idx < len(goals) and balance >= goals[idx].target_usd:
            balance -= goals[idx].target_usd
            results.append(GoalResult(goals[idx].name, goals[idx].target_usd, month))
            idx += 1
        balances.append(balance)
        if idx == len(goals):
            break

    results.extend(GoalResult(g.name, g.target_usd, None) for g in goals[idx:])
    return Projection(results, balances)


def build_cost_usd(land_usd: float, m2: float, cost_per_m2_usd: float) -> float:
    return land_usd + m2 * cost_per_m2_usd


def instrument_for(years: float) -> Instrument:
    return next(inst for limit, inst in _INSTRUMENTS_BY_HORIZON if years < limit)


@dataclass
class PlanRow:
    goal: Goal
    years: float
    instrument: Instrument
    allocated_now: float
    start_month: int | None = None  # primer mes con aporte; None = cubierta con lo que ya tenés
    end_month: int | None = None  # mes en que se completa

    @property
    def months(self) -> int:
        return max(round(self.years * 12), 1)


@dataclass
class Plan:
    rows: list[PlanRow]
    monthly_usd: float  # ahorro mensual mínimo para cumplir todos los plazos


def _simulate(rows: list[PlanRow], monthly: float) -> bool:
    """Aporta `monthly` a la meta pendiente más urgente; el sobrante pasa a la siguiente."""
    balances = [r.allocated_now for r in rows]
    rates = [(1 + r.instrument.annual_return) ** (1 / 12) - 1 for r in rows]
    for r in rows:
        r.start_month = None
        r.end_month = 0 if r.allocated_now >= r.goal.target_usd else None
    for month in range(1, max(r.months for r in rows) + 1):
        balances = [b * (1 + i) for b, i in zip(balances, rates)]
        left = monthly
        for k, r in enumerate(rows):
            if left <= 0:
                break
            if r.end_month is not None:
                continue
            put = min(left, r.goal.target_usd - balances[k])
            balances[k] += put
            left -= put
            if r.start_month is None:
                r.start_month = month
        for k, r in enumerate(rows):
            if r.end_month is None and balances[k] >= r.goal.target_usd - 1e-6:
                r.end_month = month
            if r.end_month is None and month >= r.months:
                return False
    return True


def plan_by_goal(cfg: GoalsConfig, mep: float) -> Plan:
    """
    Ahorro mensual mínimo para cumplir cada meta en su plazo, financiándolas de a una
    (la más urgente primero). El instrumento de cada meta depende de su plazo, y el
    ahorro actual más los ingresos extra esperados se reparten en ese mismo orden.
    """
    rows: list[PlanRow] = []
    for idx, goal in enumerate(all_goals(cfg, mep)):
        is_emergency = idx == 0 and emergency_fund_usd(cfg, mep) > 0
        years = EMERGENCY_YEARS if is_emergency else goal.years
        if not years or years <= 0:
            continue
        instrument = EMERGENCY_INSTRUMENT if is_emergency else instrument_for(years)
        rows.append(PlanRow(goal, years, instrument, 0.0))
    if not rows:
        return Plan([], 0.0)

    rows.sort(key=lambda r: r.months)
    available = cfg.savings_usd + ((cfg.savings_ars + cfg.extra_income_ars) / mep if mep else 0.0)
    for r in rows:
        r.allocated_now = min(available, r.goal.target_usd)
        available -= r.allocated_now

    if _simulate(rows, 0.0):
        return Plan(rows, 0.0)
    lo, hi = 0.0, sum(r.goal.target_usd for r in rows)
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if _simulate(rows, mid) else (mid, hi)
    _simulate(rows, hi)
    return Plan(rows, hi)
