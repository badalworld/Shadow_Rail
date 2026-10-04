#!/usr/bin/env python3
"""
Research driver used to *choose* the shipped defaults.

Runs a matrix of policies over three markets:

    momentum 0.0 — pure random walk            (control: nothing should win)
    momentum 1.0 — the market the sim ships    (what the dashboard trades)
    momentum 2.0 — strongly trending           (does the policy scale with edge?)

A policy only earns a default if it beats the shipping configuration on the
trending markets **and** does not lose more than the shipping configuration on
the random-walk control.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app import backtest as bt, marketgen                    # noqa: E402
from app.config import EdgeSettings                          # noqa: E402
from app.exchange.sim import DEFAULT_UNIVERSE                # noqa: E402
from app.indicators.ghost import GhostParams                 # noqa: E402

BARS = 12_000
SYMBOLS = 40


def universe(n: int = SYMBOLS):
    seen, rows = set(), []
    for sym, price, sigma in DEFAULT_UNIVERSE:
        if sym in seen:
            continue
        seen.add(sym)
        rows.append((sym, price, sigma))
        if len(rows) >= n:
            break
    return rows


def datasets(bars: int = BARS):
    out = {}
    for mom in (0.0, 1.0, 2.0):
        panel = marketgen.generate_panel(universe(), bars, seed=20261003, momentum=mom)
        out[mom] = bt.build_dataset(panel, GhostParams())
        print(f"market momentum={mom}: {sum(len(d.signals) for d in out[mom])} flips")
    return out


def show(name, res):
    pf = res.profit_factor
    pf = "∞" if pf == float("inf") else f"{pf:5.2f}"
    return (f"{res.count:>6} {res.win_rate:>6.1f}% {pf:>6} {res.net_pnl:>11.0f} "
            f"{res.return_pct:>9.1f}% {res.expectancy_r:>8.3f}R {res.max_drawdown_pct:>7.1f}%")


HEAD = ("policy".ljust(34) + f"{'trades':>6} {'win':>7} {'PF':>6} {'net':>11} "
        f"{'return':>10} {'expect':>9} {'maxDD':>8}")


def policies():
    """(name, BacktestConfig) — the candidate policies, cheapest to describe."""
    out = []

    def cfg(**kw):
        return bt.BacktestConfig(**kw)

    # ── the shipping configuration ────────────────────────────────────────
    out.append(("A shipping: 1.5/3.0 ATR, 8% margin",
                cfg(edge_enabled=False, min_confidence=60.0)))
    # ── exit-structure variants, everything else identical ────────────────
    out.append(("B shipping stops, no TP (runner)",
                cfg(edge_enabled=False, min_confidence=60.0, tp_enabled=False)))
    out.append(("C 4 ATR stop, no TP",
                cfg(edge_enabled=False, min_confidence=60.0, sl_atr_mult=4.0,
                    tp_enabled=False)))
    out.append(("D 4 ATR stop, no TP, 36-bar cap",
                cfg(edge_enabled=False, min_confidence=60.0, sl_atr_mult=4.0,
                    tp_enabled=False, hold_bars=36)))
    out.append(("E 4 ATR stop, no TP, 36-bar cap, 1% risk sizing",
                cfg(edge_enabled=False, min_confidence=60.0, sl_atr_mult=4.0,
                    tp_enabled=False, hold_bars=36, sizing_mode="risk",
                    risk_pct_per_trade=1.0)))
    out.append(("F E + break-even lock + 18/10 trail",
                cfg(edge_enabled=True, min_confidence=60.0, sl_atr_mult=4.0,
                    tp_enabled=False, hold_bars=36, sizing_mode="risk",
                    risk_pct_per_trade=1.0,
                    edge=EdgeSettings(adaptive_exits_enabled=False,
                                      entry_filters_enabled=False,
                                      time_stop_enabled=False,
                                      confidence_sizing_enabled=False,
                                      drawdown_throttle_enabled=False),
                    trail_activation_roi=18.0, trail_distance_roi=10.0)))
    out.append(("G F + adaptive vol-normalised stop",
                cfg(edge_enabled=True, min_confidence=60.0, tp_enabled=False,
                    hold_bars=36, sizing_mode="risk", risk_pct_per_trade=1.0,
                    edge=EdgeSettings(adaptive_exits_enabled=True,
                                      entry_filters_enabled=False,
                                      time_stop_enabled=False,
                                      confidence_sizing_enabled=False,
                                      drawdown_throttle_enabled=False),
                    trail_activation_roi=18.0, trail_distance_roi=10.0)))
    out.append(("H G + entry gates (quality/vol/cost)",
                cfg(edge_enabled=True, min_confidence=60.0, tp_enabled=False,
                    hold_bars=36, sizing_mode="risk", risk_pct_per_trade=1.0,
                    edge=EdgeSettings(adaptive_exits_enabled=True,
                                      entry_filters_enabled=True,
                                      time_stop_enabled=False,
                                      confidence_sizing_enabled=False,
                                      drawdown_throttle_enabled=False),
                    trail_activation_roi=18.0, trail_distance_roi=10.0)))
    out.append(("I H + time stop + sizing + throttle (full)",
                cfg(edge_enabled=True, min_confidence=60.0, tp_enabled=False,
                    hold_bars=36, sizing_mode="risk", risk_pct_per_trade=1.0,
                    edge=EdgeSettings(trail_activation_roi_pct=18.0,
                                      trail_distance_roi_pct=10.0),
                    trail_activation_roi=18.0, trail_distance_roi=10.0)))
    return out


def main() -> int:
    ds = datasets()
    split = int(BARS * 0.6)
    for mom, data in ds.items():
        label = {0.0: "RANDOM WALK (control)", 1.0: "SHIPPED SIM MARKET",
                 2.0: "STRONGLY TRENDING"}[mom]
        print(f"\n{'=' * 100}\n{label} — momentum={mom}   "
              f"(IS bars 0–{split}, OOS {split}–{BARS})\n{'=' * 100}")
        print(HEAD)
        print("─" * len(HEAD))
        for name, cfg in policies():
            is_res = bt.run_dataset(data, bt._with_bounds(cfg, 0, split), name)
            oos_res = bt.run_dataset(data, bt._with_bounds(cfg, split, BARS), name)
            print(f"{name:<34}" + show("", is_res) + "   │IS")
            print(f"{'':<34}" + show("", oos_res) + "   │OOS")
        print("─" * len(HEAD))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
