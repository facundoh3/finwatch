"""
Tracker de precisión: guarda cada recomendación con su precio y la evalúa N días después.
Archivo: config/recommendation_tracker.json (ignorado en .gitignore — es dato local).
"""
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from loguru import logger

_TRACKER_FILE = Path(__file__).parent.parent.parent / "config" / "recommendation_tracker.json"

# Umbrales para decidir CORRECT/INCORRECT — un movimiento de ±1% es ruido, no
# una señal validada. MEANINGFUL_MOVE confirma la tesis; WRONG_MOVE la refuta
# con el mismo -7% que usa technical_service.calc_stop_loss (regla de O'Neil),
# para no inventar un número nuevo: si el movimiento hubiera saltado el
# stop-loss real de la app, la recomendación fue mala.
MEANINGFUL_MOVE_PCT = 0.03
WRONG_MOVE_PCT = 0.07

# Bajo esta cantidad de recomendaciones resueltas, el % de precisión no es
# estadísticamente confiable y la UI debe avisarlo en vez de mostrarlo pelado.
MIN_RELIABLE_SAMPLE = 30


@dataclass
class TrackerEntry:
    date: str
    ticker: str
    action: str
    price_at_analysis: float
    check_days: int
    check_date: str
    resolved: bool = False
    final_price: float | None = None
    outcome: str | None = None  # CORRECT / INCORRECT / NEUTRAL


def save_recommendations(recommendations: list, market_snapshots: list) -> None:
    price_map = {s.ticker: s.current_price for s in market_snapshots}
    entries = _load()
    today = date.today().isoformat()
    existing = {(e.date, e.ticker) for e in entries}
    added = 0
    for rec in recommendations:
        if (today, rec.ticker) in existing:
            continue
        price = price_map.get(rec.ticker)
        if not price:
            continue
        check_days = rec.wait_days or 10
        entries.append(TrackerEntry(
            date=today,
            ticker=rec.ticker,
            action=rec.action.value if hasattr(rec.action, "value") else str(rec.action),
            price_at_analysis=float(price),
            check_days=check_days,
            check_date=(date.today() + timedelta(days=check_days)).isoformat(),
        ))
        added += 1
    if added:
        _save(entries)
        logger.info(f"Tracker: {added} recomendaciones guardadas")


def resolve_pending(current_prices: dict) -> None:
    entries = _load()
    today = date.today().isoformat()
    resolved = 0
    for e in entries:
        if e.resolved or e.check_date > today:
            continue
        price_now = current_prices.get(e.ticker)
        if not price_now:
            continue
        pct = (price_now - e.price_at_analysis) / e.price_at_analysis
        if e.action == "BUY":
            if pct >= MEANINGFUL_MOVE_PCT:
                e.outcome = "CORRECT"
            elif pct <= -WRONG_MOVE_PCT:
                e.outcome = "INCORRECT"
            else:
                e.outcome = "NEUTRAL"
        elif e.action == "AVOID":
            if pct <= -MEANINGFUL_MOVE_PCT:
                e.outcome = "CORRECT"
            elif pct >= WRONG_MOVE_PCT:
                e.outcome = "INCORRECT"
            else:
                e.outcome = "NEUTRAL"
        else:
            e.outcome = "NEUTRAL"
        e.final_price = float(price_now)
        e.resolved = True
        resolved += 1
    if resolved:
        _save(entries)
        logger.info(f"Tracker: {resolved} entradas resueltas")


def get_accuracy_stats() -> dict:
    entries = _load()
    evaluable = [e for e in entries if e.resolved and e.outcome != "NEUTRAL"]
    if not evaluable:
        return {"total": 0, "correct": 0, "accuracy": None, "by_action": {}, "reliable": False}
    correct = sum(1 for e in evaluable if e.outcome == "CORRECT")
    by_action = {}
    for action in ("BUY", "AVOID"):
        subset = [e for e in evaluable if e.action == action]
        if subset:
            c = sum(1 for e in subset if e.outcome == "CORRECT")
            by_action[action] = {"total": len(subset), "correct": c, "pct": round(c / len(subset) * 100)}
    return {
        "total": len(evaluable),
        "correct": correct,
        "accuracy": round(correct / len(evaluable) * 100),
        "by_action": by_action,
        "pending": len([e for e in entries if not e.resolved]),
        "reliable": len(evaluable) >= MIN_RELIABLE_SAMPLE,
    }


def _load() -> list[TrackerEntry]:
    if not _TRACKER_FILE.exists():
        return []
    try:
        return [TrackerEntry(**e) for e in json.loads(_TRACKER_FILE.read_text())]
    except Exception as e:
        logger.warning(f"Tracker load error: {e}")
        return []


def _save(entries: list[TrackerEntry]) -> None:
    try:
        _TRACKER_FILE.write_text(json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"Tracker save error: {e}")
