"""
Offline backtest lab.

The engine can only be judged on the trades it *would* have taken, and the live
5-minute cycle produces a few dozen of those per day — far too few to tune
anything on.  This module replays the engine's real decision chain

    Ghost/Shadow-Rail indicator ─▶ edge gates ─▶ analyst confidence ─▶ size
    ─▶ one TP/SL system ─▶ break-even / ROI trail / time stop ─▶ fees + funding

over a deterministic candle panel, thousands of times, so a change can be
scored before it is allowed anywhere near real money.

Honesty rules baked into the design:

* **One panel, many configs.**  The indicator series is computed once per
  symbol; every configuration is then replayed over the *same* bars, so a
  difference in the result is the configuration and nothing else.
* **In-sample / out-of-sample.**  ``split`` cuts the panel by time; the sweep
  selects on the first part and reports the second, so a curve-fitted default
  shows up as an IS→OOS collapse instead of a fake win.
* **Costs are always charged.**  Two taker fills per round trip plus funding
  every 8 h, exactly like the venue.  A configuration that only wins gross is
  reported as a loser.
* **Nothing is invented.**  If the market model has no edge, the baseline
  prints a profit factor near 1.0 — and the filters can only be judged against
  that number, not against zero.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import edge
from .confidence import ConfidenceModel
from .exchange.base import TAKER_FEE
from .indicators import ghost
from .indicators.ghost import GhostParams
from .util import Candle

BARS_PER_8H_5M = 96
DEFAULT_WARMUP = 320

# The lab scores setups with the *untrained* heuristic analyst so that a config
# is never credited for a model it cannot have in production yet.
_MODEL: ConfidenceModel | None = None


def _model() -> ConfidenceModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = ConfidenceModel(model_path="/nonexistent/shadow_rail_no_model.json")
    return _MODEL


# --------------------------------------------------------------------------- #
# configuration                                                               #
# --------------------------------------------------------------------------- #
@dataclass
class BacktestConfig:
    """Everything a run needs.  Defaults mirror the live engine's defaults."""

    # portfolio
    starting_equity: float = 10_000.0
    leverage: int = 10
    # "margin" = the shipping rule (size_pct_per_trade % of equity as margin);
    # "risk"   = fixed-fractional: the cash at risk if the stop is hit is
    #            risk_pct_per_trade % of equity, whatever the stop distance is.
    #            The two are only equal when the stop happens to be 1/8 of the
    #            way to a 10x liquidation — which is why a wider, volatility
    #            -normalised stop MUST ship with risk-based sizing.
    sizing_mode: str = "margin"
    size_pct_per_trade: float = 8.0
    risk_pct_per_trade: float = 1.0
    max_margin_per_trade_pct: float = 8.0
    max_margin_utilization_pct: float = 100.0
    max_concurrent_trades: int = 10
    fee: float = TAKER_FEE
    funding_rate: float = 0.0001          # per 8h epoch, positive = longs pay
    slippage_bps: float = 0.0

    # the ONE risk system (used verbatim when adaptive exits are off)
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    tp_enabled: bool = True
    use_reverse_signal_exit: bool = True
    hold_bars: int = 0                    # research: exit after N bars (0 = off)
    max_stop_distance_pct: float = 8.0
    liq_safety_buffer_pct: float = 35.0

    # analyst
    min_confidence: float = 60.0

    # protective trail — "roi" is the shipping rule (percent of margin),
    # "r" measures the same thing in multiples of the trade's own risk, which
    # is the only unit that survives a volatility-normalised stop
    trail_enabled: bool = True
    trail_mode: str = "roi"
    trail_activation_roi: float = 25.0
    trail_distance_roi: float = 15.0
    trail_min_step_roi: float = 1.0
    trail_activation_r: float = 1.5
    trail_distance_r: float = 0.9
    trail_min_step_r: float = 0.1

    # edge layer
    edge: Any = None                      # EdgeSettings-like
    edge_enabled: bool = True

    # walk-forward bounds
    warmup: int = DEFAULT_WARMUP
    start_bar: int = 0
    end_bar: int = 0                      # 0 = whole panel

    def risk_mode(self) -> str:
        return "edge" if self.edge_enabled else "baseline"



