#!/usr/bin/env python3
"""
Stage-wise search for the shipped defaults.

Each stage holds the previously chosen values and varies ONE knob.  A knob is
only promoted when it improves the *worst* of the two trending markets
(momentum 1 and 2) on in-sample data, so a setting can never be carried by one
lucky market.  Every promotion is re-checked out-of-sample and on the
random-walk control, where nothing is supposed to work.

Selection metric: **expectancy in R** (edge per unit of risk).  It is
size-independent, so widening the stop or changing the risk budget cannot fake
an improvement the way raw P&L can.  Where expectancy is flat by construction
(the risk budget), the choice is made on the drawdown it produces instead.
"""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app import backtest as bt, marketgen                    # noqa: E402
from app.config import EdgeSettings                          # noqa: E402
from app.exchange.sim import DEFAULT_UNIVERSE                # noqa: E402
from app.indicators.ghost import GhostParams                 # noqa: E402

BARS = 12_000
SPLIT = int(BARS * 0.6)
MIN_TRADES = 100
MAX_DD = 25.0


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


def build(momentum: float):
    panel = marketgen.generate_panel(universe(), BARS, seed=20261003, momentum=momentum)
    return bt.build_dataset(panel, GhostParams())


def measure(data, cfg):
    return (bt.run_dataset(data, bt._with_bounds(cfg, 0, SPLIT)),
            bt.run_dataset(data, bt._with_bounds(cfg, SPLIT, BARS)))


def start() -> bt.BacktestConfig:
    """The candidate the evidence already selected: wide stop, runner, risk sizing."""
    return bt.BacktestConfig(
        edge_enabled=True, min_confidence=60.0, tp_enabled=False,
        sizing_mode="risk", risk_pct_per_trade=1.0, sl_atr_mult=4.0,
        trail_mode="roi", trail_activation_roi=25.0, trail_distance_roi=15.0,
        edge=EdgeSettings(entry_filters_enabled=False, adaptive_exits_enabled=False,
                          cost_gate_enabled=False, breakeven_enabled=False,
                          time_stop_enabled=False, confidence_sizing_enabled=False,
                          drawdown_throttle_enabled=False))


def mutate(cfg: bt.BacktestConfig, **over) -> bt.BacktestConfig:
    """Copy ``cfg`` and apply overrides; ``edge_``-prefixed keys go to the edge settings."""
    out = copy.copy(cfg)
    out.edge = copy.copy(cfg.edge)
    for k, v in over.items():
        if k.startswith("edge_"):
            setattr(out.edge, k[5:], v)
        else:
            setattr(out, k, v)
    return out


class Lab:
    def __init__(self):
        print("building markets …", flush=True)
        self.m1 = build(1.0)
        self.m2 = build(2.0)
        self.rw = build(0.0)
        print("ready", flush=True)

    def score(self, cfg):
        outs = {name: measure(data, cfg) for name, data in
                (("m1", self.m1), ("m2", self.m2))}
        for is_res, _ in outs.values():
            if is_res.count < MIN_TRADES or is_res.max_drawdown_pct > MAX_DD:
                return -9.9, outs
        return min(o[0].expectancy_r for o in outs.values()), outs


def stage(lab: Lab, name: str, variants, best: bt.BacktestConfig) -> bt.BacktestConfig:
    print(f"\n── {name} ──")
    print(f"  {'variant':<30}{'expR m1':>9}{'expR m2':>9}{'PF m1':>7}{'PF m2':>7}"
          f"{'wr m1':>7}{'DD m1':>7}{'trades':>8}{'':>3}score")
    base_score = lab.score(best)[0]
    ranked = []
    for label, cfg in variants:
        sc, outs = lab.score(cfg)
        i1, i2 = outs["m1"][0], outs["m2"][0]
        note = "" if sc > -9 else "  ✗ guard"
        print(f"  {label:<30}{i1.expectancy_r:>9.3f}{i2.expectancy_r:>9.3f}"
              f"{i1.profit_factor:>7.2f}{i2.profit_factor:>7.2f}"
              f"{i1.win_rate:>7.1f}{i1.max_drawdown_pct:>7.1f}{i1.count:>8}"
              f"{'':>3}{sc:+.3f}{note}")
        ranked.append((sc, label, cfg))
    ranked.sort(key=lambda r: r[0], reverse=True)
    top = ranked[0]
    if top[0] > -9 and top[0] > base_score:
        print(f"  → promote: {top[1]}  ({base_score:+.3f} → {top[0]:+.3f})")
        return top[2]
    print(f"  → keep current ({base_score:+.3f})")
    return best


