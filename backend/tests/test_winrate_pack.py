"""
Win-rate pack — the strategy-upgrade invariants.

Covers:
  • entry confluence gates (volume, body, HTF slope, volatility band,
    rail extension, BTC regime) and their fail-open behaviour
  • the break-even stop lock on the ONE stop order (never a second one)
  • the stall exit for trades whose thesis never arrived
  • conviction-weighted sizing
  • loss-streak cooldowns, journal-driven symbol blacklist
  • confidence model v2 factors + stale-model rejection + new defaults
"""
from __future__ import annotations

import json
import time

import pytest

from app import strategy as ST
from app.config import RiskSettings
from app.confidence import MODEL, WEIGHTS, ConfidenceModel
from app.risk import (RiskEngine, breakeven_stop_price, roi_points, roi_price_step)
from app.exchange.base import SymbolFilter
from tests.test_engine_flow import make_engine, make_opportunity, _set_price
from tests.test_trail import ENTRY, QTY, MARGIN, _open, _set_clean, _tick

pytestmark = pytest.mark.asyncio


def _sig(**over) -> dict:
    base = {"direction": "LONG", "entry": 100.0, "atr_pct": 0.55, "atr": 0.55,
            "vol_surge": 1.2, "body_strength": 0.6, "htf_ema_up": True,
            "htf_ready": True, "rail_distance_pct": 0.8, "trend_quality": 0.7,
            "clean_ratio": 0.7}
    base.update(over)
    return base


# ───────────────────────────────── pure gates ───────────────────────────── #
async def test_volume_and_body_gates_reject_unconfirmed_flips():
    r = RiskSettings()
    ok, why = ST.entry_gate(_sig(vol_surge=0.4), r)
    assert not ok and "volume" in why
    ok, why = ST.entry_gate(_sig(body_strength=0.05), r)
    assert not ok and "body" in why
    # a long with a negative close-vs-open body is rejected; a short wants it
    assert ST.entry_gate(_sig(direction="SHORT", body_strength=-0.6, htf_ema_up=False), r)[0]
    assert not ST.entry_gate(_sig(direction="SHORT", body_strength=0.6, htf_ema_up=False), r)[0]


async def test_htf_slope_volatility_band_and_rail_extension_gates():
    r = RiskSettings()
    assert not ST.entry_gate(_sig(htf_ema_up=False), r)[0]           # tide against us
    assert ST.entry_gate(_sig(direction="SHORT", body_strength=-0.6,
                             htf_ema_up=False), r)[0]
    assert not ST.entry_gate(_sig(atr_pct=0.10), r)[0]               # dead chop
    assert not ST.entry_gate(_sig(atr_pct=3.0), r)[0]                # event chaos
    assert not ST.entry_gate(_sig(rail_distance_pct=9.0), r)[0]     # chasing
    assert ST.entry_gate(_sig(rail_distance_pct=-0.4), r)[0]        # fresh off the rail


async def test_btc_regime_gate_and_fail_open_rules():
    r = RiskSettings()
    ok, why = ST.entry_gate(_sig(), r, btc_aligned=False)
    assert not ok and "BTC" in why
    assert ST.entry_gate(_sig(), r, btc_aligned=None)[0]            # no opinion → pass
    assert ST.entry_gate(_sig(), r, btc_aligned=True)[0]
    # missing measurements NEVER fabricate a reject (warm-up safety)
    assert ST.entry_gate({"direction": "LONG"}, r, btc_aligned=None)[0]
    # and the operator can turn every gate off
    r2 = RiskSettings(require_volume_confirm=False, require_body_confirm=False,
                      require_htf_slope=False, require_volatility_band=False,
                      enforce_rail_extension=False, require_btc_alignment=False)
    assert ST.entry_gate(_sig(vol_surge=0.0, atr_pct=5.0, body_strength=0.0),
                         r2, btc_aligned=False)[0]


async def test_btc_regime_allows_semantics():
    assert ST.btc_regime_allows(-1, False, "LONG") is False    # firmly bearish
    assert ST.btc_regime_allows(1, True, "LONG") is True
    assert ST.btc_regime_allows(-1, True, "LONG") is True      # split tape → our chart
    assert ST.btc_regime_allows(0, True, "SHORT") is None     # warm-up → no opinion


