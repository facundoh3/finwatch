"""
Tests de core/services/fundamentals_service.py con datos reales de la SEC
(fixtures recortados de trader-skill, MIT — ver third_party/trader-skill/LICENSE).
Correr con: pytest tests/unit/test_fundamentals_service.py -v
"""
import json
from pathlib import Path

import httpx
import pytest
import respx

from core.models.market import MarketOverview
from core.models.news import NewsCollection
from core.models.recommendation import AgentContext
from core.services.cache_service import CacheService
from core.services.fundamentals_service import (
    FACTS_URL,
    TICKERS_URL,
    analyze_facts,
    format_line,
    fundamental_signals,
    get_fundamentals,
    valid_user_agent,
)

FIX = Path(__file__).parent.parent / "fixtures" / "sec"
UA = "Test User test@finwatch.dev"


def _facts(cik: str) -> dict:
    return json.loads((FIX / f"companyfacts-CIK{cik}.json").read_text())


@pytest.fixture
def aapl():
    return analyze_facts("AAPL", _facts("0000320193"))


def test_latest_quarter_matches_apple_10q(aapl):
    assert aapl.period_end == "2026-06-27"
    assert aapl.fiscal == "Q3 2026"
    assert aapl.revenue == 109_417_000_000
    assert aapl.revenue_yoy == 16.4
    assert aapl.eps == 2.02
    assert aapl.eps_yoy == 28.7
    assert aapl.net_margin == 27.2
    assert not aapl.derived


def test_annual_eps_growth_uses_last_two_fiscal_years(aapl):
    assert aapl.annual_fy == "FY2025"
    assert aapl.annual_eps_growth == 22.7


def test_oneil_signals(aapl):
    c, a, sales = fundamental_signals(aapl)
    assert c.ok  # +28.7% ≥ 25%
    assert not a.ok  # +22.7%
    assert not sales.ok  # +16.4%


def test_missing_data_is_a_failed_signal_not_an_error():
    ko = analyze_facts("KO", _facts("0000021344"))
    assert ko.revenue_yoy == 12.1 and ko.eps_yoy == 18.2
    assert ko.annual_eps_growth is None
    assert "Sin dato" in fundamental_signals(ko)[1].detail


def test_no_us_gaap_facts_returns_none():
    assert analyze_facts("YPF", {"entityName": "YPF", "facts": {"ifrs-full": {}}}) is None


def test_format_line_for_prompt(aapl):
    assert format_line(aapl) == (
        "AAPL (Q3 2026): EPS trim. +29% a/a · ventas +16% a/a · EPS anual +23% · margen neto 27% → 1/3 O'Neil"
    )


def test_prompt_block_includes_fundamentals(aapl):
    ctx = AgentContext(
        news=NewsCollection(items=[], tickers_queried=["AAPL"], hours_back=24),
        market=MarketOverview(snapshots=[]),
        query_tickers=["AAPL"],
        fundamentals={"AAPL": aapl},
    )
    assert "FUNDAMENTOS" in ctx.to_claude_prompt_block()
    assert "AAPL (Q3 2026): EPS trim. +29%" in ctx.to_claude_prompt_block()


@pytest.mark.parametrize("ua,ok", [("", False), ("Facundo", False), ("Name you@example.com", False), (UA, True)])
def test_user_agent_validation(ua, ok):
    assert valid_user_agent(ua) is ok


@pytest.mark.asyncio
async def test_without_user_agent_nothing_is_fetched():
    assert await get_fundamentals(["AAPL"], "") == {}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_caches_data_and_remembers_tickers_without_data(tmp_path):
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 884394, "ticker": "SPY", "title": "SPDR S&P 500 ETF TRUST"},
    }))
    aapl_route = respx.get(FACTS_URL.format(cik="0000320193")).mock(
        return_value=httpx.Response(200, json=_facts("0000320193")))
    spy_route = respx.get(FACTS_URL.format(cik="0000884394")).mock(return_value=httpx.Response(404))
    cache = CacheService(cache_dir=tmp_path)

    first = await get_fundamentals(["AAPL", "SPY", "ZZZZ"], UA, cache)
    assert list(first) == ["AAPL"] and first["AAPL"].eps_yoy == 28.7
    assert aapl_route.call_count == 1 and spy_route.call_count == 1
    assert aapl_route.calls[0].request.headers["User-Agent"] == UA

    second = await get_fundamentals(["AAPL", "SPY", "ZZZZ"], UA, cache)
    assert second["AAPL"] == first["AAPL"]
    assert aapl_route.call_count == 1 and spy_route.call_count == 1  # todo desde caché


@pytest.mark.asyncio
@respx.mock
async def test_server_errors_are_skipped_and_retried_next_time(tmp_path):
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    }))
    route = respx.get(FACTS_URL.format(cik="0000320193")).mock(return_value=httpx.Response(503))
    cache = CacheService(cache_dir=tmp_path)
    assert await get_fundamentals(["AAPL"], UA, cache) == {}
    await get_fundamentals(["AAPL"], UA, cache)
    assert route.call_count == 2  # un 503 no se cachea como "sin datos"


def test_global_pacing_spaces_requests_across_clients():
    from core.services.fundamentals_service import _reserve_slot

    waits = [_reserve_slot(0.2) for _ in range(4)]
    gaps = [b - a for a, b in zip(waits, waits[1:])]
    assert all(abs(g - 0.2) < 0.01 for g in gaps)


@pytest.mark.asyncio
@respx.mock
async def test_403_stops_the_remaining_requests(tmp_path):
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 21344, "ticker": "KO", "title": "Coca-Cola"},
        "2": {"cik_str": 70858, "ticker": "BAC", "title": "Bank of America"},
    }))
    routes = [respx.get(FACTS_URL.format(cik=c)).mock(return_value=httpx.Response(403))
              for c in ("0000320193", "0000021344", "0000070858")]
    cache = CacheService(cache_dir=tmp_path)
    assert await get_fundamentals(["AAPL", "KO", "BAC"], UA, cache) == {}
    assert sum(r.call_count for r in routes) == 1  # después del primer 403 no se insiste
    assert cache.get("sec_fund_none_AAPL") is None  # un bloqueo no se guarda como "sin datos"
