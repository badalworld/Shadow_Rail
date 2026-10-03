"""Indicator port correctness — Pine semantics + GCSR behaviour."""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.indicators import ghost, pine
from app.util import Candle


def mk_series(prices, start_t=0, step=300_000, vol=1000.0):
    out = []
    for i, p in enumerate(prices):
        o = prices[i - 1] if i else p
        out.append(Candle(start_t + i * step, o, max(o, p) * 1.001, min(o, p) * 0.999, p, vol))
    return out


# ───────────────────────────────── Pine primitives ─────────────────────────
def test_sma_matches_manual_and_propagates_na():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = pine.sma(x, 3)
    assert math.isnan(out[0]) and math.isnan(out[1])
    assert out[2] == pytest.approx(2.0)
    assert out[4] == pytest.approx(4.0)
    with_na = np.array([1.0, np.nan, 3.0, 4.0])
    out = pine.sma(with_na, 2)
    assert math.isnan(out[1])          # window [1, na] contains na → na (Pine rule)
    assert math.isnan(out[2])          # window [na, 3] contains na → na
    assert out[3] == pytest.approx(3.5)


def test_ema_seeds_with_sma_then_alpha():
    x = [10.0, 20.0, 30.0, 40.0, 50.0]
    out = pine.ema(x, 3)
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(20.0)                 # SMA seed
    alpha = 2 / (3 + 1)
    assert out[3] == pytest.approx(alpha * 40 + (1 - alpha) * 20)
    assert out[4] == pytest.approx(alpha * 50 + (1 - alpha) * out[3])


def test_rma_wilder_and_atr():
    x = [2.0, 4.0, 6.0, 8.0]
    out = pine.rma(x, 2)
    assert out[1] == pytest.approx(3.0)
    assert out[2] == pytest.approx(0.5 * 6 + 0.5 * 3.0)
    candles = mk_series([100, 102, 101, 105, 107, 104, 108, 110, 109, 112])
    atr = pine.atr([c.h for c in candles], [c.l for c in candles],
                   [c.c for c in candles], 3)
    assert not math.isnan(atr[-1]) and atr[-1] > 0


def test_stdev_is_population():
    x = [1.0, 2.0, 3.0, 4.0]
    assert pine.stdev(x, 4)[3] == pytest.approx(float(np.std(x)))


def test_linreg_slope_on_linear_series():
    x = [float(i) for i in range(20)]
    out = pine.linreg(x, 5, 0)
    assert out[-1] == pytest.approx(19.0, abs=1e-6)      # straight line fits exactly


def test_crossover_semantics():
    a = [1.0, 1.0, 3.0, 4.0]
    b = [2.0, 2.0, 2.0, 2.0]
    assert list(pine.crossover(a, b)) == [False, False, True, False]


# ───────────────────────────────── GCSR pipeline ───────────────────────────
def test_selftest_passes():
    res = ghost.selftest()
    assert res["ok"], res
    assert res["flips_confirmed"] <= res["flips_raw"]
    assert res["rail_bars"] > 0


def test_trend_side_is_only_1_or_minus_1_after_warmup():
    candles = mk_series([100 + 8 * math.sin(i / 12.0) for i in range(400)])
    s = ghost.compute(candles, ghost.GhostParams(mtfGate=False))
    assert set(np.unique(s.trend[200:])).issubset({-1, 1})


def test_flips_alternate_and_risk_map_is_directional():
    candles = mk_series([100 + 10 * math.sin(i / 9.0) for i in range(600)])
    s = ghost.compute(candles, ghost.GhostParams(mtfGate=False))
    ups = np.where(s.turn_up)[0]
    dns = np.where(s.turn_dn)[0]
    assert len(ups) + len(dns) > 0
    # a confirmed flip always has risk levels on the correct side of entry
    for i in ups:
        assert s.stop_lvl[i] < s.entry_lvl[i] < s.target_lvl[i]
    for i in dns:
        assert s.target_lvl[i] < s.entry_lvl[i] < s.stop_lvl[i]


def test_htf_gate_blocks_counter_trend_flips():
    # strong uptrend with deep history: LONG flips allowed, SHORT flips blocked
    candles = mk_series([100 * (1.002 ** i) for i in range(1500)])
    gated = ghost.compute(candles, ghost.GhostParams(mtfGate=True, mtfFrame="60",
                                                    mtfEmaBars=50))
    assert gated.htf_ready[-1] is True or bool(gated.htf_ready[-1])
    assert gated.htf_bull[-1] == 1.0
    assert gated.turn_dn.sum() == 0


def test_htf_gate_reports_not_ready_without_enough_history():
    """Regression guard: too little history must NOT silently produce a
    one-sided (short-only) signal stream — the series is flagged not-ready."""
    candles = mk_series([100 * (1.002 ** i) for i in range(600)])
    g = ghost.compute(candles, ghost.GhostParams(mtfGate=True, mtfFrame="60",
                                                mtfEmaBars=50))
    assert bool(g.htf_ready[-1]) is False
    sig = ghost.latest_signal(g)
    if sig:
        assert sig["htf_ready"] is False


def test_strong_flip_tier_requires_quality():
    candles = mk_series([100 + 12 * math.sin(i / 7.0) for i in range(800)])
    s = ghost.compute(candles, ghost.GhostParams(mtfGate=False))
    strong = np.where(s.strong_up | s.strong_dn)[0]
    for i in strong:
        assert s.clean_ratio[i] >= 0.60
        assert s.turn_up[i] or s.turn_dn[i]


def test_latest_signal_payload():
    candles = mk_series([100 + 15 * math.sin(i / 11.0) for i in range(700)])
    s = ghost.compute(candles, ghost.GhostParams(mtfGate=False))
    idx = int(np.where(s.turn_up | s.turn_dn)[0][-1])
    sig = ghost.latest_signal(s, idx)
    assert sig and sig["direction"] in ("LONG", "SHORT")
    assert sig["atr"] > 0 and sig["entry"] > 0
    assert 0 <= sig["trend_quality"] <= 1.5
    assert sig["tier"] in ("strong", "normal")
