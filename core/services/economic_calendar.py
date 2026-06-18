"""
Calendario de eventos macro de alto impacto (FED, CPI, NFP) sin depender
de un plan pago de Finnhub (su /calendar/economic devuelve 403 en plan free).

FOMC y CPI: fechas estáticas en config/economic_calendar.yaml (mantenidas a
mano, se publican con meses de anticipación — actualizar 1 vez al año).
NFP: se calcula, siempre cae el primer viernes de cada mes.

Esto solo cubre eventos PROGRAMADOS. Anuncios sorpresa (ej. reunión de
emergencia de la FED, shock geopolítico) no pueden anticiparse en un
calendario por definición — esos se capturan en el pipeline de noticias
(context_agent) cuando ocurren, no acá.
"""
import calendar
from datetime import date, timedelta
from pathlib import Path

import yaml

_CALENDAR_PATH = Path(__file__).parent.parent.parent / "config" / "economic_calendar.yaml"

_LABELS = {
    "fomc": ("Decisión de tasas FED (FOMC)", "high"),
    "cpi": ("Inflación CPI (EE.UU.)", "high"),
}


def _load_static_events() -> list[dict]:
    if not _CALENDAR_PATH.exists():
        return []
    data = yaml.safe_load(_CALENDAR_PATH.read_text()) or {}
    events = []
    for key, dates in data.items():
        prefix = key.split("_")[0]
        if prefix not in _LABELS:
            continue
        label, impact = _LABELS[prefix]
        for d in dates or []:
            events.append({"event": label, "time": str(d), "impact": impact, "country": "US"})
    return events


def _first_friday(year: int, month: int) -> str:
    cal = calendar.monthcalendar(year, month)
    first_week, second_week = cal[0], cal[1]
    day = first_week[calendar.FRIDAY] or second_week[calendar.FRIDAY]
    return date(year, month, day).isoformat()


def _nfp_dates(year: int) -> list[str]:
    return [_first_friday(year, month) for month in range(1, 13)]


def get_upcoming_events(days_ahead: int = 10) -> list[dict]:
    """Eventos macro programados entre hoy y hoy+days_ahead, ordenados por fecha."""
    today = date.today()
    cutoff = (today + timedelta(days=days_ahead)).isoformat()
    today_str = today.isoformat()

    events = _load_static_events()
    for year in (today.year, today.year + 1):
        for d in _nfp_dates(year):
            events.append({"event": "Empleo no agrícola (NFP)", "time": d, "impact": "high", "country": "US"})

    upcoming = [e for e in events if today_str <= e["time"] <= cutoff]
    return sorted(upcoming, key=lambda e: e["time"])
