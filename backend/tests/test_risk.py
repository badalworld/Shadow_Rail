"""Risk engine — the rules that must never break."""
from __future__ import annotations

import pytest

from app.config import RiskSettings
from app.exchange.base import SymbolFilter
from app.risk import (RiskEngine, clamp_stop_to_liquidation, estimate_liquidation,
                      stop_loss_price, take_profit_price)

FLT = SymbolFilter(symbol="BTCUSDT", tick_size=0.1, step_size=0.001, min_qty=0.001,
                   min_notional=5.0, max_qty=1000.0)


def engine(**kw) -> RiskEngine:
    s = RiskSettings()
    for k, v in kw.items():
        setattr(s, k, v)
    return RiskEngine(s)


def plan(**kw):
    """Split kwargs: RiskSettings fields configure the engine, the rest the call."""
    settings = {k: v for k, v in kw.items() if k in RiskSettings.model_fields}
    call = {k: v for k, v in kw.items() if k not in RiskSettings.model_fields}
    defaults = dict(symbol="BTCUSDT", side="LONG", price=60_000.0, atr=600.0,
                    equity=10_000.0, available=10_000.0, flt=FLT, open_trades=0)
    defaults.update(call)
    return engine(**settings).plan(**defaults)


# ───────────────────────────── sizing ─────────────────────────────
def test_margin_is_eight_percent_of_equity_at_ten_x():
    p = plan()
    assert p.ok, p.reason
    # qty is floored to the exchange step, so margin never rounds *up* past 8%
    assert p.margin == pytest.approx(800.0, rel=0.01)          # 8% of 10k
    assert p.margin <= 800.0 + 1e-9
    assert p.notional == pytest.approx(8_000.0, rel=0.01)      # 10x
    assert p.qty * p.entry == pytest.approx(p.notional, rel=1e-3)
    assert p.leverage == 10 and p.margin_type == "CROSS"


def test_default_system_is_indicator_map_1_5_3_0():
    p = plan()
    sl_mult, tp_mult, tp_on = RiskSettings().active_tp_sl()
    assert (sl_mult, tp_mult, tp_on) == (1.5, 3.0, True)
    assert p.sl_price == pytest.approx(60_000 - 1.5 * 600, rel=1e-4)
    assert p.tp_price == pytest.approx(60_000 + 3.0 * 600, rel=1e-4)


def test_only_one_system_ever_active():
    e = engine(risk_mode="shadow_3x")
    assert e.s.active_tp_sl() == (3.0, 0.0, False)
    p = plan(risk_mode="shadow_3x", equity=10_000.0)
    assert p.ok and p.tp_price == 0.0 and p.tp_enabled is False
    assert p.sl_price == pytest.approx(60_000 - 3.0 * 600, rel=1e-4)
    e2 = engine(risk_mode="custom", custom_sl_atr_mult=2.0, custom_tp_atr_mult=4.0,
                custom_tp_enabled=True)
    assert e2.s.active_tp_sl() == (2.0, 4.0, True)
    e3 = engine(risk_mode="custom", custom_sl_atr_mult=2.0, custom_tp_atr_mult=4.0,
                custom_tp_enabled=False)
    assert e3.s.active_tp_sl()[2] is False


# ─────────────────────── liquidation protection ───────────────────────
def test_stop_never_crosses_liquidation_long_and_short():
    # a huge ATR stop would sit beyond liquidation at 10x — must be tightened
    p = plan(price=100.0, atr=30.0, equity=10_000.0)
    assert p.ok
    assert p.stop_clamped is True
    assert p.liquidation_price < p.sl_price < p.entry
    liq = estimate_liquidation(100.0, "LONG", 10)
    assert p.liquidation_price == pytest.approx(liq, rel=1e-6)

    ps = plan(price=100.0, atr=30.0, side="SHORT")
    assert ps.ok and ps.stop_clamped is True
    assert ps.entry < ps.sl_price < ps.liquidation_price


def test_clamp_helper_moves_stop_only_toward_entry():
    sl, clamped = clamp_stop_to_liquidation(100.0, "LONG", 80.0, 90.0, 35.0)
    assert clamped and 90.0 < sl < 100.0
    sl2, clamped2 = clamp_stop_to_liquidation(100.0, "LONG", 96.0, 90.0, 35.0)
    assert not clamped2 and sl2 == 96.0            # already safe → untouched
    sl3, _ = clamp_stop_to_liquidation(100.0, "SHORT", 120.0, 110.0, 35.0)
    assert 100.0 < sl3 < 110.0


def test_stop_distance_cap_rejects_wild_setups():
    # 2x leverage → liquidation is far away, the ATR stop is 30% wide and the
    # 8% distance cap must reject it (it can no longer be blamed on liquidation).
    p = plan(price=100.0, atr=20.0, leverage=2)
    assert not p.ok and "max" in p.reason


# ─────────────────────────── hard guards ───────────────────────────
def test_max_concurrent_trades_blocks():
    p = plan(open_trades=10)  # default max_concurrent_trades = 10
    assert not p.ok and "max concurrent" in p.reason


def test_min_notional_rejected_not_rounded_up():
    from app.exchange.base import SymbolFilter
    big_min = SymbolFilter(symbol="BTCUSDT", tick_size=0.1, step_size=0.001,
                           min_qty=0.001, min_notional=500.0)
    p = plan(equity=100.0, flt=big_min)             # $8 margin → $80 notional < 500 min
    assert not p.ok and "min" in p.reason


def test_confidence_below_threshold_rejected():
    p = plan(confidence=42.0)
    assert not p.ok and "confidence" in p.reason
    p2 = plan(confidence=95.0)
    assert p2.ok


def test_insufficient_balance_rejected():
    p = plan(equity=10_000.0, available=10.0)
    assert not p.ok and "balance" in p.reason


def test_qty_floored_to_step_size():
    p = plan(price=60_000.0, atr=600.0)
    assert p.ok
    assert abs(p.qty / FLT.step_size - round(p.qty / FLT.step_size)) < 1e-6


def test_reverse_exit_logic():
    e = engine()
    assert e.reverse_exit_reason("LONG", "SHORT") is True
    assert e.reverse_exit_reason("SHORT", "LONG") is True
    assert e.reverse_exit_reason("LONG", "LONG") is False
    e2 = engine(use_reverse_signal_exit=False)
    assert e2.reverse_exit_reason("LONG", "SHORT") is False


def test_stop_and_target_helpers_are_directional():
    assert stop_loss_price(100, "LONG", 2, 1.5) == pytest.approx(97.0)
    assert stop_loss_price(100, "SHORT", 2, 1.5) == pytest.approx(103.0)
    assert take_profit_price(100, "LONG", 2, 3) == pytest.approx(106.0)
    assert take_profit_price(100, "SHORT", 2, 3) == pytest.approx(94.0)


def test_daily_drawdown_setting_parses():
    e = engine(daily_drawdown_stop_pct=20.0)
    assert e.s.daily_drawdown_stop_pct == 20.0
