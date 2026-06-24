"""
Tests unitarios para core/services/tracker_service.py.
Usan tmp_path para no tocar el config/recommendation_tracker.json real.
Correr con: pytest tests/unit/test_tracker_service.py -v
"""

import json

import pytest

from core.services import tracker_service as ts


@pytest.fixture(autouse=True)
def isolated_tracker_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "_TRACKER_FILE", tmp_path / "recommendation_tracker.json")


def _entry(action: str, price_at_analysis: float, check_date: str = "2024-01-01") -> ts.TrackerEntry:
    return ts.TrackerEntry(
        date="2023-12-20",
        ticker="TSLA",
        action=action,
        price_at_analysis=price_at_analysis,
        check_days=10,
        check_date=check_date,
    )


# ─── resolve_pending: umbrales de movimiento real, no ruido ──────────────────

def test_buy_noise_move_is_neutral_not_correct():
    ts._save([_entry("BUY", 100.0)])
    ts.resolve_pending({"TSLA": 101.0})  # +1%, ruido bajo MEANINGFUL_MOVE_PCT
    entries = ts._load()
    assert entries[0].outcome == "NEUTRAL"


def test_buy_meaningful_gain_is_correct():
    ts._save([_entry("BUY", 100.0)])
    ts.resolve_pending({"TSLA": 105.0})  # +5% >= 3%
    assert ts._load()[0].outcome == "CORRECT"


def test_buy_small_drop_is_neutral_not_incorrect():
    ts._save([_entry("BUY", 100.0)])
    ts.resolve_pending({"TSLA": 95.0})  # -5%, no llega al -7% de stop-loss
    assert ts._load()[0].outcome == "NEUTRAL"


def test_buy_stop_loss_drop_is_incorrect():
    ts._save([_entry("BUY", 100.0)])
    ts.resolve_pending({"TSLA": 92.0})  # -8% <= -7%
    assert ts._load()[0].outcome == "INCORRECT"


def test_avoid_meaningful_drop_is_correct():
    ts._save([_entry("AVOID", 100.0)])
    ts.resolve_pending({"TSLA": 95.0})  # -5% <= -3%
    assert ts._load()[0].outcome == "CORRECT"


def test_avoid_strong_rally_is_incorrect():
    ts._save([_entry("AVOID", 100.0)])
    ts.resolve_pending({"TSLA": 108.0})  # +8% >= 7%
    assert ts._load()[0].outcome == "INCORRECT"


def test_wait_always_neutral():
    ts._save([_entry("WAIT", 100.0)])
    ts.resolve_pending({"TSLA": 130.0})
    assert ts._load()[0].outcome == "NEUTRAL"


# ─── get_accuracy_stats: aviso de muestra chica ──────────────────────────────

def test_accuracy_stats_unreliable_below_threshold():
    entries = [_entry("BUY", 100.0) for _ in range(5)]
    for e in entries:
        e.resolved = True
        e.outcome = "CORRECT"
    ts._save(entries)
    stats = ts.get_accuracy_stats()
    assert stats["total"] == 5
    assert stats["reliable"] is False


def test_accuracy_stats_reliable_at_threshold():
    entries = [_entry("BUY", 100.0) for _ in range(ts.MIN_RELIABLE_SAMPLE)]
    for e in entries:
        e.resolved = True
        e.outcome = "CORRECT"
    ts._save(entries)
    stats = ts.get_accuracy_stats()
    assert stats["total"] == ts.MIN_RELIABLE_SAMPLE
    assert stats["reliable"] is True


def test_accuracy_stats_empty_is_unreliable():
    stats = ts.get_accuracy_stats()
    assert stats["reliable"] is False
    assert stats["total"] == 0
