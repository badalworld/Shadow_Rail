"""
Strategy v2 — the win-rate layer on top of Ghost Candle with Shadow Rail.

The base system (GCSR flip + 1h EMA-50 gate + 7-factor confidence) is kept
exactly as specified.  This module adds *veto-able* layers that were measured
to be worth less than a win rate in the live journal:

Entry gates (a flip that fails one is not traded — it costs nothing):
  * trend strength  — ADX(14) >= adx_min.  Flip systems whipsaw in flat,
                      choppy markets; ADX is the classic "is there a trend?"
                      filter.
  * volatility regime — ATR% must be >= `vol_expansion_min` times the median
                      ATR% of the previous `vol_regime_bars` bars.  Flips
                      earn their keep when volatility is expanding, not when
                      the tape is dead.
  * momentum alignment — the 5m close must sit on the signal side of its own
                      EMA-20, so the entry follows the short-term impulse
                      instead of fading it.

Exit layers (they only ever *tighten* the single protective stop, so the
hard "one TP/SL system" rule stays intact):
  * break-even ratchet — once the trade is up `breakeven_r` R (R = distance
    to the initial stop) the stop moves to entry + 0.1×ATR in favour.  Losers
    that round-tripped after running a full R become scratches, which is the
    single biggest win-rate lever in a 1.5×ATR-stop system.
  * time stop — a position that has not progressed `time_stop_min_r` R after
    `time_stop_bars` bars is closed: dead trades are usually just late losers
    paying the second fee leg.

Portfolio layers:
  * direction crowding — at most `max_same_direction` open positions per
    direction (10 correlated alts all long is one trade, not ten).
  * symbol veto — a symbol with `symbol_veto_min_trades`+ closed trades and a
    win rate below `symbol_veto_max_win_rate`% is hard-rejected, not just
    scored down.

Every decision is a pure function of (series, config) so the backtester
(`scripts/backtest.py`) can replay identical logic offline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .indicators import pine as P
from .config import StrategySettings


# ─────────────────────────────────────────────────────────────────────────── #
# signal series
# ─────────────────────────────────────────────────────────────────────────── #
@dataclass
class StrategySignals:
    """Bar-aligned Strategy-v2 series, computed over one symbol's candles."""
    adx: np.ndarray                 # Wilder ADX
    atr_pct: np.ndarray             # ATR14 as % of close
    vol_expansion: np.ndarray       # atr_pct[i] / median(atr_pct[i-w .. i-1])
    mom_diff: np.ndarray            # close - EMA(close, momentum_ema_bars)

    def gate(self, i: int, direction: str, cfg: StrategySettings
             ) -> tuple[bool, list[str]]:
        """
        Entry gate for a flip on bar `i`.  Returns (ok, reasons).
        NaN (indicator not warm) never vetoes — the HTF warm-up rule already
        refuses symbols without deep history, and a single missing sample
        must not silently kill a symbol.
        """
        reasons: list[str] = []
        if cfg.adx_filter:
            a = self.adx[i]
            if not math.isnan(a) and a < cfg.adx_min:
                reasons.append(f"adx {a:.1f} < {cfg.adx_min:.0f}")
        if cfg.vol_regime_filter:
            v = self.vol_expansion[i]
            if not math.isnan(v) and v < cfg.vol_expansion_min:
                reasons.append(f"vol expansion {v:.2f} < {cfg.vol_expansion_min:.2f}")
        if cfg.momentum_filter:
            d = self.mom_diff[i]
            if not math.isnan(d):
                aligned = (d > 0) if direction == "LONG" else (d < 0)
                if not aligned:
                    reasons.append("momentum against the flip")
        return (len(reasons) == 0), reasons

    def features(self, i: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, arr in (("adx", self.adx), ("vol_expansion", self.vol_expansion),
                         ("momentum", self.mom_diff)):
            v = arr[i]
            out[key] = 0.0 if math.isnan(v) else float(v)
        return out


def enrich(series: Any, cfg: StrategySettings) -> StrategySignals:
    """
    Compute the Strategy-v2 series for a GhostSeries (bar-aligned).
    `series` must carry open/high/low/close and atr14 as in ghost.GhostSeries.
    """
    n = len(series.times)
    close = np.asarray(series.close, dtype=float)
    atr = np.asarray(series.atr14, dtype=float)
    atr_pct = np.full(n, np.nan)
    for i in range(n):
        if not math.isnan(atr[i]) and close[i] > 0:
            atr_pct[i] = atr[i] / close[i] * 100.0

    adx = P.adx(series.high, series.low, close, max(2, int(cfg.adx_length)))

    # volatility expansion vs the trailing median (window excludes bar i so the
    # gate never reacts to the bar it is filtering)
    w = max(20, int(cfg.vol_regime_bars))
    vol_expansion = np.full(n, np.nan)
    if n > w:
        from numpy.lib.stride_tricks import sliding_window_view
        win = sliding_window_view(atr_pct, w + 1)          # (n-w, w+1)
        med = np.full(n, np.nan)
        valid = ~np.isnan(win[:, :-1]).any(axis=1)          # history window complete
        if valid.any():
            med[w:][valid] = np.median(win[:, :-1][valid], axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = atr_pct / med
        vol_expansion[:] = ratio

    mom = P.ema(close, max(2, int(cfg.momentum_ema_bars)))
    mom_diff = np.full(n, np.nan)
    for i in range(n):
        if not math.isnan(mom[i]):
            mom_diff[i] = close[i] - mom[i]

    return StrategySignals(adx=adx, atr_pct=atr_pct,
                           vol_expansion=vol_expansion, mom_diff=mom_diff)


# ─────────────────────────────────────────────────────────────────────────── #
# exit layers (pure maths — the engine and the backtester share them)
# ─────────────────────────────────────────────────────────────────────────── #
def r_progress(side: str, entry: float, mark: float, sl_distance: float) -> float:
    """Unrealised progress in R units (1 R = the distance to the initial stop)."""
    if sl_distance <= 0 or entry <= 0:
        return 0.0
    if side == "LONG":
        return (mark - entry) / sl_distance
    return (entry - mark) / sl_distance


def breakeven_stop(*, side: str, entry: float, mark: float, atr: float,
                   sl_distance: float, current_stop: float,
                   cfg: StrategySettings) -> float | None:
    """
    Candidate break-even stop, or None when no move is due.

    Rules:
      * only after the mark has progressed `breakeven_r` R in favour;
      * stop = entry ± `breakeven_offset_atr` × ATR (a hair past entry so a
        round-trip does not book a micro-loss through fees alone);
      * never loosens: the candidate must be strictly tighter than the
        current stop (higher for LONG, lower for SHORT);
      * never on the wrong side of the mark (placement guard).
    """
    if not cfg.breakeven_enabled:
        return None
    if atr <= 0 or sl_distance <= 0 or current_stop <= 0:
        return None
    if r_progress(side, entry, mark, sl_distance) < cfg.breakeven_r:
        return None
    offset = cfg.breakeven_offset_atr * atr
    if side == "LONG":
        cand = entry + offset
        if cand <= current_stop:
            return None          # stop already at/beyond break-even
        if cand >= mark:
            return None          # would sit at/through the mark — never place
        return cand
    cand = entry - offset
    if cand >= current_stop:
        return None
    if cand <= mark:
        return None
    return cand


def time_stop_due(*, bars_elapsed: float, r_now: float,
                  cfg: StrategySettings) -> bool:
    """A dead trade: held `time_stop_bars` bars without `time_stop_min_r` R."""
    if not cfg.time_stop_enabled or cfg.time_stop_bars <= 0:
        return False
    return bars_elapsed >= cfg.time_stop_bars and r_now < cfg.time_stop_min_r


# ─────────────────────────────────────────────────────────────────────────── #
# portfolio layers
# ─────────────────────────────────────────────────────────────────────────── #
def direction_crowded(side: str, open_sides: list[str], cfg: StrategySettings) -> bool:
    """True when adding another `side` position would exceed the per-direction cap."""
    cap = int(cfg.max_same_direction)
    if cap <= 0:
        return False
    same = sum(1 for s in open_sides if s == side)
    return same >= cap


def symbol_vetoed(symbol_stats: dict[str, Any] | None,
                  cfg: StrategySettings) -> str | None:
    """
    Hard veto on structurally losing symbols.  Returns a reason string when
    vetoed, None otherwise.  Needs a minimum sample so a single early loss
    cannot black-list a name.
    """
    if not symbol_stats:
        return None
    trades = int(symbol_stats.get("trades", 0) or 0)
    if trades < cfg.symbol_veto_min_trades:
        return None
    wr = float(symbol_stats.get("win_rate", 0.0) or 0.0)
    if wr < cfg.symbol_veto_max_win_rate:
        return (f"symbol veto: {wr:.0f}% win-rate over {trades} closed trades "
                f"(< {cfg.symbol_veto_max_win_rate:.0f}%)")
    return None