async def test_sizing_tiers_and_confidence_effect_in_plan():
    assert ST.sizing_multiplier(92) == 1.15
    assert ST.sizing_multiplier(85) == 1.0
    assert ST.sizing_multiplier(70) == 0.8
    assert ST.sizing_multiplier(None) == 1.0

    flt = SymbolFilter(symbol="TESTUSDT")
    s = RiskSettings(size_pct_per_trade=8.0, min_confidence=0.0)
    eng = RiskEngine(s)
    base = eng.plan(symbol="TESTUSDT", side="LONG", price=100.0, atr=0.5,
                    equity=10_000.0, available=10_000.0, flt=flt, open_trades=0,
                    confidence=None)
    big = eng.plan(symbol="TESTUSDT", side="LONG", price=100.0, atr=0.5,
                   equity=10_000.0, available=10_000.0, flt=flt, open_trades=0,
                   confidence=95.0)
    small = eng.plan(symbol="TESTUSDT", side="LONG", price=100.0, atr=0.5,
                     equity=10_000.0, available=10_000.0, flt=flt, open_trades=0,
                     confidence=70.0)
    assert base.ok and big.ok and small.ok
    assert big.margin == pytest.approx(base.margin * 1.15, rel=1e-6)
    assert small.margin == pytest.approx(base.margin * 0.8, rel=1e-6)
    # disabled → flat sizing again
    eng_off = RiskEngine(RiskSettings(size_pct_per_trade=8.0, min_confidence=0.0,
                                      confidence_sizing=False))
    off = eng_off.plan(symbol="TESTUSDT", side="LONG", price=100.0, atr=0.5,
                       equity=10_000.0, available=10_000.0, flt=flt, open_trades=0,
                       confidence=95.0)
    assert off.margin == pytest.approx(base.margin, rel=1e-6)


# ───────────────────────────────── trade management ──────────────────────── #
async def test_breakeven_lock_moves_and_never_loosens():
    entry, qty, margin = ENTRY, QTY, MARGIN
    step = roi_price_step(margin, qty)
    assert step == pytest.approx(0.1)
    # below the activation ROI the stop is untouched
    stop, armed = breakeven_stop_price(entry=entry, side="LONG", peak_price=entry + 0.7,
                                       mark=entry + 0.7, qty=qty, margin=margin,
                                       prev_stop=97.0, activation_roi=8.0, buffer_roi=1.5)
    assert not armed and stop == 97.0
    # above it, the stop jumps to entry + the buffer (+1.5 ROI ⇒ +0.15 price)
    stop, armed = breakeven_stop_price(entry=entry, side="LONG", peak_price=entry + 0.9,
                                       mark=entry + 0.9, qty=qty, margin=margin,
                                       prev_stop=97.0, activation_roi=8.0, buffer_roi=1.5)
    assert armed and stop == pytest.approx(100.15)
    # already past break-even (e.g. the trail got there first) → no re-place
    stop, armed = breakeven_stop_price(entry=entry, side="LONG", peak_price=entry + 1.0,
                                       mark=entry + 1.0, qty=qty, margin=margin,
                                       prev_stop=100.5, activation_roi=8.0, buffer_roi=1.5)
    assert not armed
    # the mark has fallen back under the BE price → placement would fire at once
    stop, armed = breakeven_stop_price(entry=entry, side="LONG", peak_price=entry + 1.0,
                                       mark=entry + 0.02, qty=qty, margin=margin,
                                       prev_stop=97.0, activation_roi=8.0, buffer_roi=1.5,
                                       mark_gap_pct=0.05)
    assert stop < entry or not armed     # engine guard keeps losses out of the lock
    # shorts mirror
    stop, armed = breakeven_stop_price(entry=entry, side="SHORT", peak_price=entry - 0.9,
                                       mark=entry - 0.9, qty=qty, margin=margin,
                                       prev_stop=103.0, activation_roi=8.0, buffer_roi=1.5)
    assert armed and stop == pytest.approx(99.85)


async def test_stall_exit_rules():
    step = roi_price_step(MARGIN, QTY)
    due = ST.stall_exit_due(opened_at_ms=0, now_ms=int(95 * 60_000), entry=ENTRY,
                            peak_price=ENTRY + 0.05 * step, qty=QTY, margin=MARGIN,
                            side="LONG", minutes=90, min_peak_roi=2.0, roi_points_fn=roi_points)
    assert due, "95 min without even +2 % ROI must stall out"
    assert not ST.stall_exit_due(opened_at_ms=0, now_ms=int(60 * 60_000), entry=ENTRY,
                                 peak_price=ENTRY, qty=QTY, margin=MARGIN, side="LONG",
                                 minutes=90, min_peak_roi=2.0, roi_points_fn=roi_points)
    assert not ST.stall_exit_due(opened_at_ms=0, now_ms=int(95 * 60_000), entry=ENTRY,
                                 peak_price=ENTRY + 3 * step, qty=QTY, margin=MARGIN,
                                 side="LONG", minutes=90, min_peak_roi=2.0,
                                 roi_points_fn=roi_points)
    # disabled with 0
    assert not ST.stall_exit_due(opened_at_ms=0, now_ms=10**12, entry=ENTRY,
                                 peak_price=ENTRY, qty=QTY, margin=MARGIN, side="LONG",
                                 minutes=0, min_peak_roi=2.0, roi_points_fn=roi_points)


