"""
Strategy v2 — the win-rate layer.

Covers the pure decision functions (entry gates, break-even ratchet, time
stop, crowding, symbol veto), the Wilder ADX primitive, and the config
round-trip for the new `strategy` section.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.config import StrategySettings, ConfigStore
from app.indicators import pine as P
from app.strategy import (StrategySignals, breakeven_stop, direction_crowded,
                          r_progress, symbol_vetoed, time_stop_due)


# ─────────────────────────────────────────────────────────────────────────── #
# ADX primitive
# ─────────────────────────────────────────────────────────────────────────── #
def _candles(prices):
    from app.util import Candle
    out = []
    for i, p in enumerate(prices):
        out.append(Candle(i * 300_000, p, p * 1.001, p * 0.999, p, 100.0))
    return out


def test_adx_high_in_a_trend_and_low_in_flat_noise():
    n = 600
    up = [100 * (1 + 0.004 * i) for i in range(n)]
    dn = [100 * (1 - 0.004 * i) for i in range(n)]
    rng = np.random.default_rng(1)
    flat = [100 + float(rng.normal(0, 0.2)) for _ in range(n)]

    def adx_last(prices):
        cs = _candles(prices)
        return float(P.adx([c.h for c in cs], [c.l for c in cs],
                           [c.c for c in cs], 14)[-1])

    assert adx_last(up) > 60
    assert adx_last(dn) > 60
    assert adx_last(flat) < adx_last(up) / 3


def test_adx_length_respected_and_warmup_is_nan():
    prices = [100 + 0.01 * i for i in range(40)]
    cs = _candles(prices)
    length = 14
    out = P.adx([c.h for c in cs], [c.l for c in cs], [c.c for c in cs], length)
    # rma(rma(·)) → first valid bar at 2×length − 2 (Pine: tr[0] and +DM[0]=0
    # are valid, so each Wilder smoothing starts one bar earlier than na)
    first_valid = 2 * length - 2
    assert all(math.isnan(v) for v in out[:first_valid])
    assert not math.isnan(out[first_valid])


# ─────────────────────────────────────────────────────────────────────────── #
# entry gate
# ─────────────────────────────────────────────────────────────────────────── #
def _signals(adx=30.0, vol_exp=1.5, mom=1.0) -> StrategySignals:
    n = 10
    return StrategySignals(
        adx=np.full(n, adx), atr_pct=np.full(n, 0.5),
        vol_expansion=np.full(n, vol_exp), mom_diff=np.full(n, mom))


def test_gate_passes_when_all_layers_agree():
    ok, reasons = _signals().gate(5, "LONG", StrategySettings())
    assert ok and reasons == []


def test_gate_vetoes_weak_trend():
    ok, reasons = _signals(adx=12.0).gate(5, "LONG", StrategySettings())
    assert not ok and any("adx" in r for r in reasons)


def test_gate_vetoes_dead_volatility():
    ok, reasons = _signals(vol_exp=0.7).gate(5, "LONG", StrategySettings())
    assert not ok and any("vol" in r for r in reasons)


def test_gate_vetoes_momentum_against_the_flip():
    ok, reasons = _signals(mom=-2.0).gate(5, "LONG", StrategySettings())
    assert not ok and any("momentum" in r for r in reasons)
    ok, reasons = _signals(mom=2.0).gate(5, "SHORT", StrategySettings())
    assert not ok
    ok, reasons = _signals(mom=-2.0).gate(5, "SHORT", StrategySettings())
    assert ok


def test_gate_nan_never_vetoes():
    s = _signals()
    s.adx[5] = np.nan
    s.vol_expansion[5] = np.nan
    s.mom_diff[5] = np.nan
    ok, reasons = s.gate(5, "LONG", StrategySettings())
    assert ok and reasons == []


def test_gate_off_layers_are_ignored():
    cfg = StrategySettings(adx_filter=False, vol_regime_filter=False,
                           momentum_filter=False)
    ok, _ = _signals(adx=1.0, vol_exp=0.1, mom=-9.0).gate(5, "LONG", cfg)
    assert ok


# ─────────────────────────────────────────────────────────────────────────── #
# break-even ratchet
# ─────────────────────────────────────────────────────────────────────────── #
def test_breakeven_fires_after_one_r_and_locks_entry():
    cfg = StrategySettings()
    # LONG @ 100, stop 3 away (1 R = 3), ATR 1 → +3 moves R to 1.0
    cand = breakeven_stop(side="LONG", entry=100.0, mark=103.0, atr=1.0,
                          sl_distance=3.0, current_stop=97.0, cfg=cfg)
    assert cand is not None
    assert cand == pytest.approx(100.0 + cfg.breakeven_offset_atr * 1.0)
    assert cand > 100.0


def test_breakeven_does_not_fire_below_the_trigger():
    cfg = StrategySettings()
    assert breakeven_stop(side="LONG", entry=100.0, mark=102.99, atr=1.0,
                          sl_distance=3.0, current_stop=97.0, cfg=cfg) is None


def test_breakeven_never_loosens_the_stop():
    cfg = StrategySettings()
    # the ROI trail already parked the stop far above break-even
    assert breakeven_stop(side="LONG", entry=100.0, mark=103.0, atr=1.0,
                          sl_distance=3.0, current_stop=104.0, cfg=cfg) is None
    # short mirror
    assert breakeven_stop(side="SHORT", entry=100.0, mark=97.0, atr=1.0,
                          sl_distance=3.0, current_stop=96.0, cfg=cfg) is None


def test_breakeven_short_mirror():
    cfg = StrategySettings()
    cand = breakeven_stop(side="SHORT", entry=100.0, mark=97.0, atr=1.0,
                          sl_distance=3.0, current_stop=103.0, cfg=cfg)
    assert cand is not None and cand < 100.0


def test_breakeven_respects_the_disabled_switch():
    cfg = StrategySettings(breakeven_enabled=False)
    assert breakeven_stop(side="LONG", entry=100.0, mark=110.0, atr=1.0,
                          sl_distance=3.0, current_stop=97.0, cfg=cfg) is None


# ─────────────────────────────────────────────────────────────────────────── #
# time stop
# ─────────────────────────────────────────────────────────────────────────── #
def test_time_stop_cuts_dead_trades_only():
    cfg = StrategySettings()
    assert time_stop_due(bars_elapsed=25, r_now=0.1, cfg=cfg)
    assert not time_stop_due(bars_elapsed=23, r_now=0.1, cfg=cfg)
    assert not time_stop_due(bars_elapsed=30, r_now=0.5, cfg=cfg)
    assert not time_stop_due(bars_elapsed=99, r_now=-1.0,
                             cfg=StrategySettings(time_stop_enabled=False))


def test_r_progress_signs():
    assert r_progress("LONG", 100.0, 103.0, 3.0) == pytest.approx(1.0)
    assert r_progress("SHORT", 100.0, 103.0, 3.0) == pytest.approx(-1.0)
    assert r_progress("LONG", 0.0, 1.0, 0.0) == 0.0


# ─────────────────────────────────────────────────────────────────────────── #
# portfolio layers
# ─────────────────────────────────────────────────────────────────────────── #
def test_crowding_cap_counts_per_direction():
    cfg = StrategySettings()
    assert not direction_crowded("LONG", ["LONG", "LONG"], cfg)
    assert direction_crowded("LONG", ["LONG"] * 5, cfg)
    assert not direction_crowded("SHORT", ["LONG"] * 5, cfg)
    assert not direction_crowded("LONG", ["LONG"] * 5,
                                 StrategySettings(max_same_direction=0))


def test_symbol_veto_needs_a_sample_and_a_low_win_rate():
    cfg = StrategySettings()
    assert symbol_vetoed({"trades": 3, "win_rate": 10.0}, cfg) is None
    assert symbol_vetoed({"trades": 5, "win_rate": 10.0}, cfg) is not None
    assert symbol_vetoed({"trades": 5, "win_rate": 40.0}, cfg) is None
    assert symbol_vetoed(None, cfg) is None


# ─────────────────────────────────────────────────────────────────────────── #
# config round-trip
# ─────────────────────────────────────────────────────────────────────────── #
def test_strategy_section_round_trips_through_the_store(tmp_path):
    store = ConfigStore(path=tmp_path / "config.json")
    store.update({"strategy": {"adx_min": 25.0, "v2_enabled": False,
                               "breakeven_offset_atr": 0.5}})
    assert store.cfg.strategy.adx_min == 25.0
    assert store.cfg.strategy.v2_enabled is False
    assert store.cfg.strategy.breakeven_offset_atr == 0.5
    # untouched defaults survive a partial patch
    assert store.cfg.strategy.vol_regime_bars == 200
    assert store.unknown_keys({"strategy": {"adx_min": 20}}) == []
    assert store.unknown_keys({"strategy": {"nope": 1}}) == ["strategy.nope"]


def test_v2_defaults_are_sane():
    s = StrategySettings()
    assert s.v2_enabled
    assert s.adx_min >= 15 and s.adx_min <= 30
    assert 0 < s.breakeven_offset_atr < 1.0
    assert s.time_stop_bars > 0
    assert s.max_same_direction >= 1


# ─────────────────────────────────────────────────────────────────────────── #
# engine integration (real engine, sim transport)
# ─────────────────────────────────────────────────────────────────────────── #
from tests.test_engine_flow import _set_price, make_engine, make_opportunity  # noqa: E402


@pytest.mark.asyncio
async def test_breakeven_ratchet_moves_the_single_stop_and_labels_the_exit(db, store):
    eng = await make_engine(store, universe=12)
    store.cfg.risk.trail_roi_enabled = False      # isolate the break-even layer
    store.cfg.strategy.v2_enabled = True
    store.cfg.strategy.breakeven_enabled = True
    store.cfg.strategy.breakeven_r = 1.0
    store.cfg.strategy.breakeven_offset_atr = 0.3
    store.save()

    await _set_price(eng, "BTCUSDT", 100.0)
    # ATR 2 → 1 R = 1.5 × 2 = 3.0 price.  +3 moves R to 1.0.
    trade = await eng._open_trade(
        "execution-1", make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
        {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    assert trade is not None
    base = float(trade["entry_price"])
    fixed = float(trade["sl_price"])
    assert fixed < base                            # initial stop below entry

    # below the trigger: stop untouched
    await _tick_be(eng, trade, base + 2.0)
    assert float(trade["sl_price"]) == fixed

    # at +1 R: the stop jumps just past entry (break-even + offset)
    await _tick_be(eng, trade, base + 3.05)
    new_stop = float(trade["sl_price"])
    assert new_stop > base, "break-even stop must lock the entry"
    # exactly one live stop order, at the new price
    stops = [o for o in eng.hub.exchange.orders.values()
             if o.symbol == "BTCUSDT" and o.type == "STOP_MARKET" and o.status == "NEW"]
    assert len(stops) == 1
    assert stops[0].stop_price == pytest.approx(new_stop, rel=1e-6)

    # a further move must NOT move it again (the ratchet fires once)
    before = float(trade["sl_price"])
    await _tick_be(eng, trade, base + 6.0)
    assert float(trade["sl_price"]) == before

    # the close is labelled breakeven, not a plain stop-loss
    closed = await _finalise_be(eng, trade, new_stop)
    assert closed["close_reason"] == "breakeven", closed["close_reason"]
    assert closed["net_pnl"] > 0, "a break-even scratch must book a small win"


@pytest.mark.asyncio
async def test_time_stop_cuts_a_dead_trade(db, store):
    eng = await make_engine(store, universe=12)
    store.cfg.risk.trail_roi_enabled = False
    store.cfg.strategy.v2_enabled = True
    store.cfg.strategy.breakeven_enabled = False
    store.cfg.strategy.time_stop_enabled = True
    store.cfg.strategy.time_stop_bars = 1          # fire next bar for the test
    store.cfg.strategy.time_stop_min_r = 0.3
    store.save()

    await _set_price(eng, "BTCUSDT", 100.0)
    trade = await eng._open_trade(
        "execution-1", make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
        {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    assert trade is not None
    base = float(trade["entry_price"])

    # age the trade past the time-stop window, then tick with no progress
    trade["opened_at"] = int(eng.hub.exchange._virtual_now) - 2 * 300_000
    pos = next(p for p in await eng.hub.positions() if p.symbol == "BTCUSDT")
    await _set_price(eng, "BTCUSDT", base)         # flat: R ≈ 0
    pos.mark_price = base
    await eng._monitor_tick(trade, pos)

    assert int(trade["id"]) not in eng.open_trades, "the time stop must have closed it"
    row = await db.get_trade(int(trade["id"]))
    assert row["status"] == "closed"
    assert row["close_reason"] == "time_stop"


async def _tick_be(eng, trade, price):
    from tests.test_trail import _set_clean
    await _set_clean(eng, price)
    pos = next(p for p in await eng.hub.positions() if p.symbol == "BTCUSDT")
    pos.mark_price = price
    await eng._monitor_tick(trade, pos)


async def _finalise_be(eng, trade, stop_price):
    """Let the venue fill the (break-even) stop, then finalise from fills."""
    from tests.test_trail import _set_clean
    await _set_clean(eng, stop_price - 0.01, low=stop_price - 0.01)
    eng.hub.exchange.tickers["BTCUSDT"].mark = stop_price - 0.01
    await eng.hub.exchange._check_protective_orders()
    positions = await eng.hub.positions()
    assert not [p for p in positions if p.symbol == "BTCUSDT"], "the stop must fire"
    return await eng._finalize_close(int(trade["id"]), stop_price)