# --------------------------------------------------------------------------- #
# results                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    symbol: str
    side: str
    bar_in: int
    bar_out: int
    entry: float
    exit_price: float
    qty: float
    margin: float
    risk_amount: float
    gross: float
    fee: float
    funding: float
    net: float
    reason: str
    confidence: float
    r_multiple: float
    mfe_r: float
    mae_r: float
    equity_at_entry: float = 0.0

    @property
    def win(self) -> bool:
        return self.net > 0


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    starting_equity: float = 10_000.0
    ending_equity: float = 10_000.0
    bars: int = 0
    signals_seen: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    label: str = ""

    # ------------------------------------------------------------------ stats
    @property
    def count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.win)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.count * 100.0) if self.count else 0.0

    @property
    def gross_profit(self) -> float:
        return sum(t.net for t in self.trades if t.net > 0)

    @property
    def gross_loss(self) -> float:
        return -sum(t.net for t in self.trades if t.net < 0)

    @property
    def profit_factor(self) -> float:
        if self.gross_loss <= 0:
            return float("inf") if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def net_pnl(self) -> float:
        return self.ending_equity - self.starting_equity

    @property
    def total_fees(self) -> float:
        return sum(t.fee for t in self.trades)

    @property
    def total_funding(self) -> float:
        return sum(t.funding for t in self.trades)

    @property
    def expectancy(self) -> float:
        return (self.net_pnl / self.count) if self.count else 0.0

    @property
    def expectancy_r(self) -> float:
        if not self.count:
            return 0.0
        return sum(t.r_multiple for t in self.trades) / self.count

    @property
    def max_drawdown_pct(self) -> float:
        peak = self.starting_equity
        worst = 0.0
        for v in self.equity_curve:
            peak = max(peak, v)
            if peak > 0:
                worst = max(worst, (peak - v) / peak * 100.0)
        return worst

    @property
    def return_pct(self) -> float:
        return (self.net_pnl / self.starting_equity * 100.0) if self.starting_equity else 0.0

    @property
    def exposure_pct(self) -> float:
        if not self.bars:
            return 0.0
        held = sum(t.bar_out - t.bar_in + 1 for t in self.trades)
        return held / self.bars * 100.0

    def reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.trades:
            out[t.reason] = out.get(t.reason, 0) + 1
        return out

    def summary(self) -> dict[str, Any]:
        pf = self.profit_factor
        return {
            "label": self.label,
            "trades": self.count,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(pf, 3) if math.isfinite(pf) else None,
            "net_pnl": round(self.net_pnl, 2),
            "return_pct": round(self.return_pct, 2),
            "expectancy": round(self.expectancy, 2),
            "expectancy_r": round(self.expectancy_r, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "fees": round(self.total_fees, 2),
            "funding": round(self.total_funding, 2),
            "exposure_pct": round(self.exposure_pct, 1),
            "signals": self.signals_seen,
            "rejected": dict(sorted(self.rejected.items(),
                                    key=lambda kv: -kv[1])[:6]),
            "reasons": self.reasons(),
            "bars": self.bars,
        }


# --------------------------------------------------------------------------- #
# precomputation                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class SymbolData:
    symbol: str
    candles: list[Candle]
    series: Any
    signals: list[tuple[int, str]] = field(default_factory=list)   # (bar, direction)
    features: dict[int, dict[str, float]] = field(default_factory=dict)


def build_symbol_data(candles: Sequence[Candle], params: GhostParams | None = None,
                      symbol: str = "", er_bars: int = 24,
                      base_tf: str = "5m") -> SymbolData:
    """Compute the indicator once and cache every candidate flip + its features."""
    params = params or GhostParams()
    series = ghost.compute(list(candles), params, base_tf)
    signals: list[tuple[int, str]] = []
    feats: dict[int, dict[str, float]] = {}
    warm = max(series.warmup_bars, 60)
    for i in range(warm, len(candles)):
        direction = "LONG" if series.turn_up[i] else ("SHORT" if series.turn_dn[i] else "")
        if not direction:
            continue
        if params.mtfGate and not series.htf_ready[i]:
            continue
        signals.append((i, direction))
        feats[i] = edge.signal_features(series, i, candles, er_bars)
    return SymbolData(symbol=symbol or "SYM", candles=list(candles), series=series,
                      signals=signals, features=feats)


def build_dataset(panel, params: GhostParams | None = None, er_bars: int = 24,
                  base_tf: str = "5m", symbols: Iterable[str] | None = None):
    names = list(symbols) if symbols else panel.symbols
    return [build_symbol_data(panel.candles[s], params, s, er_bars, base_tf)
            for s in names if s in panel.candles]


# --------------------------------------------------------------------------- #
# the walk                                                                    #
# --------------------------------------------------------------------------- #
class _Position:
    __slots__ = ("symbol", "side", "bar_in", "entry", "qty", "margin", "notional",
                 "sl", "tp", "risk_amount", "risk_distance", "entry_fee",
                 "peak", "peak_roi", "peak_r", "trail_active", "be_armed", "confidence",
                 "mfe_r", "mae_r", "last_funding_bar", "funding", "roi_step",
                 "equity_at_entry")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, 0.0))


