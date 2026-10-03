"""End-to-end workflow: scan → analyse → execute → verify → monitor → close."""
from __future__ import annotations

import asyncio

import pytest

from app.bots import build_registry
from app.bus import BUS
from app.db import DB
from app.engine import Opportunity, Proposal, TradingEngine
from app.exchange.sim import SimExchange
from app.indicators import ghost
from app.journal import Journal
from app.risk import RiskEngine

pytestmark = pytest.mark.asyncio


async def make_engine(store, hub=None, **overrides) -> TradingEngine:
    store.cfg.risk.max_concurrent_trades = overrides.get("max_trades", 10)
    store.cfg.risk.min_confidence = overrides.get("min_confidence", 0.0)
    store.cfg.indicator.mtfGate = False
    store.cfg.engine.sim_time_accel = 3000.0        # don't let the clock run during tests
    store.save()
    from app.exchange.hub import MarketHub
    if hub is None:
        hub = MarketHub(store=store)
        sim = SimExchange(balance=10_000.0, universe=overrides.get("universe", 12),
                          history_bars=300, interval="5m", time_accel=3000.0)
        hub.exchange = sim
        hub.transport = "sim"
        hub.mode = "sim"
        hub.filters = await sim.all_filters()
        hub.tickers = await sim.tickers_all()
        await hub.warmup(list(sim.syms.keys()))
        hub.universe = list(sim.syms.keys())
        hub.started = True
    reg = build_registry(store.cfg)
    eng = TradingEngine(store=store, hub=hub, journal=Journal(store=store), registry=reg)
    eng.running = True
    eng.scanner_buckets = hub.scanner_allocation(5, 30)
    eng._assign_scanner_symbols()
    return eng


def make_opportunity(symbol: str, direction: str = "LONG", entry: float = 100.0,
                     atr: float = 1.0, confidence: float = 85.0) -> Proposal:
    opp = Opportunity(
        symbol=symbol, direction=direction, entry=entry,
        stop=entry - 1.5 * atr if direction == "LONG" else entry + 1.5 * atr,
        target=entry + 3 * atr if direction == "LONG" else entry - 3 * atr,
        atr=atr, atr_pct=atr / entry * 100, trend_quality=0.72, tier="strong",
        rail=entry - 0.5 * atr, rail_distance_pct=0.5, htf_bull=direction == "LONG",
        flow_bias=0.3, volume=1000.0, bar_time=0, scanner_id="scanner-1",
        features={"trend_quality": 0.72, "tier": "strong", "htf_bull": direction == "LONG",
                  "atr_pct": atr / entry * 100, "rail_distance_pct": 0.5, "flow_bias": 0.3,
                  "symbol": symbol, "direction": direction},
    )
    return Proposal(opportunity=opp, analyst_id="analyst-1", confidence=confidence,
                    approved=True, factors={}, notes=[], model="heuristic")


async def _set_price(eng: TradingEngine, symbol: str, price: float) -> None:
    """Move the simulated market to a target price (single-symbol step)."""
    sim: SimExchange = eng.hub.exchange
    candles = sim.candles[symbol]
    last = candles[-1]
    candles[-1] = type(last)(last.t, price, max(price, last.h) , min(price, last.l), price, last.v)
    sim.tickers[symbol].last = price
    sim.tickers[symbol].mark = price


