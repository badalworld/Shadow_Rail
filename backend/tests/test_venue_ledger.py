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