async def test_loss_streak_cooldown_and_blacklist():
    assert ST.loss_streak_cooldown_s(0, 300) == 300
    assert ST.loss_streak_cooldown_s(1, 300) == 300
    assert ST.loss_streak_cooldown_s(2, 300) == 600
    assert ST.loss_streak_cooldown_s(3, 300) == 900
    assert ST.loss_streak_cooldown_s(9, 300) == 1200          # capped at 4×
    assert ST.blacklist_entry(trades=10, win_rate=25.0, net=-50.0,
                              min_trades=8, max_winrate=30.0)
    assert not ST.blacklist_entry(trades=5, win_rate=10.0, net=-50.0,
                                  min_trades=8, max_winrate=30.0)
    assert not ST.blacklist_entry(trades=10, win_rate=40.0, net=5.0,
                                  min_trades=8, max_winrate=30.0)
    assert not ST.blacklist_entry(trades=10, win_rate=25.0, net=5.0,     # net positive
                                  min_trades=8, max_winrate=30.0)
    assert not ST.blacklist_entry(trades=10, win_rate=25.0, net=-50.0,
                                  min_trades=8, max_winrate=0)          # guard off


# ───────────────────────────────── confidence v2 ─────────────────────────── #
async def test_confidence_scores_all_v2_factors_and_neutralizes_missing_data():
    res = MODEL.score({"direction": "LONG", "trend_quality": 0.8, "htf_bull": True,
                       "tier": "strong", "atr_pct": 0.6, "flow_bias": 0.4,
                       "rail_distance_pct": 0.5})
    assert set(res.factors) >= set(WEIGHTS.keys())
    assert res.factors["htf_momentum"] == pytest.approx(60.0)   # no data → neutral
    assert res.factors["market_regime"] == pytest.approx(60.0)
    assert res.factors["candle_confirm"] == pytest.approx(60.0)

    res2 = MODEL.score({"direction": "LONG", "trend_quality": 0.8, "htf_bull": True,
                        "tier": "strong", "atr_pct": 0.6, "flow_bias": 0.4,
                        "rail_distance_pct": 0.5, "htf_ema_up": True, "htf_ready": True,
                        "vol_surge": 1.6, "body_strength": 0.8, "btc_aligned": True})
    assert res2.factors["htf_momentum"] == 100.0
    assert res2.factors["market_regime"] == 100.0
    assert res2.factors["candle_confirm"] > 60.0
    assert res2.score > res.score

    res3 = MODEL.score({"direction": "LONG", "trend_quality": 0.8, "htf_bull": True,
                        "tier": "strong", "atr_pct": 0.6, "flow_bias": 0.4,
                        "rail_distance_pct": 0.5, "htf_ema_up": False, "htf_ready": True,
                        "vol_surge": 0.5, "body_strength": 0.0, "btc_aligned": False})
    assert res3.factors["htf_momentum"] == 15.0
    assert res3.factors["market_regime"] == 25.0
    assert res3.score < res2.score


async def test_stale_model_on_a_different_factor_set_is_refused(tmp_path):
    p = tmp_path / "confidence_model.json"
    p.write_text(json.dumps({"coef": [0.1] * 7, "intercept": 0.0, "samples": 999,
                             "keys": ["trend_quality", "htf_alignment", "volume_flow",
                                      "volatility_fit", "rail_position", "signal_tier",
                                      "symbol_history"]}))
    m = ConfidenceModel(model_path=str(p))
    assert m.coef is None, "a v1 model must never score a v2 feature vector"