async def test_full_trade_lifecycle_is_recorded(db, store):
    eng = await make_engine(store)
    await eng._load_open_trades()
    await _set_price(eng, "BTCUSDT", 100.0)
    prop = make_opportunity("BTCUSDT", "LONG", entry=100.0, atr=2.0)
    account = await eng.hub.account()
    state = await eng.journal.update(account)
    trade = await eng._open_trade("execution-1", prop, {
        "equity": state.equity, "available": state.available, "open_count": 0})
    assert trade is not None
    assert trade["side"] == "LONG" and trade["qty"] > 0
    assert trade["sl_price"] < trade["entry_price"] < trade["tp_price"]
    assert trade["liquidation_price"] < trade["sl_price"]
    assert trade["margin"] == pytest.approx(state.equity * 0.08, rel=0.05)

    # the exchange really holds the position and the protective orders
    positions = await eng.hub.positions()
    assert any(p.symbol == "BTCUSDT" for p in positions)
    orders = await eng.hub.open_orders("BTCUSDT")
    types = {o["type"] for o in orders}
    assert "STOP_MARKET" in types and "TAKE_PROFIT_MARKET" in types

    # Info Bot verification passes
    verified = await eng._stage_verify([trade])
    assert len(verified) == 1

    # price moves in our favour → close on reverse signal, journal updates
    await _set_price(eng, "BTCUSDT", 103.0)
    events: list[dict] = []
    BUS.on("trade.closed", lambda e: events.append(e.data))
    closed = await eng._close_trade(int(trade["id"]), "reverse_signal")
    assert closed is not None and closed["status"] == "closed"
    assert closed["close_reason"] == "reverse_signal"
    assert closed["exit_price"] > closed["entry_price"]
    assert closed["net_pnl"] > 0

    row = await DB.get_trade(int(trade["id"]))
    assert row["status"] == "closed"
    stats = await eng.journal.refresh_stats(force=True)
    assert stats["total_trades"] == 1 and stats["wins"] == 1
    assert row["fee_paid"] > 0
    assert not await eng.hub.positions()
    assert events, "celebration/close event must be published"


