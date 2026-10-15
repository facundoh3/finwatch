"""
Qué tienen y qué movieron los grandes fondos, según su 13F-HR trimestral en SEC EDGAR.

Adaptado de trader-skill/trader-copycat/edgar13f.py — MIT, (c) 2026 Jake
Schincariol, ver third_party/trader-skill/LICENSE. filers.json y
share_classes.json vienen de ese mismo repo.

Un 13F muestra las posiciones largas en acciones de EE.UU. al último día del
trimestre y se presenta hasta 45 días después: no muestra ventas en corto,
efectivo, bonos ni lo que compraron y vendieron dentro del trimestre.
"""
import datetime as _dt
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from loguru import logger

from core.models.institutional import FundHoldings, Holding
from core.services.cache_service import CacheService
from core.services.fundamentals_service import TICKERS_URL, SECClient, valid_user_agent

_DATA = Path(__file__).parent.parent.parent / "third_party" / "trader-skill"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"
FIGI_URL = "https://api.openfigi.com/v3/mapping"
BERKSHIRE_CIK = "0001067983"
_DOLLARS_FROM = _dt.date(2023, 1, 3)
_CACHE_TTL_MIN = 24 * 60
_TICKERS_TTL_MIN = 7 * 24 * 60

STATUS_NEW, STATUS_ADDED, STATUS_TRIMMED, STATUS_SAME, STATUS_SOLD = (
    "NUEVA", "AUMENTÓ", "REDUJO", "SIN CAMBIOS", "VENDIÓ TODO")


def load_filers() -> list[dict]:
    return json.loads((_DATA / "filers.json").read_text())["filers"]


def _share_class_overrides() -> dict[str, str]:
    return json.loads((_DATA / "share_classes.json").read_text())["cusips"]


# ---------------------------------------------------------------- filings

def latest_two_13f(submissions: dict) -> tuple[dict, dict | None]:
    """El 13F-HR original más nuevo y el del trimestre anterior."""
    rec = submissions.get("filings", {}).get("recent", {})
    originals = [
        {"accession": rec["accessionNumber"][i], "filed": rec["filingDate"][i], "period": rec["reportDate"][i]}
        for i, form in enumerate(rec.get("form", []))
        if form == "13F-HR"
    ]
    if not originals:
        raise ValueError("este fondo no tiene 13F-HR")
    originals.sort(key=lambda f: (f["period"], f["filed"]), reverse=True)
    latest = originals[0]
    prior = next((f for f in originals if f["period"] < latest["period"]), None)
    return latest, prior


def info_table_name(index_json: dict) -> str:
    names = [it["name"] for it in index_json.get("directory", {}).get("item", [])]
    xmls = [n for n in names if n.lower().endswith(".xml") and n.lower() not in ("primary_doc.xml", "filingsummary.xml")]
    if not xmls:
        raise ValueError("el 13F no tiene tabla de información en XML")
    return next((n for n in xmls if re.search(r"info|table|13f", n, re.I)), xmls[0])


def value_multiplier(filed: str) -> int:
    """Desde 2023-01-03 los valores vienen en dólares; antes, en miles."""
    return 1 if _dt.date.fromisoformat(filed[:10]) >= _DOLLARS_FROM else 1000


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_info_table(data: bytes, multiplier: int = 1) -> list[dict]:
    rows = []
    for el in ET.fromstring(data).iter():
        if _local(el.tag) != "infoTable":
            continue
        f = {}
        for child in el.iter():
            name = _local(child.tag)
            if name not in f and child.text and child.text.strip():
                f[name] = child.text.strip()
        rows.append({
            "issuer": f.get("nameOfIssuer", ""),
            "title": f.get("titleOfClass", ""),
            "cusip": f.get("cusip", "").upper(),
            "value": float(f.get("value", "0").replace(",", "")) * multiplier,
            "shares": float(f.get("sshPrnamt", "0").replace(",", "")),
            "share_type": f.get("sshPrnamtType", "SH").upper(),
            "put_call": f.get("putCall", "").upper(),
        })
    return rows


