"""Utilidades de calendario de mercado NYSE."""
from datetime import datetime, timedelta, timezone

_ET = timezone(timedelta(hours=-4))  # EDT (Mar-Nov); diferencia mínima en invierno (UTC-5)
_CLOSE_HOUR = 16
_CLOSE_MIN = 15


def get_last_close_date() -> str:
    """
    Retorna 'YYYY-MM-DD' del último día de cierre de NYSE.
    Si son las 10am ET del martes, retorna el lunes.
    Si son las 5pm ET del martes (ya cerró), retorna el martes.
    """
    now_et = datetime.now(_ET)
    close_today = now_et.replace(hour=_CLOSE_HOUR, minute=_CLOSE_MIN, second=0, microsecond=0)
    target = now_et if now_et >= close_today else now_et - timedelta(days=1)
    while target.weekday() >= 5:  # saltar sábado (5) y domingo (6)
        target -= timedelta(days=1)
    return target.strftime("%Y-%m-%d")


def minutes_until_next_close() -> int | None:
    """
    Minutos hasta el próximo cierre de NYSE.
    Retorna None si el mercado está cerrado (fin de semana o fuera de horario).
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return None
    open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_et = now_et.replace(hour=_CLOSE_HOUR, minute=_CLOSE_MIN, second=0, microsecond=0)
    if open_et <= now_et < close_et:
        return int((close_et - now_et).total_seconds() / 60)
    return None
