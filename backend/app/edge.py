"""
Edge engine — the win-rate / profitability upgrade layer.

Everything the engine used to decide with "a flip happened, take it" now goes
through three extra questions, and every one of them is measurable:

    1. **Is this flip worth its cost?**   (``cost_gate``)
       A 5-minute flip system pays a round trip of 2 × taker fee on the
       notional.  At 10× that is ~1 % of the margin committed, while the
       default 1.5×ATR stop risks ~4.5 %.  Friction is therefore ~22 % of R —
       a trade whose target is not several multiples of that cost is paying
       rent, not taking an edge.

    2. **Is the market in a state where a flip means something?** (``gates``)
       A Shadow Rail flip in a chopping, low-efficiency, counter-flow,
       over-extended or dead market is noise.  Five independent, cheap filters
       reject it before a single dollar of margin is committed.

    3. **Is the exit sized to the market, and does it protect itself?**
       (``adaptive_exits`` / ``breakeven`` / ``time_stop``)
       The stop distance is normalised by volatility so the *cash* risk per
       trade stays in the band where fees are a small fraction of R; the single
       stop the trade already owns is then ratcheted to a fee-covered
       break-even as soon as the trade earns the right, and a trade that goes
       nowhere is cut instead of paying funding to stand still.

Design rules that are never violated:

* **One risk system per position.**  Break-even and the ROI trail *move* the
  single STOP_MARKET order the trade already has — they never add a second one.
* **Every rejection is named.**  ``EntryVerdict.reasons`` is what the dashboard
  and the workflow log show, so a filtered trade is auditable rather than
  mysterious.
* **Nothing is hidden behind a learned model.**  The gates are transparent
  thresholds; the logistic confidence model still runs on top of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .exchange.base import TAKER_FEE
from .util import Candle

# --------------------------------------------------------------------------- #
# feature extraction                                                          #
# --------------------------------------------------------------------------- #
def efficiency_ratio(closes: Sequence[float], i: int, bars: int) -> float:
    """
    Kaufman efficiency ratio at bar ``i``:  net move / sum of absolute moves.

    1.0 = a straight line (trend), 0.0 = pure chop.  This is the single cheapest
    honest "is this market trending right now" number there is, and it is
    computed from closes only, so it cannot repaint.
    """
    n = len(closes)
    if n < 2 or bars < 2:
        return 0.0
    start = max(0, i - bars)
    if i - start < 2:
        return 0.0
    window = closes[start:i + 1]
    net = abs(window[-1] - window[0])
    total = 0.0
    for k in range(1, len(window)):
        total += abs(window[k] - window[k - 1])
    if total <= 0:
        return 0.0
    return min(1.0, net / total)


def bar_body_ratio(candle: Candle) -> float:
    """(close - open) / (high - low) in [-1, 1] — the conviction of the bar."""
    rng = candle.h - candle.l
    if rng <= 0:
        return 0.0
    return max(-1.0, min(1.0, (candle.c - candle.o) / rng))


def signal_features(series: Any, i: int, candles: Sequence[Candle],
                    er_bars: int = 24) -> dict[str, float]:
    """
    Everything the gates need about one candidate flip, bar ``i``.

    ``series`` is a ``GhostSeries``; only the arrays it exposes are used, so
    this works identically for the live engine and the backtest lab.
    """
    close = float(series.close[i])
    atr = float(series.atr14[i]) if series.atr14[i] == series.atr14[i] else 0.0
    cr = float(series.clean_ratio[i]) if series.clean_ratio[i] == series.clean_ratio[i] else 0.0
    fb = float(series.flow_bias[i]) if series.flow_bias[i] == series.flow_bias[i] else 0.0
    rail = series.rail_value(i)
    rail_dist = abs((close - rail) / close * 100.0) if rail == rail and close else 0.0
    candle = candles[i]
    up_flags = [bool(series.turn_up[k]) or bool(series.turn_dn[k])
                for k in range(max(0, i - 400), i)]
    return {
        "close": close,
        "atr": atr,
        "atr_pct": (atr / close * 100.0) if close else 0.0,
        "clean_ratio": cr,
        "flow_bias": fb,
        "rail_distance_pct": rail_dist,
        "efficiency_ratio": efficiency_ratio(series.close, i, er_bars),
        "body_ratio": bar_body_ratio(candle),
        "volume_ratio": _volume_ratio(candles, i),
        "bars_since_flip": (len(up_flags) - 1 - max(
            (idx for idx, v in enumerate(up_flags) if v), default=-1)) if up_flags else 0,
        "trend": int(series.trend[i]),
        "htf_bull": bool(series.htf_bull[i] == 1.0),
    }


def _volume_ratio(candles: Sequence[Candle], i: int, lookback: int = 50) -> float:
    """Current bar volume / mean volume — participation behind the flip."""
    if i < lookback + 1 or len(candles) < lookback + 1:
        return 1.0
    window = candles[i - lookback:i]
    mean = sum(c.v for c in window) / len(window)
    if mean <= 0:
        return 1.0
    return candles[i].v / mean


def round_trip_cost_pct(leverage: int = 10, fee: float = TAKER_FEE) -> float:
    """
    Round-trip friction expressed as a percentage of **price**.

    Two taker fills are paid on the notional (``qty × price``), and the P&L of a
    price move is also ``qty × Δprice`` — so the leverage cancels and the cost is
    simply ``2 × fee``, i.e. 0.10 % of price at Binance's 0.05 % taker.

    The leverage argument is kept because callers pass it; it does not change
    the answer, which is exactly the point: leverage scales the *risk* but it
    cannot scale away the *friction*.
    """
    return 2.0 * fee * 100.0


# --------------------------------------------------------------------------- #
# entry gates                                                                 #
# --------------------------------------------------------------------------- #
@dataclass
class EntryVerdict:
    ok: bool
    score_delta: float = 0.0
    reasons: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "score_delta": round(self.score_delta, 2),
                "reasons": self.reasons,
                "features": {k: round(v, 4) for k, v in self.features.items()}}


def evaluate_entry(features: dict[str, float], s: Any, *, direction: str,
                   leverage: int = 10, tp_distance_pct: float = 0.0,
                   enabled: bool = True) -> EntryVerdict:
    """
    Apply the entry gates.  ``s`` is an ``EdgeSettings`` (or any object with the
    same attributes) so the backtester can sweep it without touching config.
    """
    reasons: list[str] = []
    delta = 0.0
    if not enabled:
        return EntryVerdict(True, 0.0, reasons, features)

    atr_pct = features.get("atr_pct", 0.0)
    cr = features.get("clean_ratio", 0.0)
    # ``entry_filters_enabled`` is the master switch for the quality gates —
    # turning it off must really turn them off, otherwise "no filters" silently
    # means "the default thresholds" and every A/B comparison is a lie.
    if s.entry_filters_enabled:
        if cr < s.min_clean_ratio:
            reasons.append(f"trend quality {cr:.2f} < {s.min_clean_ratio:.2f}")

        er = features.get("efficiency_ratio", 0.0)
        if er < s.min_efficiency_ratio:
            reasons.append(f"chop: efficiency {er:.2f} < {s.min_efficiency_ratio:.2f}")

        if atr_pct < s.min_atr_pct:
            reasons.append(f"market dead (ATR {atr_pct:.2f}% < {s.min_atr_pct:.2f}%)")
        elif atr_pct > s.max_atr_pct:
            reasons.append(f"volatility extreme (ATR {atr_pct:.2f}% > {s.max_atr_pct:.2f}%)")

        rail_dist = features.get("rail_distance_pct", 0.0)
        if rail_dist > s.max_rail_distance_pct:
            reasons.append(f"chasing: {rail_dist:.2f}% from the rail "
                           f"(> {s.max_rail_distance_pct:.2f}%)")

        if s.require_flow_alignment:
            bias = features.get("flow_bias", 0.0)
            aligned = bias if direction == "LONG" else -bias
            if aligned < s.min_flow_alignment:
                reasons.append(f"order flow fights the trade ({bias:+.2f})")

        if s.require_bar_confirmation:
            body = features.get("body_ratio", 0.0)
            signed = body if direction == "LONG" else -body
            if signed < s.min_body_ratio:
                reasons.append(f"signal bar closed against the trade "
                               f"(body {signed:+.2f} < {s.min_body_ratio:+.2f})")

    if s.cost_gate_enabled:
        cost = round_trip_cost_pct(leverage)
        if tp_distance_pct > 0:
            edge_pct, what = tp_distance_pct, f"target {tp_distance_pct:.2f}%"
        else:
            # no fixed target (runner mode): the gross move we expect to capture
            # is a fraction of an ATR, so price the same rule in volatility
            edge_pct = atr_pct * max(0.0, s.expected_edge_atr)
            what = (f"expected move {edge_pct:.3f}% "
                    f"({atr_pct:.2f}% ATR × {s.expected_edge_atr:g})")
        ratio = edge_pct / cost if cost else 0.0
        if ratio < s.min_edge_to_cost:
            reasons.append(f"edge/cost {ratio:.1f}× < {s.min_edge_to_cost:.1f}× "
                           f"({what} vs round-trip cost {cost:.2f}%)")

    # soft bonuses — they shift the analyst score without vetoing the trade
    if s.min_volume_ratio > 0:
        vr = features.get("volume_ratio", 1.0)
        if vr < 1.0:
            delta -= (1.0 - vr) * 6.0
        else:
            delta += min(4.0, (vr - 1.0) * 4.0)
    if cr >= 0.60:
        delta += 2.0

    return EntryVerdict(not reasons, delta, reasons, features)


# --------------------------------------------------------------------------- #
# adaptive exits                                                              #
# --------------------------------------------------------------------------- #
def exit_multipliers(atr_pct: float, clean_ratio: float, s: Any,
                     *, enabled: bool = True) -> tuple[float, float]:
    """
    Volatility-normalised stop / target, in ATR units.

    The problem it solves: a fixed 1.5×ATR stop risks a *different amount of
    cash* in every regime.  In a dead market (ATR 0.1 %) the stop is so tight
    that fees are most of R and noise stops us out; in a hot one it is so wide
    that a single loss is a hole.  Scaling the multiple by ``target/ATR%`` keeps
    the cash risk per trade roughly constant — and therefore keeps friction a
    constant, small fraction of R.

    Clean trends earn a wider target (they are the ones that run).
    """
    if not enabled:
        return s.sl_atr_base, s.tp_atr_base
    if atr_pct <= 0:
        return s.sl_atr_base, s.tp_atr_base
    scale = s.vol_target_atr_pct / atr_pct
    scale = max(s.sl_scale_min, min(s.sl_scale_max, scale))
    sl = s.sl_atr_base * scale
    bonus = 1.0 + s.quality_tp_bonus * max(0.0, clean_ratio - 0.45)
    tp = s.tp_atr_base * scale * bonus
    return sl, tp


def breakeven_price(*, entry: float, side: str, qty: float, entry_fee: float,
                    exit_fee_est: float, risk_distance: float, s: Any) -> float | None:
    """
    Price that locks the trade at *fee-covered* break-even — the stop level that
    turns "this trade is going to be a loss" into "this trade is a small win".

    Two parts, both strictly on the winning side of the entry:

      * both commissions, so the booked P&L cannot be negative, and
      * ``breakeven_buffer_r`` × the initial risk distance, so the locked
        profit is a real fraction of R rather than rounding noise.

    Returns ``None`` when the arithmetic lands on the losing side (the caller
    must then simply leave the existing stop where it is).
    """
    if qty <= 0 or entry <= 0:
        return None
    fees = abs(entry_fee) + abs(exit_fee_est)
    offset = fees / qty + max(0.0, s.breakeven_buffer_r) * max(0.0, risk_distance)
    if offset <= 0:
        return None
    if side == "LONG":
        price = entry + offset
        return price if price > entry else None
    price = entry - offset
    return price if price < entry else None


def should_arm_breakeven(*, roi: float, sl_roi: float, s: Any,
                         enabled: bool = True) -> bool:
    """
    Arm the break-even ratchet once the trade has travelled ``breakeven_arm_r``
    multiples of its own risk.  ``roi`` is the position ROI in points (percent of
    margin) and ``sl_roi`` the ROI of the initial stop (negative), so
    ``roi / |sl_roi|`` is exactly "R multiple achieved".
    """
    if not enabled or sl_roi >= 0:
        return False
    return (roi / abs(sl_roi)) >= s.breakeven_arm_r


def time_stop_due(bars_held: int, roi: float, s: Any, *, enabled: bool = True) -> bool:
    """A trade that has not earned anything in ``time_stop_bars`` is cut."""
    if not enabled or bars_held < s.time_stop_bars:
        return False
    return roi < s.time_stop_min_roi


def size_fraction(confidence: float, s: Any, *, enabled: bool = True) -> float:
    """
    Conviction-weighted sizing: the analyst score moves the margin between
    ``size_min_fraction`` and ``size_max_fraction`` of the configured size.

    High-conviction setups carry more, marginal ones carry less — the same
    portfolio risk, spent where the evidence is.
    """
    if not enabled:
        return 1.0
    span = max(1e-9, s.size_confidence_high - s.size_confidence_low)
    t = (confidence - s.size_confidence_low) / span
    t = max(0.0, min(1.0, t))
    return s.size_min_fraction + t * (s.size_max_fraction - s.size_min_fraction)


def throttle_active(*, drawdown_pct: float, loss_streak: int, s: Any,
                    enabled: bool = True) -> str | None:
    """
    Stop opening new trades while the account is bleeding.  Returns a reason
    (for the log) or ``None``.  Protective closes are never throttled.
    """
    if not enabled:
        return None
    if s.max_loss_streak and loss_streak >= s.max_loss_streak:
        return f"loss streak {loss_streak} >= {s.max_loss_streak}"
    if s.throttle_drawdown_pct > 0 and drawdown_pct >= s.throttle_drawdown_pct:
        return f"drawdown {drawdown_pct:.1f}% >= {s.throttle_drawdown_pct:.1f}%"
    return None


def describe(s: Any) -> dict[str, Any]:
    """Human-readable summary for Settings / the API."""
    gates = {
        "entry_filters_enabled": bool(s.entry_filters_enabled),
        "min_clean_ratio": s.min_clean_ratio,
        "min_efficiency_ratio": s.min_efficiency_ratio,
        "er_bars": s.er_bars,
        "min_atr_pct": s.min_atr_pct,
        "max_atr_pct": s.max_atr_pct,
        "max_rail_distance_pct": s.max_rail_distance_pct,
        "require_flow_alignment": bool(s.require_flow_alignment),
        "require_bar_confirmation": bool(s.require_bar_confirmation),
        "cost_gate_enabled": bool(s.cost_gate_enabled),
        "min_edge_to_cost": s.min_edge_to_cost,
    }
    exits = {
        "adaptive_exits_enabled": bool(s.adaptive_exits_enabled),
        "sl_atr_base": s.sl_atr_base,
        "tp_atr_base": s.tp_atr_base,
        "vol_target_atr_pct": s.vol_target_atr_pct,
        "breakeven_enabled": bool(s.breakeven_enabled),
        "breakeven_arm_r": s.breakeven_arm_r,
        "breakeven_buffer_r": s.breakeven_buffer_r,
        "time_stop_enabled": bool(s.time_stop_enabled),
        "time_stop_bars": s.time_stop_bars,
        "time_stop_min_roi": s.time_stop_min_roi,
    }
    sizing = {
        "confidence_sizing_enabled": bool(s.confidence_sizing_enabled),
        "size_min_fraction": s.size_min_fraction,
        "size_max_fraction": s.size_max_fraction,
        "size_confidence_low": s.size_confidence_low,
        "size_confidence_high": s.size_confidence_high,
        "drawdown_throttle_enabled": bool(s.drawdown_throttle_enabled),
        "throttle_drawdown_pct": s.throttle_drawdown_pct,
        "max_loss_streak": s.max_loss_streak,
    }
    return {"gates": gates, "exits": exits, "sizing": sizing}
