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


async def test_sim_state_survives_a_restart():
    """A restart must not wipe the paper account: balance, positions and the
    price level all continue where the previous run stopped."""
    sim = SimExchange(universe=20, history_bars=300, warm_candles=60)
    await sim.market_order("BTCUSDT", "BUY", 0.01)
    await sim.market_order("ETHUSDT", "SELL", 0.2)
    snapshot = sim.export_state()

    reborn = SimExchange(universe=20, history_bars=300, warm_candles=60)
    reborn.import_state(snapshot)

    assert set(reborn.positions) == set(sim.positions)
    assert reborn.balance == pytest.approx(sim.balance, rel=1e-9)
    for sym in ("BTCUSDT", "ETHUSDT"):
        assert reborn.syms[sym].price == pytest.approx(sim.syms[sym].price, rel=1e-9)
        assert reborn.candles[sym][-1].c == pytest.approx(sim.candles[sym][-1].c, rel=1e-3)
    # and it keeps trading from there
    before = reborn.syms["BTCUSDT"].price
    await reborn.step_bar()
    assert reborn.syms["BTCUSDT"].price != before


async def test_boot_payload_has_everything_the_dashboard_paints_with(db, store):
    """The inlined first-paint snapshot must match the websocket hello frame."""
    import app.api as api
    from tests.test_engine_flow import make_engine

    engine = await make_engine(store, universe=8)
    previous, api.ENGINE = api.ENGINE, engine
    try:
        payload = await api.boot_payload()
    finally:
        api.ENGINE = previous

    for key in ("bots", "status", "equity", "stats", "scan", "open_trades",
                "closed_trades", "curve", "config", "logs", "links"):
        assert key in payload, f"boot payload is missing {key}"
    assert isinstance(payload["bots"], list) and payload["bots"]
    assert "workflow" in payload["status"] and "api" in payload["status"]
    # the API-key masking rule must survive the boot path
    b = payload["config"]["binance"]
    assert "api_secret" not in b and "api_secret_masked" in b


async def test_hub_restores_a_persisted_paper_account(db, store):
    """The hub must re-adopt the persisted sim snapshot on boot (balance,
    positions and prices) — otherwise a restart silently resets the demo."""
    from app.exchange.hub import MarketHub

    store.cfg.engine.sim_time_accel = 3000.0
    store.cfg.engine.universe_size = 20
    store.cfg.binance.transport = "sim"
    store.save()

    first = MarketHub(store=store)
    await first.start()
    assert first.transport == "sim"
    await first.exchange.market_order("BTCUSDT", "BUY", 0.01)
    await first.persist_sim_state()
    entry = first.exchange.positions["BTCUSDT"].entry

    second = MarketHub(store=store)
    await second.start()
    assert "BTCUSDT" in second.exchange.positions
    assert second.exchange.positions["BTCUSDT"].entry == pytest.approx(entry)
    assert second.exchange.balance == pytest.approx(first.exchange.balance, rel=1e-9)
    await second.stop()
    await first.stop()


async def test_equity_bridge_reconciles_with_the_exchange(db, store):
    """starting + released + unrealised − open entry fees == exchange equity.

    This is the arithmetic the Main Page shows; if it ever drifts, the
    dashboard is lying about money.
    """
    from app.exchange.hub import MarketHub
    from app.journal import Journal

    store.cfg.engine.universe_size = 20
    store.cfg.binance.transport = "sim"
    store.cfg.engine.sim_time_accel = 3000.0
    store.save()

    hub = MarketHub(store=store)
    await hub.start()
    sim = hub.exchange
    sym = "BTCUSDT"

    journal = Journal(store=store)
    account = await hub.account()
    await journal.update(account)
    assert journal.state.starting_balance == pytest.approx(sim.balance, rel=0.02)

    # open a position and re-sync
    await sim.market_order(sym, "BUY", 0.01)
    account = await hub.account()
    state = await journal.update(account)

    bridge = (state.starting_balance + state.released_pnl
              + state.unrealized - state.open_entry_fees)
    assert state.open_entry_fees > 0
    assert bridge == pytest.approx(account.total_margin_balance, rel=0.002), (
        f"bridge {bridge:.2f} != exchange equity {account.total_margin_balance:.2f}")
    # the reported total fee count includes the open position's entry leg
    assert state.as_dict()["fees_paid_total"] == pytest.approx(
        state.fees_paid + state.open_entry_fees, abs=1e-4)   # as_dict rounds to 4dp
    await hub.stop()


async def test_sim_state_round_trips_with_protective_orders():
    """Regression: export_state() referenced SimOrder fields that did not exist,
    so every persist raised and the snapshot silently froze at boot."""
    from app.exchange.sim import SimExchange, SimOrder

    sim = SimExchange(universe=6, history_bars=120, warm_candles=40)
    await sim.market_order("BTCUSDT", "BUY", 0.01)
    stop = await sim.stop_market("BTCUSDT", "SELL",
                                 sim.last_price("BTCUSDT") * 0.9, qty=0.01)
    take = await sim.take_profit_market("BTCUSDT", "SELL",
                                        sim.last_price("BTCUSDT") * 1.1, qty=0.01)
    assert stop.status == "NEW" and take.status == "NEW"

    state = sim.export_state()                     # must not raise
    assert len(state["orders"]) == 2

    reborn = SimExchange(universe=6, history_bars=120, warm_candles=40)
    reborn.import_state(state)
    assert set(reborn.orders) == set(sim.orders)
    for oid, order in sim.orders.items():
        copy = reborn.orders[oid]
        for field in ("symbol", "side", "type", "qty", "stop_price", "close_position"):
            assert getattr(copy, field) == getattr(order, field), field
    assert reborn.positions["BTCUSDT"].qty == pytest.approx(sim.positions["BTCUSDT"].qty)


async def test_hub_persist_reports_failure_instead_of_freezing(db, store, monkeypatch):
    """A broken snapshot must be visible, not silently ignored."""
    from app.bus import BUS
    from app.exchange.hub import MarketHub

    store.cfg.engine.universe_size = 12
    store.cfg.binance.transport = "sim"
    store.cfg.engine.sim_time_accel = 3000.0
    store.save()
    hub = MarketHub(store=store)
    await hub.start()

    assert await hub.persist_sim_state() is True
    seen: list[dict] = []
    BUS.on("sim.persist_failed", lambda ev: seen.append(ev.data))

    def boom() -> dict:
        raise RuntimeError("serialisation exploded")

    monkeypatch.setattr(hub.exchange, "export_state", boom)
    assert await hub.persist_sim_state() is False
    assert seen and "serialisation exploded" in seen[0]["error"]
    await hub.stop()


async def test_restored_prices_are_continuous_with_the_seeded_history():
    """The chart (and therefore the indicator) must not see a price jump at the
    seam: the last seeded candle has to close where the previous run stopped."""
    sim = SimExchange(universe=8, history_bars=200, warm_candles=40)
    await sim.step_bar()
    state = sim.export_state()

    mid = SimExchange(universe=8, history_bars=200, warm_candles=40, restore=state)
    for sym in state["end_prices"]:
        last_close = mid.candles[sym][-1].c
        assert last_close == pytest.approx(state["end_prices"][sym], rel=1e-9), sym
        assert mid.syms[sym].price == pytest.approx(last_close, rel=1e-9), sym
        assert mid.tickers[sym].last == pytest.approx(last_close, rel=1e-9), sym
    # and the restored series still behaves (indicator inputs stay sane)
    ratios = [c.c / c.o for c in mid.candles["BTCUSDT"][-50:]]
    assert all(0.5 < r < 2.0 for r in ratios)