@dataclass
class _Ctx:
    """Per-run scratch: the config plus the reverse-signal lookup."""
    cfg: BacktestConfig
    reverse: dict[tuple[str, int], str] = field(default_factory=dict)


def run_dataset(dataset: Sequence[SymbolData], cfg: BacktestConfig,
                label: str = "") -> Result:
    """Replay the engine's decision chain over a precomputed dataset."""
    es = cfg.edge
    model = _model()
    res = Result(label=label or cfg.risk_mode(), starting_equity=cfg.starting_equity)
    res.ending_equity = cfg.starting_equity

    n_bars = max((len(d.candles) for d in dataset), default=0)
    start = max(cfg.warmup, cfg.start_bar)
    end = cfg.end_bar or n_bars
    res.bars = max(0, end - start)

    equity = cfg.starting_equity
    peak_equity = equity
    open_pos: dict[str, _Position] = {}
    by_bar: dict[int, list[tuple[str, str]]] = {}
    sym_stats: dict[str, list[int]] = {}
    loss_streak = 0
    last_symbol_exit: dict[str, int] = {}
    equity_curve: list[float] = []

    for d in dataset:
        for bar, direction in d.signals:
            if start <= bar < end:
                by_bar.setdefault(bar, []).append((d.symbol, direction))
                res.signals_seen += 1

    data_by_symbol = {d.symbol: d for d in dataset}
    ctx = _Ctx(cfg=cfg,
               reverse={(d.symbol, bar): direction
                        for d in dataset for bar, direction in d.signals})

    for bar in range(start, end):
        # ── 1. exits & protection, before new entries free the margin ──────
        for symbol, pos in list(open_pos.items()):
            d = data_by_symbol[symbol]
            c = d.candles[bar]
            close_reason, exit_price = _process_bar(pos, c, bar, ctx)
            if close_reason:
                net, fee, funding = _book(pos, exit_price, cfg)
                equity += net
                r = (pos.mfe_r, pos.mae_r)
                res.trades.append(Trade(
                    symbol=symbol, side=pos.side, bar_in=pos.bar_in, bar_out=bar,
                    entry=pos.entry, exit_price=exit_price, qty=pos.qty,
                    margin=pos.margin, risk_amount=pos.risk_amount,
                    equity_at_entry=pos.equity_at_entry,
                    gross=(exit_price - pos.entry) * pos.qty
                    * (1 if pos.side == "LONG" else -1),
                    fee=fee, funding=funding, net=net, reason=close_reason,
                    confidence=pos.confidence,
                    r_multiple=(net / pos.risk_amount) if pos.risk_amount else 0.0,
                    mfe_r=r[0], mae_r=r[1]))
                del open_pos[symbol]
                last_symbol_exit[symbol] = bar
                sym_stats.setdefault(symbol, [0, 0])
                sym_stats[symbol][0] += 1
                sym_stats[symbol][1] += 1 if net > 0 else 0
                loss_streak = loss_streak + 1 if net <= 0 else 0
        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)

        # ── 2. entries ─────────────────────────────────────────────────────
        if len(open_pos) >= cfg.max_concurrent_trades:
            continue
        dd_pct = (peak_equity - equity) / peak_equity * 100.0 if peak_equity else 0.0
        if cfg.edge_enabled and es is not None:
            blocked = edge.throttle_active(
                drawdown_pct=dd_pct, loss_streak=loss_streak, s=es,
                enabled=es.drawdown_throttle_enabled)
            if blocked:
                res.rejected["throttle: " + blocked] = \
                    res.rejected.get("throttle: " + blocked, 0) + 1
                continue

        for symbol, direction in by_bar.get(bar, ()):
            if len(open_pos) >= cfg.max_concurrent_trades:
                break
            if symbol in open_pos:
                continue
            if bar - last_symbol_exit.get(symbol, -10_000) < 1:
                continue
            d = data_by_symbol[symbol]
            feats = d.features.get(bar)
            if feats is None:
                continue
            c = d.candles[bar]
            entry_price = c.c * (1.0 + cfg.slippage_bps / 10_000.0)

            # ── exit levels first: the cost gate needs the target distance ──
            atr = feats["atr"]
            if atr <= 0:
                continue
            if cfg.edge_enabled and es is not None and es.adaptive_exits_enabled:
                sl_mult, tp_mult = edge.exit_multipliers(
                    feats["atr_pct"], feats["clean_ratio"], es, enabled=True)
            else:
                sl_mult, tp_mult = cfg.sl_atr_mult, cfg.tp_atr_mult
            atr_pct = feats["atr_pct"]
            if sl_mult * atr_pct > cfg.max_stop_distance_pct:
                sl_mult = cfg.max_stop_distance_pct / max(atr_pct, 1e-9)
            sl = entry_price - sl_mult * atr if direction == "LONG" else entry_price + sl_mult * atr
            tp = (entry_price + tp_mult * atr if direction == "LONG"
                  else entry_price - tp_mult * atr) if cfg.tp_enabled else 0.0

            # ── edge gates ────────────────────────────────────────────────
            tp_dist_pct = abs(tp - entry_price) / entry_price * 100.0 if tp else 0.0
            verdict = edge.evaluate_entry(
                feats, es, direction=direction, leverage=cfg.leverage,
                tp_distance_pct=tp_dist_pct, enabled=cfg.edge_enabled and es is not None)
            if not verdict.ok:
                for reason in verdict.reasons:
                    key = reason.split(" (")[0].split(" — ")[0]
                    key = _bucket_reason(reason)
                    res.rejected[key] = res.rejected.get(key, 0) + 1
                continue

            # ── analyst confidence (the live model, untrained / heuristic) ──
            confidence_feats = {
                "trend_quality": feats["clean_ratio"],
                "htf_bull": feats["htf_bull"],
                "direction": direction,
                "flow_bias": feats["flow_bias"],
                "atr_pct": atr_pct,
                "rail_distance_pct": feats["rail_distance_pct"],
                "tier": "strong" if feats["clean_ratio"] >= 0.60 else "normal",
            }
            stats = sym_stats.get(symbol)
            symbol_stats = None
            if stats and stats[0] >= 3:
                symbol_stats = {"trades": stats[0],
                                "win_rate": stats[1] / stats[0] * 100.0}
            score = model.score(confidence_feats, symbol_stats,
                                analyst_variance=verdict.score_delta)
            if score.score < cfg.min_confidence:
                res.rejected["confidence below threshold"] = \
                    res.rejected.get("confidence below threshold", 0) + 1
                continue

            # ── size & open ───────────────────────────────────────────────
            risk_distance = abs(entry_price - sl)
            fraction = edge.size_fraction(
                score.score, es,
                enabled=cfg.edge_enabled and es is not None
                and es.confidence_sizing_enabled) if es is not None else 1.0
            stop_frac = risk_distance / entry_price if entry_price else 0.0
            if cfg.sizing_mode == "risk" and stop_frac > 0:
                risk_cash = equity * (cfg.risk_pct_per_trade / 100.0) * fraction
                margin = risk_cash / (stop_frac * cfg.leverage)
            else:
                margin = equity * (cfg.size_pct_per_trade / 100.0) * fraction
            # the shipping 8 %-of-equity margin rule stays as a hard ceiling
            cap = equity * (cfg.max_margin_per_trade_pct / 100.0)
            if margin > cap:
                margin = cap
            margin_in_use = sum(o.margin for o in open_pos.values())
            headroom = equity * (cfg.max_margin_utilization_pct / 100.0) - margin_in_use
            if margin > headroom:
                margin = headroom
            if margin <= 0:
                continue
            notional = margin * cfg.leverage
            qty = notional / entry_price
            if qty <= 0 or notional < 5.0:
                continue
            open_pos[symbol] = _Position(
                symbol=symbol, side=direction, bar_in=bar, entry=entry_price,
                qty=qty, margin=margin, notional=notional, sl=sl, tp=tp,
                risk_amount=risk_distance * qty, risk_distance=risk_distance,
                entry_fee=notional * cfg.fee, peak=entry_price, peak_roi=0.0,
                trail_active=False, be_armed=False, confidence=score.score,
                mfe_r=0.0, mae_r=0.0, last_funding_bar=bar, funding=0.0,
                roi_step=margin / (100.0 * qty), equity_at_entry=equity)

    # ── mark anything still open to the last bar ───────────────────────────
    for symbol, pos in list(open_pos.items()):
        d = data_by_symbol[symbol]
        exit_price = d.candles[min(end, len(d.candles)) - 1].c
        net, fee, funding = _book(pos, exit_price, cfg)
        equity += net
        res.trades.append(Trade(
            symbol=symbol, side=pos.side, bar_in=pos.bar_in, bar_out=end - 1,
            entry=pos.entry, exit_price=exit_price, qty=pos.qty, margin=pos.margin,
            risk_amount=pos.risk_amount,
            gross=(exit_price - pos.entry) * pos.qty * (1 if pos.side == "LONG" else -1),
            fee=fee, funding=funding, net=net, reason="end_of_run",
            confidence=pos.confidence,
            r_multiple=(net / pos.risk_amount) if pos.risk_amount else 0.0,
            mfe_r=pos.mfe_r, mae_r=pos.mae_r))

    res.ending_equity = equity
    res.equity_curve = equity_curve
    res.trades.sort(key=lambda t: t.bar_in)
    return res


