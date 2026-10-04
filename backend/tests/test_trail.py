"""
ROI trailing stop — the user's rule:

    arm at +25 % ROI, keep the stop 15 ROI-points behind the peak
    (so it starts at +10 % ROI) and only ever ratchet up.

ROI is measured on margin, like Binance: roi = uPnL / margin × 100.
"""
from __future__ import annotations

import pytest

from app.db import DB
from app.risk import roi_points, roi_price_step, trail_stop_price
from tests.test_engine_flow import make_engine, make_opportunity, _set_price

pytestmark = pytest.mark.asyncio

ENTRY, QTY, MARGIN = 100.0, 80.0, 800.0


def _price_for_roi(roi: float) -> float:
    """Price that yields `roi` ROI points on a 10x long at 100."""
    return ENTRY + roi * roi_price_step(MARGIN, QTY)


async def test_roi_is_measured_against_the_margin():
    assert roi_price_step(MARGIN, QTY) == pytest.approx(0.1)
    assert roi_points(ENTRY, 102.5, QTY, MARGIN, "LONG") == pytest.approx(25.0)
    assert roi_points(ENTRY, 97.5, QTY, MARGIN, "SHORT") == pytest.approx(25.0)
    assert roi_points(ENTRY, 97.5, QTY, MARGIN, "LONG") == pytest.approx(-25.0)


async def test_the_trail_arms_at_25_and_starts_at_10():
    off = trail_stop_price(entry=ENTRY, side="LONG", peak_price=_price_for_roi(24.9),
                           mark=_price_for_roi(24.9), qty=QTY, margin=MARGIN,
                           prev_stop=95.0, activation_roi=25.0, distance_roi=15.0)
    assert off == (95.0, False), "must not arm one tick below the activation"
    stop, armed = trail_stop_price(entry=ENTRY, side="LONG",
                                   peak_price=_price_for_roi(25.0), mark=_price_for_roi(25.0),
                                   qty=QTY, margin=MARGIN, prev_stop=95.0,
                                   activation_roi=25.0, distance_roi=15.0)
    assert armed
    assert stop == pytest.approx(_price_for_roi(10.0))     # 25 − 15 = +10 % ROI


async def test_the_trail_ratchets_with_the_peak_and_never_loosens():
    stop = _price_for_roi(10.0)
    best = stop
    for peak_roi in (30, 40, 55, 80):
        peak = _price_for_roi(peak_roi)
        new, armed = trail_stop_price(entry=ENTRY, side="LONG", peak_price=peak,
                                      mark=peak, qty=QTY, margin=MARGIN, prev_stop=stop,
                                      activation_roi=25.0, distance_roi=15.0)
        assert armed and new > stop
        assert new == pytest.approx(_price_for_roi(peak_roi - 15.0))
        stop = best = new
    # the market falls back: the peak stays, the stop must not follow down
    new, _ = trail_stop_price(entry=ENTRY, side="LONG", peak_price=_price_for_roi(80.0),
                              mark=_price_for_roi(20.0), qty=QTY, margin=MARGIN,
                              prev_stop=stop, activation_roi=25.0, distance_roi=15.0)
    assert new == pytest.approx(best)


async def test_the_trail_never_sits_on_the_losing_side_or_beyond_liquidation():
    # a very wide distance can never drag the stop below break-even
    stop, armed = trail_stop_price(entry=ENTRY, side="LONG", peak_price=_price_for_roi(40.0),
                                   mark=_price_for_roi(40.0), qty=QTY, margin=MARGIN,
                                   prev_stop=90.0, activation_roi=25.0, distance_roi=90.0)
    assert armed and stop >= ENTRY, (stop, armed)
    # shorts mirror it
    stop, armed = trail_stop_price(entry=ENTRY, side="SHORT", peak_price=97.5, mark=97.5,
                                   qty=QTY, margin=MARGIN, prev_stop=101.5,
                                   activation_roi=25.0, distance_roi=15.0)
    assert armed and stop == pytest.approx(99.0) and stop < ENTRY
    # the mark gap keeps the trigger behind the last price (no instant fire)
    stop, _ = trail_stop_price(entry=ENTRY, side="LONG", peak_price=130.0, mark=103.0,
                               qty=QTY, margin=MARGIN, prev_stop=95.0,
                               activation_roi=25.0, distance_roi=15.0, mark_gap_pct=0.5)
    assert stop <= 103.0 * 0.995


async def _open(eng):
    await _set_price(eng, "BTCUSDT", ENTRY)
    return await eng._open_trade("execution-1", make_opportunity("BTCUSDT", "LONG", ENTRY, 2.0),
                                 {"equity": 10_000.0, "available": 10_000.0, "open_count": 0})


async def _set_clean(eng, price: float, low: float | None = None) -> None:
    """Force the last candle to a clean o=h=c=price (l=low) — the shared test
    helper keeps the seeded BTC high, which would trip an unrelated order."""
    sim = eng.hub.exchange
    await _set_price(eng, "BTCUSDT", price)
    candles = sim.candles["BTCUSDT"]
    last = candles[-1]
    candles[-1] = type(last)(last.t, price, price, low if low is not None else price,
                             price, last.v)


async def _tick(eng, trade, price):
    await _set_clean(eng, price)
    pos = next(p for p in await eng.hub.positions() if p.symbol == "BTCUSDT")
    pos.mark_price = price
    await eng._monitor_tick(trade, pos)


