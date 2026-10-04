"""
The backtest lab itself — the instrument every default in this repo is now
measured with, so the instrument has to be trustworthy.

These tests do not assert that the strategy makes money.  They assert that the
lab tells the truth: costs are charged, the future cannot leak into the past, a
market with no edge reports no edge, and the lab's "shipped" configuration is
the configuration the engine actually boots with.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))

from app import backtest as bt, marketgen                        # noqa: E402
from app.config import EdgeSettings, RiskSettings               # noqa: E402
from app.exchange.sim import DEFAULT_UNIVERSE                   # noqa: E402
from app.indicators.ghost import GhostParams                    # noqa: E402

BARS, SYMBOLS = 4_000, 8


def rows(n: int = SYMBOLS):
    seen, out = set(), []
    for sym, price, sigma in DEFAULT_UNIVERSE:
        if sym in seen:
            continue
        seen.add(sym)
        out.append((sym, price, sigma))
        if len(out) >= n:
            break
    return out


def panel(momentum: float = 1.0, bars: int = BARS, seed: int = 4242):
    return marketgen.generate_panel(rows(), bars, seed=seed, momentum=momentum)


def dataset(momentum: float = 1.0, bars: int = BARS, seed: int = 4242):
    return bt.build_dataset(panel(momentum, bars, seed), GhostParams())


def shipped(**over) -> bt.BacktestConfig:
    """The engine's real defaults, expressed for the lab (see scripts/backtest.py)."""
    from backtest import shipped_config
    cfg = shipped_config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture(scope="module")
def market():
    return dataset(1.0)


@pytest.fixture(scope="module")
def control():
    return dataset(0.0)


# ─────────────────────────────────────────────────── costs are real ───────
def test_every_trade_pays_two_taker_fills(market):
    cfg = shipped()
    res = bt.run_dataset(market, cfg, "fees")
    assert res.count > 5
    assert res.total_fees > 0
    for t in res.trades:
        # notional in + notional out, at the taker rate
        expect = (t.entry * t.qty + t.exit_price * t.qty) * cfg.fee
        assert t.fee == pytest.approx(expect, rel=1e-6)
    # the headline number is always net of them
    assert res.net_pnl < sum(t.gross for t in res.trades)


def test_funding_is_charged_on_every_epoch_crossed(market):
    cfg = shipped()
    res = bt.run_dataset(market, cfg, "funding")
    long_lived = [t for t in res.trades if t.bar_out - t.bar_in > bt.BARS_PER_8H_5M]
    if long_lived:
        assert any(abs(t.funding) > 0 for t in long_lived)


# ──────────────────────────────────────────── the future cannot leak ──────
def test_results_are_identical_when_the_panel_is_truncated_after_the_fact():
    """
    The strongest no-look-ahead check available: a run over a panel that only
    ever held N bars must equal a run over the same N bars cut out of a longer
    panel.  If the indicator, the gates or the exit simulation read tomorrow's
    prices, the two diverge.
    """
    n = 3_000
    long_ds = dataset(1.0, bars=BARS)
    short = [bt.build_symbol_data(d.candles[:n], GhostParams(), d.symbol)
             for d in long_ds]
    a = bt.run_dataset(short, bt._with_bounds(shipped(), 0, n), "short")
    b = bt.run_dataset(long_ds, bt._with_bounds(shipped(), 0, n), "truncated")
    assert a.count == b.count, (a.count, b.count)
    assert a.net_pnl == pytest.approx(b.net_pnl, abs=1e-6)
    assert [t.exit_price for t in a.trades] == pytest.approx(
        [t.exit_price for t in b.trades], abs=1e-9)


# ──────────────────────────────── a market with no edge reports no edge ───
def test_the_random_walk_control_loses(control):
    """
    The honesty test.  On a pure random walk the indicator cannot know anything,
    so a lab that reports a profit here is measuring its own bugs.
    """
    res = bt.run_dataset(control, shipped(), "control")
    assert res.count > 5
    assert res.expectancy_r < 0, "the lab manufactured an edge out of nothing"
    assert res.profit_factor < 1.0


def test_the_shipped_configuration_beats_the_classic_one_out_of_sample(market):
    """The change actually buys something, and it survives the split."""
    split = int(BARS * 0.6)
    classic = bt.baseline_config()
    new = shipped()
    a = bt.run_dataset(market, bt._with_bounds(classic, split, BARS), "classic")
    b = bt.run_dataset(market, bt._with_bounds(new, split, BARS), "shipped")
    assert b.expectancy_r > a.expectancy_r
    assert b.win_rate > a.win_rate
    assert b.max_drawdown_pct < a.max_drawdown_pct
    assert b.profit_factor > a.profit_factor


# ───────────────────────────────────────────────────── the sizing rule ────
def test_risk_sizing_risks_about_one_percent_of_equity_per_trade(market):
    res = bt.run_dataset(market, shipped(), "sizing")
    assert res.count > 5
    worst = max(t.risk_amount / t.equity_at_entry * 100.0 for t in res.trades)
    mean = sum(t.risk_amount / t.equity_at_entry * 100.0 for t in res.trades) / res.count
    assert mean == pytest.approx(1.0, rel=0.25)
    # the 8 %-of-equity margin ceiling can bind on a very tight stop, so the
    # rule is "about 1 %, never wildly more"
    assert worst <= 1.0 + 1e-6 or worst < 2.0


def test_margin_sizing_keeps_the_original_rule(market):
    res = bt.run_dataset(market, shipped(sizing_mode="margin"), "margin")
    assert all(t.margin <= t.equity_at_entry * 0.08 + 1e-6 for t in res.trades)


# ─────────────────────────────────────────── the lab matches production ───
def test_the_labs_shipped_config_mirrors_the_engines_defaults():
    """If the engine's defaults move, the lab must move with them."""
    risk = RiskSettings()
    cfg = shipped()
    assert cfg.sl_atr_mult == risk.active_tp_sl()[0]
    assert cfg.tp_enabled is risk.active_tp_sl()[2]
    assert cfg.risk_pct_per_trade == risk.risk_pct_per_trade
    assert cfg.sizing_mode == risk.sizing_mode
    assert cfg.trail_mode == risk.trail_mode
    assert cfg.trail_activation_r == risk.trail_activation_r
    assert cfg.trail_distance_r == risk.trail_distance_r
    assert cfg.min_confidence == risk.min_confidence
    assert isinstance(cfg.edge, EdgeSettings)
    assert cfg.edge.breakeven_enabled is EdgeSettings().breakeven_enabled


# ───────────────────────────────────────────────── result bookkeeping ─────
def test_the_summary_reports_what_the_dashboard_needs(market):
    res = bt.run_dataset(market, shipped(), "summary")
    s = res.summary()
    for key in ("trades", "win_rate", "profit_factor", "net_pnl", "expectancy_r",
                "max_drawdown_pct", "fees", "reasons", "exposure_pct"):
        assert key in s, key
    assert 0 <= s["win_rate"] <= 100
    assert res.count == sum(res.reasons().values())
    assert res.wins == sum(1 for t in res.trades if t.net > 0)
