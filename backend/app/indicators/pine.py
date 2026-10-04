"""
Pine Script `ta.*` primitives re-implemented with TradingView-exact semantics.

Rules honoured:
  * `na` is represented by numpy NaN and propagates through every window
    function — a window containing one NaN yields NaN (this is how ta.sma,
    ta.ema, ta.rma, ta.atr, ta.stdev… behave in Pine).
  * ta.ema seeds with the SMA of the first `length` valid samples,
    then alpha = 2/(length+1)   (TradingView implementation).
  * ta.rma (Wilder) seeds with the SMA of the first `length` valid samples,
    then alpha = 1/length.
  * ta.stdev is the *population* (biased) standard deviation.
  * ta.atr uses rma(trueRange, length).
"""
from __future__ import annotations

import numpy as np

NaN = float("nan")


# --------------------------------------------------------------------------- #
def _as_array(x) -> np.ndarray:
    if isinstance(x, np.ndarray):
        return x.astype(float, copy=False)
    return np.asarray(list(x), dtype=float)


def sma(src, length: int) -> np.ndarray:
    x = _as_array(src)
    n = len(x)
    out = np.full(n, np.nan)
    if length <= 0 or n < length:
        return out
    valid = ~np.isnan(x)
    csum = np.concatenate(([0.0], np.cumsum(np.where(valid, x, 0.0))))
    ccount = np.concatenate(([0], np.cumsum(valid.astype(int))))
    for i in range(length - 1, n):
        if ccount[i + 1] - ccount[i + 1 - length] == length:
            out[i] = (csum[i + 1] - csum[i + 1 - length]) / length
    return out


def _seeded_ma(x: np.ndarray, length: int, alpha: float, seed: str = "sma") -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan)
    if length <= 0 or n == 0:
        return out
    valid = ~np.isnan(x)
    start = None
    run = 0
    for i in range(n):
        if valid[i]:
            run += 1
            if run == length:
                start = i - length + 1
                break
        else:
            run = 0
    if start is None:
        return out
    window = x[start:start + length]
    if seed == "sma":
        prev = float(np.sum(window) / length)
    else:                       # first sample
        prev = float(window[0])
    out[start + length - 1] = prev
    for i in range(start + length, n):
        v = x[i]
        if np.isnan(v):
            out[i] = np.nan              # Pine: na propagates
            continue
        prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def ema(src, length: int) -> np.ndarray:
    x = _as_array(src)
    return _seeded_ma(x, length, 2.0 / (length + 1.0), seed="sma")


def rma(src, length: int) -> np.ndarray:
    x = _as_array(src)
    return _seeded_ma(x, length, 1.0 / float(length), seed="sma")


def change(src) -> np.ndarray:
    x = _as_array(src)
    out = np.full(len(x), np.nan)
    if len(x) > 1:
        out[1:] = x[1:] - x[:-1]
    return out


def rolling_sum(src, length: int) -> np.ndarray:
    x = _as_array(src)
    n = len(x)
    out = np.full(n, np.nan)
    if length <= 0 or n < length:
        return out
    valid = ~np.isnan(x)
    csum = np.concatenate(([0.0], np.cumsum(np.where(valid, x, 0.0))))
    ccount = np.concatenate(([0], np.cumsum(valid.astype(int))))
    for i in range(length - 1, n):
        if ccount[i + 1] - ccount[i + 1 - length] == length:
            out[i] = csum[i + 1] - csum[i + 1 - length]
    return out


def highest(src, length: int) -> np.ndarray:
    x = _as_array(src)
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(length - 1, n):
        w = x[i - length + 1:i + 1]
        out[i] = np.nan if np.isnan(w).any() else float(np.max(w))
    return out


def stdev(src, length: int) -> np.ndarray:
    x = _as_array(src)
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(length - 1, n):
        w = x[i - length + 1:i + 1]
        if np.isnan(w).any():
            continue
        out[i] = float(np.std(w))          # population / biased, as in Pine
    return out