async def _protective_orders(eng):
    return [o for o in eng.hub.exchange.orders.values()
            if o.symbol == "BTCUSDT" and o.status == "NEW"]


async def test_the_trail_moves_the_single_stop_order_on_the_venue(db, store):
    eng = await make_engine(store, universe=12)
    store.cfg.risk.trail_min_step_roi_pct = 0.0      # every tick may ratchet
    store.cfg.risk.be_lock_enabled = False           # isolate the trail's arming
    store.save()
    trade = await _open(eng)
    assert trade
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])
    fixed_sl = float(trade["sl_price"])
    orders = await _protective_orders(eng)
    assert len(orders) == 2, "one stop + one target, exactly one system"

    # +20 % ROI → still the original stop
    await _tick(eng, trade, base + 20 * step)
    assert not trade["trail_active"]
    assert float(trade["sl_price"]) == fixed_sl

    # +25 % ROI → armed, stop locks +10 % ROI
    await _tick(eng, trade, base + 25 * step)
    assert trade["trail_active"] == 1
    assert float(trade["sl_price"]) == pytest.approx(base + 10 * step, rel=1e-4)
    assert trade["trail_stop"] == float(trade["sl_price"])

    # +45 % ROI → the stop follows to +30 %
    await _tick(eng, trade, base + 45 * step)
    assert float(trade["sl_price"]) == pytest.approx(base + 30 * step, rel=1e-4)

    # price gives some back — the stop must not move with it
    await _tick(eng, trade, base + 33 * step)
    assert float(trade["sl_price"]) == pytest.approx(base + 30 * step, rel=1e-4)

    # still exactly ONE live stop order, and it is the new trail price
    orders = await _protective_orders(eng)
    assert len(orders) == 2, "the trail must replace the stop, never add one"
    stops = [o for o in orders if o.type == "STOP_MARKET"]
    assert len(stops) == 1
    assert stops[0].stop_price == pytest.approx(float(trade["sl_price"]), rel=1e-6)
    assert str(trade["sl_order_id"]) == stops[0].order_id

    row = await DB.get_trade(int(trade["id"]))
    assert row["trail_active"] == 1
    assert row["trail_stop"] == pytest.approx(float(trade["trail_stop"]))
    events = await DB.trade_events(int(trade["id"]))
    assert any(e["kind"] == "trail" for e in events)


async def test_the_trail_stop_closes_the_trade_and_is_labelled_trail(db, store):
    eng = await make_engine(store, universe=12)
    store.cfg.risk.trail_min_step_roi_pct = 0.0
    store.save()
    trade = await _open(eng)
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])
    await _tick(eng, trade, base + 40 * step)          # arm, stop at +25 % ROI
    trail_price = float(trade["sl_price"])

    # the market falls through the trail: the venue fills the stop itself
    # (a clean candle: high = mark, low pierces the trail — the TP is far above)
    await _set_clean(eng, base + 5 * step, low=trail_price - 0.01)
    eng.hub.exchange.tickers["BTCUSDT"].mark = base + 5 * step
    await eng.hub.exchange._check_protective_orders()

    positions = await eng.hub.positions()
    assert not [p for p in positions if p.symbol == "BTCUSDT"], "the stop must fire"

    closed = await eng._finalize_close(int(trade["id"]), base + 5 * step)
    assert closed["close_reason"] == "trail", closed["close_reason"]
    assert closed["net_pnl"] > 0, "a trailed exit must still lock a profit"
    assert closed["pnl_source"] == "fills"


async def test_trail_is_disabled_when_the_operator_turns_it_off(db, store):
    eng = await make_engine(store, universe=12)
    store.cfg.risk.trail_roi_enabled = False
    store.cfg.risk.be_lock_enabled = False           # off means off — both of them
    store.save()
    trade = await _open(eng)
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])
    fixed = float(trade["sl_price"])
    await _tick(eng, trade, base + 60 * step)
    assert not trade["trail_active"]
    assert float(trade["sl_price"]) == fixed


async def test_the_trail_survives_a_restart_and_still_sees_the_live_stop(db, store):
    """After a restart the journal must find the stop that is already on the
    venue, otherwise the trail would leave a second, orphaned order behind."""
    eng = await make_engine(store, universe=12)
    store.cfg.risk.trail_min_step_roi_pct = 0.0
    store.save()
    trade = await _open(eng)
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])
    await _tick(eng, trade, base + 30 * step)
    assert trade["trail_active"] == 1
    trail_price = float(trade["sl_price"])

    # a restart loses the in-memory id (and old rows have no column value):
    # the engine must recover it from the venue before re-placing anything
    eng.open_trades.clear()
    await eng._load_open_trades()
    reloaded = next(iter(eng.open_trades.values()))
    assert str(reloaded["sl_order_id"]), "the live stop order id must be recovered"

    reloaded["sl_order_id"] = ""          # simulate a row with no id at all
    await _tick(eng, reloaded, base + 40 * step)
    stops = [o for o in await _protective_orders(eng) if o.type == "STOP_MARKET"]
    assert len(stops) == 1, f"exactly one stop order, found {len(stops)}"
    assert stops[0].stop_price > trail_price, "the trail must have ratcheted up"
