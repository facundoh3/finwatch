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
        Goal("Auto usado", 12_000),
        Goal("Anticipo casa (25%)", 27_000),
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
        goals.insert(0, Goal(f"Fondo de emergencia ({cfg.emergency_months} meses)", round(fund)))
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
