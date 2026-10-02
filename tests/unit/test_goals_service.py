"""
Tests unitarios para core/services/goals_service.py.
Correr con: pytest tests/unit/test_goals_service.py -v
"""
from datetime import date

from core.services.goals_service import (
    Goal,
    GoalResult,
    GoalsConfig,
    all_goals,
    build_cost_usd,
    load_goals,
    project,
    save_goals,
)

MEP = 1000.0


def _cfg(**kw) -> GoalsConfig:
    base = dict(income_ars=1_000_000, expenses_ars=500_000, annual_return=0.0, emergency_months=0, goals=[])
    base.update(kw)
    return GoalsConfig(**base)


def test_zero_return_reaches_goal_in_target_over_savings_months():
    # 500k ARS/mes = US$500/mes → US$6.000 en 12 meses
    proj = project(_cfg(goals=[Goal("Auto", 6_000)]), MEP)
    assert proj.results[0].month == 12


def test_compound_interest_reaches_goal_sooner():
    simple = project(_cfg(goals=[Goal("Casa", 60_000)]), MEP).results[0].month
    compound = project(_cfg(annual_return=0.07, goals=[Goal("Casa", 60_000)]), MEP).results[0].month
    assert compound < simple


def test_goals_are_funded_sequentially():
    proj = project(_cfg(goals=[Goal("A", 1_000), Goal("B", 1_000)]), MEP)
    assert [r.month for r in proj.results] == [2, 4]


def test_existing_savings_count_in_both_currencies():
    proj = project(_cfg(savings_ars=1_000_000, savings_usd=1_000, goals=[Goal("A", 2_000)]), MEP)
    assert proj.results[0].month == 0


def test_extra_income_is_spread_over_months():
    # 3M ARS en 3 meses = US$1.000 extra por mes durante 3 meses
    proj = project(_cfg(extra_income_ars=3_000_000, extra_income_months=3, goals=[Goal("A", 4_500)]), MEP)
    assert proj.results[0].month == 3


def test_emergency_fund_goes_first():
    cfg = _cfg(emergency_months=3, goals=[Goal("Auto", 1_000)])
    goals = all_goals(cfg, MEP)
    assert goals[0].name.startswith("Fondo de emergencia")
    assert goals[0].target_usd == 1_500  # 3 meses × 500k ARS / 1000


def test_unreachable_goal_has_no_month():
    proj = project(_cfg(expenses_ars=1_000_000, goals=[Goal("Casa", 100_000)]), MEP)
    assert proj.results[0].month is None


def test_reached_on_rolls_over_year():
    assert GoalResult("A", 1, 5).reached_on(date(2026, 10, 2)) == date(2027, 3, 1)


def test_build_cost():
    assert build_cost_usd(40_000, 60, 1_000) == 100_000


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "goals.json"
    cfg = _cfg(goals=[Goal("Auto", 12_000)])
    save_goals(cfg, path)
    assert load_goals(path) == cfg


def test_load_missing_file_returns_defaults(tmp_path):
    assert load_goals(tmp_path / "nope.json") == GoalsConfig()