def stock_positions(rows: list[dict]) -> tuple[list[dict], float]:
    """Una posición por emisor (clases de acciones sumadas), solo acciones. % sobre todo el 13F."""
    total = sum(r["value"] for r in rows)
    by_issuer: dict[str, dict] = {}
    for r in rows:
        if r["share_type"] != "SH" or r["put_call"]:
            continue
        key = r["cusip"][:6]
        p = by_issuer.setdefault(key, {"issuer": r["issuer"], "title": r["title"], "cusip": r["cusip"],
                                       "value": 0.0, "shares": 0.0, "class_values": {}})
        p["value"] += r["value"]
        p["shares"] += r["shares"]
        p["class_values"][r["cusip"]] = p["class_values"].get(r["cusip"], 0.0) + r["value"]
    positions = list(by_issuer.values())
    for p in positions:
        # el ticker y el nombre salen de la clase con más valor
        p["cusip"] = max(p["class_values"], key=p["class_values"].get)
        p["pct"] = round(100.0 * p["value"] / total, 2) if total else 0.0
    positions.sort(key=lambda p: (-p["value"], p["cusip"]))
    return positions, total


# ---------------------------------------------------------------- tickers

_SUFFIX_STATE = re.compile(r"\s*/\s*[A-Z]{2,5}\s*/?\s*$")
_ABBREV = {
    "AMER": "AMERICA", "AMERN": "AMERICAN", "FINL": "FINANCIAL", "FIN": "FINANCIAL",
    "PETE": "PETROLEUM", "HLDGS": "HOLDINGS", "HLDG": "HOLDING", "HOLDG": "HOLDING",
    "INTL": "INTERNATIONAL", "GRP": "GROUP", "SVCS": "SERVICES", "SVC": "SERVICE",
    "SYS": "SYSTEMS", "MGMT": "MANAGEMENT", "INDS": "INDUSTRIES", "NATL": "NATIONAL",
    "ENTMT": "ENTERTAINMENT", "PAC": "PACIFIC", "LABS": "LABORATORIES",
    "PPTYS": "PROPERTIES", "RLTY": "REALTY", "TECHS": "TECHNOLOGIES", "TECH": "TECHNOLOGY",
    "COMMNS": "COMMUNICATIONS", "COMMUNICATN": "COMMUNICATIONS", "MFG": "MANUFACTURING",
    "ELEC": "ELECTRIC", "PHARMS": "PHARMACEUTICALS", "BANCORPORATION": "BANCORP",
    "RES": "RESOURCES", "WTR": "WATER", "MTRS": "MOTORS", "AIRLS": "AIRLINES", "CHEM": "CHEMICAL",
}
_DROP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
         "PLC", "LLC", "LP", "SA", "NV", "AG", "SE", "THE", "AND", "OF"}
_NONCOMMON = re.compile(r"-P[A-Z]*$|-WT[A-Z]*$|-WS$|-U$|-UN$|-R$|-RT$")


def _name_keys(name: str) -> tuple[str, str]:
    s = (name or "").upper().replace("&", " AND ")
    for _ in range(2):
        s = _SUFFIX_STATE.sub("", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s.replace("'", "").replace("’", ""))
    words = [w for w in (_ABBREV.get(w, w) for w in s.split()) if w not in _DROP]
    return "".join(words), " ".join(sorted(words))


def norm_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    return t if _NONCOMMON.search(t) else re.sub(r"[-/ ]([A-Z]{1,2})$", r".\1", t)


def _class_letter(title: str) -> str | None:
    m = re.search(r"\b(?:CL|CLASS|SER|SERIES)\s*([A-Z])\b", (title or "").upper())
    return m.group(1) if m else None


