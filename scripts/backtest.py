#!/usr/bin/env python3
"""
Shadow Rail — strategy backtester / win-rate-pack A-B bench.

Replays bar-by-bar with the *exact* code paths the live engine uses:
the GCSR port (``app.indicators.ghost``), the confluence gates
(``app.strategy``), the analyst scorer (``app.confidence.MODEL``), and the
risk maths (``app.risk``: ATR stops, the ROI break-even lock, the ROI trail,
the stall exit, confidence sizing).  Nothing here re-implements the strategy —
that is the point: what this measures is what the swarm will trade.

Data sources
  --source sim      deterministic synthetic momentum/volatility-regime market
                    from the engine's own simulator (works fully offline)
  --source binance  real Binance USDT-M klines via the public REST endpoint
                    (no keys needed; run this to validate on real history)
  --source file     JSON: {"BTCUSDT": [[openTime,o,h,l,c,v], …], …}

Variants
  baseline   the pre-upgrade book: flip → market, SL 1.5×ATR / TP 3.0×ATR,
             reverse-signal exit, ROI trail 25/15, min confidence 60
  winrate    the win-rate pack: confluence gates + min confidence 65 +
             break-even lock + stall exit + A+ conviction sizing
  both       (default) — prints baseline, winrate and the delta

Modeling notes (honesty first):
  * one position per symbol, entries at the signal bar's close, same as live
  * protective orders are evaluated like the venue: a bar trading through the
    stop fills at the stop; a gap through it fills at the (worse) open
  * taker fees on both legs; funding, slippage, side-cap and portfolio
    concurrency are NOT modeled — the win-rate pack barely touches them
  * this measures *your* rules on *past* data; it is a filter for bad ideas,
    not a promise about tomorrow

Examples
  python scripts/backtest.py                                   # offline sim
  python scripts/backtest.py --source binance --days 45 --symbols \
      BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from app import strategy as ST                      # noqa: E402
from app.config import RiskSettings                  # noqa: E402
from app.confidence import MODEL                     # noqa: E402
from app.indicators import ghost                     # noqa: E402
from app.risk import breakeven_stop_price, roi_points, trail_stop_price   # noqa: E402
from app.util import Candle                          # noqa: E402

TAKER_FEE = 0.0005
BAR_MIN = 5


# ────────────────────────────────── variants ────────────────────────────── #
def variant(name: str) -> RiskSettings:
    r = RiskSettings()
    if name == "baseline":                       # the pre-upgrade defaults
        r.min_confidence = 60.0
        r.require_volume_confirm = False
        r.require_body_confirm = False
        r.require_htf_slope = False
        r.require_volatility_band = False
        r.enforce_rail_extension = False
        r.require_btc_alignment = False
        r.be_lock_enabled = False
        r.stall_exit_minutes = 0.0
        r.max_same_side_trades = 0
        r.confidence_sizing = False
    return r


# ─────────────────────────────────── data ──────────────────────────────── #
def load_sim(symbols: list[str], bars: int, seed: int) -> dict[str, list[Candle]]:
    from app.exchange.sim import SimExchange
    sim = SimExchange(balance=10_000.0, universe=150, history_bars=bars, seed=seed)
    out: dict[str, list[Candle]] = {}
    wanted = symbols or list(sim.candles.keys())[:8]
    for sym in wanted:
        if sym in sim.candles and len(sim.candles[sym]) >= 400:
            out[sym] = list(sim.candles[sym])
    return out


def load_binance(symbols: list[str], days: int) -> dict[str, list[Candle]]:
    import urllib.request
    bars = max(400, min(days, 90)) * (1440 // BAR_MIN)
    out: dict[str, list[Candle]] = {}
    for sym in symbols:
        rows: list[list[Any]] = []
        end = None
        while len(rows) < bars:
            url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}"
                   f"&interval={BAR_MIN}m&limit=1500")
            if end:
                url += f"&endTime={end}"
            try:
                with urllib.request.urlopen(url, timeout=30) as fh:
                    chunk = json.loads(fh.read())
            except Exception as exc:                       # noqa: BLE001
                print(f"  !! {sym}: fetch failed ({exc}) — run this where Binance "
                      f"is reachable, or use --source sim", file=sys.stderr)
                return {}
            if not chunk:
                break
            rows = chunk + rows
            end = chunk[0][0] - 1
            if len(chunk) < 1500:
                break
        rows = rows[-bars:]
        out[sym] = [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                           float(r[4]), float(r[5])) for r in rows]
    return out


def load_file(path: str) -> dict[str, list[Candle]]:
    with open(path) as fh:
        data = json.load(fh)
    out: dict[str, list[Candle]] = {}
    for sym, rows in data.items():
        out[sym] = [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                           float(r[4]), float(r[5])) for r in rows]
    return out


# ───────────────────────────────── simulation ──────────────────────────── #
@dataclass
class Trade:
    symbol: str
    side: str
    entry_i: int
    entry: float
    qty: float
    margin: float
    sl: float
    tp: float
    peak: float
    bars: int = 0
    exit_i: int = 0
    exit: float = 0.0
    reason: str = ""
    net: float = 0.0


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    equity_path: list[float] = field(default_factory=list)


def features_from(sig: dict, series, i: int, symbol: str) -> dict:
    def clean(v):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v
    return {
        "trend_quality": sig["trend_quality"], "tier": sig["tier"],
        "htf_bull": bool(sig.get("htf_bull")), "atr_pct": sig["atr_pct"],
        "rail_distance_pct": sig.get("rail_distance_pct", 0.0),
        "flow_bias": float(sig.get("flow_bias", 0.0)),
        "symbol": symbol, "direction": sig["direction"],
        "vol_surge": clean(sig.get("vol_surge")),
        "body_strength": clean(sig.get("body_strength")),
        "htf_ema_up": sig.get("htf_ema_up"),
        "htf_ready": bool(sig.get("htf_ready", True)),
        "btc_aligned": None,               # no cross-symbol context per-series
    }


def simulate_symbol(symbol: str, candles: list[Candle], r: RiskSettings,
                     equity0: float = 10_000.0) -> Result:
    gparams = ghost.GhostParams(mtfGate=True, slAtrX=r.active_tp_sl()[0],
                                tpAtrX=max(0.1, r.active_tp_sl()[1] or 3.0))
    s = ghost.compute(candles, gparams, base_tf="5m")
    n = len(candles)
    o, h, l, c = s.open, s.high, s.low, s.close
    res = Result()
    equity = equity0
    pos: Trade | None = None
    start = max(s.warmup_bars, 220)

    def close_pos(bar_i: int, price: float, reason: str) -> None:
        nonlocal pos, equity
        assert pos is not None
        gross = (price - pos.entry) * pos.qty * (1 if pos.side == "LONG" else -1)
        fee = (pos.entry + price) * pos.qty * TAKER_FEE
        net = gross - fee
        pos.exit_i, pos.exit, pos.reason, pos.net = bar_i, price, reason, net
        equity += net
        res.trades.append(pos)
        res.equity_path.append(equity)
        pos = None

    i = start
    while i < n:
        if pos is not None:
            pos.bars += 1
            # 1) exchange-side protection during this bar (stop has priority —
            #    conservative, and faithful to STOP_MARKET beating a resting TP)
            if pos.side == "LONG":
                if l[i] <= pos.sl:
                    close_pos(i, min(pos.sl, o[i]) if o[i] < pos.sl else pos.sl, "sl")
                elif pos.tp > 0 and h[i] >= pos.tp:
                    close_pos(i, max(pos.tp, o[i]) if o[i] > pos.tp else pos.tp, "tp")
            else:
                if h[i] >= pos.sl:
                    close_pos(i, max(pos.sl, o[i]) if o[i] > pos.sl else pos.sl, "sl")
                elif pos.tp > 0 and l[i] <= pos.tp:
                    close_pos(i, min(pos.tp, o[i]) if o[i] < pos.tp else pos.tp, "tp")
            if pos is None:
                i += 1
                continue
            # 2) engine-level exits evaluated at the bar close
            if (s.turn_dn[i] and pos.side == "LONG") or (s.turn_up[i] and pos.side == "SHORT"):
                close_pos(i, c[i], "reverse")
                i += 1
                continue
            peak = max(pos.peak, h[i]) if pos.side == "LONG" else min(pos.peak, l[i])
            pos.peak = peak
            if (r.stall_exit_minutes > 0 and pos.bars * BAR_MIN >= r.stall_exit_minutes
                    and roi_points(pos.entry, pos.peak, pos.qty, pos.margin, pos.side)
                    < r.stall_min_roi_pct):
                close_pos(i, c[i], "stalled")
                i += 1
                continue
            # 3) protection updates — the one-stop ladder: BE lock, then the trail
            new_stop = pos.sl
            if r.be_lock_enabled:
                be, armed = breakeven_stop_price(
                    entry=pos.entry, side=pos.side, peak_price=pos.peak, mark=c[i],
                    qty=pos.qty, margin=pos.margin, prev_stop=new_stop,
                    activation_roi=r.be_lock_roi_pct, buffer_roi=r.be_buffer_roi_pct,
                    mark_gap_pct=r.trail_mark_gap_pct)
                if armed:
                    new_stop = be
            if r.trail_roi_enabled:
                tr, armed = trail_stop_price(
                    entry=pos.entry, side=pos.side, peak_price=pos.peak, mark=c[i],
                    qty=pos.qty, margin=pos.margin, prev_stop=new_stop,
                    activation_roi=r.trail_activation_roi_pct,
                    distance_roi=r.trail_distance_roi_pct,
                    mark_gap_pct=r.trail_mark_gap_pct)
                if armed:
                    new_stop = tr
            pos.sl = new_stop
        else:
            if (s.turn_up[i] or s.turn_dn[i]) and i < n - 1:      # need a next bar
                sig = ghost.latest_signal(s, i) or {}
                direction = "LONG" if s.turn_up[i] else "SHORT"
                sig = dict(sig); sig["direction"] = direction
                ok, _ = ST.entry_gate(sig, r, mtf_gate_enabled=True, btc_aligned=None)
                if ok:
                    conf = MODEL.score(features_from(sig, s, i, symbol), None).score
                    if conf >= r.min_confidence:
                        atr = float(s.atr14[i])
                        margin = equity * (r.size_pct_per_trade / 100.0) \
                            * (ST.sizing_multiplier(conf) if r.confidence_sizing else 1.0)
                        notional = margin * float(r.leverage)
                        qty = notional / c[i]
                        sl_mult, tp_mult, tp_on = r.active_tp_sl()
                        sl = c[i] - sl_mult * atr if direction == "LONG" else c[i] + sl_mult * atr
                        tp = (c[i] + tp_mult * atr if direction == "LONG"
                              else c[i] - tp_mult * atr) if tp_on else 0.0
                        pos = Trade(symbol=symbol, side=direction, entry_i=i, entry=c[i],
                                    qty=qty, margin=margin, sl=sl, tp=tp, peak=c[i])
            # a flip without next-bar management room is dropped (end of data)
        i += 1
    if pos is not None:
        close_pos(n - 1, c[n - 1], "eod")
    return res


# ────────────────────────────────── metrics ────────────────────────────── #
def metrics(results: dict[str, Result]) -> dict[str, Any]:
    trades = [t for rs in results.values() for t in rs.trades]
    if not trades:
        return {"trades": 0}
    wins = [t for t in trades if t.net > 0]
    losses = [t for t in trades if t.net <= 0]
    gross_w = sum(t.net for t in wins)
    gross_l = -sum(t.net for t in losses)
    eq = 10_000.0
    per_symbol_curve = []
    for sym, rs in results.items():
        peak, dd = eq, 0.0
        for v in rs.equity_path:
            peak = max(peak, v)
            dd = max(dd, (peak - v) / peak * 100.0)
        per_symbol_curve.append(dd)
    nets = [t.net for rs in results.values() for t in rs.trades]
    total = sum(nets)
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    # expectancy in R (risk = the initial stop distance in USD)
    rs_list = []
    for t in trades:
        rs_list.append(t.net / (abs(t.sl - t.entry) * t.qty) if t.sl != t.entry else 0.0)
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        "profit_factor": pf,
        "avg_win": (gross_w / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_l / len(losses)) if losses else 0.0,
        "expectancy_r": sum(rs_list) / len(rs_list) if rs_list else 0.0,
        "net_pnl": total,
        "return_pct": total / 10_000.0 * 100.0,
        "max_dd_pct": max(per_symbol_curve) if per_symbol_curve else 0.0,
        "avg_hold_bars": sum(t.bars for t in trades) / len(trades),
        "fees": sum((t.entry + t.exit) * t.qty * TAKER_FEE for t in trades),
        "by_reason": {},
    }


def fmt(m: dict) -> str:
    if not m.get("trades"):
        return "  (no trades)"
    return (f"  trades {m['trades']:>4}   win rate {m['win_rate']:5.1f}%   "
            f"PF {m['profit_factor']:5.2f}   expectancy {m['expectancy_r']:+.3f} R\n"
            f"  net {m['net_pnl']:+10,.2f}  ({m['return_pct']:+6.2f}% on 10k)   "
            f"maxDD {m['max_dd_pct']:5.2f}%   fees {m['fees']:,.2f}\n"
            f"  avg win {m['avg_win']:+8,.2f}   avg loss {m['avg_loss']:+8,.2f}   "
            f"hold {m['avg_hold_bars']:.0f} bars")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["sim", "binance", "file"], default="sim")
    ap.add_argument("--file", default="", help="JSON candles file (source=file)")
    ap.add_argument("--symbols", default="", help="comma separated; sim/binance default = 8 top coins")
    ap.add_argument("--days", type=int, default=30, help="history for source=binance")
    ap.add_argument("--bars", type=int, default=4000, help="bars per symbol for source=sim")
    ap.add_argument("--seed", type=int, default=20261003, help="sim world seed (repeatable runs)")
    ap.add_argument("--variant", choices=["baseline", "winrate", "both"], default="both")
    ap.add_argument("--json", default="", help="write raw trades to this JSON file")
    args = ap.parse_args()

    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    if args.source == "sim":
        data = load_sim(symbols, args.bars, args.seed)
    elif args.source == "binance":
        symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
                              "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT"]
        data = load_binance(symbols, args.days)
    else:
        data = load_file(args.file)
    if not data:
        print("no candle data loaded — check symbols/source", file=sys.stderr)
        return 2

    print(f"\nShadow Rail backtest — {len(data)} symbols × "
          f"{max(len(v) for v in data.values())} bars — source={args.source}"
          + (f" seed={args.seed}" if args.source == "sim" else "") + "\n")
    variants = ([("baseline", variant("baseline")), ("winrate", variant("winrate"))]
                if args.variant == "both" else
                [(args.variant, variant(args.variant))])
    dumps: dict[str, Any] = {}
    outs: dict[str, dict] = {}
    for name, r in variants:
        results = {sym: simulate_symbol(sym, cl, r) for sym, cl in data.items()}
        m = metrics(results)
        outs[name] = m
        print(f"[{name}]")
        print(fmt(m))
        counts: dict[str, int] = {}
        for rs in results.values():
            for t in rs.trades:
                counts[t.reason] = counts.get(t.reason, 0) + 1
        print("  exits: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        print()
        dumps[name] = {"metrics": m,
                       "exits": counts,
                       "trades": [{"symbol": t.symbol, "side": t.side, "net": round(t.net, 2),
                                   "reason": t.reason, "bars": t.bars,
                                   "entry": t.entry, "exit": t.exit}
                                  for rs in results.values() for t in rs.trades]}
    if args.variant == "both" and outs["baseline"].get("trades") and outs["winrate"].get("trades"):
        b, w = outs["baseline"], outs["winrate"]
        print("[delta  winrate − baseline]")
        print(f"  win rate      {w['win_rate'] - b['win_rate']:+6.2f} pts   "
              f"(baseline {b['win_rate']:.1f}% → winrate {w['win_rate']:.1f}%)")
        print(f"  profit factor {w['profit_factor'] - b['profit_factor']:+6.2f}   "
              f"expectancy  {w['expectancy_r'] - b['expectancy_r']:+.3f} R")
        print(f"  net P&L       {w['net_pnl'] - b['net_pnl']:+10,.2f}   "
              f"max DD      {w['max_dd_pct'] - b['max_dd_pct']:+6.2f} pts")
        print(f"  trades        {w['trades'] - b['trades']:+6}   "
              f"(fewer, better: gates subtract — that is the trade for win rate)\n")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(dumps, fh, indent=1)
        print(f"wrote {args.json}")
    print("⚠ backtests measure the past; run --source binance with real history and\n"
          "  paper-trade before sizing up. No setting here guarantees profit.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
