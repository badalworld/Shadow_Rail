"""Journal (Trade/Equity Manager) + simulation exchange invariants."""
from __future__ import annotations

import pytest

from app.exchange.base import Ticker
from app.exchange.sim import MAKER_FEE, TAKER_FEE, SimExchange


# ───────────────────────────── journal ─────────────────────────────
async def test_starting_balance_locks_and_never_changes(db, store):
    from app.journal import Journal

    j = Journal(store=store)
    locked = await j.ensure_starting_balance(10_000.0, "binance")
    assert locked is True
    assert j.state.starting_balance == 10_000.0
    # a later, much larger balance must NOT overwrite the origin number
    locked2 = await j.ensure_starting_balance(25_000.0, "binance")
    assert locked2 is False
    assert j.state.starting_balance == 10_000.0
    # a fresh Journal instance (engine restart) still reads the locked value
    j2 = Journal(store=store)
    await j2.ensure_starting_balance(999.0, "binance")
    assert j2.state.starting_balance == 10_000.0
    assert j2.state.starting_locked is True


async def test_reset_starting_balance_is_operator_only(db, store):
    from app.journal import Journal

    j = Journal(store=store)
    await j.ensure_starting_balance(5_000.0, "binance")
    await j.reset_starting_balance()
    assert j.state.starting_locked is False
    await j.ensure_starting_balance(7_500.0, "binance")
    assert j.state.starting_balance == 7_500.0


async def test_stats_math_including_fees_and_funding(db, store):
    from app.journal import Journal

    j = Journal(store=store)
    await j.ensure_starting_balance(10_000.0, "binance")
    rows = [
        dict(symbol="BTCUSDT", side="LONG", status="closed", qty=1, entry_price=100,
             exit_price=110, leverage=10, margin=10, notional=100, sl_price=95, tp_price=110,
             opened_at=1, closed_at=2, gross_pnl=10, fee_paid=1.0, funding_paid=0.5,
             net_pnl=8.5, close_reason="tp", mode="sim"),
        dict(symbol="ETHUSDT", side="SHORT", status="closed", qty=1, entry_price=100,
             exit_price=105, leverage=10, margin=10, notional=100, sl_price=105, tp_price=90,
             opened_at=1, closed_at=3, gross_pnl=-5, fee_paid=0.5, funding_paid=-0.25,
             net_pnl=-5.25, close_reason="sl", mode="sim"),
    ]
    from app.db import DB

    for r in rows:
        await DB.insert_trade(r)
    stats = await j.refresh_stats(force=True)
    assert stats["total_trades"] == 2
    assert stats["wins"] == 1 and stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(50.0)
    assert stats["net_pnl"] == pytest.approx(3.25)
    assert stats["fees_paid"] == pytest.approx(1.5)
    assert stats["funding_paid"] == pytest.approx(0.25)      # 0.5 paid − 0.25 received
    assert j.state.released_pnl == pytest.approx(3.25)


async def test_symbol_stats_feed_the_confidence_model(db, store):
    from app.db import DB
    from app.journal import Journal

    j = Journal(store=store)
    for i, net in enumerate([5, 5, -2, 5]):
        await DB.insert_trade(dict(symbol="SOLUSDT", side="LONG", status="closed", qty=1,
                                   entry_price=1, exit_price=1.1, leverage=10, margin=1,
                                   notional=1, opened_at=1, closed_at=10 + i, gross_pnl=net,
                                   fee_paid=0, funding_paid=0, net_pnl=net, mode="sim"))
    s = (await j.symbol_stats(force=True))["SOLUSDT"]
    assert s["trades"] == 4 and s["wins"] == 3
    assert s["win_rate"] == pytest.approx(75.0)


# ─────────────────────────── simulation exchange ───────────────────────────
async def test_sim_market_has_a_tradeable_universe():
    sim = SimExchange(universe=150, history_bars=300, interval="5m")
    tickers = await sim.tickers_all()
    assert len(tickers) >= 150
    liquid = [t for t in tickers.values() if t.quote_volume > 2_000_000]
    assert len(liquid) >= 140, f"only {len(liquid)} symbols pass the liquidity filter"


async def test_sim_order_lifecycle_and_fees():
    sim = SimExchange(universe=20, history_bars=200)
    sym = "BTCUSDT"
    price = sim.last_price(sym)
    res = await sim.market_order(sym, "BUY", 0.01)
    assert res.status == "FILLED" and res.avg_price > 0
    pos = await sim.positions_all()
    assert len(pos) == 1 and pos[0].side == "LONG" and pos[0].qty == pytest.approx(0.01)
    assert sim.fees_paid == pytest.approx(0.01 * res.avg_price * TAKER_FEE, rel=0.15)

    # protective stop below entry fills when price trades through it
    stop = price * 0.50
    await sim.stop_market(sym, "SELL", stop)
    await sim.step_bar()
    while (await sim.positions_all()):
        await sim.step_bar()
    assert not await sim.positions_all()


async def test_sim_liquidation_estimate_direction():
    sim = SimExchange(universe=10, history_bars=100)
    long_liq = sim.liquidation_price("BTCUSDT", "LONG", 100.0, 1.0, 10)
    short_liq = sim.liquidation_price("BTCUSDT", "SHORT", 100.0, 1.0, 10)
    assert long_liq < 100.0 < short_liq
    assert long_liq == pytest.approx(90.5, rel=1e-6)
    assert short_liq == pytest.approx(109.5, rel=1e-6)


async def test_sim_account_reflects_equity_moves():
    sim = SimExchange(universe=10, history_bars=120, balance=10_000.0)
    snap = await sim.account()
    assert snap.total_margin_balance == pytest.approx(10_000.0)
    await sim.market_order("BTCUSDT", "BUY", 0.05)
    snap2 = await sim.account()
    assert snap2.open_count == 1
    assert snap2.total_initial_margin > 0
    assert snap2.available_balance < snap2.total_margin_balance


async def test_sim_candles_are_closed_and_monotonic():
    sim = SimExchange(universe=5, history_bars=300)
    candles = await sim.klines("BTCUSDT", "5m", limit=250)
    assert len(candles) == 250
    times = [c.t for c in candles]
    assert times == sorted(times) and len(set(times)) == len(times)
    for c in candles:
        assert c.l <= min(c.o, c.c) <= max(c.o, c.c) <= c.h
