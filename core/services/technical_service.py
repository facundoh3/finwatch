"""
Checklist técnico determinístico basado en:
- Stan Weinstein: Stage Analysis (30-week MA)
- William O'Neil: CAN SLIM (SMA50, volumen, 52w high)
- Clásico: RSI 14, SMA20
Sin LLM — matemática pura.
"""
from dataclasses import dataclass

import pandas as pd


@dataclass
class TechnicalSignal:
    name: str
    ok: bool
    detail: str
    icon: str = ""

    def __post_init__(self):
        self.icon = "✅" if self.ok else "❌"


@dataclass
class TechnicalReport:
    ticker: str
    signals: list[TechnicalSignal]
    score: int
    max_score: int
    stage: str

    @property
    def grade(self) -> str:
        ratio = self.score / self.max_score if self.max_score else 0
        if ratio >= 0.8:
            return "FUERTE"
        elif ratio >= 0.5:
            return "MIXTO"
        else:
            return "DÉBIL"

    @property
    def grade_color(self) -> str:
        return {"FUERTE": "#1a4a1a", "MIXTO": "#4a3a00", "DÉBIL": "#4a1010"}.get(self.grade, "#333")

    @property
    def summary(self) -> str:
        return f"{self.score}/{self.max_score} señales OK — {self.grade}"


def analyze(ticker: str, df: pd.DataFrame, current_price: float | None = None, high_52w: float | None = None) -> TechnicalReport:
    """
    Evalúa 6 señales técnicas.
    df: DataFrame diario con OHLCV, idealmente 200+ velas.
    """
    price = current_price or (float(df["Close"].iloc[-1]) if not df.empty else None)
    if price is None or df.empty:
        return TechnicalReport(ticker=ticker, signals=[], score=0, max_score=6, stage="Sin datos")

    signals: list[TechnicalSignal] = []
    close = df["Close"]

    # 1. Weinstein Stage 2: precio > SMA150 (30 semanas) con pendiente positiva
    sma150 = close.rolling(150).mean()
    sma150_val = float(sma150.dropna().iloc[-1]) if not sma150.dropna().empty else None
    if sma150_val:
        sma150_prev = float(sma150.dropna().iloc[-5]) if len(sma150.dropna()) >= 5 else sma150_val
        rising = sma150_val > sma150_prev
        ok = price > sma150_val and rising
        stage = "Stage 2 (alcista)" if ok else ("Stage 1/3 (lateral)" if price > sma150_val else "Stage 4 (bajista)")
        signals.append(TechnicalSignal(
            "Weinstein Stage 2",
            ok,
            f"Precio ${price:.2f} vs SMA150 ${sma150_val:.2f} {'↑' if rising else '↓'}",
        ))
    else:
        stage = "Indeterminado (pocos datos)"
        signals.append(TechnicalSignal("Weinstein Stage 2", False, "Necesita 150+ velas diarias"))

    # 2. O'Neil: precio > SMA50 (tendencia media)
    sma50 = close.rolling(50).mean()
    sma50_val = float(sma50.dropna().iloc[-1]) if not sma50.dropna().empty else None
    if sma50_val:
        ok = price > sma50_val
        signals.append(TechnicalSignal("SMA50 (O'Neil)", ok, f"${price:.2f} {'>' if ok else '<'} SMA50 ${sma50_val:.2f}"))
    else:
        signals.append(TechnicalSignal("SMA50 (O'Neil)", False, "Sin datos SMA50"))

    # 3. Clásico: precio > SMA20 (tendencia corta)
    sma20 = close.rolling(20).mean()
    sma20_val = float(sma20.dropna().iloc[-1]) if not sma20.dropna().empty else None
    if sma20_val:
        ok = price > sma20_val
        signals.append(TechnicalSignal("SMA20", ok, f"${price:.2f} {'>' if ok else '<'} SMA20 ${sma20_val:.2f}"))
    else:
        signals.append(TechnicalSignal("SMA20", False, "Sin datos SMA20"))

    # 4. RSI 14: zona saludable 40–70
    rsi_val = _calc_rsi(close, 14)
    if rsi_val is not None:
        ok = 40 <= rsi_val <= 70
        zone = "sobrecomprado" if rsi_val > 70 else ("sobrevendido" if rsi_val < 40 else "zona saludable")
        signals.append(TechnicalSignal("RSI 14", ok, f"RSI {rsi_val:.0f} — {zone}"))
    else:
        signals.append(TechnicalSignal("RSI 14", False, "Sin suficientes datos"))

    # 5. O'Neil: volumen en alza (últimos 5 días vs promedio 20 días)
    if "Volume" in df.columns and len(df) >= 20:
        recent_vol = df["Volume"].iloc[-5:].mean()
        avg_vol = df["Volume"].iloc[-20:].mean()
        ok = avg_vol > 0 and recent_vol >= avg_vol * 0.85
        signals.append(TechnicalSignal(
            "Volumen (O'Neil)",
            ok,
            f"Vol. reciente {recent_vol/1e6:.1f}M vs avg {avg_vol/1e6:.1f}M",
        ))
    else:
        signals.append(TechnicalSignal("Volumen (O'Neil)", False, "Sin datos de volumen"))

    # 6. O'Neil: precio dentro del 25% del máximo de 52 semanas
    h52 = high_52w
    if h52 is None:
        h52 = float(df["High"].rolling(min(252, len(df))).max().iloc[-1]) if not df.empty else None
    if h52 and h52 > 0:
        pct_below = (h52 - price) / h52
        ok = pct_below <= 0.25
        signals.append(TechnicalSignal(
            "52s máximo (O'Neil)",
            ok,
            f"A {pct_below*100:.0f}% del máximo ${h52:.2f}",
        ))
    else:
        signals.append(TechnicalSignal("52s máximo (O'Neil)", False, "Sin datos 52 semanas"))

    # 7. MACD: línea MACD por encima de la señal (momentum alcista)
    macd_ok = _calc_macd_bullish(close)
    if macd_ok is not None:
        signals.append(TechnicalSignal(
            "MACD",
            macd_ok,
            "MACD > señal (momentum alcista)" if macd_ok else "MACD < señal (momentum bajista)",
        ))
    else:
        signals.append(TechnicalSignal("MACD", False, "Sin suficientes datos (necesita 35+ velas)"))

    score = sum(1 for s in signals if s.ok)
    return TechnicalReport(ticker=ticker, signals=signals, score=score, max_score=len(signals), stage=stage)


def calc_stop_loss(buy_price: float, pct: float = 0.07) -> float:
    """Stop loss a -7% desde el precio de compra (regla de O'Neil)."""
    return round(buy_price * (1 - pct), 2)


def _calc_macd_bullish(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> bool | None:
    """True si MACD > línea de señal (momentum alcista). Necesita al menos slow+signal velas."""
    if len(close) < slow + signal:
        return None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return bool(macd_line.iloc[-1] > signal_line.iloc[-1])


def _calc_rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("inf"))
    rsi = 100 - 100 / (1 + rs)
    val = rsi.dropna()
    return float(val.iloc[-1]) if not val.empty else None