async def test_win_and_loss_reactions_and_journal_extremes(db, store):
    eng = await make_engine(store)
    await _set_price(eng, "ETHUSDT", 100.0)
    win = await eng._open_trade("execution-1", make_opportunity("ETHUSDT", "LONG", 100.0, 2.0),
                                {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    await _set_price(eng, "ETHUSDT", 104.0)
    await eng._close_trade(int(win["id"]), "tp")
    await _set_price(eng, "SOLUSDT", 100.0)
    loss = await eng._open_trade("execution-2", make_opportunity("SOLUSDT", "SHORT", 100.0, 2.0),
                                 {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    await _set_price(eng, "SOLUSDT", 104.0)
    await eng._close_trade(int(loss["id"]), "sl")
    stats = await eng.journal.refresh_stats(force=True)
    assert stats["total_trades"] == 2
    assert stats["wins"] == 1 and stats["losses"] == 1
    moods = {b.mood for b in eng.registry.all()}
    assert "happy" in moods or "sad" in moods


async def test_max_concurrent_trades_is_never_exceeded(db, store):
    eng = await make_engine(store, max_trades=3, universe=20)
    symbols = ["ADAUSDT", "XRPUSDT", "DOGEUSDT", "SOLUSDT", "BNBUSDT", "ETHUSDT"][:6]
    for sym in symbols:
        await _set_price(eng, sym, 100.0)
    props = [make_opportunity(s, "LONG", 100.0, 2.0) for s in symbols]
    opened = await eng._stage_execute(props)
    assert len(opened) <= 3
    assert len(eng.open_trades) <= 3
    # a further cycle must refuse to add more
    await _set_price(eng, "BTCUSDT", 100.0)
    more = await eng._stage_execute([make_opportunity("BTCUSDT", "LONG", 100.0, 2.0)])
    assert more == [] or len(eng.open_trades) <= 3


async def test_scan_stage_produces_scan_snapshot_and_opportunities(db, store):
    eng = await make_engine(store, universe=12)
    ents = await eng._stage_scan()
    isinstance(ents, list)
    assert set(eng.scan_snapshot["by_bot"].keys()) == {f"scanner-{i+1}" for i in range(5)}
    total_rows = sum(len(v) for v in eng.scan_snapshot["by_bot"].values())
    assert total_rows >= 10
    row = next(iter(eng.scan_snapshot["by_bot"].values()))[0]
    for key in ("symbol", "price", "trend", "quality", "rail", "flip", "bar_time"):
        assert key in row


async def test_analysts_score_and_gate_by_confidence(db, store):
    eng = await make_engine(store, min_confidence=60.0)
    props = [
        make_opportunity("BTCUSDT", "LONG", 100.0, 2.0, confidence=90.0),
        make_opportunity("ETHUSDT", "SHORT", 100.0, 2.0, confidence=90.0),
    ]
    out = await eng._stage_analyze([p.opportunity for p in props])
    assert len(out) == 2
    for p in out:
        assert 0 <= p.confidence <= 100
        assert p.analyst_id.startswith("analyst-")
        assert isinstance(p.approved, bool)
        assert set(p.factors) >= {"trend_quality", "htf_alignment", "volume_flow"}


async def test_failed_protection_closes_the_position_immediately(db, store):
    """Fail-safe: if the stop cannot be placed, the engine must flatten at once."""
    eng = await make_engine(store)
    sim: SimExchange = eng.hub.exchange

    async def boom(*a, **kw):
        raise RuntimeError("stop rejected by exchange")

    sim.stop_market = boom
    await _set_price(eng, "BTCUSDT", 100.0)
    prop = make_opportunity("BTCUSDT", "LONG", 100.0, 2.0)
    trade = await eng._open_trade("execution-1", prop,
                                  {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    assert trade is None
    assert not await eng.hub.positions(), "position must be flattened when unprotected"


async def test_engine_respects_api_ceiling_for_orders(db, store):
    """With the governor capped out, execution must refuse rather than hammer Binance."""
    from app.ratelimit import GOVERNOR

    eng = await make_engine(store)
    original_limit, original_pct = GOVERNOR.limit_per_min, GOVERNOR.budget_pct
    GOVERNOR.limit_per_min = 5
    GOVERNOR.budget_pct = 95.0                            # cap = 4 weight/min
    for _ in range(4):
        await GOVERNOR.acquire("test-bot", "ping")        # fill the cap exactly
    assert GOVERNOR.snapshot()["halted"] is True
    await _set_price(eng, "BTCUSDT", 100.0)
    prop = make_opportunity("BTCUSDT", "LONG", 100.0, 2.0)
    trade = await eng._open_trade("execution-1", prop,
                                  {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    assert trade is None, "entries must be refused at the API ceiling"
    assert not eng.open_trades
    GOVERNOR.limit_per_min, GOVERNOR.budget_pct = original_limit, original_pct
    GOVERNOR._events.clear()


async def test_startup_adoption_of_open_positions(db, store):
    eng = await make_engine(store)
    await _set_price(eng, "BTCUSDT", 100.0)
    trade = await eng._open_trade("execution-1", make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    assert trade
    # a restarted engine pointed at the same exchange must adopt the position
    eng2 = await make_engine(store, hub=eng.hub)
    await eng2._load_open_trades()
    assert int(trade["id"]) in eng2.open_trades
    assert eng2.symbol_to_trade["BTCUSDT"] == int(trade["id"])


async def test_fills_are_never_attributed_across_symbols(db, store):
    """Regression: hub.fills() used to ignore the symbol filter in simulation,
    which inflated fees and produced impossible exit prices."""
    eng = await make_engine(store, universe=12)
    sim: SimExchange = eng.hub.exchange
    await _set_price(eng, "BTCUSDT", 100.0)
    await _set_price(eng, "ETHUSDT", 100.0)
    await sim.market_order("BTCUSDT", "BUY", 1.0)
    await sim.market_order("ETHUSDT", "BUY", 1.0)
    only_btc = await eng.hub.fills(["BTCUSDT"])
    assert only_btc and all(f.symbol == "BTCUSDT" for f in only_btc)
    only_eth = await eng.hub.fills(["ETHUSDT"])
    assert only_eth and all(f.symbol == "ETHUSDT" for f in only_eth)


async def test_consecutive_trades_on_one_symbol_do_not_double_count(db, store):
    eng = await make_engine(store, universe=12)
    fees = []
    for i in range(3):
        await _set_price(eng, "BTCUSDT", 100.0)
        trade = await eng._open_trade("execution-1", make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                      {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
        assert trade, f"open #{i} failed"
        await _set_price(eng, "BTCUSDT", 100.5)
        closed = await eng._close_trade(int(trade["id"]), "reverse_signal")
        assert closed
        fees.append(closed["fee_paid"])
        eng.cooldowns.clear()          # bypass the churn guard for this test
    assert all(0 < f < 60 for f in fees), fees
    # each trade books roughly two taker fills on its own notional (~8 USDT)
    for f in fees:
        assert f == pytest.approx(8.0, rel=0.45), fees


async def test_losses_stay_bounded_by_the_stop_distance(db, store):
    """A stop-out may never lose dramatically more than entry-minus-stop."""
    eng = await make_engine(store, universe=12)
    await _set_price(eng, "BTCUSDT", 100.0)
    trade = await eng._open_trade("execution-1", make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    sl = trade["sl_price"]
    assert trade["liquidation_price"] < sl < trade["entry_price"]
    risk_usd = abs(trade["entry_price"] - sl) * trade["qty"]

    # touch the stop exactly (no gap) and let the exchange-side order fill
    sim: SimExchange = eng.hub.exchange
    await _set_price(eng, "BTCUSDT", sl)
    await sim.step_bar()
    positions = await eng.hub.positions()
    if any(p.symbol == "BTCUSDT" for p in positions):
        await eng._close_trade(int(trade["id"]), "sl")      # local watchdog path
    else:
        await eng._finalize_close(int(trade["id"]), sl, reason="sl")

    row = await DB.get_trade(int(trade["id"]))
    assert row["status"] == "closed"
    # fees (~8 USDT on an 8k notional) are the only overshoot allowed
    assert row["net_pnl"] > -(risk_usd + 25), (
        f"loss {row['net_pnl']:.2f} exceeded the stop risk {risk_usd:.2f} materially")


async def test_exchange_side_stop_books_the_full_round_trip(db, store):
    """Regression: a protective stop that fires on the exchange must be booked
    with both fills, the real exit price and the 'sl' reason — not just entry fees."""
    eng = await make_engine(store, universe=12)
    await _set_price(eng, "BTCUSDT", 100.0)
    trade = await eng._open_trade("execution-1", make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    assert trade and trade["sl_price"] < trade["entry_price"]

    # touch the stop exactly (no gap) — the fill price should equal the stop
    sim: SimExchange = eng.hub.exchange
    stop = trade["sl_price"]
    await _set_price(eng, "BTCUSDT", stop)
    await sim.step_bar()
    assert not await eng.hub.positions(), "protective stop should have closed the position"

    # the monitor notices the vanished position and finalises the trade
    closed = await eng._finalize_close(int(trade["id"]), stop)
    assert closed is not None
    assert closed["close_reason"] == "sl", closed["close_reason"]
    assert closed["gross_pnl"] < 0, "a stop-out must book a negative gross"
    notional = float(trade["notional"])
    # both legs are charged: ~0.1% of notional round trip
    assert closed["fee_paid"] == pytest.approx(notional * 0.001, rel=0.35), closed["fee_paid"]
    assert closed["net_pnl"] < closed["gross_pnl"]        # fees make it worse
    assert closed["exit_price"] <= stop


async def test_take_profit_exit_is_recognised(db, store):
    eng = await make_engine(store, universe=12)
    await _set_price(eng, "ETHUSDT", 100.0)
    trade = await eng._open_trade("execution-1", make_opportunity("ETHUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})
    sim: SimExchange = eng.hub.exchange
    await _set_price(eng, "ETHUSDT", trade["tp_price"] * 1.01)
    await sim.step_bar()
    assert not await eng.hub.positions()
    closed = await eng._finalize_close(int(trade["id"]), trade["tp_price"])
    assert closed and closed["close_reason"] == "tp"
    assert closed["gross_pnl"] > 0 and closed["net_pnl"] > 0