def adx(high, low, close, length: int) -> np.ndarray:
    """
    Pine ta.dmi / ta.adx — Wilder's Average Directional Index.

        upMove   = high - high[1]
        downMove = low[1] - low
        +DM      = upMove > downMove and upMove > 0 ? upMove : 0
        -DM      = downMove > upMove and downMove > 0 ? downMove : 0
        tr       = ta.tr(true)
        +DI      = 100 * ta.rma(+DM, length) / ta.rma(tr, length)
        -DI      = 100 * ta.rma(-DM, length) / ta.rma(tr, length)
        dx       = 100 * abs(+DI - -DI) / (+DI + -DI)
        adx      = ta.rma(dx, length)

    Not part of the Ghost Candle port — a Strategy-layer primitive (used by
    the entry trend-strength gate).
    """
    h, l, c = _as_array(high), _as_array(low), _as_array(close)
    n = len(h)
    out = np.full(n, np.nan)
    if length <= 1 or n < 2:
        return out
    up_move = np.full(n, 0.0)
    dn_move = np.full(n, 0.0)
    for i in range(1, n):
        up_move[i] = h[i] - h[i - 1]
        dn_move[i] = l[i - 1] - l[i]
    pos_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    tr = true_range(h, l, c)
    tr_r = rma(tr, length)
    pos_dm_r = rma(pos_dm, length)          # each rma computed exactly once
    neg_dm_r = rma(neg_dm, length)
    with np.errstate(invalid="ignore", divide="ignore"):
        pos_di = np.where((tr_r > 0) & ~np.isnan(tr_r), 100.0 * pos_dm_r / tr_r, np.nan)
        neg_di = np.where((tr_r > 0) & ~np.isnan(tr_r), 100.0 * neg_dm_r / tr_r, np.nan)
    dx = np.full(n, np.nan)
    denom = pos_di + neg_di
    valid = (~np.isnan(pos_di)) & (~np.isnan(neg_di)) & (denom > 0)
    dx[valid] = 100.0 * np.abs(pos_di[valid] - neg_di[valid]) / denom[valid]
    return rma(dx, length)


def true_range(high, low, close) -> np.ndarray:
    h, l, c = _as_array(high), _as_array(low), _as_array(close)
    n = len(h)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    out[0] = h[0] - l[0]
    for i in range(1, n):
        pc = c[i - 1]
        if np.isnan(pc):
            out[i] = h[i] - l[i]
        else:
            out[i] = max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc))
    return out


def atr(high, low, close, length: int) -> np.ndarray:
    return rma(true_range(high, low, close), length)


def crossover(a, b) -> np.ndarray:
    x, y = _as_array(a), _as_array(b)
    n = len(x)
    out = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(x[i]) or np.isnan(y[i]) or np.isnan(x[i - 1]) or np.isnan(y[i - 1]):
            continue
        out[i] = x[i] > y[i] and x[i - 1] <= y[i - 1]
    return out


def linreg(src, length: int, offset: int = 0) -> np.ndarray:
    """Least-squares linear regression value projected `offset` bars ahead."""
    x = _as_array(src)
    n = len(x)
    out = np.full(n, np.nan)
    idx = np.arange(length, dtype=float)
    idx_mean = idx.mean()
    idx_var = float(np.sum((idx - idx_mean) ** 2))
    for i in range(length - 1, n):
        w = x[i - length + 1:i + 1]
        if np.isnan(w).any():
            continue
        w_mean = w.mean()
        slope = float(np.sum((idx - idx_mean) * (w - w_mean)) / idx_var) if idx_var else 0.0
        intercept = w_mean - slope * idx_mean
        out[i] = intercept + slope * (length - 1 + offset)
    return out


def nz(value, replacement: float = 0.0):
    """Pine nz() — for scalars and arrays."""
    if isinstance(value, np.ndarray):
        return np.where(np.isnan(value), replacement, value)
    if value is None:
        return replacement
    try:
        import math
        return replacement if math.isnan(float(value)) else value
    except (TypeError, ValueError):
        return replacement


def na(value) -> bool:
    if value is None:
        return True
    if isinstance(value, np.ndarray):
        return bool(np.isnan(value).all())
    try:
        import math
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True