def _bucket_reason(reason: str) -> str:
    for key in ("trend quality", "chop", "market dead", "volatility extreme",
                "chasing", "order flow", "signal bar", "edge/cost"):
        if reason.startswith(key):
            return key
    return reason[:40]


def _process_bar(pos: _Position, c: Candle, bar: int, ctx: _Ctx
                 ) -> tuple[str, float]:
    """
    Advance one open position by one bar.

    Order of events inside the bar is deliberately pessimistic: a bar that can
    touch both levels is assumed to hit the **stop** first, and a gap through the
    stop is filled at the worse of the two prices.  A backtest that assumes the
    favourable order only manufactures edge.
    """
    cfg = ctx.cfg
    es = cfg.edge
    side = pos.side
    entry = pos.entry
    qty = pos.qty
    margin = pos.margin
    if margin <= 0 or qty <= 0:
        return "invalid", c.c

    # ── 1. hard exits ──────────────────────────────────────────────────────
    if side == "LONG":
        if c.o <= pos.sl:
            return "sl", min(c.o, pos.sl)
        if c.l <= pos.sl:
            return "sl", pos.sl
        if pos.tp and c.h >= pos.tp:
            return "tp", pos.tp
        mark_hi, mark_lo = c.h, c.l
    else:
        if c.o >= pos.sl:
            return "sl", max(c.o, pos.sl)
        if c.h >= pos.sl:
            return "sl", pos.sl
        if pos.tp and c.l <= pos.tp:
            return "tp", pos.tp
        mark_hi, mark_lo = c.l, c.h

    # ── 2. track the best/worst excursion (in R) ───────────────────────────
    if pos.risk_amount > 0:
        fav = (mark_hi - entry) * qty if side == "LONG" else (entry - mark_lo) * qty
        adv = (mark_lo - entry) * qty if side == "LONG" else (entry - mark_hi) * qty
        pos.mfe_r = max(pos.mfe_r, fav / pos.risk_amount)
        pos.mae_r = min(pos.mae_r, adv / pos.risk_amount)

    # ── 3. protective ratchets — the ONE stop, only ever tightened ──────────
    # Two ratchets can move it: the ROI/R trail and the fee-covered break-even.
    # Both are computed as a *candidate* level; the stricter one wins, it is
    # only written if it is a real improvement, and if the bar already traded
    # through it the position is booked there instead of pretending the stop
    # sat somewhere kinder.
    step = margin / (100.0 * qty) if qty else 0.0
    candidate, kind = None, ""

    if cfg.trail_enabled and step > 0:
        peak_price = max(pos.peak, mark_hi) if side == "LONG" else min(pos.peak, mark_lo)
        pos.peak = peak_price
        roi = _roi(entry, peak_price, qty, margin, side)
        pos.peak_roi = max(pos.peak_roi, roi)
        act_roi, dist_roi, min_step = (cfg.trail_activation_roi,
                                       cfg.trail_distance_roi, cfg.trail_min_step_roi)
        if cfg.trail_mode == "r":
            # one R = risk_amount/margin, expressed in ROI points — the only
            # unit that survives a volatility-normalised stop and risk sizing
            roi_per_r = abs(pos.risk_amount) / margin * 100.0 if margin else 0.0
            act_roi = cfg.trail_activation_r * roi_per_r
            dist_roi = cfg.trail_distance_r * roi_per_r
            min_step = cfg.trail_min_step_r * roi_per_r
        if pos.peak_roi >= act_roi:
            floor_roi = max(0.0, max(act_roi - dist_roi, pos.peak_roi - dist_roi))
            cand = entry + floor_roi * step if side == "LONG" else entry - floor_roi * step
            if _improves(cand, pos.sl, side, min_step * step, pos.trail_active):
                candidate, kind = cand, "trail"

    if (cfg.edge_enabled and es is not None and es.breakeven_enabled
            and not pos.be_armed):
        roi_now = _roi(entry, c.c, qty, margin, side)
        sl_roi = -abs(pos.risk_amount) / margin * 100.0 if margin else 0.0
        if edge.should_arm_breakeven(roi=roi_now, sl_roi=sl_roi, s=es, enabled=True):
            be = edge.breakeven_price(entry=entry, side=side, qty=qty,
                                      entry_fee=pos.entry_fee,
                                      exit_fee_est=entry * qty * cfg.fee,
                                      risk_distance=pos.risk_distance, s=es)
            pos.be_armed = True
            if be is not None and _improves(be, pos.sl, side, 0.0, False):
                if candidate is None or (side == "LONG" and be > candidate) \
                        or (side == "SHORT" and be < candidate):
                    candidate, kind = be, "breakeven"

    if candidate is not None:
        # the bar may already have traded through the new level
        if (side == "LONG" and c.l <= candidate) or (side == "SHORT" and c.h >= candidate):
            return ("trail" if kind == "trail" else "sl"), candidate
        pos.sl = candidate
        if kind == "trail":
            pos.trail_active = True

    # ── 4b. fixed-hold research exit ───────────────────────────────────────
    if cfg.hold_bars and bar - pos.bar_in >= cfg.hold_bars:
        return "hold_expiry", c.c

    # ── 5. time stop ───────────────────────────────────────────────────────
    if cfg.edge_enabled and es is not None and es.time_stop_enabled:
        roi_now = _roi(entry, c.c, qty, margin, side)
        if edge.time_stop_due(bar - pos.bar_in, roi_now, es, enabled=True):
            return "time_stop", c.c

    # ── 6. funding (every 8h epoch crossed) ────────────────────────────────
    epochs = (bar - pos.last_funding_bar) // BARS_PER_8H_5M
    if epochs > 0:
        pos.last_funding_bar += epochs * BARS_PER_8H_5M
        pos.funding += -pos.notional * cfg.funding_rate * epochs \
            * (1 if side == "LONG" else -1)

    # ── 7. reverse signal exit ─────────────────────────────────────────────
    if cfg.use_reverse_signal_exit:
        wanted = "SHORT" if side == "LONG" else "LONG"
        if ctx.reverse.get((pos.symbol, bar)) == wanted:
            return "reverse_signal", c.c

    return "", 0.0