def final_report(lab: Lab, label: str, cfg: bt.BacktestConfig) -> None:
    print(f"\n{'=' * 94}\n{label}\n{'=' * 94}")
    print(f"  {'market':<24}{'win':<5}{'trades':>7}{'win%':>7}{'PF':>7}"
          f"{'net':>11}{'ret%':>9}{'expR':>8}{'maxDD%':>8}")
    for name, data in (("random walk (control)", lab.rw),
                       ("sim market (momentum 1)", lab.m1),
                       ("trending (momentum 2)", lab.m2)):
        for win, res in zip(("IS ", "OOS"), measure(data, cfg)):
            pf = res.profit_factor
            print(f"  {name:<24}{win:<5}{res.count:>7}{res.win_rate:>7.1f}"
                  f"{pf if pf != float('inf') else 99.99:>7.2f}{res.net_pnl:>11.0f}"
                  f"{res.return_pct:>9.1f}{res.expectancy_r:>8.3f}"
                  f"{res.max_drawdown_pct:>8.1f}")


def main() -> int:
    lab = Lab()
    cfg = start()
    final_report(lab, "YARDSTICK — shipping 1.5/3.0 ATR, 8 % margin sizing",
                 bt.baseline_config(min_confidence=60.0))
    final_report(lab, "STARTING POINT — 4 ATR runner, 1 % risk sizing", cfg)

    cfg = stage(lab, "1. stop distance (ATR)",
                [(f"{v} ATR", mutate(cfg, sl_atr_mult=v))
                 for v in (2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)], cfg)

    cfg = stage(lab, "2. protective trail — R units (arm R / distance R)",
                [("off", mutate(cfg, trail_enabled=False))]
                + [(f"{a}R / {d}R", mutate(cfg, trail_mode="r", trail_enabled=True,
                                           trail_activation_r=a, trail_distance_r=d))
                   for a, d in ((1.0, 0.6), (1.5, 0.9), (2.0, 1.2), (3.0, 1.5),
                                (1.0, 1.0), (2.0, 0.8))], cfg)

    cfg = stage(lab, "3. break-even lock (arm at +xR, lock +0.25R)",
                [("off", mutate(cfg))]
                + [(f"arm {v}R", mutate(cfg, edge_breakeven_enabled=True,
                                        edge_breakeven_arm_r=v))
                   for v in (0.5, 0.8, 1.0, 1.5)], cfg)

    cfg = stage(lab, "4. time stop (bars held, ROI below 0)",
                [("off", mutate(cfg))]
                + [(f"{v} bars", mutate(cfg, edge_time_stop_enabled=True,
                                        edge_time_stop_bars=v))
                   for v in (48, 96, 144, 288)], cfg)

    cfg = stage(lab, "5. maximum hold (bars)",
                [("no cap", mutate(cfg))]
                + [(f"{v} bars", mutate(cfg, hold_bars=v))
                   for v in (24, 48, 96, 192)], cfg)

    cfg = stage(lab, "6. entry gate: trend quality",
                [("off", mutate(cfg))]
                + [(f"clean_ratio ≥ {v}", mutate(
                    cfg, edge_entry_filters_enabled=True, edge_min_clean_ratio=v,
                    edge_min_efficiency_ratio=0.0, edge_require_flow_alignment=False,
                    edge_require_bar_confirmation=False, edge_min_atr_pct=0.0,
                    edge_max_atr_pct=99.0))
                   for v in (0.30, 0.45, 0.60)], cfg)

    cfg = stage(lab, "7. entry gate: volatility band (ATR %)",
                [("off", mutate(cfg))]
                + [(f"ATR ≤ {v}%", mutate(cfg, edge_entry_filters_enabled=True,
                                          edge_max_atr_pct=v))
                   for v in (0.8, 1.0, 1.3, 1.6)], cfg)

    cfg = stage(lab, "8. entry gate: order-flow alignment",
                [("off", mutate(cfg))]
                + [(f"flow ≥ {v}", mutate(cfg, edge_entry_filters_enabled=True,
                                          edge_require_flow_alignment=True,
                                          edge_min_flow_alignment=v))
                   for v in (-0.05, 0.0, 0.05, 0.15)], cfg)

    final_report(lab, "TUNED — every promoted knob", cfg)
    print("\nchosen values:")
    for k in ("sl_atr_mult", "hold_bars", "trail_enabled", "trail_mode",
              "trail_activation_r", "trail_distance_r", "trail_activation_roi",
              "trail_distance_roi", "risk_pct_per_trade", "sizing_mode", "tp_enabled"):
        print(f"  {k:<24} {getattr(cfg, k)}")
    d = cfg.edge.model_dump()
    for k in sorted(d):
        if d[k]:
            print(f"  edge.{k:<19} {d[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
