"""Shared helpers: time, numbers, symbol filters, candle maths."""
from __future__ import annotations

import math
import time
from typing import Sequence

# ---------------------------------------------------------------- time ------
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000,
}
TIMEFRAME_MIN: dict[str, int] = {k: v // 60_000 for k, v in TIMEFRAME_MS.items()}


def now_ms() -> int:
    return int(time.time() * 1000)



def normalize_tf(tf: str | int) -> str:
    """
    Accepts both interface styles: '5m' / '1h' AND Pine-style minute integers
    ('5', '60', '240') so indicator settings can be entered verbatim.
    """
    raw = str(tf).strip().lower()
    if raw in TIMEFRAME_MS:
        return raw
    if raw.isdigit():
        minutes = int(raw)
        for key, ms in TIMEFRAME_MS.items():
            if ms // 60_000 == minutes:
                return key
        # e.g. 90m -> keep as a custom minute timeframe
        if minutes > 0:
            return f"{minutes}m"
    raise ValueError(f"unsupported timeframe: {tf}")


def tf_ms(tf: str | int) -> int:
    tf = str(tf)
    if tf in TIMEFRAME_MS:
        return TIMEFRAME_MS[tf]
    norm = normalize_tf(tf)
    if norm in TIMEFRAME_MS:
        return TIMEFRAME_MS[norm]
    if norm.endswith("m") and norm[:-1].isdigit():     # custom minute timeframe
        return int(norm[:-1]) * 60_000
    raise ValueError(f"unsupported timeframe: {tf}")


def candle_open_ms(now: int, tf: str) -> int:
    step = tf_ms(tf)
    return (now // step) * step



def seconds_to_next_candle(now: int, tf: str) -> float:
    step = tf_ms(tf)
    return max(0.0, (candle_open_ms(now, tf) + step - now) / 1000.0)


# --------------------------------------------------------------- numbers ----
def fnum(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def pct(a: float, b: float) -> float:
    return 0.0 if not b else (a - b) / abs(b) * 100.0


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def round_step(value: float, step: float) -> float:
    """Round *down* to the exchange step size (never round up a quantity)."""
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return float(f"{math.floor(value / step + 1e-9) * step:.{precision}f}")


def round_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    precision = max(0, int(round(-math.log10(tick)))) if tick < 1 else 0
    return float(f"{round(price / tick) * tick:.{precision}f}")


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


# ---------------------------------------------------------------- candles ---
class Candle(tuple):
    """(open_time_ms, open, high, low, close, volume) — light immutable row."""
    __slots__ = ()

    def __new__(cls, open_time: int, o: float, h: float, l: float, c: float, v: float):
        return super().__new__(cls, (open_time, o, h, l, c, v))

    @property
    def t(self) -> int:
        return self[0]

    @property
    def o(self) -> float:
        return self[1]

    @property
    def h(self) -> float:
        return self[2]

    @property
    def l(self) -> float:
        return self[3]

    @property
    def c(self) -> float:
        return self[4]

    @property
    def v(self) -> float:
        return self[5]



def true_range(prev_close: float | None, high: float, low: float) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


