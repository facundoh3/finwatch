"""
Lo que muestra la pantalla de Inicio: qué hacer esta semana, cuánta plata tenés
y cómo vas con cada meta. Todo se decide acá para que la pantalla no tenga que
interpretar indicadores.
"""
from dataclasses import dataclass
from datetime import date

from core.services.goals_service import GoalsConfig, pending_goals_in_order, project, this_month

KIND_SETUP, KIND_MONTHLY, KIND_SELL, KIND_REVIEW, KIND_GOAL = "setup", "monthly", "sell", "review", "goal"


@dataclass
class HomeAction:
    kind: str
    title: str
    detail: str


@dataclass
class GoalProgress:
    name: str
    current: float
    target: float
    currency: str  # "ARS" o "USD"
    eta: date | None = None

    @property
    def ratio(self) -> float:
        return min(self.current / self.target, 1.0) if self.target else 1.0


def is_configured(cfg: GoalsConfig) -> bool:
    return cfg.income_ars > 0


def total_money_ars(cfg: GoalsConfig, mep: float, portfolio: dict) -> float:
    stocks = sum(float(p.get("ars_amount") or 0.0) for p in portfolio.values())
    return cfg.savings_ars + cfg.savings_usd * mep + stocks


def _ars(v: float) -> str:
    return f"${v:,.0f}".replace(",", ".")


def _usd(v: float) -> str:
    return f"US${v:,.0f}".replace(",", ".")


def monthly_steps(cfg: GoalsConfig, mep: float) -> list[str]:
    """Los pasos de este mes en una o dos frases cada uno."""
    a = this_month(cfg, mep)
    steps = []
    if a.keep_in_mp_ars > 0:
        steps.append(f"Separá **{_ars(a.keep_in_mp_ars)}** para tu fondo de emergencia (en pesos, en Reservas o en IOL).")
    if a.transfer_ars > 0:
        what = f"**{a.instrument.name}** para {a.goal.name}" if a.goal and a.instrument else "lo que prefieras"
        steps.append(f"Pasá **{_ars(a.transfer_ars)}** a IOL, convertilos a dólares (≈{_usd(a.transfer_usd)}) y comprá {what}.")
    return steps


def weekly_actions(
    cfg: GoalsConfig,
    mep: float,
    portfolio: dict,
    prices: dict[str, float] | None = None,
    rec_actions: dict[str, str] | None = None,
    today: date | None = None,
) -> list[HomeAction]:
    """Lo que hay que hacer, más urgente primero. Lista vacía = no hay nada que hacer."""
    today = today or date.today()
    prices = prices or {}
    rec_actions = rec_actions or {}
    if not is_configured(cfg):
        return [HomeAction(KIND_SETUP, "Cargá tus números",
                           "En ⚙️ Ajustes poné cuánto ganás, cuánto gastás y cuánto tenés ahorrado. "
                           "Con eso la app arma tu plan.")]

    actions: list[HomeAction] = []
    for ticker, pos in portfolio.items():
        price, stop = prices.get(ticker), pos.get("stop_price")
        if price is not None and stop and price < stop:
            actions.append(HomeAction(
                KIND_SELL, f"Vendé {ticker}",
                "Bajó más de 7% desde que la compraste. La regla es cortar la pérdida ahora, "
                "mientras es chica, en vez de esperar a que se recupere.",
            ))
        elif rec_actions.get(ticker) == "AVOID":
            actions.append(HomeAction(
                KIND_REVIEW, f"Revisá {ticker}",
                "El análisis ahora recomienda evitarla. Mirá el motivo en 📚 Aprender → Mi bolsillo.",
            ))
        elif pos.get("date_bought") and pos.get("days_to_hold"):
            held = (today - date.fromisoformat(pos["date_bought"])).days
            if held >= int(pos["days_to_hold"]):
                actions.append(HomeAction(
                    KIND_REVIEW, f"Revisá {ticker}",
                    f"Se cumplió el plazo de {pos['days_to_hold']} días que te pusiste. Fijate si vender o mantenerla.",
                ))

    remaining = cfg.savings_usd
    for g in pending_goals_in_order(cfg):
        if remaining < g.target_usd:
            break
        remaining -= g.target_usd
        actions.append(HomeAction(
            KIND_GOAL, f"¡Llegaste a {g.name}! 🎉",
            "Cuando uses esa plata, tildala como cumplida en 🎯 Mi plan y la app pasa a la siguiente meta.",
        ))

    if cfg.last_done_month != today.strftime("%Y-%m") and cfg.monthly_savings_ars > 0:
        steps = monthly_steps(cfg, mep)
        if steps:
            actions.append(HomeAction(KIND_MONTHLY, "Lo de este mes", "\n".join(steps)))
    return actions


def goal_progress(cfg: GoalsConfig, mep: float, today: date | None = None) -> list[GoalProgress]:
    """Fondo de emergencia (en pesos) y cada meta pendiente (en dólares), con fecha estimada."""
    today = today or date.today()
    out = []
    fund = cfg.expenses_ars * cfg.emergency_months
    etas = {}
    if is_configured(cfg) and cfg.monthly_savings_ars > 0 and mep > 0:
        etas = {r.name: r.reached_on(today) for r in project(cfg, mep).results}
    if fund > 0:
        name = next((n for n in etas if n.startswith("Fondo de emergencia")), None)
        out.append(GoalProgress("Fondo de emergencia", min(cfg.savings_ars, fund), fund, "ARS",
                                None if cfg.savings_ars >= fund else etas.get(name)))
    remaining = cfg.savings_usd
    for g in pending_goals_in_order(cfg):
        current = min(remaining, g.target_usd)
        remaining -= current
        out.append(GoalProgress(g.name, current, g.target_usd, "USD", None if current >= g.target_usd else etas.get(g.name)))
    return out


def first_sentence(text: str, limit: int = 220) -> str:
    text = (text or "").strip()
    for sep in (". ", "; "):
        idx = text.find(sep)
        if 0 < idx < limit:
            return text[: idx + 1]
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
