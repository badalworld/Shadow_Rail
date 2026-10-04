#!/usr/bin/env python3
"""
Robustness grid — the search that actually picks the shipped defaults.

The stage-wise tuner picks spikes: with ~160 trades per cell the standard error
on expectancy is ~0.12 R, so a 0.1 R "improvement" is noise.  This script
instead averages every configuration over **two independent market seeds and two
trend strengths** (four panels, ~700 trades per cell), and reports

    * the mean and the *worst* panel — a setting that wins on average but
      collapses on one panel is not shippable,
    * the random-walk control, where nothing is allowed to look good,
    * the out-of-sample half of every panel.

The shipped default is the configuration with the best worst-panel expectancy,
not the best average.
"""
from __future__ import annotations

import copy
import itertools
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app import backtest as bt, marketgen                    # noqa: E402
from app.config import EdgeSettings                          # noqa: E402
from app.exchange.sim import DEFAULT_UNIVERSE                # noqa: E402
from app.indicators.ghost import GhostParams                 # noqa: E402

BARS = 12_000
SPLIT = int(BARS * 0.6)
SEEDS = (20261003, 777)
MOMENTUMS = (1.0, 2.0)


def universe(n: int = 40):
    seen, rows = set(), []
    for sym, price, sigma in DEFAULT_UNIVERSE:
        if sym in seen:
            continue
        seen.add(sym)
        rows.append((sym, price, sigma))
        if len(rows) >= n:
            break
    return rows


