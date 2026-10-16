"""
Tests de core/services/institutional_service.py con los 13F reales de Berkshire
(Q1 y Q2 2026, fixtures de trader-skill, MIT — ver third_party/trader-skill/LICENSE).
Correr con: pytest tests/unit/test_institutional_service.py -v
"""
import json
from collections import defaultdict
from pathlib import Path

import httpx
import pytest
import respx

from core.services.cache_service import CacheService
from core.services.fundamentals_service import TICKERS_URL
from core.services.institutional_service import (
    ARCHIVE_URL,
    BERKSHIRE_CIK,
    FIGI_URL,
    STATUS_NEW,
    STATUS_SOLD,
    STATUS_TRIMMED,
    SUBMISSIONS_URL,
    TickerIndex,
    diff,
    get_fund_holdings,
    info_table_name,
    latest_two_13f,
    load_filers,
    norm_ticker,
    parse_info_table,
    pretty_name,
    resolve_tickers,
    stock_positions,
    summarize_for,
    value_multiplier,
)

FIX = Path(__file__).parent.parent / "fixtures" / "sec13f"
UA = "Test User test@finwatch.dev"
Q2, Q1 = "000119312526352200", "000119312526226661"


def _json(name: str):
    return json.loads((FIX / name).read_text())


def _quarter(folder: str, filed: str):
    name = info_table_name(_json(f"{folder}/index.json"))
    return stock_positions(parse_info_table((FIX / folder / name).read_bytes(), value_multiplier(filed)))


def _index() -> TickerIndex:
    return TickerIndex([(str(v["cik_str"]).zfill(10), v["ticker"], v["title"]) for v in _json("company_tickers.json").values()])


@pytest.fixture
def quarters():
    cur, total = _quarter(Q2, "2026-08-14")
    prev, ptotal = _quarter(Q1, "2026-05-15")
    resolve_tickers(cur + prev, _index())
    return cur, total, prev, ptotal


def test_latest_and_prior_filings():
    latest, prior = latest_two_13f(_json("CIK0001067983.json"))
    assert (latest["period"], latest["filed"], latest["accession"]) == ("2026-06-30", "2026-08-14", "0001193125-26-352200")
    assert prior["period"] == "2026-03-31"


def test_totals_and_top_five_match_the_filing(quarters):
    cur, total, _, ptotal = quarters
    assert total == 299_253_556_246 and ptotal == 263_095_703_570
    assert [(p["ticker"], p["pct"]) for p in cur[:5]] == [
        ("AAPL", 22.04), ("AXP", 17.14), ("GOOGL", 12.62), ("KO", 10.86), ("BAC", 9.2)]


def test_share_classes_are_combined(quarters):
    alphabet = next(p for p in quarters[0] if p["ticker"] == "GOOGL")
    assert alphabet["shares"] == 105_979_600
    assert alphabet["value"] == 37_764_088_383


def test_quarter_over_quarter_changes(quarters):
    cur, _, prev, _ = quarters
    holdings, sold = diff(cur, prev)
    by = defaultdict(list)
    for h in holdings + sold:
        by[h.status].append(h.ticker)
    assert by[STATUS_NEW] == ["DHI"]
    assert by[STATUS_SOLD] == ["STZ"]
    assert sorted(by[STATUS_TRIMMED]) == sorted(["BAC", "KR", "COF", "NUE", "ALLY", "DVA"])
    bac = next(h for h in holdings if h.ticker == "BAC")
    assert bac.change_pct < 0
    assert sold[0].pct == 0.0


def test_names_use_the_sec_title(quarters):
    names = {p["ticker"]: pretty_name(p["issuer"], p.get("sec_title")) for p in quarters[0]}
    assert names["AAPL"] == "Apple"
    assert names["BAC"] == "Bank of America"


def test_ticker_helpers():
    assert norm_ticker("BRK-B") == "BRK.B"
    assert norm_ticker("LEN/B") == "LEN.B"


def test_bundled_filers_include_berkshire():
    assert any(f["cik"] == BERKSHIRE_CIK for f in load_filers())


@pytest.mark.asyncio
async def test_without_user_agent_returns_none():
    assert await get_fund_holdings(BERKSHIRE_CIK, "") is None


def _mock_sec():
    respx.get(SUBMISSIONS_URL.format(cik=BERKSHIRE_CIK)).mock(return_value=httpx.Response(200, json=_json("CIK0001067983.json")))
    for folder in (Q2, Q1):
        base = ARCHIVE_URL.format(cik=1067983, folder=folder)
        index = _json(f"{folder}/index.json")
        respx.get(base + "index.json").mock(return_value=httpx.Response(200, json=index))
        name = info_table_name(index)
        respx.get(base + name).mock(return_value=httpx.Response(200, content=(FIX / folder / name).read_bytes()))
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=_json("company_tickers.json")))
    return respx.post(FIGI_URL).mock(return_value=httpx.Response(200, json=_json("openfigi.json")))


@pytest.mark.asyncio
@respx.mock
async def test_get_fund_holdings_end_to_end_and_cached(tmp_path):
    figi = _mock_sec()
    cache = CacheService(cache_dir=tmp_path)
    fund = await get_fund_holdings(BERKSHIRE_CIK, UA, cache)
    assert fund.name == "Berkshire Hathaway Inc"
    assert (fund.period, fund.prior_period) == ("2026-06-30", "2026-03-31")
    assert fund.holdings[0].ticker == "AAPL"
    assert [h.ticker for h in fund.sold] == ["STZ"]
    assert figi.called  # tickers dudosos se verifican en OpenFIGI

    calls = respx.calls.call_count
    again = await get_fund_holdings(BERKSHIRE_CIK, UA, cache)
    assert again == fund and respx.calls.call_count == calls


@pytest.mark.asyncio
@respx.mock
async def test_openfigi_failure_keeps_name_matches(tmp_path):
    _mock_sec().mock(return_value=httpx.Response(429))
    fund = await get_fund_holdings(BERKSHIRE_CIK, UA, CacheService(cache_dir=tmp_path))
    assert fund.holdings[0].ticker == "AAPL"


@pytest.mark.asyncio
@respx.mock
async def test_summary_for_prompt_only_mentions_analyzed_tickers(tmp_path):
    _mock_sec()
    fund = await get_fund_holdings(BERKSHIRE_CIK, UA, CacheService(cache_dir=tmp_path))
    line = summarize_for(["AAPL", "BAC", "STZ", "NVDA"], fund)
    assert line.startswith("Berkshire Hathaway Inc (13F al 2026-06-30, presentado 2026-08-14):")
    assert "AAPL 22.0% del fondo, SIN CAMBIOS" in line
    assert "BAC 9.2% del fondo, REDUJO" in line
    assert "STZ VENDIÓ TODO" in line
    assert "NVDA" not in line
    assert summarize_for(["NVDA"], fund) == ""


@pytest.mark.asyncio
@respx.mock
async def test_blocked_by_sec_raises_a_clear_error(tmp_path):
    from core.services.fundamentals_service import SECBlockedError

    _mock_sec()
    respx.get(ARCHIVE_URL.format(cik=1067983, folder=Q2) + "index.json").mock(return_value=httpx.Response(403))
    with pytest.raises(SECBlockedError, match="403"):
        await get_fund_holdings(BERKSHIRE_CIK, UA, CacheService(cache_dir=tmp_path))