def _improves(candidate: float, current: float, side: str, min_step: float,
              already_active: bool) -> bool:
    """True when ``candidate`` tightens the stop enough to be worth re-placing."""
    if side == "LONG":
        gain = candidate - current
    else:
        gain = current - candidate
    if gain <= 0:
        return False
    return (not already_active) or min_step <= 0 or gain >= min_step


def _roi(entry: float, price: float, qty: float, margin: float, side: str) -> float:
    if margin <= 0 or qty <= 0:
        return 0.0
    move = (price - entry) * (1.0 if side == "LONG" else -1.0)
    return move * qty / margin * 100.0


def _book(pos: _Position, exit_price: float, cfg: BacktestConfig
          ) -> tuple[float, float, float]:
    """(net, fees, funding) for a closed position."""
    exit_fee = exit_price * pos.qty * cfg.fee
    gross = (exit_price - pos.entry) * pos.qty * (1 if pos.side == "LONG" else -1)
    fee = pos.entry_fee + exit_fee
    net = gross - fee + pos.funding
    return net, fee, pos.funding


# --------------------------------------------------------------------------- #
# presets & sweeps                                                            #
# --------------------------------------------------------------------------- #
def baseline_config(**kw) -> BacktestConfig:
    """The engine exactly as it shipped: 1.5/3.0 ATR, no edge layer."""
    cfg = BacktestConfig(edge_enabled=False, **kw)
    return cfg


