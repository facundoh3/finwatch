"""
Topes para invertir en acciones, atados al plan de Metas: las acciones son un
"bolsillo de riesgo" chico, separado de la plata del auto y la casa.

Idea tomada de trader-skill/trader-budget y trader-risk (MIT, (c) 2026 Jake
Schincariol, ver third_party/trader-skill/LICENSE): tope por posición,
colchón que no se toca y bloqueo de lo que se pase.
"""
from dataclasses import dataclass

from core.services.goals_service import GoalsConfig


@dataclass
class StockBudget:
    emergency_ok: bool  # sin fondo de emergencia completo, no se compran acciones
    total_savings_ars: float  # pesos + dólares + lo ya invertido en acciones
    cap_ars: float  # máximo total en acciones
    invested_ars: float  # lo que ya hay en acciones (al costo)
    per_stock_cap_ars: float  # máximo en una sola acción

    @property
    def available_ars(self) -> float:
        return max(self.cap_ars - self.invested_ars, 0.0) if self.emergency_ok else 0.0


def invested_by_ticker(portfolio: dict) -> dict[str, float]:
    return {t: float(p.get("ars_amount") or 0.0) for t, p in portfolio.items()}


def stock_budget(cfg: GoalsConfig, portfolio: dict, mep: float) -> StockBudget:
    invested = sum(invested_by_ticker(portfolio).values())
    total = cfg.savings_ars + cfg.savings_usd * mep + invested
    cap = total * cfg.stocks_max_pct
    return StockBudget(
        emergency_ok=cfg.savings_ars >= cfg.expenses_ars * cfg.emergency_months,
        total_savings_ars=total,
        cap_ars=cap,
        invested_ars=invested,
        per_stock_cap_ars=cap * cfg.stock_position_max_pct,
    )


def max_for_ticker(budget: StockBudget, portfolio: dict, ticker: str) -> float:
    """Cuánto más se puede poner en esta acción sin romper ningún tope."""
    already = invested_by_ticker(portfolio).get(ticker, 0.0)
    return max(min(budget.available_ars, budget.per_stock_cap_ars - already), 0.0)


def check_buy(budget: StockBudget, portfolio: dict, ticker: str, amount_ars: float) -> list[str]:
    """Problemas de una compra contra los topes; lista vacía = está dentro del plan."""
    problems = []
    if not budget.emergency_ok:
        problems.append("Todavía no completaste el fondo de emergencia: primero eso, después acciones.")
    elif amount_ars > budget.available_ars:
        problems.append(
            f"Supera tu tope para acciones: te quedan {ars(budget.available_ars)} de {ars(budget.cap_ars)}."
        )
    already = invested_by_ticker(portfolio).get(ticker, 0.0)
    if already + amount_ars > budget.per_stock_cap_ars:
        problems.append(
            f"Quedarías con {ars(already + amount_ars)} en {ticker}, más que el máximo por acción "
            f"({ars(budget.per_stock_cap_ars)})."
        )
    return problems


def ars(v: float) -> str:
    return f"${v:,.0f}".replace(",", ".")
