"""
Venue ledger accounting + live-stream ownership.

Two regressions are pinned here:

1. **Only trading income is trading income.**  Binance's income ledger also
   carries transfers, bonuses, insurance clears and rebates.  Counting those as
   realised P&L made `/api/reconcile` report drift on an otherwise clean
   account (and could hide a genuine mis-booking behind a deposit).
2. **A background stream must be referenced.**  `asyncio` keeps only a weak
   reference to a task, so a bare `create_task()` can be garbage-collected
   mid-flight — which would silently kill the market stream of a live engine.
"""
from __future__ import annotations

from app.exchange.base import Fill
from app.exchange.binance import BinanceFutures


def _income(kind: str, amount: float) -> Fill:
    return Fill(symbol="BTCUSDT", side=kind, qty=0.0, price=0.0,
                realized_pnl=amount, kind=kind)


async def test_venue_ledger_counts_only_trading_income(monkeypatch):
    client = BinanceFutures("k", "s")

    async def fake_income(limit: int = 1000):        # noqa: ARG001 — signature match
        return [
            _income("REALIZED_PNL", 120.0),
            _income("COMMISSION", -7.5),
            _income("FUNDING_FEE", -1.25),
            _income("TRANSFER", 5_000.0),            # a deposit, not profit
            _income("WELCOME_BONUS", 50.0),
            _income("REFERRAL_KICKBACK", 3.0),
        ]

    monkeypatch.setattr(client, "income", fake_income)
    totals = await client.income_totals()
    assert totals is not None
    assert round(totals["realized"], 6) == 120.0
    assert round(totals["fees"], 6) == 7.5          # fees are reported positive
    assert round(totals["funding"], 6) == -1.25     # signed, as charged
    assert round(totals["other"], 6) == 5_053.0     # cash movements kept apart
    assert totals["rows"] == 6


async def test_reconcile_gross_never_sees_a_deposit(monkeypatch):
    """The number the journal is reconciled against excludes cash movements."""
    client = BinanceFutures("k", "s")

    async def fake_income(limit: int = 1000):        # noqa: ARG001
        return [_income("REALIZED_PNL", 10.0), _income("TRANSFER", 1_000.0)]

    monkeypatch.setattr(client, "income", fake_income)
    totals = await client.income_totals()
    assert totals["realized"] - totals["fees"] + totals["funding"] == 10.0


async def test_kline_streams_keep_a_strong_task_reference(monkeypatch):
    client = BinanceFutures("k", "s")
    seen: list[str] = []

    async def fake_worker(url, on_candle, on_close):   # noqa: ARG001
        seen.append(url)

    monkeypatch.setattr(client, "_kline_worker", fake_worker)
    await client.stream_klines([f"SYM{i}USDT" for i in range(320)], "5m",
                               lambda payload: None, None)

    # 320 symbols / 150 per connection → three workers, all still referenced
    assert len(client._stream_tasks) == 3
    for task in client._stream_tasks:
        assert not task.cancelled()
    assert all("streams=" in url for url in seen)


async def test_funding_on_open_positions_is_reported_not_counted_as_drift(db, store):
    """
    The venue bills funding on a position while it is still open; the journal
    books it when the trade closes.  The trading-P&L drift must stay exact
    through both halves, and the venue's early charge must be visible on its own
    line instead of hiding inside a loose tolerance.
    """
    import pytest

    from app.exchange.base import Fill
    from app.util import now_ms
    from tests.test_engine_flow import make_engine, make_opportunity, _set_price

    eng = await make_engine(store, universe=12)
    await _set_price(eng, "BTCUSDT", 100.0)
    trade = await eng._open_trade("execution-1",
                                  make_opportunity("BTCUSDT", "LONG", 100.0, 2.0),
                                  {"equity": 10_000.0, "available": 10_000.0,
                                   "open_count": 0})
    assert trade

    # …the venue charges funding on the still-open position (exactly what the
    # simulator's _maybe_funding does: balance, ledger and the fill record)
    ex = eng.hub.exchange
    charge = -4.75
    ex.balance += charge
    ex.funding_net += charge
    ex.positions["BTCUSDT"].funding_paid += charge
    ex.fills.append(Fill(symbol="BTCUSDT", side="FUNDING_FEE", qty=0.0, price=0.0,
                         realized_pnl=charge, ts=now_ms(), kind="FUNDING_FEE",
                         trade_id="funding-test"))

    rep = await eng.reconcile_exchange()
    assert rep["open_trades"] == 1
    assert abs(rep["net_drift"]) < 0.01, rep          # trading P&L is untouched
    assert rep["funding_drift"] == pytest.approx(charge, abs=1e-6)
    assert rep["balanced"], rep

    # once the trade closes the journal books that funding: the funding line
    # returns to zero and the drift stays exact
    await _set_price(eng, "BTCUSDT", 101.0)
    closed = await eng._close_trade(int(trade["id"]), "reverse_signal")
    assert closed and closed["funding_paid"] == pytest.approx(-charge, abs=1e-9)
    rep = await eng.reconcile_exchange()
    assert abs(rep["net_drift"]) < 0.01, rep
    assert rep["funding_drift"] == pytest.approx(0.0, abs=1e-6), rep
    assert rep["balanced"], rep