def sweep(dataset: Sequence[SymbolData], base: BacktestConfig,
          grid: dict[str, list[Any]], *, split_at: int,
          score_key: str = "profit_factor", min_trades: int = 25
          ) -> list[dict[str, Any]]:
    """
    Evaluate the cross-product of ``grid`` (attributes of ``cfg.edge``), select
    on the in-sample half and report both halves.

    Returns rows sorted by in-sample score, each carrying the out-of-sample
    numbers so an overfitted setting is visible on the same line.
    """
    import itertools

    keys = list(grid)
    rows: list[dict[str, Any]] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        for k, v in zip(keys, combo):
            setattr(base.edge, k, v)
        is_res = run_dataset(dataset, _with_bounds(base, 0, split_at), "IS")
        if is_res.count < min_trades:
            continue
        oos_res = run_dataset(dataset, _with_bounds(base, split_at, 0), "OOS")
        rows.append({
            "params": dict(zip(keys, combo)),
            "is": is_res.summary(),
            "oos": oos_res.summary(),
            "is_score": is_res.summary().get(score_key) or 0.0,
            "oos_score": oos_res.summary().get(score_key) or 0.0,
        })
    rows.sort(key=lambda r: r["is_score"], reverse=True)
    return rows


def _with_bounds(cfg: BacktestConfig, start: int, end: int) -> BacktestConfig:
    out = copy.copy(cfg)
    out.start_bar = start
    out.end_bar = end
    return out