class TickerIndex:
    """Busca el ticker de un emisor del 13F por su nombre en company_tickers.json de la SEC."""

    def __init__(self, rows: list[tuple[str, str, str]]):
        self.by_cik: dict[str, list[str]] = {}
        self.titles: dict[str, str] = {}
        self.spaceless: dict[str, set[str]] = {}
        self.sorted_words: dict[str, set[str]] = {}
        for cik, ticker, title in rows:
            self.by_cik.setdefault(cik, []).append(ticker)
            if cik in self.titles:
                continue
            self.titles[cik] = title
            a, b = _name_keys(title)
            if a:
                self.spaceless.setdefault(a, set()).add(cik)
                self.sorted_words.setdefault(b, set()).add(cik)

    def match(self, issuer: str, title_of_class: str) -> tuple[str | None, bool, str | None]:
        """(ticker, hay_que_verificarlo, nombre oficial en la SEC)."""
        a, b = _name_keys(issuer)
        if not a:
            return None, True, None
        cik, prefix = None, False
        for hits in (self.spaceless.get(a), self.sorted_words.get(b)):
            if hits and len(hits) == 1:
                cik = next(iter(hits))
                break
        if cik is None:
            # el 13F trunca nombres ("JEFFERIES FINANCIAL GROUP IN")
            best = sorted(((len(k), next(iter(c))) for k, c in self.spaceless.items()
                           if len(k) >= 8 and len(c) == 1 and (a.startswith(k) or k.startswith(a))), reverse=True)
            if best and (len(best) == 1 or best[0][0] > best[1][0]):
                cik, prefix = best[0][1], True
        if cik is None:
            return None, True, None
        title = self.titles.get(cik)
        tickers = self.by_cik.get(cik, [])
        cands = [t for t in tickers if not _NONCOMMON.search(t)] or tickers
        if len(cands) == 1 or all(t.startswith(cands[0]) for t in cands[1:]):
            return norm_ticker(cands[0]), prefix, title
        letter = _class_letter(title_of_class)
        if letter:
            for t in cands:
                if t.upper().endswith(("-" + letter, "." + letter, "/" + letter)):
                    return norm_ticker(t), prefix, title
        return norm_ticker(cands[0]), True, title


def parse_figi(cusips: list[str], answer: list[dict]) -> dict[str, str]:
    out = {}
    for c, res in zip(cusips, answer):
        data = (res or {}).get("data") or []
        stocks = [d for d in data if d.get("marketSector") == "Equity"] or data
        if stocks and stocks[0].get("ticker"):
            out[c] = norm_ticker(stocks[0]["ticker"])
    return out


def pretty_name(issuer: str, sec_title: str | None = None) -> str:
    name = _SUFFIX_STATE.sub("", sec_title or issuer or "").strip()
    name = re.sub(r",?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|Limited|plc|LLC|"
                  r"L\.P\.|N\.V\.|S\.A\.|New)$", "", name, flags=re.I).strip()
    if name.isupper():
        small = {"OF", "AND", "THE", "FOR", "IN"}
        name = " ".join(w.lower() if (w in small and i) else w.capitalize() for i, w in enumerate(name.split()))
    return name


def resolve_tickers(positions: list[dict], index: TickerIndex | None, figi: dict[str, str] | None = None) -> None:
    overrides = _share_class_overrides()
    figi = figi or {}
    for p in positions:
        c = p["cusip"]
        if index is not None and "sec_title" not in p:
            p["sec_title"] = index.match(p["issuer"], p["title"])[2]
        if c in overrides:
            p["ticker"], p["ticker_check"] = norm_ticker(overrides[c]), False
        elif c in figi:
            p["ticker"], p["ticker_check"] = figi[c], False
        elif index is not None:
            p["ticker"], p["ticker_check"], _ = index.match(p["issuer"], p["title"])
        else:
            p["ticker"], p["ticker_check"] = None, True


# ---------------------------------------------------------------- análisis

def diff(current: list[dict], prior: list[dict]) -> tuple[list[Holding], list[Holding]]:
    """Compara por emisor (6 primeros caracteres del CUSIP). Devuelve (actuales, vendidas)."""
    before = {p["cusip"][:6]: p for p in prior}
    now_keys = set()
    holdings = []
    for p in current:
        key = p["cusip"][:6]
        now_keys.add(key)
        q = before.get(key)
        if q is None:
            status, change = STATUS_NEW, None
        elif p["shares"] > q["shares"]:
            status, change = STATUS_ADDED, round(100.0 * (p["shares"] - q["shares"]) / q["shares"], 1)
        elif p["shares"] < q["shares"]:
            status, change = STATUS_TRIMMED, round(100.0 * (p["shares"] - q["shares"]) / q["shares"], 1)
        else:
            status, change = STATUS_SAME, 0.0
        holdings.append(_holding(p, status, change))
    sold = [_holding(q, STATUS_SOLD, -100.0, keep_value=False) for k, q in before.items() if k not in now_keys]
    sold.sort(key=lambda h: -h.value)
    return holdings, sold


def _holding(p: dict, status: str, change: float | None, keep_value: bool = True) -> Holding:
    return Holding(
        name=pretty_name(p["issuer"], p.get("sec_title")), ticker=p.get("ticker"), ticker_check=p.get("ticker_check", True),
        cusip=p["cusip"], value=p["value"], pct=p["pct"] if keep_value else 0.0,
        shares=p["shares"], status=status, change_pct=change,
    )