async def test_train_accepts_factor_rows(tmp_path):
    m = ConfidenceModel(model_path=str(tmp_path / "m.json"))
    import random
    rng = random.Random(3)
    rows = []
    for _ in range(80):
        win = rng.random() > 0.5
        f = {k: (90.0 if win else 40.0) if k == "trend_quality"
             else (rng.uniform(30, 70)) for k in WEIGHTS}
        rows.append({"factors": f, "win": 1 if win else 0})
    res = m.train(rows)
    assert res["trained"] and m.coef is not None
    saved = json.loads((tmp_path / "m.json").read_text())
    assert saved["format"] == 2 and list(saved["keys"]) == list(WEIGHTS.keys())
    # reload respects the version + key check
    m2 = ConfidenceModel(model_path=str(tmp_path / "m.json"))
    assert m2.coef is not None and m2.samples == 80


async def test_winrate_pack_defaults_are_live():
    r = RiskSettings()
    assert r.min_confidence >= 65.0, "A-grade gate is the new default"
    assert r.daily_drawdown_stop_pct > 0.0, "the circuit breaker ships armed"
    assert r.require_volume_confirm and r.require_body_confirm
    assert r.require_htf_slope and r.require_volatility_band
    assert r.enforce_rail_extension and r.require_btc_alignment
    assert r.be_lock_enabled and r.be_lock()[1] > 0
    assert r.stall_exit_minutes > 0
    assert 0 < r.max_same_side_trades < r.max_concurrent_trades
    assert r.confidence_sizing and r.loss_streak_cooldown and r.auto_blacklist_symbols


# ───────────────────────────────── engine integration ─────────────────────── #
async def test_breakeven_lock_arms_on_the_live_monitor_and_protects_the_exit(db, store):
    eng = await make_engine(store, universe=12)
    trade = await _open(eng)
    assert trade
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])

    # +10 % ROI → below the trail (25) but above BE (8): the ONE stop moves
    await _tick(eng, trade, base + 10 * step)
    assert float(trade["sl_price"]) > base, "break-even lock must move the stop past entry"
    assert float(trade["sl_price"]) == pytest.approx(base + 1.5 * step, rel=1e-3)
    orders = await eng.hub.open_orders("BTCUSDT")
    n_stop = sum(1 for o in orders if o["type"] == "STOP_MARKET")
    assert n_stop == 1, f"still exactly ONE stop order after the lock (got {n_stop})"

    # the market dips through the locked level → the stop fires AT the lock,
    # converting the would-be -300 stop-out into a scratch-with-fees (a win)
    be_stop = float(trade["sl_price"])
    sim = eng.hub.exchange
    await _set_clean(eng, base + 0.6, low=be_stop - 0.01)
    sim.tickers["BTCUSDT"].mark = base + 0.6
    await sim._check_protective_orders()
    assert not [p for p in await eng.hub.positions() if p.symbol == "BTCUSDT"], \
        "the break-even stop must fire"
    closed = await eng._finalize_close(int(trade["id"]), be_stop)
    assert closed["gross_pnl"] > 0, "the exit is booked at the locked level, not lower"
    assert closed["exit_price"] >= base, "break-even must never close a long below entry"
    assert closed["r_multiple"] > 0


async def test_stalled_trade_is_closed_by_the_monitor(db, store):
    eng = await make_engine(store, universe=12)
    trade = await _open(eng)
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])
    # aged out, and it never got more than +0.5 % ROI
    trade["opened_at"] = trade["opened_at"] - int(95 * 60_000)
    await _tick(eng, trade, base + 0.05 * step)
    assert not eng.open_trades, "the stall exit must have closed the trade"
    from app.db import DB
    row = await DB.get_trade(int(trade["id"]))
    assert row["close_reason"] == "stalled"


async def test_stall_exit_leaves_a_working_trade_alone(db, store):
    eng = await make_engine(store, universe=12)
    trade = await _open(eng)
    step = roi_price_step(float(trade["margin"]), float(trade["qty"]))
    base = float(trade["entry_price"])
    trade["opened_at"] = trade["opened_at"] - int(200 * 60_000)
    await _tick(eng, trade, base + 6 * step)     # +6 % peak ROI → thesis arrived
    assert eng.open_trades, "a trade that showed promise keeps running"


async def test_same_side_concentration_cap(db, store):
    eng = await make_engine(store, universe=20)
    store.cfg.risk.max_same_side_trades = 1
    store.save()
    for sym in ("ADAUSDT", "XRPUSDT"):
        await _set_price(eng, sym, 100.0)
    opened = await eng._stage_execute([make_opportunity("ADAUSDT", "LONG", 100.0, 2.0),
                                       make_opportunity("XRPUSDT", "LONG", 100.0, 2.0)])
    assert len(opened) == 1, "ten long alts are one long BTC — the cap holds"
    # the opposite side is still free
    await _set_price(eng, "SOLUSDT", 100.0)
    shorts = await eng._stage_execute([make_opportunity("SOLUSDT", "SHORT", 100.0, 2.0)])
    assert len(shorts) == 1


