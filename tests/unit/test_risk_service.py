"""
Tests de core/services/risk_service.py.
Correr con: pytest tests/unit/test_risk_service.py -v
"""
from core.services.goals_service import GoalsConfig
from core.services.risk_service import check_buy, max_for_ticker, stock_budget

MEP = 1000.0


def _cfg(**kw) -> GoalsConfig:
    # fondo de emergencia = 3 × 1M = 3M; ahorro total sin acciones = 3M + 7.000 USD × 1000 = 10M
    base = dict(income_ars=2_000_000, expenses_ars=1_000_000, emergency_months=3,
                savings_ars=3_000_000, savings_usd=7_000, stocks_max_pct=0.10, stock_position_max_pct=0.25)
    base.update(kw)
    return GoalsConfig(**base)


def test_cap_is_a_share_of_all_savings():
    b = stock_budget(_cfg(), {}, MEP)
    assert b.total_savings_ars == 10_000_000
    assert b.cap_ars == 1_000_000
    assert b.per_stock_cap_ars == 250_000
    assert b.available_ars == 1_000_000


def test_existing_positions_use_up_the_cap():
    portfolio = {"AAPL": {"ars_amount": 200_000}, "KO": {"ars_amount": 100_000}}
    b = stock_budget(_cfg(), portfolio, MEP)
    assert b.invested_ars == 300_000
    assert b.cap_ars == 1_030_000  # lo invertido también es ahorro
    assert b.available_ars == 730_000
    assert max_for_ticker(b, portfolio, "AAPL") == b.per_stock_cap_ars - 200_000
    assert max_for_ticker(b, portfolio, "NVDA") == b.per_stock_cap_ars


def test_no_stocks_until_the_emergency_fund_is_complete():
    b = stock_budget(_cfg(savings_ars=2_000_000), {}, MEP)
    assert not b.emergency_ok
    assert b.available_ars == 0
    assert max_for_ticker(b, {}, "AAPL") == 0
    assert "fondo de emergencia" in check_buy(b, {}, "AAPL", 10_000)[0]


def test_buy_inside_the_plan_has_no_problems():
    b = stock_budget(_cfg(), {}, MEP)
    assert check_buy(b, {}, "AAPL", 250_000) == []


def test_buy_over_the_per_stock_cap():
    b = stock_budget(_cfg(), {}, MEP)
    problems = check_buy(b, {}, "AAPL", 300_000)
    assert len(problems) == 1 and "máximo por acción" in problems[0]


def test_buy_over_the_total_cap():
    # 1M invertido → ahorro total 11M, tope 1,1M, quedan 100 mil; 200 mil no pasa el máximo por acción (275 mil)
    portfolio = {t: {"ars_amount": 250_000} for t in ("A", "B", "C", "D")}
    b = stock_budget(_cfg(), portfolio, MEP)
    assert b.available_ars == 100_000
    problems = check_buy(b, portfolio, "E", 200_000)
    assert len(problems) == 1 and "tope para acciones" in problems[0]
