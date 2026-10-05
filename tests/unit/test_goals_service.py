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
    EMERGENCY_INSTRUMENT,
    build_cost_usd,
    instrument_for,
    load_goals,
    plan_by_goal,
    project,
    save_goals,
    this_month,
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


def test_instrument_gets_riskier_with_horizon():
    returns = [instrument_for(y).annual_return for y in (0.5, 2, 4, 10)]
    assert returns == sorted(returns)
    assert len(set(returns)) == 4


def test_plan_funds_most_urgent_goal_first():
    cfg = _cfg(goals=[Goal("Casa", 20_000, 6), Goal("Auto", 12_000, 2)])
    plan = plan_by_goal(cfg, MEP)
    auto, casa = plan.rows
    assert auto.goal.name == "Auto"
    assert auto.start_month == 1 and auto.end_month <= 24
    assert casa.start_month >= auto.end_month and casa.end_month <= 72


def test_plan_monthly_is_the_minimum_that_meets_deadlines():
    from core.services.goals_service import _simulate
    plan = plan_by_goal(_cfg(goals=[Goal("Auto", 12_000, 2), Goal("Casa", 30_000, 7)]), MEP)
    assert _simulate(plan.rows, plan.monthly_usd)
    assert not _simulate(plan.rows, plan.monthly_usd * 0.99)


def test_returns_lower_required_saving():
    plan = plan_by_goal(_cfg(goals=[Goal("Auto", 12_000, 2)]), MEP)
    assert 450 < plan.monthly_usd < 500  # 500 sin intereses


def test_plan_allocates_savings_and_extra_income_by_urgency():
    cfg = _cfg(
        emergency_months=1,  # fondo = US$500
        savings_usd=300,
        extra_income_ars=500_000,  # US$500
        goals=[Goal("Casa", 30_000, 7), Goal("Sin plazo", 5_000), Goal("Auto", 12_000, 2)],
    )
    rows = plan_by_goal(cfg, MEP).rows
    assert [r.goal.name for r in rows] == ["Fondo de emergencia (1 mes)", "Auto", "Casa"]
    assert rows[0].instrument == EMERGENCY_INSTRUMENT
    assert rows[0].allocated_now == 500 and rows[0].end_month == 0
    assert rows[1].allocated_now == 300
    assert rows[2].allocated_now == 0


def test_plan_needs_nothing_when_savings_cover_everything():
    plan = plan_by_goal(_cfg(savings_usd=50_000, goals=[Goal("Auto", 12_000, 2)]), MEP)
    assert plan.monthly_usd == 0


def test_done_goals_are_ignored_everywhere():
    cfg = _cfg(goals=[Goal("Auto", 6_000, 1, done=True), Goal("Casa", 6_000, 2)])
    assert [g.name for g in all_goals(cfg, MEP)] == ["Casa"]
    assert project(cfg, MEP).results[0].month == 12
    assert this_month(cfg, MEP).goal.name == "Casa"


def test_this_month_completes_emergency_fund_first():
    # fondo = 3 × 500k = 1,5M; tiene 1,2M → faltan 300k de los 500k que ahorra
    a = this_month(_cfg(emergency_months=3, savings_ars=1_200_000, goals=[Goal("Auto", 12_000, 2)]), MEP)
    assert a.keep_in_mp_ars == 300_000
    assert a.transfer_ars == 200_000 and a.transfer_usd == 200
    assert a.emergency_complete


def test_this_month_keeps_everything_while_fund_is_far():
    a = this_month(_cfg(emergency_months=3, savings_ars=0, goals=[Goal("Auto", 12_000, 2)]), MEP)
    assert a.keep_in_mp_ars == 500_000 and a.transfer_ars == 0
    assert not a.emergency_complete


def test_this_month_moves_excess_pesos_to_iol():
    a = this_month(_cfg(emergency_months=1, savings_ars=2_000_000, goals=[Goal("Auto", 12_000, 2)]), MEP)
    assert a.keep_in_mp_ars == 0
    assert a.excess_mp_ars == 1_500_000
    assert a.transfer_ars == 2_000_000


def test_this_month_picks_next_goal_once_dollars_cover_the_first():
    cfg = _cfg(savings_usd=13_000, goals=[Goal("Casa", 30_000, 7), Goal("Auto", 12_000, 2)])
    a = this_month(cfg, MEP)
    assert a.goal.name == "Casa"
    assert a.goal_saved_usd == 1_000
    assert a.instrument == instrument_for(7)


def test_this_month_without_pending_goals():
    a = this_month(_cfg(goals=[Goal("Auto", 12_000, 2, done=True)]), MEP)
    assert a.goal is None and a.instrument is None
    assert a.transfer_ars == 500_000