async def test_loss_streak_extends_the_reentry_cooldown(db, store):
    eng = await make_engine(store, universe=12)
    base = 100.0
    for n in (1, 2):
        await _set_price(eng, "BTCUSDT", base)
        trade = await eng._open_trade("execution-1",
                                      make_opportunity("BTCUSDT", "LONG", base, 2.0),
                                      {"equity": 10_000.0, "available": 10_000.0,
                                       "open_count": 0})
        assert trade
        await _set_price(eng, "BTCUSDT", base - 5)          # deep loss
        await eng._close_trade(int(trade["id"]), "sl")
        wait = eng.cooldowns["BTCUSDT"] - time.time()
        if n == 1:
            assert 250 < wait <= 310, f"first loss waits the base cooldown ({wait:.0f}s)"
        else:
            assert 550 < wait <= 610, f"second consecutive loss waits 2× ({wait:.0f}s)"
        eng.cooldowns.clear()


async def test_btc_alignment_helper_is_opinion_only(db, store, monkeypatch):
    eng = await make_engine(store, universe=12)

    async def no_series(symbol, force=False):
        return None
    monkeypatch.setattr(eng, "_compute_series", no_series)
    assert await eng._btc_alignment("LONG", "ETHUSDT") is None   # missing data → pass

    class FakeSeries:
        last = 0
        htf_ready = [True]
        trend = [-1]
        htf_bull = [0.0]
    async def bearish(symbol, force=False):
        return FakeSeries()
    monkeypatch.setattr(eng, "_compute_series", bearish)
    assert await eng._btc_alignment("LONG", "ETHUSDT") is False
    assert await eng._btc_alignment("SHORT", "ETHUSDT") is True


async def test_scan_stage_logs_gate_rejections_on_real_series(db, store):
    """The scanner path must *explain* every filtered flip — no silent no's."""
    eng = await make_engine(store, universe=12)
    last_bar = eng.hub.candles("ETHUSDT")[-1].t
    sig = _sig(bar_time=last_bar, entry=100.0, atr_pct=0.55, vol_surge=0.1)
    sig.update({"direction": "LONG", "stop": 99.0, "target": 102.0, "atr": 0.55,
                "rail": 99.5, "flow_bias": 0.2, "volume": 500.0, "htf_bull": True,
                "tier": "normal", "strong_flip": False})
    series = await eng._compute_series("ETHUSDT")
    opp = await eng._to_opportunity("ETHUSDT", sig, series, "scanner-1")
    assert opp is None, "a zero-volume flip must be refused by the scanner"
    logged = []
    while not eng._log_queue.empty():
        logged.append(eng._log_queue.get_nowait()["message"])
    assert any("flip skipped" in m and "volume confirm" in m for m in logged), logged

    # with the gate fed a healthy signal, the same path produces an Opportunity
    sig["vol_surge"] = 1.4
    store.cfg.risk.require_btc_alignment = False     # deterministic: no leader opinion
    store.save()
    opp = await eng._to_opportunity("ETHUSDT", sig, series, "scanner-1")
    assert opp is not None and opp.features["vol_surge"] == 1.4


async def test_blacklist_refresh_skips_repeat_offenders(db, store):
    eng = await make_engine(store, universe=12)
    # seed the journal with 10 losing trades on one symbol
    for _ in range(10):
        await _set_price(eng, "DOGEUSDT", 100.0)
        trade = await eng._open_trade("execution-1",
                                      make_opportunity("DOGEUSDT", "LONG", 100.0, 2.0),
                                      {"equity": 10_000.0, "available": 10_000.0,
                                       "open_count": 0})
        if not trade:
            continue
        await _set_price(eng, "DOGEUSDT", 96.0)
        await eng._close_trade(int(trade["id"]), "sl")
        eng.cooldowns.clear()
    await eng._refresh_blacklist()
    assert "DOGEUSDT" in eng._blacklist, "a proven leak gets quarantined"

    # and flips on it are refused while the quarantine holds
    sig = _sig()
    sig["bar_time"] = eng.hub.candles("DOGEUSDT")[-1].t
    eng.cooldowns.clear()
    eng._blacklist["DOGEUSDT"] = int(time.time() * 1000) + 60_000
    opp = await eng._to_opportunity("DOGEUSDT", sig, None, "scanner-1")
    assert opp is None, "a blacklisted symbol is skipped without an exchange call"
