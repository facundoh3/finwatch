"""
Tests de core/services/home_service.py.
Correr con: pytest tests/unit/test_home_service.py -v
"""
from datetime import date

from core.services.goals_service import Goal, GoalsConfig
from core.services.home_service import (
    KIND_GOAL,
    KIND_MONTHLY,
    KIND_REVIEW,
    KIND_SELL,
    KIND_SETUP,
    first_sentence,
    goal_progress,
    total_money_ars,
    weekly_actions,
)

MEP = 1000.0
TODAY = date(2026, 10, 16)


def _cfg(**kw) -> GoalsConfig:
    base = dict(income_ars=2_000_000, expenses_ars=1_000_000, emergency_months=3, savings_ars=3_000_000,
                savings_usd=0, annual_return=0.0, goals=[Goal("Auto", 12_000, 2), Goal("Casa", 50_000, 8)])
    base.update(kw)
    return GoalsConfig(**base)


def _kinds(actions):
    return [a.kind for a in actions]


def test_unconfigured_only_asks_to_set_up():
    actions = weekly_actions(GoalsConfig(), MEP, {}, today=TODAY)
    assert _kinds(actions) == [KIND_SETUP]


def test_monthly_task_until_marked_done():
    actions = weekly_actions(_cfg(), MEP, {}, today=TODAY)
    assert _kinds(actions) == [KIND_MONTHLY]
    assert "Pasá **$1.000.000** a IOL" in actions[0].detail
    assert "para Auto" in actions[0].detail
    assert weekly_actions(_cfg(last_done_month="2026-10"), MEP, {}, today=TODAY) == []


def test_monthly_task_completes_the_emergency_fund_first():
    actions = weekly_actions(_cfg(savings_ars=500_000), MEP, {}, today=TODAY)
    assert "Separá **$1.000.000** para tu fondo de emergencia" in actions[0].detail
    assert "IOL" not in actions[0].detail.split("\n")[0][:20]


def test_stop_loss_comes_first():
    portfolio = {"KO": {"ars_amount": 50_000, "stop_price": 65.0}}
    actions = weekly_actions(_cfg(), MEP, portfolio, prices={"KO": 60.0}, today=TODAY)
    assert _kinds(actions) == [KIND_SELL, KIND_MONTHLY]
    assert actions[0].title == "Vendé KO"


def test_avoid_and_holding_period_ask_for_review():
    portfolio = {
        "TSLA": {"ars_amount": 50_000},
        "KO": {"ars_amount": 50_000, "date_bought": "2026-09-01", "days_to_hold": 30},
        "AAPL": {"ars_amount": 50_000, "date_bought": "2026-10-10", "days_to_hold": 30},
    }
    actions = weekly_actions(_cfg(last_done_month="2026-10"), MEP, portfolio, rec_actions={"TSLA": "AVOID"}, today=TODAY)
    assert [(a.kind, a.title) for a in actions] == [(KIND_REVIEW, "Revisá TSLA"), (KIND_REVIEW, "Revisá KO")]


def test_reached_goal_is_celebrated():
    actions = weekly_actions(_cfg(savings_usd=12_500, last_done_month="2026-10"), MEP, {}, today=TODAY)
    assert [(a.kind, a.title) for a in actions] == [(KIND_GOAL, "¡Llegaste a Auto! 🎉")]


def test_progress_and_estimated_dates():
    prog = goal_progress(_cfg(savings_ars=1_500_000, savings_usd=3_000), MEP, today=TODAY)
    assert [(p.name, p.currency) for p in prog] == [("Fondo de emergencia", "ARS"), ("Auto", "USD"), ("Casa", "USD")]
    fund, auto, casa = prog
    assert fund.ratio == 0.5 and fund.eta is not None
    assert auto.current == 3_000 and auto.ratio == 0.25
    assert casa.current == 0
    assert fund.eta <= auto.eta <= casa.eta


def test_completed_fund_has_no_eta():
    fund = goal_progress(_cfg(), MEP, today=TODAY)[0]
    assert fund.ratio == 1.0 and fund.eta is None


def test_total_money_includes_stocks():
    assert total_money_ars(_cfg(savings_usd=1_000), MEP, {"KO": {"ars_amount": 50_000}}) == 3_000_000 + 1_000_000 + 50_000


def test_first_sentence():
    assert first_sentence("El S&P subió 1%. La FED habló.") == "El S&P subió 1%."
    assert first_sentence("") == ""
    assert first_sentence("x " * 200).endswith("…")
