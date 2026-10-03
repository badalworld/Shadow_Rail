"""The cash identity: a close must move the venue balance by exactly net_pnl."""
from __future__ import annotations

import pytest

from tests.test_engine_flow import make_engine, make_opportunity, _set_price

pytestmark = pytest.mark.asyncio


async def test_close_moves_the_balance_by_exactly_net_pnl(db, store):
    eng = await make_engine(store, universe=12)
    await _set_price(eng, "BTCUSDT", 100.0)
    ex = eng.hub.exchange
    before = ex.balance
    trade = await eng._open_trade("execution-1",
                                  make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0,
                                   "open_count": 0})
    assert trade
    after_open = ex.balance
    await _set_price(eng, "BTCUSDT", 101.0)
    closed = await eng._close_trade(int(trade["id"]), "reverse_signal")
    assert closed
    print("\nentry:", trade["entry_price"], "exit:", closed["exit_price"],
          "qty:", trade["qty"])
    print("fills:", [(f.side, round(f.qty, 4), round(f.price, 4),
                      round(f.commission, 4), f.order_id) for f in ex.fills])
    print("gross", closed["gross_pnl"], "fee", closed["fee_paid"],
          "net", closed["net_pnl"], "source", closed["pnl_source"])
    print("cash open:", round(after_open - before, 6),
          "cash close:", round(ex.balance - after_open, 6),
          "exchange fees:", round(ex.fees_paid, 6))
    print("income:", await eng.hub.income_totals())
    assert abs((ex.balance - before) - closed["net_pnl"]) < 1e-6


async def test_entry_fee_is_booked_even_when_the_close_happens_after_a_restart(db, store):
    """
    Regression: a position opened in one run and closed in the next used to lose
    its entry commission — the closing window no longer holds the opening fill —
    so the released P&L (and the equity bridge) drifted by exactly that fee.
    """
    from app.exchange.sim import SimExchange
    from app.journal import Journal

    eng = await make_engine(store, universe=12)
    ex = eng.hub.exchange
    await _set_price(eng, "BTCUSDT", 100.0)
    before = ex.balance
    trade = await eng._open_trade("execution-1",
                                  make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0,
                                   "open_count": 0})
    assert trade and trade["entry_fee"] > 0

    # ---- restart: snapshot the venue, rebuild it from the snapshot --------
    snapshot = ex.export_state()
    assert snapshot["realized_total"] == ex.realized_total
    reborn = SimExchange(balance=10_000.0, universe=12, history_bars=300,
                         interval="5m", time_accel=3000.0, restore=snapshot)
    assert reborn.balance == pytest.approx(ex.balance), "the adopted balance must be real"
    assert "BTCUSDT" in reborn.positions

    eng.hub.exchange = reborn
    eng.open_trades = {}
    await eng._load_open_trades()
    assert int(trade["id"]) in eng.open_trades, "the journal re-adopts the position"

    # the venue closes it with a real fill: the entry leg is gone from the window
    await _set_price(eng, "BTCUSDT", 101.0)
    reborn.tickers["BTCUSDT"].last = reborn.tickers["BTCUSDT"].mark = 101.0
    close_order = await reborn.market_order("BTCUSDT", "SELL", float(trade["qty"]),
                                            reduce_only=True)
    assert close_order.status == "FILLED"
    after_venue = reborn.balance
    closed = await eng._finalize_close(int(trade["id"]), 101.0, "tp")

    # the trade's net must equal every cent the venue moved for it — the entry
    # commission paid before the restart plus the exit commission paid now
    cash = after_venue - before
    assert closed["pnl_source"] == "fills"
    assert abs(cash - closed["net_pnl"]) < 1e-6, (cash, closed["net_pnl"])
    assert closed["fee_paid"] > float(trade["entry_fee"]), "both legs must be booked"

    # and the equity bridge still balances with the venue ledger
    journal = Journal(store=store)
    state = await journal.update(await eng.hub.account(), persist=False)
    assert abs((state.equity or 0) - (before + closed["net_pnl"])) < 1e-6
    rep = await eng.reconcile_exchange()
    assert abs(rep["net_drift"]) < 1.0, rep


async def test_sim_income_totals_survive_a_restart(db, store):
    """The ledger is cumulative, not a window over the (rolling) fill list."""
    from app.exchange.sim import SimExchange

    eng = await make_engine(store, universe=12)
    ex = eng.hub.exchange
    await _set_price(eng, "BTCUSDT", 100.0)
    await ex.market_order("BTCUSDT", "BUY", 1.0)
    await ex.market_order("BTCUSDT", "SELL", 1.0, reduce_only=True)
    totals = await ex.income_totals()
    assert totals["fills"] == 2

    reborn = SimExchange(balance=10_000.0, universe=12, history_bars=300,
                         interval="5m", time_accel=3000.0, restore=ex.export_state())
    again = await reborn.income_totals()
    assert again["realized"] == pytest.approx(totals["realized"])
    assert again["fees"] == pytest.approx(totals["fees"])
    assert again["fills"] == 0, "the fill window itself does not survive"


async def test_wallet_balance_is_cash_not_equity(db, store):
    """`totalWalletBalance` must be cash (fees netted, unrealised excluded) —
    exactly like Binance — so the bridge can be checked against the balance."""
    eng = await make_engine(store, universe=12)
    await _set_price(eng, "BTCUSDT", 100.0)
    ex = eng.hub.exchange
    cash0 = ex.balance
    trade = await eng._open_trade("execution-1",
                                  make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0,
                                   "open_count": 0})
    await _set_price(eng, "BTCUSDT", 103.0)
    acct = await eng.hub.account()
    assert acct.total_wallet_balance == pytest.approx(ex.balance)
    assert acct.total_margin_balance == pytest.approx(ex.balance + acct.total_unrealized_pnl)
    assert acct.total_unrealized_pnl > 0

    # the venue cash movement must equal the journal's released P&L plus the
    # entry legs of whatever is still open
    await _set_price(eng, "BTCUSDT", 104.0)
    closed = await eng._close_trade(int(trade["id"]), "reverse_signal")
    assert closed
    acct = await eng.hub.account()
    assert (acct.total_wallet_balance - cash0) == pytest.approx(closed["net_pnl"], abs=1e-6)
    assert acct.total_margin_balance == pytest.approx(acct.total_wallet_balance)