def summarize_for(tickers: list[str], fund: FundHoldings) -> str:
    """Una línea para el prompt con lo que el fondo tiene o movió de los tickers analizados."""
    wanted = {t.upper() for t in tickers}
    parts = []
    for h in fund.holdings:
        if h.ticker and h.ticker.upper() in wanted:
            change = f" {h.change_pct:+.0f}% acciones" if h.change_pct not in (None, 0.0) else ""
            parts.append(f"{h.ticker} {h.pct:.1f}% del fondo, {h.status}{change}")
    parts += [f"{h.ticker} {STATUS_SOLD}" for h in fund.sold if h.ticker and h.ticker.upper() in wanted]
    if not parts:
        return ""
    return f"{fund.name} (13F al {fund.period}, presentado {fund.filed}): " + "; ".join(parts)


# ------------------------------------------------------------------- red

async def _ticker_index(sec: SECClient, client: httpx.AsyncClient, cache: CacheService | None) -> TickerIndex:
    rows = cache.get("sec_company_tickers_rows", override_ttl_minutes=_TICKERS_TTL_MIN) if cache else None
    if rows is None:
        raw = await sec.get_json(client, TICKERS_URL)
        rows = [[str(v["cik_str"]).zfill(10), v["ticker"], v["title"]] for v in raw.values()]
        if cache:
            cache.set("sec_company_tickers_rows", rows)
    return TickerIndex([tuple(r) for r in rows])


async def _load_quarter(sec: SECClient, client: httpx.AsyncClient, cik: str, filing: dict) -> tuple[list[dict], float]:
    folder = ARCHIVE_URL.format(cik=int(cik), folder=filing["accession"].replace("-", ""))
    index = await sec.get_json(client, folder + "index.json")
    xml = await sec.get_bytes(client, folder + info_table_name(index))
    return stock_positions(parse_info_table(xml, value_multiplier(filing["filed"])))


async def _figi(client: httpx.AsyncClient, cusips: list[str]) -> dict[str, str]:
    """OpenFIGI sin API key: 10 CUSIPs por pedido. Es opcional: si falla, queda el match por nombre."""
    out = {}
    for i in range(0, len(cusips), 10):
        chunk = cusips[i:i + 10]
        try:
            resp = await client.post(FIGI_URL, json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in chunk])
            resp.raise_for_status()
            out.update(parse_figi(chunk, resp.json()))
        except Exception as e:
            logger.debug(f"OpenFIGI: {e}")
            break
    return out


async def get_fund_holdings(cik: str, user_agent: str, cache: CacheService | None = None,
                            top_for_figi: int = 30) -> FundHoldings | None:
    if not valid_user_agent(user_agent):
        return None
    cik = str(cik).zfill(10)
    hit = cache.get(f"sec_13f_{cik}", override_ttl_minutes=_CACHE_TTL_MIN) if cache else None
    if hit:
        return FundHoldings.model_validate(hit)

    filer = next((f for f in load_filers() if f["cik"] == cik), {})
    sec = SECClient(user_agent)
    async with httpx.AsyncClient(timeout=60.0) as client:
        submissions = await sec.get_json(client, SUBMISSIONS_URL.format(cik=cik))
        latest, prior = latest_two_13f(submissions)
        current, total = await _load_quarter(sec, client, cik, latest)
        previous = (await _load_quarter(sec, client, cik, prior))[0] if prior else []
        index = await _ticker_index(sec, client, cache)
        resolve_tickers(current + previous, index)
        unsure = [p["cusip"] for p in (current[:top_for_figi] + previous[:top_for_figi]) if p["ticker_check"]]
        if unsure:
            resolve_tickers(current + previous, index, await _figi(client, list(dict.fromkeys(unsure))))

    holdings, sold = diff(current, previous)
    folder = ARCHIVE_URL.format(cik=int(cik), folder=latest["accession"].replace("-", ""))
    fund = FundHoldings(
        cik=cik, name=filer.get("name") or submissions.get("name", cik), person=filer.get("person", ""),
        period=latest["period"], filed=latest["filed"], accession=latest["accession"], url=folder,
        prior_period=prior["period"] if prior else None, total_value=total, holdings=holdings, sold=sold,
    )
    if cache:
        cache.set(f"sec_13f_{cik}", fund.model_dump())
    return fund
