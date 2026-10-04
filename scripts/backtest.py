#!/usr/bin/env python3
"""
Shadow Rail — Strategy backtester (offline, deterministic).

Replays the *exact* live pipeline — Ghost Candle with Shadow Rail flips, the
1h EMA-50 HTF gate, the 7-factor confidence model, the 1.5/3.0 ATR risk map,
the ROI trail, taker fees — over the simulator's deterministic price model,
and compares:

  v1 — the current engine defaults (the baseline it ships with)
  v2 — v1 + the Strategy v2 win-rate layers
       (ADX trend gate, volatility-regime gate, momentum gate,
        break-even ratchet, time stop, direction crowding cap,
        symbol veto)

Both runs see the *same* generated market, so the delta is the strategy.

    python scripts/backtest.py                      # 150 symbols × 12 000 bars
    python scripts/backtest.py --symbols 30 --bars 4000 --quick
    python scripts/backtest.py --out data/backtest/report.md

Honesty notes (read these):
  * The market is a synthetic GBM-with-regimes model (the same one the
    offline demo uses) — it has momentum and volatility clustering, but it is
    NOT real Binance history. Real results will differ.
  * Intra-bar stop/target resolution is conservative: when one bar touches
    both the stop and the target, the stop is assumed to fill first.
  * The ROI trail / break-even ratchet are evaluated at bar closes (the live
    engine ticks every 5 s) — a minor, conservative simplification.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.confidence import ConfidenceModel  # noqa: E402
from app.config import StrategySettings  # noqa: E402
from app.exchange.base import TAKER_FEE  # noqa: E402
from app.exchange.sim import SimExchange  # noqa: E402
from app.indicators import ghost  # noqa: E402
from app.risk import roi_price_step, trail_stop_price  # noqa: E402
from app.strategy import (breakeven_stop, direction_crowded, enrich,  # noqa: E402
                          r_progress, symbol_vetoed, time_stop_due)

TF = "5m"
TF_MS = 300_000
SL_MULT, TP_MULT = 1.5, 3.0
MARGIN_PCT, LEVERAGE = 8.0, 10
MAX_CONCURRENT = 10
MIN_CONFIDENCE = 60.0
TRAIL_ACTIVATION, TRAIL_DISTANCE, TRAIL_STEP, TRAIL_GAP = 25.0, 15.0, 1.0, 0.05


# ─────────────────────────────────────────────────────────────────────────── #
# market generation (identical RNG stream to the live simulator)
# ─────────────────────────────────────────────────────────────────────────── #
def _generate_sync(bars: int, history: int, symbols: int, seed: int) -> dict:
    sim = SimExchange(balance=10_000.0, universe=symbols, history_bars=history,
                      time_accel=1.0, seed=seed)
    sim.history_bars = history + bars + 2000     # disable the rolling trim
    loop = asyncio.new_event_loop()
    try:
        for i in range(bars):
            loop.run_until_complete(sim.step_bar())
            if (i + 1) % 1000 == 0:
                print(f"  … {i + 1}/{bars} bars", flush=True)
    finally:
        loop.close()
    return sim.candles, sim.syms, sim.filters


def generate(bars: int, history: int, symbols: int, seed: int
             ) -> tuple[dict[str, ghost.GhostSeries], dict]:
    t0 = time.time()
    candles_map, syms, filters = _generate_sync(bars, history, symbols, seed)
    print(f"  price generation: {time.time() - t0:.1f}s")

    series_map: dict[str, ghost.GhostSeries] = {}
    t0 = time.time()
    for sym in syms:
        s = ghost.compute(candles_map[sym], ghost.GhostParams(), TF)
        s.strategy = enrich(s, StrategySettings())
        series_map[sym] = s
    print(f"  indicator + strategy series: {time.time() - t0:.1f}s "
          f"({len(series_map)} symbols)")
    return series_map, filters


# ─────────────────────────────────────────────────────────────────────────── #
# one simulated trade
# ─────────────────────────────────────────────────────────────────────────── #
@dataclass
class SimTrade:
    symbol: str
    side: str
    entry: float
    qty: float
    margin: float
    atr: float
    sl0: float            # 1 R in price units (initial stop distance)
    eff_stop: float       # current protective stop (ratchets only)
    tp: float
    opened_bar: int
    peak: float
    entry_fee: float = 0.0
    stop_kind: str = "sl"          # sl | be | trail — what the stop is now
    be_done: bool = False
    trail_armed: bool = False

    @property
    def dir(self) -> int:
        return 1 if self.side == "LONG" else -1


def features_at(s: ghost.GhostSeries, j: int, symbol: str) -> dict:
    sig = ghost.latest_signal(s, j)
    return {
        "trend_quality": sig["trend_quality"], "tier": sig["tier"],
        "htf_bull": bool(sig.get("htf_bull")), "atr_pct": sig["atr_pct"],
        "rail_distance_pct": sig.get("rail_distance_pct", 0.0),
        "flow_bias": float(sig.get("flow_bias", 0.0)),
        "symbol": symbol, "direction": sig["direction"],
    }


# ─────────────────────────────────────────────────────────────────────────── #
# the replay
# ─────────────────────────────────────────────────────────────────────────── #
@dataclass
class RunResult:
    label: str
    equity_end: float = 10_000.0
    trades: int = 0
    wins: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    fees: float = 0.0
    r_sum: float = 0.0
    max_dd_pct: float = 0.0
    durations: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)
    entries_blocked: dict = field(default_factory=dict)


@dataclass
class Params:
    """One strategy configuration to replay."""
    label: str = ""
    v2: bool = True
    sl_mult: float = SL_MULT
    tp_mult: float = TP_MULT
    min_confidence: float = MIN_CONFIDENCE
    strategy: StrategySettings = field(default_factory=StrategySettings)


def build_prep(series_map: dict[str, ghost.GhostSeries]) -> dict:
    syms = sorted(series_map.keys())
    n = len(series_map[syms[0]].times)
    live0 = max(series_map[syms[0]].warmup_bars, 300)
    flips: dict[int, list[tuple[str, str]]] = {}
    for sym in syms:
        s = series_map[sym]
        for j in range(live0, n):
            d = s.flip_at(j)
            if d:
                flips.setdefault(j, []).append((sym, d))
    return {"syms": syms, "n": n, "live0": live0, "flips": flips}


def replay(series_map: dict[str, ghost.GhostSeries], filters: dict,
           prep: dict, params: Params, start_equity: float = 10_000.0) -> RunResult:
    p = params
    cfg = p.strategy
    res = RunResult(label=p.label or
                    ("v2 (strategy layers on)" if p.v2 else "v1 (baseline defaults)"))
    model = ConfidenceModel(model_path=str(REPO / "data" / "backtest_model.json"))
    model.enabled = True

    n, live0 = prep["n"], prep["live0"]
    flips = prep["flips"]
    active = p.v2 and cfg.v2_enabled
    open_trades: dict[str, SimTrade] = {}
    cooldown_until: dict[str, int] = {}
    sym_stats: dict[str, dict] = {}
    equity = start_equity
    peak_equity = start_equity
    max_dd = 0.0
    cash = start_equity

    def close_trade(t: SimTrade, j: int, price: float, reason: str) -> None:
        nonlocal cash
        gross = (price - t.entry) * t.qty * t.dir
        fee = t.entry_fee + price * t.qty * TAKER_FEE
        net = gross - fee
        cash += gross - price * t.qty * TAKER_FEE
        res.trades += 1
        res.fees += fee
        res.reasons[reason] = res.reasons.get(reason, 0) + 1
        res.durations.append(j - t.opened_bar)
        st = sym_stats.setdefault(t.symbol, {"trades": 0, "wins": 0})
        st["trades"] += 1
        if net > 0:
            st["wins"] += 1
            res.wins += 1
            res.gross_win += net
        else:
            res.gross_loss += -net
        if t.sl0 * t.qty > 0:
            res.r_sum += net / (t.sl0 * t.qty)
        cooldown_until[t.symbol] = j          # no same-bar re-entry (300 s rule)
        open_trades.pop(t.symbol, None)
        sym_stats[t.symbol]["win_rate"] = st["wins"] / st["trades"] * 100.0

    for j in range(live0, n):
        # ── 1. intra-bar protective exits (ratchet as of previous close) ──
        for sym in list(open_trades.keys()):
            t = open_trades[sym]
            s = series_map[sym]
            o, h, l, c = s.open[j], s.high[j], s.low[j], s.close[j]
            if t.side == "LONG":
                if l <= t.eff_stop:
                    fill = min(o, t.eff_stop)
                    close_trade(t, j, fill, t.stop_kind)
                    continue
                if t.tp and h >= t.tp:
                    close_trade(t, j, t.tp, "tp")
                    continue
            else:
                if h >= t.eff_stop:
                    fill = max(o, t.eff_stop)
                    close_trade(t, j, fill, t.stop_kind)
                    continue
                if t.tp and l <= t.tp:
                    close_trade(t, j, t.tp, "tp")
                    continue
            # update the peak with this bar's extreme
            t.peak = max(t.peak, h) if t.side == "LONG" else min(t.peak, l)

        # ── 2. reverse-signal exits at the bar close ─────────────────────
        for sym, d in flips.get(j, ()):
            t = open_trades.get(sym)
            if t and t.side != d:
                close_trade(t, j, float(series_map[sym].close[j]), "reverse_signal")

        # ── 3. time stop at the bar close (v2) ───────────────────────────
        if active:
            for sym in list(open_trades.keys()):
                t = open_trades[sym]
                c = float(series_map[sym].close[j])
                if time_stop_due(bars_elapsed=(j - t.opened_bar),
                                 r_now=r_progress(t.side, t.entry, c, t.sl0),
                                 cfg=cfg):
                    close_trade(t, j, c, "time_stop")

        # ── 4. ratchets at the bar close (break-even, then ROI trail) ────
        for sym, t in open_trades.items():
            s = series_map[sym]
            c = float(s.close[j])
            if active:
                cand = breakeven_stop(side=t.side, entry=t.entry, mark=c,
                                      atr=t.atr, sl_distance=t.sl0,
                                      current_stop=t.eff_stop, cfg=cfg)
                if cand is not None and not t.be_done:
                    t.eff_stop = cand
                    t.stop_kind = "be"
                    t.be_done = True
            step = roi_price_step(t.margin, t.qty)
            new_stop, armed = trail_stop_price(
                entry=t.entry, side=t.side, peak_price=t.peak, mark=c,
                qty=t.qty, margin=t.margin, prev_stop=t.eff_stop,
                activation_roi=TRAIL_ACTIVATION, distance_roi=TRAIL_DISTANCE,
                mark_gap_pct=TRAIL_GAP)
            if armed and (not t.trail_armed or
                          (t.side == "LONG" and new_stop > t.eff_stop + 1e-12) or
                          (t.side == "SHORT" and new_stop < t.eff_stop - 1e-12)):
                if armed and t.trail_armed:
                    gain = abs(new_stop - t.eff_stop) / step if step else 0.0
                    if gain < TRAIL_STEP:
                        continue
                t.eff_stop = new_stop
                t.trail_armed = True
                t.stop_kind = "trail"

        # ── 5. entries at the bar close (flip + gates + capacity) ────────
        for sym, d in flips.get(j, ()):
            if sym in open_trades:
                continue
            if cooldown_until.get(sym, -1) >= j:
                res.entries_blocked["cooldown"] = \
                    res.entries_blocked.get("cooldown", 0) + 1
                continue
            if len(open_trades) >= MAX_CONCURRENT:
                res.entries_blocked["slots"] = \
                    res.entries_blocked.get("slots", 0) + 1
                continue
            if active and direction_crowded(
                    d, [t.side for t in open_trades.values()], cfg):
                res.entries_blocked["crowding"] = \
                    res.entries_blocked.get("crowding", 0) + 1
                continue
            s = series_map[sym]
            if active:
                ok, _reasons = s.strategy.gate(j, d, cfg)
                if not ok:
                    res.entries_blocked["gate"] = \
                        res.entries_blocked.get("gate", 0) + 1
                    continue
                veto = symbol_vetoed(sym_stats.get(sym), cfg)
                if veto:
                    res.entries_blocked["veto"] = \
                        res.entries_blocked.get("veto", 0) + 1
                    continue
            feats = features_at(s, j, sym)
            score = model.score(feats, sym_stats.get(sym)).score
            if score < p.min_confidence:
                res.entries_blocked["confidence"] = \
                    res.entries_blocked.get("confidence", 0) + 1
                continue
            price = float(s.close[j])
            atr = float(s.atr14[j])
            if not (atr > 0) or not (price > 0):
                continue
            margin = equity * MARGIN_PCT / 100.0
            qty = margin * LEVERAGE / price
            flt = filters.get(sym)
            if flt:
                qty = math.floor(qty / flt.step_size) * flt.step_size
                if qty < flt.min_qty:
                    res.entries_blocked["min_qty"] = \
                        res.entries_blocked.get("min_qty", 0) + 1
                    continue
            sl_dist = p.sl_mult * atr
            side = "LONG" if d == "LONG" else "SHORT"
            entry_fee = price * qty * TAKER_FEE
            t = SimTrade(
                symbol=sym, side=side, entry=price, qty=qty, margin=margin,
                atr=atr, sl0=sl_dist,
                eff_stop=price - sl_dist if side == "LONG" else price + sl_dist,
                tp=price + p.tp_mult * atr if side == "LONG" else price - p.tp_mult * atr,
                opened_bar=j, peak=price, entry_fee=entry_fee)
            open_trades[sym] = t
            cash -= entry_fee

        # ── 6. mark-to-market equity (max drawdown) ──────────────────────
        upnl = sum((float(series_map[t.symbol].close[j]) - t.entry) * t.qty * t.dir
                   for t in open_trades.values())
        equity = cash + upnl
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_dd = max(max_dd, (peak_equity - equity) / peak_equity * 100.0)

    # force-close leftovers at the last close (marked, not filled — labelled)
    for sym, t in list(open_trades.items()):
        c = float(series_map[sym].close[-1])
        close_trade(t, n - 1, c, "eod")
    equity = cash
    res.equity_end = equity
    res.max_dd_pct = max_dd
    return res


# ─────────────────────────────────────────────────────────────────────────── #
# reporting
# ─────────────────────────────────────────────────────────────────────────── #
def _pf(r: RunResult) -> float:
    return (r.gross_win / r.gross_loss) if r.gross_loss > 0 else float("inf")


def _wr(r: RunResult) -> float:
    return (r.wins / r.trades * 100.0) if r.trades else 0.0


def _exp_r(r: RunResult) -> float:
    return (r.r_sum / r.trades) if r.trades else 0.0


def report(v1: RunResult, v2: RunResult, args, gen_secs: float) -> str:
    lines = []
    lines.append("# Shadow Rail — Strategy v1 vs v2 backtest\n")
    lines.append("*Deterministic replay of the live pipeline on the simulator's "
                 "price model — **synthetic data**, not real market history.*\n")
    days = args.bars * 5 // 60 // 24
    lines.append(f"seed {args.seed} · {args.symbols} symbols · "
                 f"{args.bars:,} live bars ({days}d of 5m) "
                 f"+ {args.history:,} warm-up bars · generated in {gen_secs:.0f}s\n")
    lines.append("| metric | v1 baseline | v2 strategy layers | Δ |")
    lines.append("|---|---:|---:|---:|")
    rows = [
        ("trades", v1.trades, v2.trades, f"{v2.trades - v1.trades:+d}"),
        ("win rate", f"{_wr(v1):.1f}%", f"{_wr(v2):.1f}%",
         f"{_wr(v2) - _wr(v1):+.1f} pp"),
        ("profit factor", f"{_pf(v1):.3f}", f"{_pf(v2):.3f}",
         f"{_pf(v2) - _pf(v1):+.3f}"),
        ("expectancy (R/trade)", f"{_exp_r(v1):+.3f}", f"{_exp_r(v2):+.3f}",
         f"{_exp_r(v2) - _exp_r(v1):+.3f}"),
        ("fees paid", f"${v1.fees:,.0f}", f"${v2.fees:,.0f}",
         f"${v2.fees - v1.fees:+,.0f}"),
        ("max drawdown", f"{v1.max_dd_pct:.1f}%", f"{v2.max_dd_pct:.1f}%",
         f"{v2.max_dd_pct - v1.max_dd_pct:+.1f} pp"),
        ("avg hold (bars)", f"{(sum(v1.durations) / len(v1.durations)):.1f}"
         if v1.durations else "—",
         f"{(sum(v2.durations) / len(v2.durations)):.1f}" if v2.durations else "—", ""),
        ("final equity", f"${v1.equity_end:,.2f}", f"${v2.equity_end:,.2f}",
         f"${v2.equity_end - v1.equity_end:+,.2f}"),
    ]
    for name, a, b, d in rows:
        lines.append(f"| {name} | {a} | {b} | {d} |")
    lines.append("")
    lines.append("**v1 exit reasons:** " + _hist(v1.reasons) + "\n")
    lines.append("**v2 exit reasons:** " + _hist(v2.reasons) + "\n")
    lines.append("**v2 entry filters:** " + _hist(v2.entries_blocked) + "\n")
    lines.append("\n> The synthetic model has momentum regimes, so trend-gated systems")
    lines.append("> do better here than on a pure random walk. Treat this as an")
    lines.append("> implementation check (v2 vs v1 on identical data), not a promise:")
    lines.append("> validate on Binance testnet / paper mode before any real size.")
    return "\n".join(lines) + "\n"


def _hist(d: dict) -> str:
    if not d:
        return "—"
    return " · ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def _params_from_args(args, label: str, v2: bool = True) -> Params:
    strat = StrategySettings()
    if v2:
        strat.adx_min = args.adx_min
        strat.vol_expansion_min = args.vol_min
        strat.breakeven_offset_atr = args.be_offset
        strat.max_same_direction = args.max_same_dir
    return Params(label=label, v2=v2, sl_mult=args.sl_mult, tp_mult=args.tp_mult,
                  min_confidence=args.min_conf, strategy=strat)


def main() -> None:
    ap = argparse.ArgumentParser(description="Shadow Rail strategy backtester")
    ap.add_argument("--bars", type=int, default=12_000,
                    help="live-region bars to replay (5m each)")
    ap.add_argument("--history", type=int, default=1_200,
                    help="warm-up bars before the live region")
    ap.add_argument("--symbols", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--out", type=str, default=str(REPO / "data" / "backtest" / "report.md"))
    ap.add_argument("--quick", action="store_true",
                    help="shorthand for --symbols 30 --bars 4000")
    ap.add_argument("--sweep", action="store_true",
                    help="run the v1/v2 comparison plus a v2 settings grid")
    ap.add_argument("--min-conf", type=float, default=60.0)
    ap.add_argument("--adx-min", type=float, default=25.0)
    ap.add_argument("--vol-min", type=float, default=1.1)
    ap.add_argument("--sl-mult", type=float, default=1.5)
    ap.add_argument("--tp-mult", type=float, default=3.0)
    ap.add_argument("--be-offset", type=float, default=0.3)
    ap.add_argument("--max-same-dir", type=int, default=5)
    args = ap.parse_args()
    if args.quick:
        args.symbols, args.bars = min(args.symbols, 30), min(args.bars, 4000)

    print(f"Generating market: {args.symbols} symbols × {args.bars:,} bars "
          f"(seed {args.seed}) …")
    t0 = time.time()
    series_map, filters = generate(args.bars, args.history, args.symbols, args.seed)
    gen_secs = time.time() - t0
    prep = build_prep(series_map)

    def _show(r: RunResult):
        return (f"{r.label}: trades={r.trades} win={_wr(r):.1f}% "
                f"PF={_pf(r):.3f} expR={_exp_r(r):+.3f} "
                f"DD={r.max_dd_pct:.1f}% net=${r.equity_end - 10_000:+,.0f}")

    print("Replaying v1 (baseline) …")
    v1 = replay(series_map, filters, prep, Params(label="v1 (baseline defaults)", v2=False))
    print("  " + _show(v1))

    print("Replaying v2 (strategy layers) …")
    v2 = replay(series_map, filters, prep, _params_from_args(args, "v2 (strategy layers on)"))
    print("  " + _show(v2))

    text = report(v1, v2, args, gen_secs)

    if args.sweep:
        grid = [
            Params(label="v2 default"),
            Params(label="v2 conf 70", min_confidence=70.0),
            Params(label="v2 conf 75", min_confidence=75.0),
            Params(label="v2 adx 30", min_confidence=args.min_conf,
                   strategy=StrategySettings(adx_min=30.0)),
            Params(label="v2 vol 1.15", min_confidence=args.min_conf,
                   strategy=StrategySettings(vol_expansion_min=1.15)),
            Params(label="v2 tp 2.0", tp_mult=2.0),
            Params(label="v2 strict (conf70·adx25·vol1.1·tp2.5)",
                   min_confidence=70.0, tp_mult=2.5,
                   strategy=StrategySettings(adx_min=25.0, vol_expansion_min=1.1)),
        ]
        print("\nSweeping v2 settings (win-rate vs selectivity) …")
        rows = []
        for g in grid:
            r = replay(series_map, filters, prep, g)
            rows.append(r)
            print("  " + _show(r))
        text += "\n## v2 settings sweep\n\n| config | trades | win rate | "
        text += "PF | exp R/trade | max DD | net $ |\n|---|---:|---:|---:|---:|---:|---:|\n"
        for r in rows:
            text += (f"| {r.label} | {r.trades} | {_wr(r):.1f}% | {_pf(r):.3f} | "
                     f"{_exp_r(r):+.3f} | {r.max_dd_pct:.1f}% | "
                     f"${r.equity_end - 10_000:+,.0f} |\n")
        text += ("\n\n> More selective settings raise the win rate but trade fewer "
                 "times and can lower total expectancy. Pick the row that matches "
                 "your risk appetite, then validate on testnet.\n")

    print("\n" + text)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"report → {out}")


if __name__ == "__main__":
    main()