def main() -> int:
    rows = universe()
    print(f"building {len(MOMENTUMS) * len(SEEDS) + 1} panels "
          f"({len(rows)} symbols × {BARS} bars each) …", flush=True)
    panels = {}
    for mom in MOMENTUMS:
        for seed in SEEDS:
            panel = marketgen.generate_panel(rows, BARS, seed=seed, momentum=mom)
            panels[(mom, seed)] = bt.build_dataset(panel, GhostParams())
    rw = bt.build_dataset(
        marketgen.generate_panel(rows, BARS, seed=20261003, momentum=0.0), GhostParams())
    print("ready\n", flush=True)

    def base_cfg(**over) -> bt.BacktestConfig:
        cfg = bt.BacktestConfig(
            edge_enabled=True, min_confidence=60.0, tp_enabled=False,
            sizing_mode="risk", risk_pct_per_trade=1.0,
            edge=EdgeSettings(entry_filters_enabled=False, adaptive_exits_enabled=False,
                              cost_gate_enabled=False, breakeven_enabled=False,
                              time_stop_enabled=False, confidence_sizing_enabled=False,
                              drawdown_throttle_enabled=False))
        out = copy.copy(cfg)
        out.edge = copy.copy(cfg.edge)
        for k, v in over.items():
            if k.startswith("edge_"):
                setattr(out.edge, k[5:], v)
            else:
                setattr(out, k, v)
        return out

    grid = {
        "sl_atr_mult": [3.0, 4.0, 6.0],
        "trail": [("off", {}),
                  ("1.5R/0.9R", dict(trail_enabled=True, trail_mode="r",
                                     trail_activation_r=1.5, trail_distance_r=0.9)),
                  ("2.5R/1.5R", dict(trail_enabled=True, trail_mode="r",
                                     trail_activation_r=2.5, trail_distance_r=1.5))],
        "breakeven": [("off", {}),
                      ("1.5R", dict(edge_breakeven_enabled=True, edge_breakeven_arm_r=1.5))],
        "gates": [("off", dict(edge_entry_filters_enabled=False)),
                  ("c>=0.30", dict(edge_entry_filters_enabled=True,
                                   edge_min_clean_ratio=0.30,
                                   edge_min_efficiency_ratio=0.0,
                                   edge_max_atr_pct=99.0,
                                   edge_max_rail_distance_pct=99.0,
                                   edge_require_flow_alignment=False,
                                   edge_require_bar_confirmation=False,
                                   edge_min_atr_pct=0.0)),
                  ("ATR<=0.8", dict(edge_entry_filters_enabled=True,
                                    edge_min_clean_ratio=0.0,
                                    edge_min_efficiency_ratio=0.0,
                                    edge_max_atr_pct=0.8,
                                    edge_max_rail_distance_pct=99.0,
                                    edge_require_flow_alignment=False,
                                    edge_require_bar_confirmation=False,
                                    edge_min_atr_pct=0.0)),
                  ("both", dict(edge_entry_filters_enabled=True,
                                edge_min_clean_ratio=0.30,
                                edge_min_efficiency_ratio=0.0,
                                edge_max_atr_pct=0.8,
                                edge_max_rail_distance_pct=99.0,
                                edge_require_flow_alignment=False,
                                edge_require_bar_confirmation=False,
                                edge_min_atr_pct=0.0))],
    }

    results = []
    for sl, (tname, tover), (bname, bover), (gname, gover) in itertools.product(
            grid["sl_atr_mult"], grid["trail"], grid["breakeven"], grid["gates"]):
        cfg = base_cfg(sl_atr_mult=sl, **tover, **bover, **gover)
        label = f"{sl:g}ATR · trail {tname} · BE {bname} · gates {gname}"
        is_rs, oos_rs = [], []
        for data in panels.values():
            is_rs.append(bt.run_dataset(data, bt._with_bounds(cfg, 0, SPLIT)))
            oos_rs.append(bt.run_dataset(data, bt._with_bounds(cfg, SPLIT, BARS)))
        trades = sum(r.count for r in is_rs)
        if trades < 200:
            continue
        exp = [r.expectancy_r for r in is_rs]
        oexp = [r.expectancy_r for r in oos_rs]
        results.append({
            "label": label, "cfg": cfg, "trades": trades,
            "mean": statistics.mean(exp), "worst": min(exp),
            "mean_oos": statistics.mean(oexp), "worst_oos": min(oexp),
            "wr": statistics.mean(r.win_rate for r in is_rs),
            "pf": statistics.mean(min(r.profit_factor, 9.99) for r in is_rs),
            "dd": max(r.max_drawdown_pct for r in is_rs),
            "ret": statistics.mean(r.return_pct for r in is_rs),
        })

    results.sort(key=lambda r: r["worst"], reverse=True)
    print(f"{'configuration':<52}{'trades':>7}{'expR':>8}{'worst':>8}"
          f"{'OOS':>8}{'OOS-':>8}{'win%':>7}{'PF':>6}{'maxDD':>7}{'ret%':>9}")
    print("─" * 120)
    for r in results[:20]:
        print(f"{r['label']:<52}{r['trades']:>7}{r['mean']:>8.3f}{r['worst']:>8.3f}"
              f"{r['mean_oos']:>8.3f}{r['worst_oos']:>8.3f}{r['wr']:>7.1f}"
              f"{r['pf']:>6.2f}{r['dd']:>7.1f}{r['ret']:>9.1f}")
    print("─" * 120)

    # yardsticks on the same panels
    print("\nYARDSTICKS (same four panels, in-sample / out-of-sample)")
    print(f"{'configuration':<52}{'trades':>7}{'expR':>8}{'worst':>8}"
          f"{'OOS':>8}{'OOS-':>8}{'win%':>7}{'PF':>6}{'maxDD':>7}{'ret%':>9}")
    yard = {
        "shipping 1.5/3.0 ATR, 8% margin": bt.baseline_config(min_confidence=60.0),
        "shipping stops, no TP (runner)": bt.baseline_config(min_confidence=60.0,
                                                             tp_enabled=False),
        "best grid configuration": results[0]["cfg"],
    }
    for name, cfg in yard.items():
        is_rs = [bt.run_dataset(d, bt._with_bounds(cfg, 0, SPLIT)) for d in panels.values()]
        oos_rs = [bt.run_dataset(d, bt._with_bounds(cfg, SPLIT, BARS)) for d in panels.values()]
        exp = [r.expectancy_r for r in is_rs]
        print(f"{name:<52}{sum(r.count for r in is_rs):>7}{statistics.mean(exp):>8.3f}"
              f"{min(exp):>8.3f}{statistics.mean(r.expectancy_r for r in oos_rs):>8.3f}"
              f"{min(r.expectancy_r for r in oos_rs):>8.3f}"
              f"{statistics.mean(r.win_rate for r in is_rs):>7.1f}"
              f"{statistics.mean(min(r.profit_factor, 9.99) for r in is_rs):>6.2f}"
              f"{max(r.max_drawdown_pct for r in is_rs):>7.1f}"
              f"{statistics.mean(r.return_pct for r in is_rs):>9.1f}")
        rwr = [bt.run_dataset(rw, bt._with_bounds(cfg, 0, SPLIT)),
               bt.run_dataset(rw, bt._with_bounds(cfg, SPLIT, BARS))]
        print(f"{'   ↳ random-walk control':<52}{sum(r.count for r in rwr):>7}"
              f"{statistics.mean(r.expectancy_r for r in rwr):>8.3f}"
              f"{min(r.expectancy_r for r in rwr):>8.3f}{'':>16}"
              f"{statistics.mean(r.win_rate for r in rwr):>7.1f}"
              f"{statistics.mean(min(r.profit_factor, 9.99) for r in rwr):>6.2f}"
              f"{max(r.max_drawdown_pct for r in rwr):>7.1f}"
              f"{statistics.mean(r.return_pct for r in rwr):>9.1f}")

    best = results[0]
    print(f"\nshipped default → {best['label']}")
    for k in ("sl_atr_mult", "hold_bars", "trail_enabled", "trail_mode",
              "trail_activation_r", "trail_distance_r", "risk_pct_per_trade",
              "sizing_mode", "tp_enabled"):
        print(f"  {k:<22} {getattr(best['cfg'], k)}")
    for k, v in best["cfg"].edge.model_dump().items():
        if v:
            print(f"  edge.{k:<17} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
